"""Stage 5: staged, resumable, budget-capped execution.

Smoke (~10% of cases, 2 repeats, validates templates/metrics/judge parsing, measures old-vs-old noise)
-> Main (the confirmed tier; adaptive repeats + early stopping) -> Deep dive (neighbours of regressions, asks first if
over the cap). Everything is idempotent (rows are looked up before any call; LLM calls are cached), so resume is simply
running the same code again: no duplicate paid calls. The hard cap pauses the run; it never silently truncates."""
import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.agents import scenario_generator
from app.agents.base import AgentError
from app.config import settings
from app.core import events
from app.core.llm import Budget, BudgetExceeded, LLMClient
from app.core.secrets import redact
from app.core.models import Job, Metric, Optimization, Plan, Project, PromptVersion, Run, RunResult, Scenario, Score
from app.pipeline import verdicts as V
from app.pipeline.collect import CONTROL, collect, metric_verdicts, noise_vars, worst_var
from app.pipeline.execute import fill_optional, project_optional, render_prompt, run_target
from app.pipeline.metric_plan import cheap_model_for, evaluate, is_binary_metric, is_easy_metric, is_pairwise
from app.pipeline.metrics_engine import metric_applies, run_programmatic
from app.pipeline.planner import Ctx as PlanCtx
from app.pipeline.planner import PlanError, drop_near_duplicates


class RunFailure(Exception):
    """Unrecoverable (bad template, unparsable judge): the run is marked failed, not paused."""


class DeepDiveNeedsBudget(Exception):
    def __init__(self, needed: float):
        super().__init__(f"deep dive needs about {needed:.4f} more than the cap allows")
        self.needed = needed


@dataclass
class RunCtx:
    engine: Engine
    client: LLMClient
    run_id: str
    project_id: str
    model: str
    judge_model: str
    old_id: str
    new_id: str
    old_text: str
    new_text: str
    levers: dict
    repeats: int
    metrics: list[Metric]
    budget: Budget
    scn: dict[str, Scenario]
    noise: dict = field(default_factory=dict)  # {metric_id: {"var", "dof"}}
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    user_stop: bool = False
    cap_hit: bool = False
    judge_errors: int = 0
    gated: int = 0
    m_tests: int = 1  # scenario x metric comparisons (multiplicity correction)
    cheap_model: str | None = None  # judge for 'easy' rubrics when the cheap-judge lever is on
    optional: set = field(default_factory=set)  # optional prompt variables (render as empty when a scenario omits them)
    done: int = 0

    @property
    def max_r(self) -> int:
        return max(self.repeats, settings.max_repeats_adaptive) if self.levers.get("adaptive_repeats") else self.repeats

    @property
    def pair_ids(self) -> set[str]:
        return {m.id for m in self.metrics if is_pairwise(m)}


_controls: dict[str, RunCtx] = {}


def is_active(run_id: str) -> bool:
    return run_id in _controls


def request_stop(run_id: str) -> bool:
    ctx = _controls.get(run_id)
    if ctx:
        ctx.user_stop = True
        ctx.stop.set()
    return ctx is not None


def upd(engine: Engine, run_id: str, **kw) -> None:
    with Session(engine) as s:
        r = s.get(Run, run_id)
        for k, v in kw.items():
            setattr(r, k, v)
        s.add(r)
        s.commit()


def emit(ctx: RunCtx, type_: str, **kw) -> None:
    events.publish(ctx.run_id, {"type": type_, "run_id": ctx.run_id, **kw})


def create_run(engine: Engine, project_id: str, cap: float | None = None) -> str:
    with Session(engine) as s:
        plan = s.exec(select(Plan).where(Plan.project_id == project_id)).first()
        if not plan or not plan.chosen:
            raise PlanError("confirm a budget plan first (stage 4)")
        ch = plan.chosen
        live = {x.id for x in s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all()}
        ids = [i for i in ch["scenario_ids"] if i in live]  # drop anything rejected since the plan was confirmed
        if not ids:
            raise PlanError("the confirmed plan has no scenarios left")
        run = Run(plan_id=plan.id, tier=ch["tier"], budget_cap=cap if cap is not None else ch["cap"], config={
            "scenario_ids": ids, "repeats": ch["repeats"], "levers": ch["levers"], "coverage_lost": ch["coverage_lost"],
            "n_candidates": len(live), "expected_cost": ch["expected"], "high_cost": ch["high"]}, warnings=[])
        s.add(run)
        s.commit()
        return run.id


def create_compare_run(engine: Engine, project_id: str, base_id: str, test_id: str, scenario_ids: list[str], cap: float, *,
                       base_label: str, test_label: str, tier: str = "heldout", repeats: int | None = None, levers: dict | None = None) -> str:
    """A run of `test` vs `base` on explicit scenarios (no deep dive). Used to validate a candidate prompt on held-out cases."""
    from app.pipeline.cost_estimator import LEVERS, get_plan

    with Session(engine) as s:
        plan = get_plan(s, project_id)
        run = Run(plan_id=plan.id, tier=tier, budget_cap=cap, config={
            "scenario_ids": list(scenario_ids), "repeats": repeats or settings.default_repeats, "levers": levers or dict(LEVERS),
            "coverage_lost": {}, "n_candidates": len(scenario_ids), "expected_cost": None, "high_cost": None, "deep_dive": False,
            "base_version_id": base_id, "test_version_id": test_id, "base_label": base_label, "test_label": test_label}, warnings=[])
        s.add(run)
        s.commit()
        return run.id


def recover_interrupted(engine: Engine) -> int:
    """After a restart no task is running: runs left 'running' become 'paused' (explicit resume; never spend unasked)."""
    with Session(engine) as s:
        rs = s.exec(select(Run).where(Run.status == "running")).all()
        for r in rs:
            r.status, r.pause_reason = "paused", "interrupted: server restarted"
            s.add(r)
        msg = "interrupted by a server restart; start it again (calls already paid for replay from the cache for free)"
        for j in s.exec(select(Job).where(Job.status.in_(["pending", "running"]))).all():  # their tasks died with the process
            j.status, j.error = "failed", msg
            s.add(j)
        for o in s.exec(select(Optimization).where(Optimization.status.in_(["pending", "running"]))).all():
            o.status, o.error = "failed", msg
            s.add(o)
        s.commit()
        return len(rs)


def load_ctx(engine: Engine, client: LLMClient, run_id: str) -> RunCtx:
    with Session(engine) as s:
        run = s.get(Run, run_id)
        plan = s.get(Plan, run.plan_id)
        p = s.get(Project, plan.project_id)
        pv = {v.label: v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == p.id)).all()}
        if run.config.get("base_version_id"):  # held-out validation: compare any two versions
            pv = {"old": s.get(PromptVersion, run.config["base_version_id"]), "new": s.get(PromptVersion, run.config["test_version_id"])}
        metrics = [m for m in s.exec(select(Metric).where(Metric.project_id == p.id)).all() if m.status != "rejected"]
        cfg = run.config
        ids = cfg["scenario_ids"] + cfg.get("deep_dive_ids", [])
        scn = {x.id: x for x in s.exec(select(Scenario).where(Scenario.id.in_(ids))).all()}
        ctx = RunCtx(engine, client, run_id, p.id, p.target_model, p.judge_model_default, pv["old"].id, pv["new"].id,
                     pv["old"].text, pv["new"].text, cfg["levers"], cfg["repeats"], metrics,
                     Budget(cap=run.budget_cap, spent=run.spent), scn, noise=(run.noise or {}).get("metrics", {}))
        ctx.optional = project_optional(s, p.id)
        ctx.cheap_model = cheap_model_for(p.judge_model_default) if cfg.get("levers", {}).get("cheap_judge_easy_rubrics") else None
        ctx.m_tests = max(1, sum(metric_applies(m.applies_to, x.category, x.tags) for x in scn.values() for m in metrics))
        return ctx


async def gather_all(*coros):
    """Wait for every task (no orphans), then re-raise the first error."""
    res = await asyncio.gather(*coros, return_exceptions=True)
    for r in res:
        if isinstance(r, BaseException):
            raise r
    return res


async def ensure_result(ctx: RunCtx, scn: Scenario, label: str, r: int) -> RunResult:
    pid = ctx.old_id if label == "old" else ctx.new_id
    with Session(ctx.engine) as s:
        rr = s.exec(select(RunResult).where(RunResult.run_id == ctx.run_id, RunResult.scenario_id == scn.id,
                                            RunResult.prompt_version_id == pid, RunResult.repeat_idx == r)).first()
        if rr:
            return rr
    res = await run_target(ctx.client, ctx.model, ctx.old_text if label == "old" else ctx.new_text, fill_optional(scn.input, ctx.optional),
                           repeat_idx=r, budget=ctx.budget)
    with Session(ctx.engine) as s:
        rr = RunResult(run_id=ctx.run_id, scenario_id=scn.id, prompt_version_id=pid, repeat_idx=r, output=res.text,
                       latency=res.latency, tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost=res.cost)
        s.add(rr)
        s.commit()
        s.refresh(rr)
        return rr


def _norm(m: Metric, v: float) -> float:
    return (v - 1) / 4 if m.scale == "likert5" or m.definition.get("output") == "likert5" else min(max(v, 0.0), 1.0)


async def score_repeat(ctx: RunCtx, scn: Scenario, old: RunResult, new: RunResult, *, persist: bool = True,
                       ignore_existing: bool = False) -> list[Score]:
    """Programmatic checks first (free), then judges; skips what is already scored. With persist=False the new rows are
    only returned (re-scoring swaps them in atomically)."""
    app = [m for m in ctx.metrics if metric_applies(m.applies_to, scn.category, scn.tags)]
    have: set = set()
    if not ignore_existing:
        with Session(ctx.engine) as s:
            have = {(x.run_result_id, x.metric_id, x.judge_repeat_idx) for x in s.exec(
                select(Score).where(Score.run_result_id.in_([old.id, new.id]))).all()}
    prog = [m for m in app if m.kind == "programmatic"]
    outs = {"old": old, "new": new}
    passed = {lab: all(run_programmatic(m.definition, rr.output, scn.expected_behavior)[0] == 1.0 for m in prog) for lab, rr in outs.items()}
    rows: list[Score] = []

    async def judge_one(m: Metric, rr: RunResult | None, lab: str):
        try:
            if m.id in ctx.pair_ids:
                res = await evaluate(ctx.client, ctx.judge_model, m, inp=scn.input, output=new.output, old_output=old.output,
                                     expected=scn.expected_behavior, budget=ctx.budget)
                value = {"new": 1.0, "tie": 0.5, "old": 0.0}[res["verdict"]]
                rows.append(Score(run_result_id=new.id, metric_id=m.id, value=value, rationale=res["rationale"], disputed=res["disputed"],
                                  judge_model=m.judge_model or ctx.judge_model,
                                  details={k: res[k] for k in ("verdict", "preserved_correct_behavior", "confidence")}))
            else:
                model = ctx.cheap_model if (ctx.cheap_model and is_easy_metric(m)) else ctx.judge_model
                res = await evaluate(ctx.client, model, m, inp=scn.input, output=rr.output, expected=scn.expected_behavior, budget=ctx.budget)
                rows.append(Score(run_result_id=rr.id, metric_id=m.id, value=_norm(m, res["value"]), rationale=res["rationale"], judge_model=m.judge_model or model,
                                  disputed=bool(res.get("disputed")), details={"second_judge": res["second_judge"]} if "second_judge" in res else None))
        except AgentError:
            ctx.judge_errors += 1

    jobs = []
    for m in app:
        if m.id in ctx.pair_ids:
            if (new.id, m.id, 0) not in have:
                jobs.append(judge_one(m, None, "new"))
            continue
        for lab, rr in outs.items():
            if (rr.id, m.id, 0) in have:
                continue
            if m.kind == "programmatic":
                v, why = run_programmatic(m.definition, rr.output, scn.expected_behavior)
                rows.append(Score(run_result_id=rr.id, metric_id=m.id, value=v, rationale=why))
            elif ctx.levers.get("programmatic_first") and prog and not passed[lab]:
                ctx.gated += 1  # judge skipped: the output already fails a programmatic check
            else:
                jobs.append(judge_one(m, rr, lab))
    try:
        await gather_all(*jobs)
    finally:  # keep whatever finished, so a cap-pause loses nothing already paid for
        if rows and persist:
            with Session(ctx.engine) as s:
                s.add_all(rows)
                s.commit()
    return rows


async def do_repeat(ctx: RunCtx, scn: Scenario, r: int) -> None:
    old, new = await gather_all(ensure_result(ctx, scn, "old", r), ensure_result(ctx, scn, "new", r))
    await score_repeat(ctx, scn, old, new)


async def control_pairs(ctx: RunCtx, scn: Scenario) -> None:
    """A/A control for pairwise metrics: old r0 vs old r1. Any preference it expresses is judge noise."""
    app = [m for m in ctx.metrics if m.id in ctx.pair_ids and metric_applies(m.applies_to, scn.category, scn.tags)]
    if not app:
        return
    r0, r1 = await gather_all(ensure_result(ctx, scn, "old", 0), ensure_result(ctx, scn, "old", 1))
    with Session(ctx.engine) as s:
        have = {x.metric_id for x in s.exec(select(Score).where(Score.run_result_id == r1.id, Score.judge_repeat_idx == CONTROL)).all()}
    rows = []

    async def one(m: Metric):
        try:
            res = await evaluate(ctx.client, ctx.judge_model, m, inp=scn.input, output=r1.output, old_output=r0.output, budget=ctx.budget)
            rows.append(Score(run_result_id=r1.id, metric_id=m.id, value={"new": 1.0, "tie": 0.5, "old": 0.0}[res["verdict"]],
                              rationale="A/A control", judge_repeat_idx=CONTROL, disputed=res["disputed"],
                              details={k: res[k] for k in ("verdict", "preserved_correct_behavior", "confidence")}))
        except AgentError:
            ctx.judge_errors += 1

    try:
        await gather_all(*[one(m) for m in app if m.id not in have])
    finally:
        if rows:
            with Session(ctx.engine) as s:
                s.add_all(rows)
                s.commit()


def _identical(mid: str, d: dict, pair_ids: set) -> bool:
    """Both arms constant and equal on this metric (for pairwise: no preference at all)."""
    if mid in pair_ids:
        return all(p == 0 for p, _ in d["pair"].values())
    return len(set(d["old"].values()) | set(d["new"].values())) <= 1


def _resolved(per_metric: dict, mv: dict, pair_ids: set, confirm: bool) -> bool:
    """More repeats would not change anything worth knowing about this case. Only metrics that repeats CAN still decide count (a
    noisy judge that cannot reach significance within the repeat budget must not force every case to the maximum). A decided
    case is stopped unless `confirm_flagged` asks to keep going; anything suggestive, unstable or different keeps running."""
    decidable = {m: v for m, v in mv.items() if v.get("decidable", True)}
    for mid, v in decidable.items():
        if v["verdict"] in ("fixed", "broken"):
            if confirm:
                return False
        elif not (v["verdict"] == "unchanged" and _identical(mid, per_metric[mid], pair_ids)):
            return False
    return True


def scenario_state(ctx: RunCtx, sid: str):
    with Session(ctx.engine) as s:
        run = s.get(Run, ctx.run_id)
        per = collect(s, run, {sid}).data.get(sid, {})
    return per, metric_verdicts(per, ctx.noise, ctx.pair_ids, ctx.m_tests, ctx.scn[sid].cluster_id, ctx.max_r)


async def process_scenario(ctx: RunCtx, sid: str) -> None:
    scn = ctx.scn[sid]
    adaptive, early = ctx.levers.get("adaptive_repeats"), ctx.levers.get("early_stopping")
    max_r = max(ctx.repeats, settings.max_repeats_adaptive) if adaptive else ctx.repeats
    r = 0
    while r < max_r:
        if ctx.stop.is_set():
            return
        await do_repeat(ctx, scn, r)
        r += 1
        if r < min(2, ctx.repeats) or r >= max_r:
            continue
        per, mv = scenario_state(ctx, sid)
        if (adaptive or early) and _resolved(per, mv, ctx.pair_ids, settings.confirm_flagged):
            break
    ctx.done += 1
    per, mv = scenario_state(ctx, sid)
    upd(ctx.engine, ctx.run_id, spent=ctx.budget.spent)
    emit(ctx, "progress", done=ctx.done, total=len(ctx.scn), spent=ctx.budget.spent, cap=ctx.budget.cap, repeats=r)
    for mid, v in mv.items():
        if v["verdict"] == "broken":
            emit(ctx, "regression", scenario_id=sid, severity=scn.severity, metric_id=mid, old=v["old"], new=v["new"])


async def run_many(ctx: RunCtx, ids: list[str], worker) -> None:
    sem, errs = asyncio.Semaphore(settings.llm_concurrency), []

    async def one(sid: str):
        async with sem:
            if ctx.stop.is_set():
                return
            try:
                await worker(ctx, sid)
            except BudgetExceeded:
                ctx.cap_hit = True
                ctx.stop.set()
            except Exception as e:
                errs.append(e)
                ctx.stop.set()

    await asyncio.gather(*(one(i) for i in ids))
    if errs:
        raise errs[0]
    if ctx.cap_hit:
        raise BudgetExceeded(ctx.budget.spent, ctx.budget.cap)


def estimate_noise(ctx: RunCtx, ids: list[str] | None = None) -> dict:
    with Session(ctx.engine) as s:
        run = s.get(Run, ctx.run_id)
        c = collect(s, run, set(ids) if ids else None)
    return noise_vars(c, ctx.metrics, ctx.pair_ids, {sid: x.cluster_id for sid, x in ctx.scn.items()}, set(ids) if ids else None)


def _store_noise(ctx: RunCtx, source: str, n: int) -> None:
    bands = {mid: V.band(worst_var(v), ctx.repeats, ctx.repeats) for mid, v in ctx.noise.items()}  # noisiest cluster
    detect = {mid: V.band(worst_var(v), ctx.repeats, ctx.repeats, ctx.m_tests) for mid, v in ctx.noise.items()}
    upd(ctx.engine, ctx.run_id, noise={"metrics": ctx.noise, "band": bands, "detect": detect, "source": source, "n_scenarios": n, "repeats": ctx.repeats})
    emit(ctx, "noise", band=bands, source=source)


def _warn(ctx: RunCtx) -> None:
    names = {m.id: m.name for m in ctx.metrics}
    max_r = max(ctx.repeats, settings.max_repeats_adaptive) if ctx.levers.get("adaptive_repeats") else ctx.repeats
    binary = {m.id for m in ctx.metrics if is_binary_metric(m)}
    need = {}
    for mid, v in ctx.noise.items():
        var = worst_var(v)  # noisiest cluster
        if mid in binary:
            k = V.needed_repeats(V.q_from_var(var), ctx.m_tests)
        elif V.band(var, max_r, max_r) >= settings.noisy_band_cut:
            k = V.suggested_repeats(var, max_r)
        else:
            continue
        if k > max_r:
            need[mid] = k
    if not need:
        return
    msg = (f"Noise is too high to call individual cases fixed or broken on: {', '.join(names[m] for m in need)} "
           f"(needs about {max(need.values())} repeats, up to {max_r} are used). Cases that look different will be reported as "
           f"inconclusive; the per-metric aggregate tests still apply. Consider more repeats.")
    with Session(ctx.engine) as s:
        run = s.get(Run, ctx.run_id)
        if msg not in (run.warnings or []):
            run.warnings = [*(run.warnings or []), msg]
            s.add(run)
            s.commit()
    emit(ctx, "warning", message=msg, suggested_repeats=max(need.values()))


async def smoke(ctx: RunCtx, ids: list[str]) -> None:
    k = min(len(ids), max(settings.smoke_min, math.ceil(len(ids) * settings.smoke_fraction)))
    pick = [ids[int(i * len(ids) / k)] for i in range(k)]
    for sid in pick:  # prompts must render fully: every {{var}} in a template needs a value in the scenario input
        for text in (ctx.old_text, ctx.new_text):
            missing = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text)) - set(ctx.scn[sid].input) - ctx.optional)
            if missing:
                raise RunFailure(f"prompt placeholder(s) {missing} have no value in scenario {sid} (input has {sorted(ctx.scn[sid].input)})")

    async def worker(c: RunCtx, sid: str):
        scn = c.scn[sid]
        for r in range(settings.smoke_repeats):
            if c.stop.is_set():
                return
            await do_repeat(c, scn, r)
        await control_pairs(c, scn)

    await run_many(ctx, pick, worker)
    if ctx.judge_errors:
        raise RunFailure(f"{ctx.judge_errors} judge outputs could not be parsed during the smoke test; fix the rubric or judge model")
    ctx.noise = estimate_noise(ctx, pick)
    _store_noise(ctx, "smoke", len(pick))
    _warn(ctx)


async def deep_dive(ctx: RunCtx, ids: list[str]) -> list[str]:
    """Neighbours of broken cases. Asks (pauses) first if it would exceed the cap."""
    with Session(ctx.engine) as s:
        run = s.get(Run, ctx.run_id)
        cfg = dict(run.config)
        if "deep_dive_ids" in cfg:
            return cfg["deep_dive_ids"]
        c = collect(s, run, set(ids))
    broken = []
    for sid in ids:
        mv = metric_verdicts(c.data.get(sid, {}), ctx.noise, ctx.pair_ids, ctx.m_tests, ctx.scn[sid].cluster_id)
        if any(v["verdict"] == "broken" for v in mv.values()):
            broken.append(sid)
    broken.sort(key=lambda i: -ctx.scn[i].severity)
    n_new = min(len(broken) * settings.deep_dive_per_regression, settings.deep_dive_max_scenarios)
    if not n_new:
        upd(ctx.engine, ctx.run_id, config={**cfg, "deep_dive_ids": []})
        return []
    needed = ctx.budget.spent / max(len(ids), 1) * n_new
    if ctx.budget.spent + needed > ctx.budget.cap:
        raise DeepDiveNeedsBudget(needed)
    with Session(ctx.engine) as s:
        try:
            pctx = PlanCtx(s, ctx.project_id)
        except PlanError:
            _add_warning(ctx, "Deep dive skipped: no stage-1 analysis to generate neighbours from.")
            upd(ctx.engine, ctx.run_id, config={**cfg, "deep_dive_ids": []})
            return []
    emit(ctx, "stage", stage="deep_dive", regressions=len(broken), new=n_new)
    new_rows: list[Scenario] = []
    for i, sid in enumerate(broken):
        if len(new_rows) >= n_new:
            break
        src = ctx.scn[sid]
        text = " | ".join(str(v) for v in src.input.values())
        gens = await scenario_generator.generate(
            ctx.client, pctx.model, context=pctx.context, diff=pctx.diff, category=src.category,
            n=min(settings.deep_dive_per_regression, n_new - len(new_rows)), seeds=[], feedback="", existing=[text],
            variation=1000 + i, input_schema=pctx.input_schema, budget=ctx.budget,
            guidance=f"This case regressed. Write close variations (same intent, different wording and details) of: {text}")
        new_rows += [Scenario(project_id=ctx.project_id, cluster_id=src.cluster_id, category=src.category, input=g.input, rationale=g.rationale,
                              expected_behavior=g.expected_behavior or src.expected_behavior, tags=[*g.tags, "deep_dive"],
                              severity=max(src.severity, g.severity), diff_relevance=src.diff_relevance) for g in gens]
    with Session(ctx.engine) as s:
        s.add_all(new_rows)
        s.commit()
        drop_near_duplicates(s, ctx.project_id, {x.id for x in new_rows})
        keep = [x for x in new_rows if s.get(Scenario, x.id)]
        for x in keep:
            ctx.scn[x.id] = s.get(Scenario, x.id)
    upd(ctx.engine, ctx.run_id, config={**cfg, "deep_dive_ids": [x.id for x in keep]})
    return [x.id for x in keep]


def load_run_tier(engine: Engine, run_id: str) -> str:
    with Session(engine) as s:
        return s.get(Run, run_id).tier


def _add_warning(ctx: RunCtx, msg: str) -> None:
    with Session(ctx.engine) as s:
        run = s.get(Run, ctx.run_id)
        run.warnings = [*(run.warnings or []), msg]
        s.add(run)
        s.commit()
    emit(ctx, "warning", message=msg)


def run_state(s: Session, run: Run) -> dict:
    cfg = run.config or {}
    return {"id": run.id, "status": run.status, "stage": run.stage, "spent": run.spent, "cap": run.budget_cap,
            "pause_reason": run.pause_reason, "error": run.error, "warnings": run.warnings or [], "noise": run.noise,
            "total": len(cfg.get("scenario_ids", [])) + len(cfg.get("deep_dive_ids", [])), "tier": run.tier}


def _finish(ctx: RunCtx, status: str, **kw) -> None:
    upd(ctx.engine, ctx.run_id, status=status, spent=ctx.budget.spent, **kw)
    from app.pipeline.report import build_report  # late import: report depends on the runner's collectors

    if status in ("done", "stopped"):
        try:
            build_report(ctx.engine, ctx.run_id)
            from app.pipeline import stale

            if (load_run_tier(ctx.engine, ctx.run_id)) != "heldout":  # held-out validation runs are not the project's main run
                with Session(ctx.engine) as s:
                    stale.clear(s, ctx.project_id, "Run", "Report")
        except Exception as e:  # a report bug must not lose the run
            _add_warning(ctx, f"report generation failed: {e}")
    with Session(ctx.engine) as s:
        emit(ctx, status, **{k: v for k, v in run_state(s, s.get(Run, ctx.run_id)).items() if k in ("spent", "cap", "pause_reason", "error", "stage")})


async def execute_run(engine: Engine, client: LLMClient, run_id: str) -> dict:
    ctx = load_ctx(engine, client, run_id)
    events.reset(run_id)
    _controls[run_id] = ctx
    with Session(engine) as s:
        run = s.get(Run, run_id)
        started = run.started_at or datetime.now(timezone.utc)
        ids = list(run.config["scenario_ids"])
        cfg = dict(run.config)
        smoke_done = bool(run.noise)
    upd(engine, run_id, status="running", started_at=started, pause_reason="", error="")
    try:
        if not smoke_done:
            upd(engine, run_id, stage="smoke")
            emit(ctx, "stage", stage="smoke")
            await smoke(ctx, ids)
        upd(engine, run_id, stage="main")
        emit(ctx, "stage", stage="main")
        await run_many(ctx, ids, process_scenario)
        if ctx.user_stop:
            _finish(ctx, "stopped")
            return {"status": "stopped"}
        ctx.noise = estimate_noise(ctx, ids)  # final, pooled over the whole main run
        _store_noise(ctx, "pooled", len(ids))
        _warn(ctx)
        extra = [] if (cfg.get("deep_dive") is False) else await deep_dive(ctx, ids)
        if extra:
            upd(engine, run_id, stage="deep_dive")
            await run_many(ctx, extra, process_scenario)
            if ctx.user_stop:
                _finish(ctx, "stopped")
                return {"status": "stopped"}
            ctx.noise = estimate_noise(ctx, ids + extra)
            _store_noise(ctx, "pooled", len(ids + extra))
        upd(engine, run_id, stage="done")
        _finish(ctx, "done", finished_at=datetime.now(timezone.utc))
        return {"status": "done"}
    except BudgetExceeded:
        if ctx.user_stop:
            _finish(ctx, "stopped")
            return {"status": "stopped"}
        _finish(ctx, "paused", pause_reason="cap")
        return {"status": "paused", "reason": "cap"}
    except DeepDiveNeedsBudget as e:
        _finish(ctx, "paused", pause_reason="deep_dive_needs_budget")
        emit(ctx, "deep_dive_needs_budget", needed=e.needed)
        return {"status": "paused", "reason": "deep_dive_needs_budget", "needed": e.needed}
    except RunFailure as e:
        _finish(ctx, "failed", error=redact(str(e)))
        return {"status": "failed", "error": str(e)}
    except Exception as e:  # provider outage etc.: keep all state, let the user resume
        _finish(ctx, "paused", pause_reason=redact(f"error: {type(e).__name__}: {e}"))
        return {"status": "paused", "reason": "error", "error": redact(str(e))}
    finally:
        _controls.pop(run_id, None)


def finalize_stopped(engine: Engine, run_id: str) -> None:
    """Stop a run that has no live task (paused / interrupted): mark stopped and build the partial report."""
    upd(engine, run_id, status="stopped", finished_at=datetime.now(timezone.utc))
    from app.pipeline.report import build_report

    build_report(engine, run_id)
