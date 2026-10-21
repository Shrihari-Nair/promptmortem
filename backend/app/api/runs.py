import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_client
from app.core import events
from app.core.db import get_session
from app.core.jobs import start_job
from app.core.llm import LLMClient
from app.core.models import Feedback, Plan, Report, Run
from app.core.trace import run_trace
from app.pipeline import report as rpt
from app.pipeline import runner
from app.pipeline.planner import PlanError

router = APIRouter()


def _run(s: Session, rid: str) -> Run:
    r = s.get(Run, rid)
    if not r:
        raise HTTPException(404, "run not found")
    return r


def _start(s: Session, client: LLMClient, run: Run) -> dict:
    engine, rid = s.get_bind(), run.id
    plan_pid = s.get(Plan, run.plan_id).project_id
    return {"run_id": rid, "job_id": start_job(engine, plan_pid, "run", lambda progress: runner.execute_run(engine, client, rid))}


@router.get("/runs/{rid}/trace")
def trace(rid: str, s: Session = Depends(get_session)):
    _run(s, rid)
    return run_trace(s.get_bind(), rid)


class RunIn(BaseModel):
    cap: float | None = None  # default: the cap confirmed in the budget step


@router.post("/projects/{pid}/runs")
async def create_run(pid: str, body: RunIn | None = None, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    try:
        rid = runner.create_run(s.get_bind(), pid, (body or RunIn()).cap)
    except PlanError as e:
        raise HTTPException(409, str(e))
    s.expire_all()
    return _start(s, client, _run(s, rid))


@router.get("/runs/{rid}")
def get_run(rid: str, s: Session = Depends(get_session)):
    return runner.run_state(s, _run(s, rid))


@router.get("/projects/{pid}/runs")
def list_runs(pid: str, s: Session = Depends(get_session)):
    plan = s.exec(select(Plan).where(Plan.project_id == pid)).first()
    rs = s.exec(select(Run).where(Run.plan_id == plan.id)).all() if plan else []
    return [runner.run_state(s, r) for r in rs]


@router.post("/runs/{rid}/resume")
async def resume(rid: str, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    run = _run(s, rid)
    if runner.is_active(rid) or run.status != "paused":
        raise HTTPException(409, f"run is {run.status}; only a paused run can be resumed")
    return _start(s, client, run)


class ExtendIn(BaseModel):
    amount: float


@router.post("/runs/{rid}/extend")
async def extend(rid: str, body: ExtendIn, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    """Raise the hard cap by `amount` and continue."""
    run = _run(s, rid)
    if body.amount <= 0:
        raise HTTPException(422, "amount must be positive")
    if runner.is_active(rid) or run.status != "paused":
        raise HTTPException(409, f"run is {run.status}; only a paused run can be extended")
    run.budget_cap += body.amount
    s.add(run)
    s.commit()
    return _start(s, client, run)


@router.post("/runs/{rid}/stop")
async def stop(rid: str, s: Session = Depends(get_session)):
    """Stop and report what was tested so far (the report says it is partial)."""
    run = _run(s, rid)
    if run.status in ("done", "stopped", "failed"):
        raise HTTPException(409, f"run is already {run.status}")
    if not runner.request_stop(rid):
        runner.finalize_stopped(s.get_bind(), rid)
    return {"status": "stopping"}


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


@router.get("/runs/{rid}/events")
async def run_events(rid: str, s: Session = Depends(get_session)):
    state = runner.run_state(s, _run(s, rid))  # snapshot now; the stream itself never touches the DB
    return StreamingResponse(_event_stream(rid, state), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _event_stream(rid: str, state: dict):
    q = events.subscribe(rid)
    try:
        yield _sse({"type": "snapshot", **state})
        if state["status"] in (*events.FINAL, "failed") and not runner.is_active(rid):
            return  # nothing is running: the snapshot is the answer (reopen the stream after resume)
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), 15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield _sse(ev)
            if ev["type"] in events.FINAL:
                return
    finally:
        events.unsubscribe(rid, q)


@router.get("/runs/{rid}/report")
def get_report(rid: str, s: Session = Depends(get_session)):
    _run(s, rid)
    return rpt.build_report(s.get_bind(), rid)  # always fresh: includes partial results of paused/stopped runs


class OverrideIn(BaseModel):
    scenario_id: str
    verdict: str  # fine | broken | improved
    comment: str = ""


def _report(s: Session, rep_id: str) -> Report:
    r = s.get(Report, rep_id)
    if not r:
        raise HTTPException(404, "report not found")
    return r


@router.post("/reports/{rep_id}/verdict-override")
def verdict_override(rep_id: str, body: OverrideIn, s: Session = Depends(get_session)):
    rep = _report(s, rep_id)
    if body.verdict not in rpt.OVERRIDE:
        raise HTTPException(422, f"verdict must be one of {list(rpt.OVERRIDE)}")
    run = _run(s, rep.run_id)
    pid = s.get(Plan, run.plan_id).project_id
    s.add(Feedback(project_id=pid, target_type="verdict", target_id=f"{rep.run_id}:{body.scenario_id}", action=body.verdict, comment=body.comment))
    s.commit()
    return rpt.build_report(s.get_bind(), rep.run_id)


@router.post("/reports/{rep_id}/rescore")
async def rescore(rep_id: str, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    rep = _report(s, rep_id)
    engine, rid = s.get_bind(), rep.run_id
    if runner.is_active(rid):
        raise HTTPException(409, "run is still executing")

    async def job(progress):
        res = await rpt.rescore(engine, client, rid)
        rpt.build_report(engine, rid)
        return res

    return {"job_id": start_job(engine, s.get(Plan, _run(s, rid).plan_id).project_id, "rescore", job)}


@router.get("/reports/{rep_id}/export")
def export(rep_id: str, format: str = "json", s: Session = Depends(get_session)):
    rep = rpt.build_report(s.get_bind(), _report(s, rep_id).run_id)
    if format == "md":
        return PlainTextResponse(rpt.to_markdown(rep["summary"]), media_type="text/markdown")
    if format == "json":
        return rep["summary"]
    raise HTTPException(422, "format must be json or md")
