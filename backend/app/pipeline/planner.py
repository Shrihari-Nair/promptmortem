"""Stage 2 orchestration: generate (uncapped) -> critic loop -> dedupe -> diff relevance -> cluster -> coverage."""
import asyncio
import re

import numpy as np
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.agents import scenario_critic, scenario_generator
from app.agents.schemas import CATEGORIES, Change, ContextAnalysis, DiffAnalysis, GenScenario
from app.config import settings
from app.core.embeddings import embed, scenario_text
from app.core.llm import LLMClient
from app.core.models import DatasetItem, Feedback, Project, ProjectAnalysis, PromptVersion, Scenario, ScenarioCluster
from app.pipeline.clustering import cluster_scenarios, update_coverage
from app.pipeline.execute import fill_optional, project_optional, run_target
from app.pipeline.feedback import feedback_text

ALWAYS = ["happy_path", "edge_case", "ambiguous", "long_noisy", "out_of_scope", "adversarial", "format_sensitive",
          "regression_guard"]


class PlanError(Exception):
    pass


class Ctx:
    """Everything generation needs, loaded once so no DB session is held across LLM awaits."""

    def __init__(self, s: Session, project_id: str):
        p = s.get(Project, project_id)
        if not p:
            raise PlanError("project not found")
        an = {a.kind: a.data for a in s.exec(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project_id)).all()}
        if "context" not in an or "diff" not in an:
            raise PlanError("run /analyze first (stage 1)")
        self.project_id, self.model = project_id, p.judge_model_default
        self.context, self.diff = ContextAnalysis(**an["context"]), DiffAnalysis(**an["diff"])
        self.description = f"{p.app_description} {p.constraints}"
        self.app_description, self.constraints = p.app_description, p.constraints
        self.seeds = [d.input for d in s.exec(select(DatasetItem).where(DatasetItem.project_id == project_id)).all()][:10]
        pv = {v.label: v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all()}
        self.input_schema = (pv.get("new") or pv.get("old") or PromptVersion(project_id="", label="", text="")).variables_schema
        self.target_model = p.target_model
        self.old_prompt = pv["old"].text if "old" in pv else ""
        self.optional = project_optional(s, project_id)


def categories_for(ctx: Ctx) -> list[str]:
    blob = " ".join([ctx.description, ctx.context.task, *ctx.context.risk_areas]).lower()
    cats = list(ALWAYS)
    if re.search(r"multilingual|languages?|translat|spanish|french|german|hindi|japanese|chinese|arabic", blob):
        cats.append("multilingual")
    if re.search(r"safety|medical|legal|harm|privacy|pii|compliance|financial|abuse", blob):
        cats.append("safety_critical")
    return cats


def _row(project_id: str, g: GenScenario, category: str) -> Scenario:
    return Scenario(project_id=project_id, category=category, input=g.input, rationale=g.rationale,
                    expected_behavior=g.expected_behavior, tags=g.tags, linked_changes=g.linked_changes,
                    severity=g.severity, diff_relevance=g.diff_relevance)


async def probe_old_prompt(client: LLMClient, ctx: Ctx, inputs: list[dict]) -> list[dict]:
    """regression_guard scenarios must exercise what the OLD prompt actually does well: run it on a few realistic inputs
    (dataset/trace inputs first) and show the generator its real replies."""
    pick = inputs[: settings.regression_probe_n]
    if not pick or not ctx.old_prompt:
        return []
    outs = await asyncio.gather(*[run_target(client, ctx.target_model, ctx.old_prompt, fill_optional(i, ctx.optional)) for i in pick])
    return [{"input": i, "old_prompt_reply": o.text[:600]} for i, o in zip(pick, outs)]


async def _generate(client: LLMClient, ctx: Ctx, s_fb: str, existing: list[str], category: str, n: int,
                    variation: int, change: Change | None = None, guidance: str = "", old_behaviors: list[dict] | None = None) -> list[Scenario]:
    gens = await scenario_generator.generate(
        client, ctx.model, context=ctx.context, diff=ctx.diff, category=category, n=n, seeds=ctx.seeds,
        feedback=s_fb, existing=existing, variation=variation, input_schema=ctx.input_schema, change=change,
        guidance=guidance, old_behaviors=old_behaviors)
    return [_row(ctx.project_id, g, category) for g in gens]


async def _gather(client, ctx, s_fb, existing, specs: list[dict], variation: int, old_behaviors: list[dict] | None = None) -> list[Scenario]:
    sem = asyncio.Semaphore(settings.llm_concurrency)

    async def one(i: int, sp: dict):
        async with sem:
            return await _generate(client, ctx, s_fb, existing, variation=variation + i, old_behaviors=old_behaviors if sp.get("category") == "regression_guard" else None, **sp)

    return [r for batch in await asyncio.gather(*(one(i, sp) for i, sp in enumerate(specs))) for r in batch]


def initial_specs(ctx: Ctx) -> list[dict]:
    specs = [{"category": c, "n": settings.scenarios_per_category_initial} for c in categories_for(ctx)]
    specs += [{"category": "diff_targeted", "n": settings.scenarios_per_diff_change, "change": ch} for ch in ctx.diff.changes]
    return specs


def drop_near_duplicates(s: Session, project_id: str, only_ids: set[str] | None = None, include_rejected: bool = False) -> int:
    """Delete generated, unreviewed, unpinned scenarios that are near-duplicates of an earlier/protected one."""
    rows = s.exec(select(Scenario).where(Scenario.project_id == project_id).order_by(text("rowid"))).all()  # insertion order
    rows = [r for r in rows if include_rejected or r.status != "rejected"]

    def droppable(r: Scenario) -> bool:
        return (r.origin == "generated" and not r.reviewed and not r.pinned and r.status != "rejected"
                and (only_ids is None or r.id in only_ids))

    rows.sort(key=droppable)  # stable: protected first, then candidates in insertion order
    emb = embed([scenario_text(r.input) for r in rows])
    kept: list[int] = []
    dropped = 0
    for i, r in enumerate(rows):
        if droppable(r) and kept and float((emb[kept] @ emb[i]).max()) >= settings.dedupe_cosine_threshold:
            s.delete(r)
            dropped += 1
        else:
            kept.append(i)
    s.commit()
    return dropped


def apply_diff_relevance(s: Session, project_id: str, ctx: Ctx, ids: set[str] | None = None) -> None:
    """0.7 * generator's score + 0.3 * embedding similarity to the changed text (PLAN section 8)."""
    changed = [c.new_text or c.old_text for c in ctx.diff.changes if (c.new_text or c.old_text)]
    rows = [r for r in s.exec(select(Scenario).where(Scenario.project_id == project_id)).all() if ids is None or r.id in ids]
    if not changed or not rows:
        return
    sim = (embed([scenario_text(r.input) for r in rows]) @ embed(changed).T).max(axis=1)
    for r, v in zip(rows, sim):
        r.diff_relevance = float(np.clip(0.7 * r.diff_relevance + 0.3 * max(0.0, float(v)), 0, 1))
        s.add(r)
    s.commit()


def _existing_texts(s: Session, project_id: str, category: str | None = None) -> list[str]:
    return [scenario_text(r.input) for r in s.exec(select(Scenario).where(Scenario.project_id == project_id)).all()
            if category is None or r.category == category]


async def plan_scenarios(engine: Engine, client: LLMClient, project_id: str, progress=lambda m: None) -> dict:
    with Session(engine) as s:
        ctx = Ctx(s, project_id)
        fb = feedback_text(s, project_id)
        # dataset items become verified, pre-approved scenarios (once)
        have = {t for r in s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.origin == "dataset")).all() for t in r.tags}
        for d in s.exec(select(DatasetItem).where(DatasetItem.project_id == project_id)).all():
            if f"dataset:{d.id}" not in have:
                # 'verified' means backed by the user's LABELS. A trace without an expected value is a real input but no ground
                # truth: it stays synthetic and unreviewed (it still seeds generation and is never deduplicated away).
                # a pass/fail `expected` labels an OBSERVED reply (it calibrates the judges); it is not wanted behaviour for a scenario
                labelled = d.expected not in (None, "") and str(d.expected).strip().lower() not in ("pass", "fail")
                s.add(Scenario(project_id=project_id, category="happy_path", input=d.input, origin="dataset",
                               tier="verified" if labelled else "synthetic", status="approved" if labelled else "proposed", reviewed=labelled,
                               tags=["dataset", f"dataset:{d.id}", *([] if d.source == "user" else [d.source])],
                               expected_behavior=str(d.expected) if labelled else "",
                               rationale="from the user's dataset" if d.source == "user" else "a real input from your traces (unlabelled)"))
        s.commit()
    progress("generating scenarios")
    specs = initial_specs(ctx)
    new = await _gather(client, ctx, fb, [], [sp for sp in specs if sp["category"] != "regression_guard"], variation=0)
    progress("probing the old prompt for regression guards")
    with Session(engine) as s:
        real = [x.input for x in s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.origin == "dataset")).all()]
    pool = real + [x.input for x in new if x.category in ("happy_path", "edge_case")]
    behaviors = await probe_old_prompt(client, ctx, pool)
    new += await _gather(client, ctx, fb, [], [sp for sp in specs if sp["category"] == "regression_guard"], variation=0, old_behaviors=behaviors)
    with Session(engine) as s:
        s.add_all(new)
        s.commit()
        new_ids = {r.id for r in new}
        stats = {"generated": len(new), "duplicates_dropped": drop_near_duplicates(s, project_id, new_ids), "unrealistic_dropped": 0}

    for rnd in range(1, settings.max_critic_rounds + 1):
        progress(f"critic round {rnd}")
        with Session(engine) as s:
            rows = s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all()
            lines = [f"{r.id} | {r.category} | {scenario_text(r.input)[:200]}" for r in rows]
            cov = {c: sum(1 for r in rows if r.category == c) for c in CATEGORIES}
        out = await scenario_critic.critique(client, ctx.model, context=ctx.context, scenarios=lines, coverage=cov, round=rnd)
        with Session(engine) as s:
            for r in s.exec(select(Scenario).where(Scenario.project_id == project_id)).all():
                if r.id in out.duplicates + out.unrealistic and r.origin == "generated" and not (r.reviewed or r.pinned):
                    s.delete(r)
                    stats["duplicates_dropped" if r.id in out.duplicates else "unrealistic_dropped"] += 1
                elif r.id in out.uncertain:
                    r.flagged = True
                    s.add(r)
            s.commit()
        gaps = [g for g in out.gaps if g.category in CATEGORIES]
        if not gaps:
            break
        with Session(engine) as s:
            ex = _existing_texts(s, project_id)
        fill = await _gather(client, ctx, fb, ex, [{"category": g.category, "n": settings.gap_fill_n, "guidance": g.description}
                                                   for g in gaps], variation=100 * rnd)
        with Session(engine) as s:
            s.add_all(fill)
            s.commit()
            stats["generated"] += len(fill)
            stats["duplicates_dropped"] += drop_near_duplicates(s, project_id, {r.id for r in fill})

    progress("clustering")
    with Session(engine) as s:
        apply_diff_relevance(s, project_id, ctx)
        clusters = cluster_scenarios(s, project_id)
        cov = update_coverage(s, project_id)
        stats |= {"scenarios": sum(sum(v.values()) for v in cov["categories"].values()), "clusters": len(clusters)}
    return stats


async def generate_more(engine: Engine, client: LLMClient, project_id: str, category: str, n: int, *,
                        change: Change | None = None, include_rejected: bool = False) -> dict:
    """Add n scenarios to a category (also used after a cluster rejection). Dedupes against existing ones."""
    with Session(engine) as s:
        ctx = Ctx(s, project_id)
        fb = feedback_text(s, project_id, category)
        ex = _existing_texts(s, project_id, category)
    behaviors = None
    if category == "regression_guard":
        with Session(engine) as s:
            seeds = [x.input for x in s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all()
                     if x.origin == "dataset" or x.category in ("happy_path", "edge_case")]
        behaviors = await probe_old_prompt(client, ctx, seeds)
    batch = await _generate(client, ctx, fb, ex, category, n, variation=len(ex), change=change, old_behaviors=behaviors)
    with Session(engine) as s:
        s.add_all(batch)
        s.commit()
        ids = {r.id for r in batch}
        dropped = drop_near_duplicates(s, project_id, ids, include_rejected=include_rejected)
        apply_diff_relevance(s, project_id, ctx, ids)
        cluster_scenarios(s, project_id, [i for i in ids])
        update_coverage(s, project_id)
    return {"added": len(batch) - dropped, "duplicates_dropped": dropped}


async def reject_cluster(engine: Engine, client: LLMClient, cluster_id: str, comment: str) -> dict:
    """Reject a cluster (keeps approved/pinned scenarios), store the comment as feedback, regenerate with it."""
    with Session(engine) as s:
        cl = s.get(ScenarioCluster, cluster_id)
        if not cl:
            raise PlanError("cluster not found")
        pid, cat = cl.project_id, cl.category
        gone = [x for x in s.exec(select(Scenario).where(Scenario.cluster_id == cluster_id)).all()
                if not (x.pinned or x.reviewed)]
        for x in gone:
            x.status = "rejected"
            s.add(x)
        cl.status = "rejected"
        s.add(cl)
        s.add(Feedback(project_id=pid, target_type="cluster", target_id=cluster_id, action="reject", comment=comment))
        s.commit()
        n, linked = max(len(gone), 5), {c for x in gone for c in x.linked_changes}
        diff = DiffAnalysis(**s.exec(select(ProjectAnalysis).where(ProjectAnalysis.project_id == pid, ProjectAnalysis.kind == "diff")).one().data)
        update_coverage(s, pid)
    added = 0
    if cat == "diff_targeted":  # keep the per-change structure
        changes = [c for c in diff.changes if c.id in linked] or diff.changes
        for c in changes:
            added += (await generate_more(engine, client, pid, cat, max(1, n // len(changes)), change=c, include_rejected=True))["added"]
    else:
        added = (await generate_more(engine, client, pid, cat, n, include_rejected=True))["added"]
    return {"rejected": len(gone), "added": added}
