"""Read a run's results/scores into per-scenario, per-metric arrays and apply the noise-adjusted verdict rules."""
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.core.models import Metric, Plan, PromptVersion, Run, RunResult, Score
from app.pipeline import verdicts as V

CONTROL = 1  # judge_repeat_idx used by A/A control comparisons (old r0 vs old r1); real scores use 0


@dataclass
class Collected:
    # data[scenario][metric] = {"old": {repeat: v}, "new": {repeat: v}, "pair": {repeat: (pref, disputed)}, "ctrl": [pref]}
    data: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)  # (scenario, "old"|"new", repeat) -> RunResult
    scores: dict = field(default_factory=dict)  # (run_result_id, metric_id) -> Score


def versions(s: Session, run: Run) -> tuple[str, str]:
    """(base, test) prompt version ids. Normal runs compare old vs new; held-out validation runs compare any two versions."""
    cfg = run.config or {}
    if cfg.get("base_version_id") and cfg.get("test_version_id"):
        return cfg["base_version_id"], cfg["test_version_id"]
    plan = s.get(Plan, run.plan_id)
    pv = {v.label: v.id for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == plan.project_id)).all()}
    return pv["old"], pv["new"]


def collect(s: Session, run: Run, scenario_ids: set[str] | None = None) -> Collected:
    old_id, new_id = versions(s, run)
    c = Collected()
    by_id = {}
    rq = select(RunResult).where(RunResult.run_id == run.id)
    if scenario_ids is not None:
        rq = rq.where(RunResult.scenario_id.in_(scenario_ids))
    for rr in s.exec(rq).all():
        label = "old" if rr.prompt_version_id == old_id else "new"
        c.results[(rr.scenario_id, label, rr.repeat_idx)] = rr
        by_id[rr.id] = (rr.scenario_id, label, rr.repeat_idx)
    q = select(Score).join(RunResult, RunResult.id == Score.run_result_id).where(RunResult.run_id == run.id)
    if scenario_ids is not None:
        q = q.where(RunResult.scenario_id.in_(scenario_ids))
    for sc in s.exec(q).all():
        if sc.run_result_id not in by_id:
            continue
        sid, label, r = by_id[sc.run_result_id]
        c.scores[(sc.run_result_id, sc.metric_id)] = sc
        m = c.data.setdefault(sid, {}).setdefault(sc.metric_id, {"old": {}, "new": {}, "pair": {}, "ctrl": []})
        if sc.details and "verdict" in sc.details:  # pairwise
            pref = 2 * sc.value - 1
            if sc.judge_repeat_idx == CONTROL:
                m["ctrl"].append(pref)
            else:
                m["pair"][r] = (pref, sc.disputed)
        else:
            m[label][r] = sc.value
    return c


def noise_vars(c: Collected, metrics: list[Metric], pair_ids: set[str], cluster_of: dict[str, str | None],
               scenario_ids: set[str] | None = None) -> dict:
    """Old-vs-old noise per metric, globally and per cluster (shrunk toward global).
    Ordinary metrics: variance of the OLD prompt's first two repeats. Pairwise metrics: variance of the preference across repeats
    within a scenario (halved: a preference compares two noisy runs), floored by the A/A control (old r0 vs old r1)."""
    out = {}
    for m in metrics:
        per_scn = {}
        for sid, d in c.data.items():
            if m.id in d and (scenario_ids is None or sid in scenario_ids):
                # ONLY repeats 0 and 1: every scenario gets those no matter when it stops. Later repeats exist only for cases
                # the adaptive rule found unstable, and using them would bias the noise estimate downward (more dof, no extra SS).
                per_scn[sid] = ([p for r, (p, _) in sorted(d[m.id]["pair"].items()) if r < 2] if m.id in pair_ids
                                else [v for r, v in sorted(d[m.id]["old"].items()) if r < 2])
        scale = 0.5 if m.id in pair_ids else 1.0
        g_var, g_dof = V.pooled_variance(list(per_scn.values()))
        g_var *= scale
        floor = 0.0
        if m.id in pair_ids:
            ctrl = [p for sid, d in c.data.items() if scenario_ids is None or sid in scenario_ids for p in d.get(m.id, {}).get("ctrl", [])]
            tau2 = (sum(p * p for p in ctrl) + 2 * V.settings.noise_prior_var * V.settings.noise_prior_n) / (len(ctrl) + V.settings.noise_prior_n)
            floor = max(tau2 / 2, V.settings.pair_noise_floor)
            g_var = max(g_var, floor)
        by_cluster = {}
        for cid in {cluster_of.get(sid) for sid in per_scn} - {None}:
            groups = [g for sid, g in per_scn.items() if cluster_of.get(sid) == cid]
            by_cluster[cid] = max(V.shrunk_variance(groups, g_var / scale) * scale, floor)
        out[m.id] = {"var": g_var, "dof": g_dof, "clusters": by_cluster}
    return out


def worst_var(n: dict) -> float:
    return max([n["var"], *n.get("clusters", {}).values()])


def _binary(*arms) -> bool:
    return all(v in (0.0, 1.0) for arm in arms for v in arm)


def metric_verdicts(per_metric: dict, noise: dict, pair_ids: set[str], m_tests: int = 1, cluster_id: str | None = None, max_r: int | None = None) -> dict:
    """{metric_id: {verdict, old, new, p|band}} for ONE scenario. Strictly paired: only repeats where both arms have a score
    count (so outputs a lever skipped never bias the comparison). `m_tests` = comparisons in the run (multiplicity).
    Binary and pairwise metrics use exact tests (rare flips are heavy-tailed: a normal band is anti-conservative);
    continuous metrics use the noise band."""
    res = {}
    for mid, d in per_metric.items():
        n = noise.get(mid, {})
        var = n.get("clusters", {}).get(cluster_id, n.get("var", V.settings.noise_prior_var))
        if mid in pair_ids:
            pref = [p for _, (p, _) in sorted(d["pair"].items())]
            if not pref:
                continue
            verdict, p = V.verdict_pairwise(pref, var, m_tests)
            res[mid] = {"verdict": verdict, "old": 0.0, "new": sum(pref) / len(pref), "p": p, "k": len(pref),
                        "decidable": max_r is None or V.needed_repeats(min(max(var, 1e-3), 0.5), m_tests) <= max_r,
                        "disputed": sum(x for _, x in d["pair"].values())}
            continue
        rs = sorted(d["old"].keys() & d["new"].keys())
        if not rs:
            continue
        old, new, k = [d["old"][r] for r in rs], [d["new"][r] for r in rs], len(rs)
        if _binary(old, new):
            verdict, p = V.verdict_binary(old, new, var, m_tests)
            extra = {"p": p, "decidable": max_r is None or V.needed_repeats(V.q_from_var(var), m_tests) <= max_r}
        else:
            verdict, extra = V.verdict(old, new, V.band(var, k, k, m_tests), V.band(var, k, k)), {"band": V.band(var, k, k, m_tests)}
        res[mid] = {"verdict": verdict, "old": sum(old) / k, "new": sum(new) / k, "k": k, **extra}
    return res
