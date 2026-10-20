"""Stage 6: paired, noise-adjusted analysis -> report (PLAN 14.9 shape plus detail), scope stamp, overrides, re-score, markdown."""
from collections import Counter, defaultdict

from scipy import stats
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.config import settings
from app.core.models import Calibration, Feedback, Metric, Plan, Project, Report, Run, RunResult, Score, Scenario, ScenarioCluster
from app.pipeline import verdicts as V
from app.pipeline.collect import collect, metric_verdicts, noise_vars, worst_var
from app.pipeline.metric_plan import cheap_model_for, is_binary_metric, is_easy_metric, is_pairwise
from app.pipeline.metrics_engine import metric_applies

OVERRIDE = {"fine": "unchanged", "broken": "broken", "improved": "fixed"}


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def overrides_for(s: Session, run_id: str) -> dict:
    out = {}
    for f in sorted(s.exec(select(Feedback).where(Feedback.target_type == "verdict")).all(), key=lambda f: f.created_at):
        rid, _, sid = f.target_id.partition(":")
        if rid == run_id and f.action in OVERRIDE:
            out[sid] = {"verdict": f.action, "comment": f.comment}
    return out


def analyze(s: Session, run_id: str) -> dict:
    run = s.get(Run, run_id)
    plan = s.get(Plan, run.plan_id)
    cfg = run.config or {}
    ids = cfg["scenario_ids"] + cfg.get("deep_dive_ids", [])
    metrics = [m for m in s.exec(select(Metric).where(Metric.project_id == plan.project_id)).all() if m.status != "rejected"]
    mname = {m.id: m.name for m in metrics}
    pair_ids = {m.id for m in metrics if is_pairwise(m)}
    scn = {x.id: x for x in s.exec(select(Scenario).where(Scenario.id.in_(ids))).all()}
    clusters = {c.id: c.name for c in s.exec(select(ScenarioCluster).where(ScenarioCluster.project_id == plan.project_id)).all()}
    c = collect(s, run)
    noise = noise_vars(c, metrics, pair_ids, {sid: x.cluster_id for sid, x in scn.items()})
    k = cfg.get("repeats", settings.default_repeats)
    bands = {mid: V.band(worst_var(v), k, k) for mid, v in noise.items()}  # noisiest cluster
    m_tests = max(1, sum(len(c.data[sid]) for sid in ids if sid in c.data))  # comparisons made (multiplicity)
    kinds = {m.id: ("pair" if m.id in pair_ids else "binary" if is_binary_metric(m) else "continuous") for m in metrics}

    def detect_at(mid: str, v: dict):
        """Smallest per-case difference that is called fixed/broken at k repeats (None: impossible, needs more repeats)."""
        var = worst_var(v)
        if kinds[mid] == "continuous":
            return V.band(var, k, k, m_tests)
        n = V.min_flips(k, var if kinds[mid] == "pair" else V.q_from_var(var), m_tests)
        return None if n is None else n / k

    detect = {mid: detect_at(mid, v) for mid, v in noise.items()}
    over = overrides_for(s, run_id)

    sv, bucket = {}, {}
    for sid in ids:
        if sid not in c.data:
            continue
        sv[sid] = metric_verdicts(c.data[sid], noise, pair_ids, m_tests, scn[sid].cluster_id)
        bucket[sid] = V.combine([v["verdict"] for v in sv[sid].values()]) if sv[sid] else "untested"
    original = dict(bucket)
    for sid, o in over.items():
        if sid in bucket:
            bucket[sid] = OVERRIDE[o["verdict"]]
    tested = [sid for sid in bucket if bucket[sid] != "untested"]
    counts = Counter(bucket[sid] for sid in tested)

    # per metric
    per_metric = []
    for m in metrics:
        rows = [(sid, sv[sid][m.id]) for sid in tested if m.id in sv[sid]]
        if not rows:
            continue
        diffs = [v["new"] - v["old"] for _, v in rows]
        boot = V.paired_bootstrap(diffs)
        applicable = sum(1 for sid in tested if metric_applies(m.applies_to, scn[sid].category, scn[sid].tags))
        entry = {"metric_id": m.id, "name": m.name, "kind": "pairwise" if m.id in pair_ids else m.kind, "n": len(rows), "applicable": applicable,
                 "old_mean": _pct(sum(v["old"] for _, v in rows) / len(rows)), "new_mean": _pct(sum(v["new"] for _, v in rows) / len(rows)),
                 "mean_diff": _pct(boot["mean"]), "ci95": [_pct(x) for x in boot["ci95"]], "noise_band": _pct(bands.get(m.id)),
                 "detection_threshold": _pct(detect.get(m.id)), "test_kind": kinds[m.id],
                 "counts": dict(Counter(v["verdict"] for _, v in rows))}
        if m.id in pair_ids:
            wins_new = sum(v["new"] > 0 for _, v in rows)
            wins_old = sum(v["new"] < 0 for _, v in rows)
            entry["test"] = {"name": "sign test (new vs old preferred)", "p": float(stats.binomtest(wins_new, wins_new + wins_old, 0.5).pvalue) if wins_new + wins_old else 1.0}
            judgments = sum(len(c.data[sid][m.id]["pair"]) for sid, _ in rows)
            disputed = sum(x for _, v in rows for x in [v.get("disputed", 0)])
            entry["disputed_rate"] = _pct(disputed / judgments) if judgments else None
        elif metric_is_binary(c, m.id, tested):
            t = V.mcnemar_exact([v["old"] >= 0.5 for _, v in rows], [v["new"] >= 0.5 for _, v in rows])
            entry["test"] = {"name": "McNemar exact (scenario level)", **t}
        per_metric.append(entry)

    # net effect: weighted mean over metrics of the per-scenario paired difference
    w = {m.id: m.weight for m in metrics}
    net = []
    for sid in tested:
        parts = [(w[mid], v["new"] - v["old"]) for mid, v in sv[sid].items() if w.get(mid, 0) > 0]
        if parts:
            net.append(sum(a * b for a, b in parts) / sum(a for a, _ in parts))
    net_effect = V.paired_bootstrap(net)

    def view(sid: str, mid: str, v: dict) -> dict:
        x = scn[sid]
        old_rr = [(r, c.results[(sid, "old", r)]) for r in range(20) if (sid, "old", r) in c.results]
        new_rr = [(r, c.results[(sid, "new", r)]) for r in range(20) if (sid, "new", r) in c.results]
        pick = 0  # show the repeat where the new output looks worst for regressions / best for fixes
        sign = -1 if v["verdict"] == "broken" else 1
        best = None
        for r, rr in new_rr:
            sc, so = c.scores.get((rr.id, mid)), None
            o = c.results.get((sid, "old", r))
            so = c.scores.get((o.id, mid)) if o else None
            if mid in pair_ids:
                gap = (2 * sc.value - 1) if sc else 0
            else:
                gap = (sc.value - so.value) if sc and so else 0
            if best is None or sign * gap > best[0]:
                best, pick = (sign * gap, r), r
        o, n = c.results.get((sid, "old", pick)), c.results.get((sid, "new", pick))
        nsc = c.scores.get((n.id, mid)) if n else None
        return {"scenario_id": sid, "cluster_id": x.cluster_id, "cluster": clusters.get(x.cluster_id), "category": x.category,
                "severity": x.severity, "metric_id": mid, "metric_name": mname.get(mid), "old_pass_rate": _pct(v["old"] if mid not in pair_ids else None),
                "new_pass_rate": _pct(v["new"] if mid not in pair_ids else None), "preference_for_new": _pct(v["new"]) if mid in pair_ids else None,
                "disputed": bool(v.get("disputed")), "input": x.input, "old_output": o.output if o else None, "new_output": n.output if n else None,
                "rationale": nsc.rationale if nsc else "", "repeats": [len(old_rr), len(new_rr)], "reviewed": x.reviewed, "tier": x.tier}

    def collect_rows(kind: str) -> list[dict]:
        rows = []
        for sid in tested:
            if bucket[sid] != kind:
                continue
            if sid in over and original.get(sid) != kind:  # human-set verdict with no metric rows of that kind
                rows.append({"scenario_id": sid, "cluster": clusters.get(scn[sid].cluster_id), "category": scn[sid].category,
                             "severity": scn[sid].severity, "metric_name": "(human verdict)", "input": scn[sid].input, "override": over[sid]})
                continue
            for mid, v in sv[sid].items():
                if v["verdict"] == kind:
                    row = view(sid, mid, v)
                    if sid in over:
                        row["override"] = over[sid]
                    rows.append(row)
        return sorted(rows, key=lambda r: (-r["severity"], r["cluster"] or "", r["scenario_id"]))

    regressions, improvements = collect_rows("broken"), collect_rows("fixed")
    inconclusive = [{"scenario_id": sid, "cluster": clusters.get(scn[sid].cluster_id), "category": scn[sid].category} for sid in tested if bucket[sid] == "inconclusive"]

    def group(keyf) -> list[dict]:
        g = defaultdict(Counter)
        for sid in tested:
            g[keyf(scn[sid])][bucket[sid]] += 1
        return [{"name": k, **{b: cn.get(b, 0) for b in ("fixed", "broken", "unchanged", "inconclusive")}, "n": sum(cn.values())} for k, cn in sorted(g.items(), key=lambda kv: str(kv[0]))]

    # judge calls a lever skipped (applicable pointwise judge metric with no score on a stored output)
    skipped = 0
    for (sid, lab, r), rr in c.results.items():
        x = scn.get(sid)
        if x:
            skipped += sum(1 for m in metrics if m.kind == "llm_judge" and m.id not in pair_ids and metric_applies(m.applies_to, x.category, x.tags)
                           and (rr.id, m.id) not in c.scores)

    # scope stamp
    n_tested = len(tested)
    done = run.status == "done"
    scope = {"tier": run.tier, "tested": n_tested, "dropped": max(cfg.get("n_candidates", n_tested) - len(cfg.get("scenario_ids", [])), 0),
             "untested_in_tier": len(ids) - n_tested, "deep_dive_added": len(cfg.get("deep_dive_ids", [])),
             "reviewed_share": _pct(sum(scn[s_].reviewed for s_ in tested) / n_tested) if n_tested else 0.0,
             "verified_share": _pct(sum(scn[s_].tier == "verified" for s_ in tested) / n_tested) if n_tested else 0.0,
             "noise_band": {mname.get(mid, mid): _pct(b) for mid, b in bands.items()},
             "detection_threshold": {mname.get(mid, mid): _pct(b) for mid, b in detect.items()}, "comparisons": m_tests,
             "judge_calls_skipped": skipped, "repeats": k, "levers": cfg.get("levers"),
             "coverage_lost": (cfg.get("coverage_lost") or {}).get("categories", {}), "clusters_dropped": (cfg.get("coverage_lost") or {}).get("clusters_dropped", []),
             "complete": done, "run_status": run.status}

    # judge reliability
    pair_j = sum(len(c.data[sid][mid]["pair"]) for sid in tested for mid in pair_ids if mid in c.data.get(sid, {}))
    pair_d = sum(1 for sid in tested for mid in pair_ids if mid in c.data.get(sid, {}) for _, dsp in c.data[sid][mid]["pair"].values() if dsp)
    cals = [x.agreement for x in s.exec(select(Calibration).where(Calibration.project_id == plan.project_id)).all() if x.agreement is not None]
    reliability = {"calibration_agreement": _pct(sum(cals) / len(cals)) if cals else None,
                   "pairwise_disagreement_rate": _pct(pair_d / pair_j) if pair_j else None, "pairwise_judgments": pair_j}

    ens = [sc for sc in c.scores.values() if sc.details and "second_judge" in sc.details]
    reliability["pointwise_disagreement_rate"] = _pct(sum(sc.disputed for sc in ens) / len(ens)) if ens else None
    reliability["ensemble_judgments"] = len(ens)
    if (cfg.get("levers") or {}).get("cheap_judge_easy_rubrics"):
        cm = cheap_model_for(s.get(Project, plan.project_id).judge_model_default)
        scope["cheap_judge"] = {"model": cm, "metrics": [m.name for m in metrics if is_easy_metric(m)]}
    probe_paid = (plan.probe or {}).get("paid")
    headline = {"fixed": counts.get("fixed", 0), "broken": counts.get("broken", 0), "unchanged": counts.get("unchanged", 0),
                "inconclusive": counts.get("inconclusive", 0), "net_effect": {"mean_diff": _pct(net_effect["mean"]), "ci95": [_pct(x) for x in net_effect["ci95"]]}}
    metric_regs = metric_regressions(per_metric, sv, tested, scn)
    headline["metric_regressions"] = metric_regs
    headline["level"], headline["recommendation"] = recommend(headline, regressions, scope, bucket, scn, clusters, metric_regs)
    return {"run_id": run_id, "compare": {"base": cfg.get("base_label", "old"), "test": cfg.get("test_label", "new")}, "headline": headline, "scope": scope, "regressions": regressions, "improvements": improvements,
            "inconclusive": inconclusive, "per_metric": per_metric, "per_category": group(lambda x: x.category),
            "per_cluster": group(lambda x: clusters.get(x.cluster_id, "(none)")), "judge_reliability": reliability,
            "cost": {"estimated": cfg.get("expected_cost"), "estimated_high": cfg.get("high_cost"), "spent": round(run.spent, 6), "cap": run.budget_cap, "probe_spent": probe_paid},
            "warnings": run.warnings or [], "overrides": over}


def metric_is_binary(c, mid: str, sids) -> bool:
    return all(v in (0.0, 1.0) for sid in sids for arm in ("old", "new") for v in c.data.get(sid, {}).get(mid, {}).get(arm, {}).values())


def metric_regressions(per_metric: list, sv: dict, tested: list, scn: dict) -> list[dict]:
    """Metrics that got significantly worse IN AGGREGATE, even if no single case is conclusive (noisy metrics make individual
    cases only 'suggestive', while the pooled evidence is clear). Bonferroni over the metrics tested; CI must exclude zero."""
    tested_m = [m for m in per_metric if "test" in m]
    out = []
    for m in tested_m:
        if m["test"]["p"] < settings.significance_alpha / max(len(tested_m), 1) and m["mean_diff"] < 0 and m["ci95"][1] < 0:
            worse = [sid for sid in tested if m["metric_id"] in sv[sid] and sv[sid][m["metric_id"]]["new"] < sv[sid][m["metric_id"]]["old"]]
            out.append({"metric_id": m["metric_id"], "name": m["name"], "mean_diff": m["mean_diff"], "p": m["test"]["p"], "cases_worse": len(worse),
                        "max_severity": max((scn[s_].severity for s_ in worse), default=0)})
    return out


def recommend(h: dict, regs: list, scope: dict, bucket: dict, scn: dict, clusters: dict, metric_regs: list | None = None) -> tuple[str, str]:
    """Rules, not an LLM opinion (advisory)."""
    metric_regs = metric_regs or []
    hi = {r["scenario_id"] for r in regs if r.get("severity", 0) >= settings.severity_protected_threshold}
    where = ", ".join(sorted({r.get("cluster") or r.get("category") for r in regs})[:4])
    n_tested = max(scope["tested"], 1)
    lo, hi_ci = h["net_effect"]["ci95"]
    if hi:
        return "block", f"block: {len(hi)} regression(s) on severity>={settings.severity_protected_threshold} cases ({where})"
    sev = [x for x in metric_regs if x["max_severity"] >= settings.severity_protected_threshold]
    if sev:
        x = sev[0]
        return "block", (f"block: '{x['name']}' got worse by {abs(x['mean_diff']):.2f} across {x['cases_worse']} cases (p={x['p']:.2g}) on "
                         f"severity>={settings.severity_protected_threshold} cases; individual cases may be inconclusive at this noise level")
    if h["broken"]:
        return "caution", f"caution: {h['broken']} regression(s) in {where}"
    if metric_regs:
        x = metric_regs[0]
        return "caution", f"caution: '{x['name']}' got worse by {abs(x['mean_diff']):.2f} across {x['cases_worse']} cases (p={x['p']:.2g}) although no single case is conclusive"
    if hi_ci is not None and hi_ci < 0:
        return "caution", "caution: net effect is negative and its 95% interval excludes zero"
    if not scope["complete"] or scope["untested_in_tier"]:
        return "incomplete", f"incomplete: {scope['tested']} of {scope['tested'] + scope['untested_in_tier']} planned cases tested; no regressions among them"
    if h["inconclusive"] / n_tested > 0.3:
        return "inconclusive", "inconclusive: too many cases are within the noise band; rerun with more repeats"
    if h["fixed"]:
        return "ok", f"no regressions found in the tested scope; {h['fixed']} case(s) improved"
    return "ok", "no change detected in the tested scope"


def build_report(engine: Engine, run_id: str) -> dict:
    with Session(engine) as s:
        summary = analyze(s, run_id)
        rep = s.exec(select(Report).where(Report.run_id == run_id)).first() or Report(run_id=run_id)
        rep.summary = summary
        s.add(rep)
        s.commit()
        s.refresh(rep)
        return {"id": rep.id, "run_id": run_id, "summary": rep.summary, "generated_at": rep.generated_at.isoformat()}


async def rescore(engine: Engine, client, run_id: str) -> dict:
    """Re-score every stored output with the CURRENT metrics, without regenerating anything. Judge calls are cached by
    content (unchanged rubric+output = free). New scores replace the old ones in ONE transaction, only if all succeed."""
    from app.pipeline.runner import control_pairs, is_active, load_ctx, score_repeat

    if is_active(run_id):
        raise RuntimeError("run is still executing")
    ctx = load_ctx(engine, client, run_id)
    with Session(engine) as s:
        run = s.get(Run, run_id)
        c = collect(s, run)
        ctrl_sids = {sid for sid, d in c.data.items() for md in d.values() if md["ctrl"]}
        pairs = [(sid, r) for (sid, lab, r) in c.results if lab == "old" and (sid, "new", r) in c.results]
    rows: list[Score] = []
    for sid, r in sorted(pairs):
        if sid in ctx.scn:
            rows += await score_repeat(ctx, ctx.scn[sid], c.results[(sid, "old", r)], c.results[(sid, "new", r)], persist=False, ignore_existing=True)
    ctrl = []
    for sid in sorted(ctrl_sids):  # A/A controls: recompute through the same path, collected
        if sid in ctx.scn:
            ctrl += await _control_rows(ctx, ctx.scn[sid])
    with Session(engine) as s:
        for sc in s.exec(select(Score).join(RunResult, RunResult.id == Score.run_result_id).where(RunResult.run_id == run_id)).all():
            s.delete(sc)
        s.commit()
        s.add_all(rows + ctrl)
        s.commit()
    return {"rescored": len(rows) + len(ctrl)}


async def _control_rows(ctx, scn) -> list[Score]:
    from app.pipeline.metric_plan import evaluate
    from app.pipeline.runner import ensure_result
    from app.agents.base import AgentError
    from app.pipeline.collect import CONTROL
    from app.pipeline.metrics_engine import metric_applies

    app = [m for m in ctx.metrics if m.id in ctx.pair_ids and metric_applies(m.applies_to, scn.category, scn.tags)]
    if not app:
        return []
    r0, r1 = await ensure_result(ctx, scn, "old", 0), await ensure_result(ctx, scn, "old", 1)
    rows = []
    for m in app:
        try:
            res = await evaluate(ctx.client, ctx.judge_model, m, inp=scn.input, output=r1.output, old_output=r0.output, budget=ctx.budget)
        except AgentError:
            continue
        rows.append(Score(run_result_id=r1.id, metric_id=m.id, value={"new": 1.0, "tie": 0.5, "old": 0.0}[res["verdict"]], rationale="A/A control",
                          judge_repeat_idx=CONTROL, disputed=res["disputed"],
                          details={k: res[k] for k in ("verdict", "preserved_correct_behavior", "confidence")}))
    return rows


def to_markdown(rep: dict) -> str:
    h, sc = rep["headline"], rep["scope"]
    cmp_ = rep.get("compare", {"base": "old", "test": "new"})
    L = [f"# Prompt change report: {cmp_['test']} vs {cmp_['base']}", "", f"**{h['recommendation']}**  (advisory, rule-based)", "",
         f"| fixed | broken | unchanged | inconclusive |\n|---|---|---|---|\n| {h['fixed']} | {h['broken']} | {h['unchanged']} | {h['inconclusive']} |", "",
         f"Net effect (mean paired difference across cases): **{h['net_effect']['mean_diff']}**, 95% CI {h['net_effect']['ci95']}", "",
         "## Scope stamp", "",
         f"- Tier: {sc['tier']} ({'complete' if sc['complete'] else 'PARTIAL, run ' + sc['run_status']}); {sc['tested']} cases tested, {sc['dropped']} dropped by the tier"
         + (f", {sc['untested_in_tier']} planned but untested" if sc["untested_in_tier"] else ""),
         f"- Reviewed by a human: {round(sc['reviewed_share'] * 100)}%; verified (your labelled data): {round(sc['verified_share'] * 100)}%; the rest is synthetic",
         f"- Repeats: {sc['repeats']} (adaptive); noise bands (old vs old, 95%): " + ", ".join(f"{k} {v}" for k, v in sc["noise_band"].items()),
         f"- A single case is called fixed/broken only beyond its detection threshold (smallest per-case difference that survives "
         f"correction for {sc['comparisons']} comparisons): "
         + ", ".join(f"{k} {'not detectable at this many repeats' if v is None else v}" for k, v in sc["detection_threshold"].items())
         + (f"; {sc['judge_calls_skipped']} judge calls were skipped because the output already failed a programmatic check" if sc["judge_calls_skipped"] else ""),
         f"- Coverage lost: {sc['coverage_lost'] or 'none'}" + (f"; clusters dropped entirely: {sc['clusters_dropped']}" if sc["clusters_dropped"] else ""), ""]
    for w in rep["warnings"]:
        L.append(f"> WARNING: {w}\n")

    def block(title, rows):
        L.extend([f"## {title}", ""] if rows else [f"## {title}", "", "None.", ""])
        for r in rows:
            L.extend([f"### [{r.get('cluster') or r.get('category')}] {r['metric_name']} (severity {r['severity']})",
                  f"- scenario `{r['scenario_id']}`: {r.get('old_pass_rate')} -> {r.get('new_pass_rate')}" + (" (disputed)" if r.get("disputed") else "")
                  + (f"; human verdict: {r['override']['verdict']} {r['override']['comment']}" if r.get("override") else ""),
                  f"- input: {r['input']}"])
            if r.get("old_output") is not None:
                L.extend([f"- old output: {r['old_output']}", f"- new output: {r['new_output']}", f"- judge: {r['rationale']}"])
            L.append("")

    block("Regressions", rep["regressions"])
    block("Improvements", rep["improvements"])
    L += ["## Per metric", "", "| metric | n (of applicable) | old | new | diff (95% CI) | noise band | test p |", "|---|---|---|---|---|---|---|"]
    for m in rep["per_metric"]:
        p = m.get("test", {}).get("p")
        L.append(f"| {m['name']} | {m['n']} of {m['applicable']} | {m['old_mean']} | {m['new_mean']} | {m['mean_diff']} {m['ci95']} | {m['noise_band']} | {'' if p is None else round(p, 4)} |")
    L += ["", "## Per category", "", "| category | fixed | broken | unchanged | inconclusive |", "|---|---|---|---|---|"]
    L += [f"| {g['name']} | {g['fixed']} | {g['broken']} | {g['unchanged']} | {g['inconclusive']} |" for g in rep["per_category"]]
    j, cst = rep["judge_reliability"], rep["cost"]
    L += ["", "## Judge reliability", "", f"- Calibration agreement with your labels: {j['calibration_agreement']}",
          f"- Pairwise A/B disagreement rate: {j['pairwise_disagreement_rate']} over {j['pairwise_judgments']} judgments", "",
          "## Cost", "", f"- Estimated {cst['estimated']}, spent {cst['spent']}, cap {cst['cap']}"]
    return "\n".join(L) + "\n"
