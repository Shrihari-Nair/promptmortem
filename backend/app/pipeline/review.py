"""Review queue: cluster representatives, critic-flagged (uncertain), and the highest-diff-relevance case per cluster."""
from sqlmodel import Session, select

from app.core.models import Scenario


def review_queue(s: Session, project_id: str) -> list[Scenario]:
    pending = s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status == "proposed")).all()
    top: dict[str, Scenario] = {}
    for x in pending:
        if x.cluster_id and (x.cluster_id not in top or x.diff_relevance > top[x.cluster_id].diff_relevance):
            top[x.cluster_id] = x
    picked = {x.id: x for x in pending if x.representative or x.flagged}
    picked.update({x.id: x for x in top.values()})
    return sorted(picked.values(), key=lambda x: (not x.flagged, -x.diff_relevance, x.id))
