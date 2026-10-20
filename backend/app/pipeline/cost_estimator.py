"""Stage 4: probe real per-call costs, then estimate low/expected/high per tier and per cost lever."""
import asyncio
import hashlib
import json

import numpy as np
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.agents.schemas import JudgeDef
from app.config import settings
from app.core.embeddings import scenario_text
from app.core.llm import LLMClient
from app.core.models import CostEstimate, Metric, Plan, Project, PromptVersion, Scenario
from app.pipeline import trimmer
from app.pipeline.execute import fill_optional, project_optional, run_target
from app.pipeline.metric_plan import cheap_cost_ratio, cheap_model_for, evaluate, is_easy_metric, is_pairwise
from app.pipeline.metrics_engine import metric_applies, run_programmatic
from app.pipeline.planner import PlanError
from app.pipeline.prioritizer import candidates, is_protected, rank

LEVERS = {"programmatic_first": True, "cheap_judge_easy_rubrics": False, "adaptive_repeats": True, "early_stopping": True}
LEVER_NOTES = {
    "programmatic_first": "LLM judges only run on outputs that pass the programmatic checks; the rest are reported by their failed check",
    "cheap_judge_easy_rubrics": "short binary rubrics go to a cheaper judge",
    "adaptive_repeats": f"quiet cases stop after 2 repeats (assumed {settings.adaptive_stable_share:.0%} of cases); cases that differ or flip "
                        f"are confirmed with up to {settings.max_repeats_adaptive} repeats, so this costs MORE when many cases differ",
    "early_stopping": "quiet cases stop after 2 repeats but never run beyond the planned repeats (same stop rule as adaptive repeats; only counts when that is off)",
}


class Metered:
    """Wraps LLMClient and records the cost of every call (cache hits count at their original price)."""

    def __init__(self, client: LLMClient):
        self.client, self.full, self.paid = client, [], []

    async def complete(self, *a, **kw):
        r = await self.client.complete(*a, **kw)
        self.full.append(r.full_cost)
        self.paid.append(r.cost)
        return r


def _stats(xs: list[float]) -> dict | None:
    if not xs:
        return None
    return {"mean": float(np.mean(xs)), "p10": float(np.percentile(xs, 10)),
            "p90": float(np.percentile(xs, settings.cost_high_percentile * 100)), "n": len(xs)}


def active_metrics(s: Session, project_id: str) -> list[Metric]:
    return list(s.exec(select(Metric).where(Metric.project_id == project_id, Metric.status != "rejected")).all())


def fingerprint(p: Project, pv: dict, metrics: list[Metric]) -> str:
    blob = json.dumps([pv["old"], pv["new"], p.target_model, p.judge_model_default,
                       sorted((m.id, m.definition, m.judge_model) for m in metrics)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _load(s: Session, project_id: str):
    p = s.get(Project, project_id)
    if not p:
        raise PlanError("project not found")
    pv = {v.label: v.text for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all()}
    if "old" not in pv or "new" not in pv:
        raise PlanError("save old and new prompts first")
    if not candidates(s, project_id):
        raise PlanError("no scenarios yet: plan scenarios first (cost is estimated after planning)")
    return p, pv


def get_plan(s: Session, project_id: str) -> Plan:
    plan = s.exec(select(Plan).where(Plan.project_id == project_id)).first() or Plan(project_id=project_id)
    s.add(plan)
    s.commit()
    s.refresh(plan)
    return plan


async def probe(engine: Engine, client: LLMClient, project_id: str) -> dict:
    """Run probe_scenarios scenarios on both prompts (the same calls the runner will make, so they are cached for it),
    then a few judge calls, and record per-call cost distributions."""
    with Session(engine) as s:
        p, pv = _load(s, project_id)
        metrics = active_metrics(s, project_id)
        xs = sorted(candidates(s, project_id), key=lambda x: (len(scenario_text(x.input)), x.id))
        k = min(settings.probe_scenarios, len(xs))
        pick = [xs[int(i * len(xs) / k)] for i in range(k)]  # evenly spaced over input length: covers short and long cases
        optional = project_optional(s, project_id)
        pick = [(fill_optional(x.input, optional), x.category, x.tags, x.expected_behavior) for x in pick]
        model, default_judge, fp = p.target_model, p.judge_model_default, fingerprint(p, pv, metrics)
    m = Metered(client)
    old_new = await asyncio.gather(*[asyncio.gather(run_target(m, model, pv["old"], inp), run_target(m, model, pv["new"], inp))
                                     for inp, *_ in pick])
    cost_old = [o.full_cost for o, _ in old_new]
    cost_new = [n.full_cost for _, n in old_new]
    prog_paid = sum(m.paid)  # target-call spend (judge spend added below)

    # programmatic pass rate (free): share of outputs that pass every applicable programmatic metric
    prog = [x for x in metrics if x.kind == "programmatic"]
    passes = []
    for (inp, cat, tags, exp), (o, n) in zip(pick, old_new):
        app = [x for x in prog if metric_applies(x.applies_to, cat, tags)]
        for out in (o.text, n.text):
            if app:
                passes.append(all(run_programmatic(x.definition, out, exp)[0] == 1.0 for x in app))
    pass_rate = float(np.mean(passes)) if passes else None

    judge_paid: list[float] = []

    async def cost_of(metric: Metric, **kw) -> float:
        mm = Metered(client)
        await evaluate(mm, default_judge, metric, **kw)
        judge_paid.append(sum(mm.paid))
        return sum(mm.full)

    pt = [x for x in metrics if x.kind == "llm_judge" and not is_pairwise(x)]
    pw = [x for x in metrics if is_pairwise(x)]
    outs = [(inp, exp, o.text) for (inp, _, _, exp), (o, _) in zip(pick, old_new)] + \
           [(inp, exp, n.text) for (inp, _, _, exp), (_, n) in zip(pick, old_new)]
    jp = await asyncio.gather(*[cost_of(pt[i % len(pt)], inp=inp, output=out, expected=exp) for i, (inp, exp, out) in enumerate(outs[:6])]) if pt else []
    jw = await asyncio.gather(*[cost_of(pw[0], inp=inp, output=n.text, old_output=o.text) for (inp, *_), (o, n) in zip(pick[:3], old_new[:3])]) if pw else []
    result = {"target_old": _stats(cost_old), "target_new": _stats(cost_new), "judge_point": _stats(list(jp)),
              "judge_pair": _stats(list(jw)), "prog_pass_rate": pass_rate, "n_probe": len(pick),
              "fingerprint": fp, "paid": prog_paid + sum(judge_paid)}  # what the probe itself cost
    with Session(engine) as s:
        plan = get_plan(s, project_id)
        plan.probe = result
        s.add(plan)
        s.commit()
    return result


def _zero():
    return {"mean": 0.0, "p10": 0.0, "p90": 0.0}


def _factors(levers: dict, probe: dict, metrics: list[Metric], repeats: int, stable_share: float, cheap_ratio: float | None = None) -> tuple[float, float]:
    """-> (judge factor, target factor). `stable_share` = share of cases that stay quiet and stop after 2 repeats.
    With adaptive repeats every other case runs up to max_repeats_adaptive (contested cases are CONFIRMED, not stopped);
    with only early stopping they run the planned repeats. Both levers describe the same stop rule, so they are one factor."""
    judge = 1.0
    prog = [x for x in metrics if x.kind == "programmatic"]
    if levers.get("programmatic_first") and prog and probe.get("prog_pass_rate") is not None:
        judge *= probe["prog_pass_rate"]
    if levers.get("cheap_judge_easy_rubrics") and cheap_ratio is not None:
        pt = [x for x in metrics if x.kind == "llm_judge" and not is_pairwise(x)]
        easy = [x for x in pt if is_easy_metric(x)]
        if pt:
            judge *= 1 - (len(easy) / len(pt)) * (1 - cheap_ratio)
    shared = 1.0
    if repeats >= 2 and levers.get("adaptive_repeats"):
        shared = (stable_share * 2 + (1 - stable_share) * max(repeats, settings.max_repeats_adaptive)) / repeats
    elif repeats > 2 and levers.get("early_stopping"):
        shared = (stable_share * 2 + (1 - stable_share) * repeats) / repeats
    return judge * shared, shared


def cost_of_selection(selected: list[Scenario], repeats: int, probe: dict, metrics: list[Metric], levers: dict, cheap_ratio: float | None = None) -> dict:
    to, tn = probe["target_old"] or _zero(), probe["target_new"] or _zero()
    jp, jw = probe["judge_point"] or _zero(), probe["judge_pair"] or _zero()
    # low: most cases stay quiet; expected: the assumed share; high: every case is contested and runs to the maximum
    share = {"low": 0.9, "expected": settings.adaptive_stable_share, "high": 0.0}
    fac = {k: _factors(levers, probe, metrics, repeats, share[k], cheap_ratio) for k in share}
    out = {"low": 0.0, "expected": 0.0, "high": 0.0}
    n_t = n_j = 0
    for x in selected:
        app = [m for m in metrics if m.kind == "llm_judge" and metric_applies(m.applies_to, x.category, x.tags)]
        n_pt, n_pw = sum(not is_pairwise(m) for m in app), sum(is_pairwise(m) for m in app)
        n_t += 2 * repeats
        n_j += repeats * (2 * n_pt + 2 * n_pw)
        for key, stat in (("low", "p10"), ("expected", "mean"), ("high", "p90")):
            jf, tf = fac[key]
            target = repeats * (to[stat] + tn[stat]) * tf
            judge = repeats * (2 * n_pt * jp[stat] + n_pw * jw[stat]) * jf
            out[key] += target + judge
    return {**{k: round(v, 6) for k, v in out.items()}, "n_calls_target": n_t, "n_calls_judge": n_j}


def _tier_row(s, tier, selected, ranked, repeats, probe, metrics, levers, cheap_ratio=None) -> dict:
    c = cost_of_selection(selected, repeats, probe, metrics, levers, cheap_ratio)
    return {"tier": tier, "n_scenarios": len(selected), "repeats": repeats,
            "cost": {k: c[k] for k in ("low", "expected", "high")}, "n_calls_target": c["n_calls_target"],
            "n_calls_judge": c["n_calls_judge"], "coverage_lost": trimmer.coverage_lost(s, ranked, selected),
            "suggested_cap": c["high"], "scenario_ids": [x.id for x in selected]}


def estimate(s: Session, project_id: str, levers: dict | None = None, custom: dict | None = None) -> dict:
    """Tiers (+ optional custom selection) from the stored probe. Needs a probe (see probe())."""
    plan = get_plan(s, project_id)
    if not plan.probe:
        raise PlanError("no probe yet")
    lv = {**LEVERS, **(levers or {})}
    judge_model = s.get(Project, project_id).judge_model_default
    cheap = cheap_model_for(judge_model)
    ratio = cheap_cost_ratio(judge_model, cheap) if cheap else None
    lv["cheap_judge_easy_rubrics"] = bool(lv["cheap_judge_easy_rubrics"] and cheap)  # no cheaper judge available: the lever is off
    probe, metrics, ranked = plan.probe, active_metrics(s, project_id), rank(s, project_id)
    tiers = [_tier_row(s, t, trimmer.select_tier(ranked, f), ranked, settings.tier_repeats[t], probe, metrics, lv, ratio)
             for t, f in trimmer.tier_fractions().items()]
    if custom:
        ids = set(custom["scenario_ids"])
        tiers.append(_tier_row(s, "custom", [x for x in ranked if x.id in ids], ranked, custom.get("repeats") or settings.default_repeats,
                               probe, metrics, lv, ratio))
    full = tiers[0]
    sel = [x for x in ranked if x.id in set(full["scenario_ids"])]
    off = {k: False for k in LEVERS}
    base = cost_of_selection(sel, full["repeats"], probe, metrics, off, ratio)["expected"]
    levers_out = [{"id": k, "enabled": lv[k], "available": k != "cheap_judge_easy_rubrics" or bool(cheap),
                   "note": LEVER_NOTES[k] if k != "cheap_judge_easy_rubrics" else (f"short binary rubrics are judged by {cheap} instead of {judge_model} (price ratio {ratio:.2f})" if cheap else "no cheaper judge is known for this provider: set PV_CHEAP_JUDGE_MODEL"),
                   "saving_expected": round(base - cost_of_selection(sel, full["repeats"], probe, metrics, {**off, k: True}, ratio)["expected"], 6) if (k != "cheap_judge_easy_rubrics" or cheap) else 0.0}
                  for k in LEVERS]
    plan.stage = "budget" if plan.stage in ("scenarios", "metrics", "budget") else plan.stage
    s.add(plan)
    for old in s.exec(select(CostEstimate).where(CostEstimate.plan_id == plan.id)).all():
        s.delete(old)
    for t in tiers:
        s.add(CostEstimate(plan_id=plan.id, tier=t["tier"], n_scenarios=t["n_scenarios"], n_calls_target=t["n_calls_target"],
                           n_calls_judge=t["n_calls_judge"], repeats=t["repeats"], low=t["cost"]["low"], expected=t["cost"]["expected"],
                           high=t["cost"]["high"], coverage_lost=t["coverage_lost"], scenario_ids=t["scenario_ids"]))
    s.commit()
    n_prot = sum(is_protected(x) for x in ranked)
    warns = []
    if ranked and n_prot / len(ranked) > 0.4:
        warns.append(f"{n_prot} of {len(ranked)} cases ({n_prot / len(ranked):.0%}) are protected (pinned or severity >= {settings.severity_protected_threshold}) and are never auto-trimmed, "
                     "so the cheaper tiers cannot be much smaller. Review the severities in Scenarios, or drop some protected cases in a custom trim (with explicit confirmation).")
    return {"tiers": tiers, "levers": levers_out, "protected_scenarios": n_prot, "warnings": warns,
            "probe": {k: v for k, v in probe.items() if k != "fingerprint"},
            "basis": "per-call costs measured on a probe; lever savings are heuristic until runs measure real rates"}


async def ensure_probe(engine: Engine, client: LLMClient, project_id: str, reprobe: bool = False) -> None:
    with Session(engine) as s:
        p, pv = _load(s, project_id)
        fp = fingerprint(p, pv, active_metrics(s, project_id))
        have = get_plan(s, project_id).probe
    if reprobe or not have or have.get("fingerprint") != fp:  # prompts/metrics/models changed => stale probe
        await probe(engine, client, project_id)
