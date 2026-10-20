"""Stage 3: design metrics, preview them on sample outputs, calibrate judges against the dataset."""
import asyncio
from dataclasses import asdict

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.agents import judge as judge_agent
from app.agents import metric_designer
from app.agents.schemas import JudgeDef, MetricSpec
from app.config import settings
from app.core.llm import Budget, LLMClient
from app.core.models import Calibration, DatasetItem, Metric, Project, PromptVersion, Scenario
from app.pipeline.execute import fill_optional, project_optional, run_target
from app.pipeline.feedback import metric_feedback_text
from app.pipeline.metrics_engine import metric_applies, run_programmatic
from app.pipeline.planner import Ctx, PlanError

MAX_METRICS = 12
def regression_spec(ctx: "Ctx") -> MetricSpec:
    """The always-present pairwise regression metric. Its rubric carries the app's constraints and the INTENDED changes: a judge
    that is not told what the developer meant to change prefers the longer, more polished reply and calls an intended
    simplification a regression (measured on real Gemini: it rated 18 compliant concise replies as regressions)."""
    intents = "; ".join(dict.fromkeys(c.intent.strip().rstrip(".") for c in ctx.diff.changes if c.intent.strip())) or "none stated"
    return MetricSpec(
        name="No regression vs previous output", kind="llm_judge", weight=2.0, applies_to={"categories": ["*"]},
        definition={"mode": "pairwise", "output": "binary", "pass_examples": [], "fail_examples": [], "rubric": (
            "Which output is the better reply to this input for the application described below? Judge ONLY: (1) is it correct, with no invented facts or promises; "
            "(2) does it contain the information the user actually needs in order to act; (3) does it comply with the application's constraints. "
            "Do NOT prefer an output because it is longer, more detailed, more polite or more thorough, and do NOT penalise an output for being shorter or more direct. "
            "If both are correct, sufficient and compliant, answer tie. "
            f"The developer deliberately changed the prompt as follows, so these differences are INTENDED and must not count against the newer output: {intents}. "
            f"Application: {ctx.app_description or '(not described)'} Constraints: {ctx.constraints or '(none stated)'}")},
        notes="Always present: compares old vs new output with A/B position swap; told the intended changes so it does not punish them.")


EXPECTED = MetricSpec(
    name="Matches user-provided expected output", kind="programmatic", weight=1.0, applies_to={"tags": ["dataset"]},
    definition={"checks": [{"type": "equals", "value": "$expected", "ignore_case": True}]},
    notes="From the dataset's expected values (verified tier).")


def is_pairwise(m: Metric) -> bool:
    return m.kind == "llm_judge" and m.definition.get("mode") == "pairwise"


def cheap_model_for(judge_model: str) -> str | None:
    """The cheaper judge used for 'easy' rubrics when that cost lever is on. Override with PV_CHEAP_JUDGE_MODEL."""
    if settings.cheap_judge_model:
        return settings.cheap_judge_model
    prefix = judge_model.partition("/")[0] if "/" in judge_model else ("anthropic" if judge_model.startswith("claude") else "openai")
    return {"gemini": "gemini/gemini-2.5-flash-lite", "openai": "openai/gpt-4o-mini", "anthropic": "anthropic/claude-haiku-5-5", "fake": "fake/cheap"}.get(prefix)


def is_easy_metric(m: Metric) -> bool:
    """Short binary pointwise rubric with no pinned judge model: safe to give to the cheaper judge."""
    return (m.kind == "llm_judge" and not is_pairwise(m) and m.judge_model is None and m.definition.get("output", "binary") == "binary"
            and len(m.definition.get("rubric", "")) <= settings.easy_rubric_max_chars)


def cheap_cost_ratio(judge_model: str, cheap_model: str) -> float:
    """cheap/default price ratio from LiteLLM's price table when both models are known, else the configured heuristic."""
    try:
        import litellm

        a = sum(litellm.cost_per_token(judge_model, 1000, 200))
        b = sum(litellm.cost_per_token(cheap_model, 1000, 200))
        if a > 0 and b > 0:
            return min(b / a, 1.0)
    except Exception:
        pass
    return settings.cheap_judge_cost_ratio


def is_binary_metric(m: Metric) -> bool:
    """Scores are 0/1 (pass/fail checks, binary rubrics, pairwise): tested exactly. Fractional/likert scores use the noise band."""
    if m.kind == "programmatic":
        return m.definition.get("aggregate", "all_pass") in ("all_pass", "any_pass")
    return m.definition.get("output", "binary") == "binary"


def _labelled(items):
    """Items with a real expected value. (SQL `!= NULL` would keep JSON nulls: unlabelled traces must never count as labelled.)"""
    return [d for d in items if d.expected not in (None, "")]


def _label(v) -> str | None:
    s = str(v).strip().lower()
    return s if s in ("pass", "fail") else None


def _row(project_id: str, spec: MetricSpec) -> Metric:
    return Metric(project_id=project_id, name=spec.name, kind=spec.kind, definition=spec.definition,
                  applies_to=spec.applies_to, scale=spec.scale, judge_model=spec.judge_model, weight=spec.weight, notes=spec.notes)


async def design_metrics(engine: Engine, client: LLMClient, project_id: str) -> dict:
    with Session(engine) as s:
        ctx = Ctx(s, project_id)
        scns = [x for x in s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all()]
        if not scns:
            raise PlanError("generate scenarios first (stage 2)")
        by_cat: dict[str, list[dict]] = {}
        for x in sorted(scns, key=lambda x: (x.status == "proposed", x.id)):  # approved first
            if len(by_cat.setdefault(x.category, [])) < 4:
                by_cat[x.category].append({"input": x.input, "tags": x.tags})
        items = _labelled(s.exec(select(DatasetItem).where(DatasetItem.project_id == project_id)).all())
        dataset = [{"input": d.input, "expected": d.expected} for d in items][:5]
        # An exact-match metric only makes sense for short, categorical expected values (labels, ids, yes/no). Free-text gold
        # answers would fail it essentially always, so they are used for judge calibration instead.
        gold = [d for d in items if _label(d.expected) is None]
        has_gold = bool(gold) and all(len(str(d.expected).split()) <= 5 for d in gold)
        fb = metric_feedback_text(s, project_id)
    out = await metric_designer.design(client, ctx.model, context=ctx.context, diff=ctx.diff, scenarios=by_cat,
                                       dataset=dataset, feedback=fb)
    with Session(engine) as s:
        for m in s.exec(select(Metric).where(Metric.project_id == project_id, Metric.status == "proposed")).all():
            s.delete(m)  # untouched earlier proposals are replaced; approved/edited/rejected are kept
        s.commit()
        kept = s.exec(select(Metric).where(Metric.project_id == project_id)).all()
        names = {m.name.lower() for m in kept}
        specs = [sp for sp in out.metrics if sp.name.lower() not in names][:MAX_METRICS]
        have_pair = any(is_pairwise(m) for m in kept) or any(sp.definition.get("mode") == "pairwise" for sp in specs)
        if not have_pair:
            specs.append(regression_spec(ctx))
        if has_gold and EXPECTED.name.lower() not in names:
            specs.append(EXPECTED)
        s.add_all([_row(project_id, sp) for sp in specs])
        s.commit()
        total = s.exec(select(Metric).where(Metric.project_id == project_id, Metric.status != "rejected")).all()
        return {"added": len(specs), "programmatic": sum(m.kind == "programmatic" for m in total),
                "llm_judge": sum(m.kind == "llm_judge" for m in total)}


async def evaluate(client: LLMClient, default_model: str, m: Metric, *, inp: dict, output: str, old_output: str | None = None,
                   expected: str = "", budget: Budget | None = None) -> dict:
    """Score one output (or one old/new pair for pairwise metrics). Shared by preview, calibration and the runner."""
    if m.kind == "programmatic":
        v, why = run_programmatic(m.definition, output, expected)
        return {"value": v, "rationale": why}
    d, model = JudgeDef.model_validate(m.definition), m.judge_model or default_model
    if d.mode == "pairwise":
        r = await judge_agent.pairwise(client, model, d, inp, old_output or "", output, budget=budget)
        return asdict(r)
    j = await judge_agent.judge(client, model, d, inp, output, budget=budget)
    res = {"value": j.score, "verdict": j.verdict, "rationale": j.rationale, "confidence": j.confidence}
    second = d.ensemble_model or settings.judge_ensemble_model
    if d.ensemble and second and second != model:  # a second, different judge; disagreement is logged, the primary verdict stands
        j2 = await judge_agent.judge(client, second, d, inp, output, budget=budget)
        res.update({"disputed": j2.verdict != j.verdict, "second_judge": {"model": second, "verdict": j2.verdict, "rationale": j2.rationale}})
    return res


async def preview(engine: Engine, client: LLMClient, metric_id: str, samples: list[dict] | None = None, n: int = 5) -> list[dict]:
    """Run a metric on 3-5 sample outputs. Without samples, generates outputs for a few scenarios (new prompt; old too for pairwise)."""
    with Session(engine) as s:
        m = s.get(Metric, metric_id)
        if not m:
            raise PlanError("metric not found")
        p = s.get(Project, m.project_id)
        pv = {v.label: v.text for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == p.id)).all()}
        if samples is None:
            if "new" not in pv or "old" not in pv:
                raise PlanError("save old and new prompts first")
            pool = [x for x in s.exec(select(Scenario).where(Scenario.project_id == p.id, Scenario.status != "rejected")).all()]
            fit = [x for x in pool if metric_applies(m.applies_to, x.category, x.tags)] or pool
            fit.sort(key=lambda x: (x.status == "proposed", -x.diff_relevance, x.id))
            samples = [{"input": x.input, "expected": x.expected_behavior, "scenario_id": x.id} for x in fit[:n]]
        pw = is_pairwise(m)
        model, default = p.target_model, p.judge_model_default
        optional = project_optional(s, p.id)
    if not samples:
        raise PlanError("no scenarios to preview on")

    async def one(sm: dict) -> dict:
        if "output" not in sm:
            sm = {**sm, "output": (await run_target(client, model, pv["new"], fill_optional(sm["input"], optional))).text}
            if pw:
                sm["old_output"] = (await run_target(client, model, pv["old"], fill_optional(sm["input"], optional))).text
        if pw and sm.get("old_output") is None:
            raise PlanError("pairwise preview needs 'old_output' in each sample")
        res = await evaluate(client, default, m, inp=sm["input"], output=sm["output"], old_output=sm.get("old_output"),
                             expected=str(sm.get("expected", "")))
        return {**sm, "result": res}

    return list(await asyncio.gather(*(one(sm) for sm in samples)))


async def calibrate(engine: Engine, client: LLMClient, metric_id: str) -> dict:
    """Agreement between a pointwise judge and YOUR labels. Only explicit labels count: a trace (or dataset row) with an observed
    `output` and `expected` = pass | fail. Free-text `expected` values are NOT assumed to be gold replies (they are often descriptions
    of the wanted behaviour, which no judge could 'pass' as a reply) and are used as expected behaviour instead.
    The labels should be about what the rubric checks: calibrate a metric on labels that match it."""
    with Session(engine) as s:
        m = s.get(Metric, metric_id)
        if not m or m.kind != "llm_judge" or is_pairwise(m):
            raise PlanError("calibration applies to pointwise LLM-judge metrics")
        p = s.get(Project, m.project_id)
        items = []
        for d in _labelled(s.exec(select(DatasetItem).where(DatasetItem.project_id == m.project_id)).all()):
            lab = _label(d.expected)
            out = d.label_notes.removeprefix("observed output: ") if lab and d.label_notes.startswith("observed output: ") else None
            if lab and out:
                items.append({"input": d.input, "output": out, "label": lab})
        items = items[: settings.calibration_max_items]
        if not items:
            raise PlanError("no labelled examples to calibrate against: import traces with an observed `output` and `expected` set to pass or fail")
        default = p.judge_model_default
    res = await asyncio.gather(*(evaluate(client, default, m, inp=i["input"], output=i["output"]) for i in items))
    rows = [{**i, "judge": r["verdict"], "agree": r["verdict"] == i["label"], "rationale": r["rationale"]} for i, r in zip(items, res)]
    agreement = sum(r["agree"] for r in rows) / len(rows)
    with Session(engine) as s:
        cal = s.exec(select(Calibration).where(Calibration.metric_id == metric_id)).first() or Calibration(project_id=m.project_id, metric_id=metric_id)
        cal.n_labeled, cal.agreement = len(rows), agreement
        cal.details = {"disagreements": [r for r in rows if not r["agree"]][:10],
                       "by_label": {lab: {"n": sum(r["label"] == lab for r in rows), "agree": sum(r["label"] == lab and r["agree"] for r in rows)} for lab in ("pass", "fail")}}
        s.add(cal)
        s.commit()
    return {"n_labeled": len(rows), "agreement": agreement}
