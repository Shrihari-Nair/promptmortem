"""Tiers over the priority ranking, with floor rules and an explicit coverage-lost diff."""
from collections import Counter

from sqlmodel import Session

from app.config import settings
from app.core.models import Scenario, ScenarioCluster
from app.pipeline.prioritizer import is_protected


def select_tier(ranked: list[Scenario], fraction: float) -> list[Scenario]:
    """Protected scenarios are always kept; the rest fill up to the tier's share by priority."""
    k = round(len(ranked) * fraction)
    prot = [x for x in ranked if is_protected(x)]
    rest = [x for x in ranked if not is_protected(x)]
    keep = {x.id for x in prot} | {x.id for x in rest[: max(0, k - len(prot))]}
    return [x for x in ranked if x.id in keep]  # keeps priority order


def _pct(lost: int, total: int) -> str:
    return f"-{round(100 * lost / total)}%"


def coverage_lost(s: Session, ranked: list[Scenario], selected: list[Scenario]) -> dict:
    """Category and cluster diff versus the full map. Only entries that lost something are listed."""
    keep = {x.id for x in selected}
    tot_cat, kept_cat = Counter(x.category for x in ranked), Counter(x.category for x in selected)
    tot_cl, kept_cl = Counter(x.cluster_id for x in ranked if x.cluster_id), Counter(x.cluster_id for x in selected if x.cluster_id)
    names = {c.id: c.name for c in (s.get(ScenarioCluster, cid) for cid in tot_cl) if c}
    return {
        "categories": {c: _pct(n - kept_cat[c], n) for c, n in sorted(tot_cat.items()) if kept_cat[c] < n},
        "clusters": {names.get(c, c): _pct(n - kept_cl[c], n) for c, n in sorted(tot_cl.items()) if kept_cl[c] < n},
        "clusters_dropped": sorted(names.get(c, c) for c in tot_cl if kept_cl[c] == 0),
    }


def tier_fractions() -> dict[str, float]:
    return settings.tiers
