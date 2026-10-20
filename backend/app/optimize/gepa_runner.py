"""Stage 7: refine the new prompt with DSPy GEPA, then validate on held-out scenarios the optimizer never saw.

Optimizer view: approved scenarios (else all non-rejected, flagged) split into train/val for GEPA and a 30% held-out set
(stratified by cluster). The 'program' is the new prompt itself, rendered by our own adapter; the metric is our approved
metrics (programmatic first, judges only if those pass) returning a score AND textual feedback; the reflection step is our
`reflector` agent. Every model call goes through LLMClient with one hard cap.
Validation: each candidate is compared with the NEW prompt and with the OLD prompt by the ordinary paired, noise-adjusted
stage-6 analysis, on the held-out scenarios only.
ponytail: GEPA itself is not resumable; re-running replays from the LLM cache (same seed, same prompts), so a retry after a cap pause is cheap."""
import asyncio
import difflib
import hashlib
from datetime import datetime, timezone

import dspy
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.agents import reflector
from app.config import settings
from app.core.llm import Budget, BudgetExceeded, LLMClient
from app.core.secrets import redact
from app.core.models import Feedback, Metric, Optimization, Project, PromptVersion, Report, Run, Scenario
from app.optimize.dspy_bridge import BudgetStop, LLMClientLM, OptState, TemplateAdapter, ensure_placeholders, run_sync, template_vars
from app.optimize.split import stratified_split
from app.pipeline import runner
from app.pipeline.cost_estimator import get_plan
from app.pipeline.metric_plan import evaluate, is_pairwise
from app.pipeline.metrics_engine import metric_applies, run_programmatic
from app.pipeline.planner import PlanError
from app.pipeline.report import build_report


def _udiff(a: str, b: str, la: str, lb: str) -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), la, lb, lineterm="", n=2))


def candidate_feedback_text(s: Session, project_id: str, limit: int = 12) -> str:
    """What the reviewer said about earlier candidates (accept / edit / reject), fed back into the reflector."""
    texts = {}
    for o in s.exec(select(Optimization).where(Optimization.project_id == project_id)).all():
        for c in (o.result or {}).get("candidates", []):
            texts[c["id"]] = c["text"]
    fbs = sorted(s.exec(select(Feedback).where(Feedback.project_id == project_id, Feedback.target_type == "candidate")).all(), key=lambda f: -f.created_at.timestamp())
    verb = {"reject": "REJECTED", "approve": "ACCEPTED", "edit": "EDITED"}
    return "\n".join(f"- A candidate you proposed was {verb.get(f.action, f.action)} by the reviewer: {f.comment or '(no comment)'}\n  Candidate began: {texts.get(f.target_id, '?')[:240]!r}"
                     for f in fbs[:limit])


def _save(engine: Engine, oid: str, **kw) -> None:
    with Session(engine) as s:
        o = s.get(Optimization, oid)
        for k, v in kw.items():
            setattr(o, k, redact(v) if k == "error" else v)
        s.add(o)
        s.commit()


async def optimize(engine: Engine, client: LLMClient, project_id: str, *, cap: float, max_metric_calls: int | None = None, n_candidates: int = 1,
                   progress=lambda m: None, on_phase=lambda p: None, optimization_id: str | None = None) -> dict:
    # ---------- load ----------
    with Session(engine) as s:
        p = s.get(Project, project_id)
        if not p:
            raise PlanError("project not found")
        pv = {v.label: v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all()}
        if "old" not in pv or "new" not in pv:
            raise PlanError("save old and new prompts first")
        all_m = [m for m in s.exec(select(Metric).where(Metric.project_id == project_id)).all() if m.status != "rejected" and not is_pairwise(m)]
        metrics = [m for m in all_m if m.status in ("approved", "edited")] or all_m
        if not metrics:
            raise PlanError("no metrics yet: design and approve metrics first (stage 3)")
        live = [x for x in s.exec(select(Scenario).where(Scenario.project_id == project_id)).all() if x.status != "rejected"]
        reviewed = [x for x in live if x.status in ("approved", "edited")]
        pool = reviewed if len(reviewed) >= settings.gepa_reviewed_min else live
        if len(pool) < settings.gepa_min_pool:
            raise PlanError(f"need at least {settings.gepa_min_pool} scenarios to optimize with a held-out set (have {len(pool)})")
        scn = {x.id: x for x in pool}
        human = candidate_feedback_text(s, project_id)
        new_p, old_p = pv["new"], pv["old"]
        info = dict(model=p.target_model, judge=p.judge_model_default, desc=f"{p.app_description}", constraints=p.constraints)
        # failures from the last report, restricted to cases the optimizer is allowed to see (filtered below)
        last = s.exec(select(Report).join(Run, Run.id == Report.run_id).where(Run.status.in_(["done", "stopped"]))).all()
        regressions = [r for rep in last for r in (rep.summary or {}).get("regressions", [])]
    variables = template_vars(new_p.text, old_p.text) or ["user_message"]
    rest, held = stratified_split([(x.id, x.cluster_id or x.category) for x in pool], settings.heldout_fraction, seed=0)
    train, val = stratified_split([(i, scn[i].cluster_id or scn[i].category) for i in rest], settings.gepa_val_fraction, seed=1)
    train = train or val  # tiny pools: never leave GEPA without training cases
    seen = set(train) | set(val)
    known = [{"input": r.get("input"), "metric": r.get("metric_name"), "judge": r.get("rationale")} for r in regressions if r.get("scenario_id") in seen][:5]
    forbidden = [str(v) for i in seen for v in scn[i].input.values()]
    max_calls = max_metric_calls or settings.gepa_max_metric_calls

    with Session(engine) as s:
        if optimization_id:
            opt = s.get(Optimization, optimization_id)
        else:
            opt = Optimization(project_id=project_id)
            s.add(opt)
            s.commit()
            s.refresh(opt)
        oid = opt.id
    cfg = {"cap": cap, "max_metric_calls": max_calls, "n_candidates": n_candidates, "heldout_ids": held, "train_ids": train, "val_ids": val,
           "seed_version_id": new_p.id, "trained_on_reviewed": pool is reviewed, "variables": variables}
    warnings = [] if pool is reviewed else [f"fewer than {settings.gepa_reviewed_min} scenarios are approved ({len(reviewed)}), which would make the train/validation/held-out split too small to mean anything, so unreviewed synthetic scenarios were used too"]
    _save(engine, oid, status="running", config=cfg, result={"warnings": warnings, "candidates": [], "reflection_log": []})

    # ---------- GEPA ----------
    on_phase("gepa")
    budget = Budget(cap=cap)
    state = OptState(budget, progress)
    target_lm = LLMClientLM(client, info["model"], state, temperature=settings.target_temperature)
    agent_lm = LLMClientLM(client, info["judge"], state, temperature=0.7, is_target=False)
    ex = lambda i: dspy.Example(scenario_id=i, **{v: str(scn[i].input.get(v, "")) for v in variables}).with_inputs(*variables)
    trainset, valset = [ex(i) for i in train], [ex(i) for i in val]
    log: list[dict] = []

    def score_output(x: Scenario, out: str) -> tuple[float, str]:
        app = [m for m in metrics if metric_applies(m.applies_to, x.category, x.tags)]
        prog = [m for m in app if m.kind == "programmatic"]
        lines, num, den, prog_ok = [], 0.0, 0.0, True
        for m in prog:
            v, why = run_programmatic(m.definition, out, x.expected_behavior)
            prog_ok &= v == 1.0
            num, den = num + m.weight * v, den + m.weight
            lines.append(f"- {'PASS' if v == 1.0 else 'FAIL'} {m.name}: {why}")
        for m in app:
            if m.kind != "llm_judge":
                continue
            if prog and not prog_ok:
                lines.append(f"- (judge '{m.name}' skipped: the reply already failed a programmatic check)")
                continue
            r = run_sync(evaluate(client, info["judge"], m, inp=x.input, output=out, expected=x.expected_behavior, budget=budget))
            num, den = num + m.weight * r["value"], den + m.weight
            lines.append(f"- {'PASS' if r['value'] >= 0.5 else 'FAIL'} {m.name}: {r['rationale']}")
        if x.expected_behavior:
            lines.append(f"Expected behaviour: {x.expected_behavior}")
        return (num / den if den else 1.0), "\n".join(lines) or "No metric applies to this case."

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        try:
            score, fb = score_output(scn[gold.scenario_id], getattr(pred, "output", "") or "")
        except BudgetExceeded:
            state.hit = True
            raise
        state.metric_calls += 1
        if state.metric_calls % 5 == 0:
            progress(f"optimizing: {state.metric_calls}/{max_calls} metric calls, spent {budget.spent:.4f} of {cap:.4f}")
        return dspy.Prediction(score=score, feedback=fb)

    class Proposer:
        """GEPA ProposalFn: our reflector agent writes the new prompt (minimal edits, constraints and placeholders kept)."""

        def __call__(self, candidate, reflective_dataset, components_to_update):
            out = {}
            for name in components_to_update:
                cur = candidate[name]
                examples = [{"inputs": str(r.get("Inputs"))[:400], "reply": str(r.get("Generated Outputs"))[:600], "why_it_scored_low": str(r.get("Feedback"))[:900]}
                            for r in reflective_dataset.get(name, [])[:settings.gepa_minibatch]]
                try:
                    r = run_sync(reflector.reflect(client, info["judge"], app_description=info["desc"], constraints=info["constraints"], current_prompt=cur,
                                                   variables=variables, examples=examples, known_failures=known, human_feedback=human, forbidden=forbidden,
                                                   budget=budget, variation=len(log)))
                except BudgetExceeded:
                    state.hit = True
                    raise
                text = ensure_placeholders(r.prompt, variables)
                log.append({"iteration": len(log) + 1, "prompt": text, "notes": r.notes})
                out[name] = text
            return out

    Sig = dspy.make_signature({**{v: (str, dspy.InputField()) for v in variables}, "output": (str, dspy.OutputField())}, instructions=new_p.text)

    class Prog(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(Sig)

        def forward(self, **kw):
            return self.predict(**kw)

    def compile_():
        with dspy.context(lm=target_lm, adapter=TemplateAdapter(variables), max_errors=10**6):
            g = dspy.GEPA(metric=metric, max_metric_calls=max_calls, reflection_minibatch_size=settings.gepa_minibatch, reflection_lm=agent_lm,
                          instruction_proposer=Proposer(), num_threads=settings.llm_concurrency, track_stats=True, seed=0, failure_score=0.0,
                          gepa_kwargs={"stop_callbacks": [BudgetStop(state)]})
            return g.compile(Prog(), trainset=trainset, valset=valset)

    stopped = None
    try:
        optimized = await asyncio.to_thread(compile_)
    except BudgetExceeded:
        optimized, stopped = None, "cap"
    except Exception as e:
        if state.hit:
            optimized, stopped = None, "cap"
        else:
            _save(engine, oid, status="failed", error=f"{type(e).__name__}: {e}", spent=budget.spent)
            raise
    if state.hit or budget.spent >= cap:
        stopped = "cap"

    # ---------- candidates ----------
    res = {"warnings": warnings, "stopped": stopped, "reflection_log": log, "metric_calls": state.metric_calls, "candidates": [],
           "seed_val_score": None, "best_val_score": None, "heldout_size": len(held), "train_size": len(train), "val_size": len(val)}
    cands: list[tuple[float, str]] = []
    if optimized is not None:
        det = getattr(optimized, "detailed_results", None)
        scores = list(getattr(det, "val_aggregate_scores", []) or [])
        progs = list(getattr(det, "candidates", []) or [])
        if scores:
            res["seed_val_score"], res["best_val_score"] = scores[0], max(scores)
        seen_txt = {new_p.text}
        for sc, prog in sorted(zip(scores, progs), key=lambda t: -t[0]):
            txt = ensure_placeholders(prog.predict.signature.instructions, variables)
            if txt not in seen_txt and sc > (scores[0] if scores else -1):
                seen_txt.add(txt)
                cands.append((sc, txt))
        if not scores:  # no stats: fall back to the program GEPA returned
            txt = ensure_placeholders(optimized.predict.signature.instructions, variables)
            if txt != new_p.text:
                cands.append((0.0, txt))
    with Session(engine) as s:
        n_existing = len([v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all() if v.label.startswith("candidate_")])
        for k, (sc, txt) in enumerate(cands[:n_candidates], n_existing + 1):
            v = PromptVersion(project_id=project_id, label=f"candidate_{k}", text=txt, variables_schema=new_p.variables_schema)
            s.add(v)
            s.commit()
            s.refresh(v)
            notes = [n for e in log if e["prompt"] == txt for n in e["notes"]] or [n for e in log for n in e["notes"]][:6]
            res["candidates"].append({"id": f"cand_{hashlib.sha1((v.id).encode()).hexdigest()[:8]}", "version_id": v.id, "label": v.label, "text": txt, "status": "proposed",
                                      "val_score": sc, "seed_val_score": res["seed_val_score"], "notes": notes, "heldout": {},
                                      "diff_vs_new": _udiff(new_p.text, txt, "new", v.label), "diff_vs_old": _udiff(old_p.text, txt, "old", v.label)})
    res["spent_search"] = budget.spent
    _save(engine, oid, result=res, spent=budget.spent)
    if stopped == "cap":
        _save(engine, oid, status="paused", error=f"hard cap reached during the search (spent {budget.spent:.4f} of {cap:.4f}); re-run with a higher cap, cached calls replay for free")
        return {"status": "paused", "reason": "cap", "optimization_id": oid}
    if not res["candidates"]:
        res["note"] = "no candidate beat the starting prompt on the validation set"
        _save(engine, oid, status="done", result=res)
        return {"status": "done", "optimization_id": oid, "candidates": 0}

    # ---------- held-out validation ----------
    on_phase("heldout")
    spent = budget.spent
    for c in res["candidates"]:
        spent += await validate_candidate(engine, client, oid, c, base_ids={"new": new_p.id, "old": old_p.id}, held=held, cap=max(cap - spent, 0.0), progress=progress)
        _save(engine, oid, result=res, spent=spent)
        if any(h.get("status") == "paused" for h in c["heldout"].values()):
            _save(engine, oid, status="paused", error="the cap was reached during held-out validation; evaluate the candidate again with more budget", result=res, spent=spent)
            return {"status": "paused", "reason": "cap", "optimization_id": oid}
    _save(engine, oid, status="done", result=res, spent=spent)
    return {"status": "done", "optimization_id": oid, "candidates": len(res["candidates"])}


async def validate_candidate(engine: Engine, client: LLMClient, oid: str, cand: dict, *, base_ids: dict, held: list[str], cap: float, progress=lambda m: None) -> float:
    """Paired, noise-adjusted comparison of the candidate vs NEW and vs OLD on the held-out scenarios. Resumes paused runs.
    Mutates cand['heldout']; returns what this call spent."""
    with Session(engine) as s:
        project_id = s.get(Optimization, oid).project_id
    spent = 0.0
    for base_label in ("new", "old"):
        entry = cand["heldout"].get(f"vs_{base_label}") or {}
        rid = entry.get("run_id")
        if rid:
            with Session(engine) as s:
                run = s.get(Run, rid)
                if run.status == "done":
                    continue
                before = run.spent
                run.budget_cap = run.spent + cap
                s.add(run)
                s.commit()
        else:
            rid = runner.create_compare_run(engine, project_id, base_ids[base_label], cand["version_id"], held, cap, base_label=base_label, test_label=cand["label"])
            before = 0.0
        progress(f"validating {cand['label']} vs {base_label} on {len(held)} held-out cases")
        r = await runner.execute_run(engine, client, rid)
        rep = build_report(engine, rid)["summary"]
        with Session(engine) as s:
            run = s.get(Run, rid)
            spent += run.spent - before
            cap = max(cap - (run.spent - before), 0.0)
        h = rep["headline"]
        cand["heldout"][f"vs_{base_label}"] = {
            "run_id": rid, "status": r["status"], "level": h["level"], "recommendation": h["recommendation"],
            "counts": {k: h[k] for k in ("fixed", "broken", "unchanged", "inconclusive")}, "net_effect": h["net_effect"],
            "scope": {k: rep["scope"][k] for k in ("tested", "complete", "reviewed_share", "repeats")}}
        if r["status"] != "done":
            break
    return spent
