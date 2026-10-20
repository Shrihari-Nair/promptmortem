from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.agents.schemas import CATEGORIES
from app.api.deps import get_client
from app.core.db import get_session
from app.core.jobs import start_job
from app.core.llm import LLMClient
from app.core.models import Feedback, Project, Scenario, ScenarioCluster
from app.pipeline import planner, stale
from app.pipeline.clustering import coverage_map, update_coverage
from app.pipeline.review import review_queue

router = APIRouter()


def _need_project(s: Session, pid: str) -> None:
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")


class GenerateIn(BaseModel):
    category: str | None = None  # None = full initial plan
    n: int = 10


@router.post("/projects/{pid}/scenarios/generate")
async def generate(pid: str, body: GenerateIn | None = None, s: Session = Depends(get_session),
                   client: LLMClient = Depends(get_client)):
    _need_project(s, pid)
    engine, body = s.get_bind(), body or GenerateIn()
    if body.category and body.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {CATEGORIES}")

    async def job(progress):
        res = await (planner.generate_more(engine, client, pid, body.category, body.n) if body.category else planner.plan_scenarios(engine, client, pid, progress))
        with Session(engine) as s2:
            stale.mark(s2, pid, "scenarios", "scenarios were regenerated or extended")
            stale.clear(s2, pid, "Scenarios")
        return res

    return {"job_id": start_job(engine, pid, "scenarios", job)}


@router.get("/projects/{pid}/scenarios", response_model=list[Scenario])
def list_scenarios(pid: str, cluster: str | None = None, status: str | None = None, category: str | None = None,
                   review_queue_only: bool = Query(False, alias="review_queue"), s: Session = Depends(get_session)):
    if review_queue_only:
        return review_queue(s, pid)
    q = select(Scenario).where(Scenario.project_id == pid)
    for col, v in ((Scenario.cluster_id, cluster), (Scenario.status, status), (Scenario.category, category)):
        if v:
            q = q.where(col == v)
    return s.exec(q).all()


class ScenarioIn(BaseModel):
    category: str = "happy_path"
    input: dict
    expected_behavior: str = ""
    severity: int = 3


@router.post("/projects/{pid}/scenarios", response_model=Scenario)
def add_scenario(pid: str, body: ScenarioIn, s: Session = Depends(get_session)):
    _need_project(s, pid)
    if body.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {CATEGORIES}")
    sc = Scenario(project_id=pid, origin="user", status="approved", reviewed=True, **body.model_dump())
    s.add(sc)
    s.commit()
    stale.mark(s, pid, "scenarios", "you added a scenario")
    s.refresh(sc)
    return sc


class ScenarioPatch(BaseModel):
    action: str | None = None  # approve | reject | edit | comment
    comment: str = ""
    pinned: bool | None = None
    input: dict | None = None
    expected_behavior: str | None = None
    severity: int | None = None


@router.patch("/scenarios/{sid}", response_model=Scenario)
def patch_scenario(sid: str, body: ScenarioPatch, s: Session = Depends(get_session)):
    sc = s.get(Scenario, sid)
    if not sc:
        raise HTTPException(404, "scenario not found")
    if body.action not in (None, "approve", "reject", "edit", "comment"):
        raise HTTPException(422, "action must be approve, reject, edit or comment")
    edited = any(v is not None for v in (body.input, body.expected_behavior, body.severity))
    for f in ("input", "expected_behavior", "severity", "pinned"):
        if getattr(body, f) is not None:
            setattr(sc, f, getattr(body, f))
    action = body.action or ("edit" if edited else None)
    if action == "approve":
        sc.status, sc.reviewed = "approved", True
    elif action == "reject":
        sc.status = "rejected"
    elif action == "edit":
        sc.status, sc.reviewed = "edited", True
    if action:
        s.add(Feedback(project_id=sc.project_id, target_type="scenario", target_id=sid, action=action, comment=body.comment))
    s.add(sc)
    s.commit()
    update_coverage(s, sc.project_id)
    if edited or action == "reject" or body.pinned is not None:  # approving changes nothing the metrics or the budget depend on
        stale.mark(s, sc.project_id, "scenarios", "a scenario was edited, rejected or (un)pinned")
    s.refresh(sc)
    return sc


class RejectClusterIn(BaseModel):
    comment: str = ""


@router.post("/clusters/{cid}/reject")
async def reject_cluster(cid: str, body: RejectClusterIn, s: Session = Depends(get_session),
                         client: LLMClient = Depends(get_client)):
    cl = s.get(ScenarioCluster, cid)
    if not cl:
        raise HTTPException(404, "cluster not found")
    engine = s.get_bind()
    async def job(progress):
        res = await planner.reject_cluster(engine, client, cid, body.comment)
        with Session(engine) as s2:
            stale.mark(s2, cl.project_id, "scenarios", "a cluster was rejected and regenerated")
        return res

    return {"job_id": start_job(engine, cl.project_id, "reject_cluster", job)}


@router.get("/projects/{pid}/coverage")
def coverage(pid: str, s: Session = Depends(get_session)):
    scns = s.exec(select(Scenario).where(Scenario.project_id == pid)).all()
    counts = {"approved": sum(x.status in ("approved", "edited") for x in scns),
              "unreviewed": sum(x.status == "proposed" for x in scns),
              "rejected": sum(x.status == "rejected" for x in scns)}
    return {**coverage_map(s, pid), "counts": counts}


class FeedbackIn(BaseModel):
    project_id: str
    target_type: str
    target_id: str
    action: str = "comment"
    comment: str = ""


@router.post("/feedback", response_model=Feedback)
def add_feedback(body: FeedbackIn, s: Session = Depends(get_session)):
    fb = Feedback(**body.model_dump())
    s.add(fb)
    s.commit()
    s.refresh(fb)
    return fb
