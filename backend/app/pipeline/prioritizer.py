"""priority = w1*diff_relevance + w2*severity + w3*uniqueness + w4*pinned_or_flagged + w5*disputed_potential.
Uniqueness = 1 - max cosine similarity to HIGHER-priority items, so ranking is greedy (max marginal relevance)."""
import numpy as np
from sqlmodel import Session, select

from app.config import settings
from app.core.embeddings import embed, scenario_text
from app.core.models import Scenario

DISPUTE_PRONE = {"ambiguous", "adversarial", "safety_critical"}


def is_protected(x: Scenario) -> bool:
    """Floor rule: never auto-trimmed."""
    return x.pinned or x.severity >= settings.severity_protected_threshold


def candidates(s: Session, project_id: str) -> list[Scenario]:
    return list(s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all())


def rank(s: Session, project_id: str) -> list[Scenario]:
    """Rank and persist priority_score. Highest priority first."""
    xs = sorted(candidates(s, project_id), key=lambda x: x.id)  # deterministic tie-break
    if not xs:
        return []
    w = settings.priority_weights
    # ponytail: disputed_potential is a category heuristic until runs measure real judge disagreement (M5+)
    base = np.array([w["diff_relevance"] * x.diff_relevance + w["severity"] * x.severity / 5
                     + w["pinned_or_flagged"] * float(x.pinned or x.flagged)
                     + w["disputed_potential"] * float(x.category in DISPUTE_PRONE) for x in xs])
    emb = embed([scenario_text(x.input) for x in xs])
    max_sim = np.zeros(len(xs))
    left = np.ones(len(xs), dtype=bool)
    order = []
    for _ in range(len(xs)):
        score = np.where(left, base + w["uniqueness"] * np.clip(1 - max_sim, 0, 1), -np.inf)
        i = int(np.argmax(score))  # argmax takes the first max => id order on ties
        xs[i].priority_score = float(score[i])
        order.append(xs[i])
        left[i] = False
        max_sim = np.maximum(max_sim, emb @ emb[i])
    s.add_all(order)
    s.commit()
    return order
