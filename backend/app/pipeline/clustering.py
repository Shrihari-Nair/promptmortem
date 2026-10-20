"""Deterministic clustering (average-linkage on cosine distance, k from cluster_target_size) + coverage map."""
from collections import Counter

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from sqlmodel import Session, select

from app.config import settings
from app.core.embeddings import embed, scenario_text
from app.core.models import Plan, Scenario, ScenarioCluster


def labels_for(emb: np.ndarray, target_size: int) -> np.ndarray:
    n = len(emb)
    k = max(1, round(n / target_size))
    if n <= 1 or k == 1:
        return np.zeros(n, dtype=int)
    return fcluster(linkage(emb, "average", metric="cosine"), k, "maxclust") - 1


def cluster_scenarios(s: Session, project_id: str, ids: list[str] | None = None) -> list[ScenarioCluster]:
    """Cluster the given scenarios (default: all non-rejected) per category; mark representatives."""
    q = select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")
    scns = [x for x in s.exec(q).all() if ids is None or x.id in ids]
    created = []
    for cat in sorted({x.category for x in scns}):
        group = [x for x in scns if x.category == cat]
        emb = embed([scenario_text(x.input) for x in group])
        labels = labels_for(emb, settings.cluster_target_size)
        for k in sorted(set(labels)):
            idx = [i for i, lab in enumerate(labels) if lab == k]
            members = [group[i] for i in idx]
            centroid = emb[idx].mean(axis=0)
            tags = Counter(t for m in members for t in m.tags if t != cat)
            cl = ScenarioCluster(project_id=project_id, category=cat, centroid_embedding=centroid.astype(np.float32).tobytes(),
                                 name=f"{tags.most_common(1)[0][0] if tags else cat} #{len(created) + 1}",
                                 description=f"{len(members)} {cat} scenarios")
            s.add(cl)
            s.flush()
            near = np.argsort(-(emb[idx] @ centroid))[: settings.review_queue_max_per_cluster]
            for j, m in enumerate(members):
                m.cluster_id = cl.id
                m.representative = j in set(near.tolist())
                s.add(m)
            created.append(cl)
    s.commit()
    return created


def coverage_map(s: Session, project_id: str) -> dict:
    scns = s.exec(select(Scenario).where(Scenario.project_id == project_id, Scenario.status != "rejected")).all()
    clusters = {c.id: c for c in s.exec(select(ScenarioCluster).where(ScenarioCluster.project_id == project_id)).all()}
    cats: dict[str, dict[str, int]] = {}
    for x in scns:
        if x.cluster_id:
            cats.setdefault(x.category, {}).setdefault(x.cluster_id, 0)
            cats[x.category][x.cluster_id] += 1
    used = {cid for row in cats.values() for cid in row}
    return {"categories": cats,
            "clusters": {cid: {"name": c.name, "category": c.category} for cid, c in clusters.items() if cid in used}}


def update_coverage(s: Session, project_id: str) -> dict:
    cov = coverage_map(s, project_id)
    plan = s.exec(select(Plan).where(Plan.project_id == project_id)).first() or Plan(project_id=project_id)
    plan.coverage_map = cov
    s.add(plan)
    s.commit()
    return cov
