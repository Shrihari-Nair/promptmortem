"""Turn stored human feedback into the 'constraints learned from the reviewer' block for generators."""
from sqlmodel import Session, select

from app.core.embeddings import scenario_text
from app.core.models import Feedback, Scenario, ScenarioCluster


VERDICT_ACTIONS = ("fine", "broken", "improved")


def feedback_text(s: Session, project_id: str, category: str | None = None, limit: int = 30) -> str:
    fbs = s.exec(select(Feedback).where(Feedback.project_id == project_id,
                                        Feedback.action.in_(["reject", "edit", "comment", *VERDICT_ACTIONS]))).all()
    rows = []
    for f in fbs:
        if f.target_type == "cluster":
            cl = s.get(ScenarioCluster, f.target_id)
            cat = cl.category if cl else None
            eg = [scenario_text(x.input)[:120] for x in s.exec(
                select(Scenario).where(Scenario.cluster_id == f.target_id, Scenario.status == "rejected")).all()[:3]]
            line = f"Reviewer rejected a whole {cat} cluster: {f.comment or '(no comment)'}" + (f" Rejected examples: {eg}" if eg else "")
        elif f.target_type == "scenario":
            sc = s.get(Scenario, f.target_id)
            cat = sc.category if sc else None
            snippet = scenario_text(sc.input)[:120] if sc else "?"
            line = f"Reviewer {f.action}ed a scenario ({snippet!r}): {f.comment or '(no comment)'}"
        elif f.target_type == "verdict":
            sc = s.get(Scenario, f.target_id.partition(":")[2])
            cat = sc.category if sc else None
            line = f"Reviewer judged a test result: for the case {scenario_text(sc.input)[:120]!r} the report was wrong, it is actually {f.action}: {f.comment or '(no comment)'}" if sc else f"Reviewer note: {f.comment}"
        else:
            cat, line = None, f"Reviewer note: {f.comment}"
        rows.append((category is not None and cat != category, -f.created_at.timestamp(), line))
    rows.sort(key=lambda r: r[:2])  # cluster-relevant first, then most recent
    return "\n".join(f"- {r[2]}" for r in rows[:limit])


def metric_feedback_text(s: Session, project_id: str, limit: int = 20) -> str:
    from app.core.models import Metric

    out = []
    for f in sorted(s.exec(select(Feedback).where(Feedback.project_id == project_id, Feedback.target_type.in_(["metric", "verdict"]),
                                                  Feedback.action.in_(["reject", "edit", "comment", *VERDICT_ACTIONS]))).all(),
                    key=lambda f: -f.created_at.timestamp()):
        if f.target_type == "verdict":  # a human overruled the report: the metrics may be too strict or too lenient
            sc = s.get(Scenario, f.target_id.partition(":")[2])
            if sc:
                out.append(f"- Reviewer overruled a result: the case {scenario_text(sc.input)[:120]!r} is actually {f.action}: {f.comment or '(no comment)'} (the metrics may be too strict or too lenient)")
            continue
        m = s.get(Metric, f.target_id)
        out.append(f"- Reviewer {f.action}ed metric {m.name!r}: {f.comment or '(no comment)'}" if m else f"- Reviewer note: {f.comment}")
    return "\n".join(out[:limit])
