"""Dependency tracking (PLAN: 'changing upstream data marks downstream as stale'). An upstream change marks every downstream
stage that has actually been DONE; performing the stage again (or acknowledging it) clears the mark. Nothing is recomputed
automatically: stale means 'review or redo this step', never 'silently different results'."""
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.models import Metric, Optimization, Plan, ProjectAnalysis, Report, Run, Scenario

STAGES = ["Understand", "Scenarios", "Metrics", "Budget", "Run", "Report", "Refine"]
DOWNSTREAM = {  # event -> the stages it invalidates
    "prompts": STAGES,
    "analysis": STAGES[1:],
    "scenarios": ["Metrics", "Budget", "Run", "Report", "Refine"],
    "metrics": ["Budget", "Run", "Report", "Refine"],
    "plan": ["Run", "Report"],
}


def _plan(s: Session, project_id: str) -> Plan:
    plan = s.exec(select(Plan).where(Plan.project_id == project_id)).first() or Plan(project_id=project_id)
    s.add(plan)
    s.flush()
    return plan


def done(s: Session, project_id: str) -> set[str]:
    plan = _plan(s, project_id)
    d = set()
    if s.exec(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project_id)).first():
        d.add("Understand")
    if s.exec(select(Scenario).where(Scenario.project_id == project_id)).first():
        d.add("Scenarios")
    if s.exec(select(Metric).where(Metric.project_id == project_id)).first():
        d.add("Metrics")
    if plan.chosen or plan.probe:
        d.add("Budget")
    runs = s.exec(select(Run).where(Run.plan_id == plan.id)).all()
    if runs:
        d.add("Run")
        if any(s.exec(select(Report).where(Report.run_id == r.id)).first() for r in runs):
            d.add("Report")
    if s.exec(select(Optimization).where(Optimization.project_id == project_id)).first():
        d.add("Refine")
    return d


def mark(s: Session, project_id: str, event: str, reason: str) -> None:
    plan = _plan(s, project_id)
    have, stale = done(s, project_id), dict(plan.stale or {})
    for stage in DOWNSTREAM[event]:
        if stage in have and stage not in stale:
            stale[stage] = {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    plan.stale = stale
    s.add(plan)
    s.commit()


def clear(s: Session, project_id: str, *stages: str) -> None:
    plan = _plan(s, project_id)
    stale = {k: v for k, v in (plan.stale or {}).items() if k not in stages}
    if stale != (plan.stale or {}):
        plan.stale = stale
        s.add(plan)
    s.commit()


def get(s: Session, project_id: str) -> dict:
    return dict(_plan(s, project_id).stale or {})
