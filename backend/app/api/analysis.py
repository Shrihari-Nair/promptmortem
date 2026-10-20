import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from app.adapters.dataset_import import parse_records, to_items
from app.agents.context_analyzer import analyze_context
from app.agents.diff_analyzer import analyze_diff
from app.agents.schemas import ContextAnalysis, DiffAnalysis
from app.api.deps import get_client
from app.core.db import get_session
from app.core.jobs import start_job
from app.core.llm import LLMClient
from app.pipeline import stale
from app.core.models import DatasetItem, Job, Project, ProjectAnalysis, PromptVersion

router = APIRouter()


def _project(s: Session, pid: str) -> Project:
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


class ImportIn(BaseModel):
    format: str  # csv | jsonl
    content: str
    mapping: dict | None = None  # {"input": {var: column}, "expected": column, "output": column}
    input_var: str = "user_message"
    known_failure_notes: str | None = None


def _import(pid: str, body: ImportIn, source: str, s: Session) -> dict:
    _project(s, pid)
    try:
        items = to_items(parse_records(body.content, body.format), body.mapping, body.input_var)
    except ValueError as e:
        raise HTTPException(422, str(e))
    seen = {json.dumps(d.input, sort_keys=True) for d in s.exec(select(DatasetItem).where(DatasetItem.project_id == pid)).all()}
    imported = skipped = 0
    for it in items:
        key = json.dumps(it["input"], sort_keys=True)
        if key in seen:  # the same input twice (re-import, or repeated in the trace log) adds nothing
            skipped += 1
            continue
        seen.add(key)
        notes = f"observed output: {it['output']}" if it["output"] else ""
        s.add(DatasetItem(project_id=pid, input=it["input"], expected=it["expected"], source=source, label_notes=notes))
        imported += 1
    s.commit()
    return {"imported": imported, "skipped_duplicates": skipped}


@router.post("/projects/{pid}/dataset/import")
def import_dataset(pid: str, body: ImportIn, s: Session = Depends(get_session)):
    return _import(pid, body, "user", s)


@router.post("/projects/{pid}/traces/import")
def import_traces(pid: str, body: ImportIn, s: Session = Depends(get_session)):
    return _import(pid, body, "trace", s)


@router.get("/projects/{pid}/dataset")
def list_dataset(pid: str, s: Session = Depends(get_session)):
    return s.exec(select(DatasetItem).where(DatasetItem.project_id == pid)).all()


def _save(s: Session, pid: str, kind: str, data: dict, edited: bool) -> None:
    row = s.exec(select(ProjectAnalysis).where(ProjectAnalysis.project_id == pid, ProjectAnalysis.kind == kind)).first() \
        or ProjectAnalysis(project_id=pid, kind=kind)
    row.data, row.edited = data, edited
    s.add(row)
    s.commit()


@router.post("/projects/{pid}/analyze")
async def analyze(pid: str, known_failure_notes: str = "", s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    p = _project(s, pid)
    pv = {v.label: v.text for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == pid)).all()}
    if "old" not in pv or "new" not in pv:
        raise HTTPException(422, "save both an 'old' and a 'new' prompt first")
    sample = [d.input for d in s.exec(select(DatasetItem).where(DatasetItem.project_id == pid)).all()][:5]
    engine, model = s.get_bind(), p.judge_model_default
    args = dict(app_description=p.app_description, constraints=p.constraints, known_failure_notes=known_failure_notes or p.known_failure_notes,
                prompt_old=pv["old"], prompt_new=pv["new"], dataset_sample=sample)

    async def job(progress):
        progress("understanding the app")
        ctx = await analyze_context(client, model, **args)
        progress("diffing prompts")
        diff = await analyze_diff(client, model, context=ctx, prompt_old=pv["old"], prompt_new=pv["new"])
        with Session(engine) as s2:
            _save(s2, pid, "context", ctx.model_dump(), False)
            _save(s2, pid, "diff", diff.model_dump(), False)
            stale.mark(s2, pid, "analysis", "the analysis was re-run")
            stale.clear(s2, pid, "Understand")
        return {"changes": len(diff.changes)}

    return {"job_id": start_job(engine, pid, "analyze", job)}


@router.get("/projects/{pid}/analysis")
def get_analysis(pid: str, s: Session = Depends(get_session)):
    rows = s.exec(select(ProjectAnalysis).where(ProjectAnalysis.project_id == pid)).all()
    return {r.kind: {"data": r.data, "edited": r.edited} for r in rows}


@router.put("/projects/{pid}/analysis/{kind}")
def edit_analysis(pid: str, kind: str, data: dict, s: Session = Depends(get_session)):
    model = {"context": ContextAnalysis, "diff": DiffAnalysis}.get(kind)
    if not model:
        raise HTTPException(404, "kind must be context or diff")
    _project(s, pid)
    try:
        clean = model.model_validate(data).model_dump()
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    _save(s, pid, kind, clean, True)
    stale.mark(s, pid, "analysis", f"the {kind} analysis was edited")
    return {"ok": True}


@router.get("/jobs/{jid}", response_model=Job)
def get_job(jid: str, s: Session = Depends(get_session)):
    j = s.get(Job, jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j
