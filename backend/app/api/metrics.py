from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from app.agents.schemas import validate_definition
from app.api.deps import get_client
from app.core.db import get_session
from app.core.jobs import start_job
from app.core.llm import LLMClient
from app.core.models import Calibration, Feedback, Metric, Project
from app.pipeline import metric_plan, stale
from app.pipeline.planner import PlanError

router = APIRouter()


def _check(kind: str, definition: dict) -> dict:
    try:
        return validate_definition(kind, definition)
    except (ValueError, ValidationError) as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False) if isinstance(e, ValidationError) else str(e))


@router.post("/projects/{pid}/metrics/generate")
async def generate(pid: str, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    engine = s.get_bind()
    async def job(progress):
        res = await metric_plan.design_metrics(engine, client, pid)
        with Session(engine) as s2:
            stale.mark(s2, pid, "metrics", "metrics were regenerated")
            stale.clear(s2, pid, "Metrics")
        return res

    return {"job_id": start_job(engine, pid, "metrics", job)}


@router.get("/projects/{pid}/metrics", response_model=list[Metric])
def list_metrics(pid: str, status: str | None = None, s: Session = Depends(get_session)):
    q = select(Metric).where(Metric.project_id == pid)
    return s.exec(q.where(Metric.status == status) if status else q).all()


class MetricIn(BaseModel):
    name: str
    kind: str
    definition: dict
    applies_to: dict = {"categories": ["*"]}
    scale: str = "binary"
    judge_model: str | None = None
    weight: float = 1.0


@router.post("/projects/{pid}/metrics", response_model=Metric)
def add_metric(pid: str, body: MetricIn, s: Session = Depends(get_session)):
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    m = Metric(project_id=pid, status="approved", **{**body.model_dump(), "definition": _check(body.kind, body.definition)})
    s.add(m)
    s.commit()
    stale.mark(s, pid, "metrics", "you added a metric")
    s.refresh(m)
    return m


class MetricPatch(BaseModel):
    status: str | None = None  # approved | rejected | proposed | edited
    comment: str = ""
    name: str | None = None
    definition: dict | None = None
    applies_to: dict | None = None
    weight: float | None = None
    judge_model: str | None = None
    notes: str | None = None


@router.patch("/metrics/{mid}", response_model=Metric)
def patch_metric(mid: str, body: MetricPatch, s: Session = Depends(get_session)):
    m = s.get(Metric, mid)
    if not m:
        raise HTTPException(404, "metric not found")
    if body.status not in (None, "approved", "rejected", "proposed", "edited"):
        raise HTTPException(422, "bad status")
    edited = False
    if body.definition is not None:
        m.definition, edited = _check(m.kind, body.definition), True
    for f in ("name", "applies_to", "weight", "judge_model", "notes"):
        if getattr(body, f) is not None:
            setattr(m, f, getattr(body, f))
            edited = True
    if body.status:
        m.status = body.status
    elif edited and m.status == "proposed":
        m.status = "edited"
    action = {"approved": "approve", "rejected": "reject"}.get(body.status or "", "edit" if edited else None)
    if action:
        s.add(Feedback(project_id=m.project_id, target_type="metric", target_id=mid, action=action, comment=body.comment))
    s.add(m)
    s.commit()
    if edited or body.status == "rejected":
        stale.mark(s, m.project_id, "metrics", "a metric was edited or rejected")
    s.refresh(m)
    return m


class PreviewIn(BaseModel):
    samples: list[dict] | None = None
    n: int = 5


@router.post("/metrics/{mid}/preview")
async def preview(mid: str, body: PreviewIn | None = None, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    body = body or PreviewIn()
    try:
        return await metric_plan.preview(s.get_bind(), client, mid, body.samples, min(max(body.n, 1), 5))
    except PlanError as e:
        raise HTTPException(422, str(e))


@router.post("/metrics/{mid}/calibrate")
async def calibrate(mid: str, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    m = s.get(Metric, mid)
    if not m:
        raise HTTPException(404, "metric not found")
    engine = s.get_bind()
    return {"job_id": start_job(engine, m.project_id, "calibrate", lambda progress: metric_plan.calibrate(engine, client, mid))}


@router.get("/projects/{pid}/calibration", response_model=list[Calibration])
def calibration(pid: str, s: Session = Depends(get_session)):
    return s.exec(select(Calibration).where(Calibration.project_id == pid)).all()
