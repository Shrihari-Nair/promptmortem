from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.core.db import get_session
from app.api.deps import get_client
from app.core.llm import LLMClient
from app.core.models import Project, PromptVersion
from app.pipeline import stale

router = APIRouter()


class ProjectIn(BaseModel):
    name: str
    app_description: str = ""
    constraints: str = ""
    known_failure_notes: str = ""
    promptfoo_enabled: bool = True
    target_model: str | None = None
    judge_model_default: str | None = None


class PromptIn(BaseModel):
    label: str  # old | new | candidate_n
    text: str
    variables_schema: dict | None = None


@router.post("/projects", response_model=Project)
def create_project(body: ProjectIn, s: Session = Depends(get_session)):
    p = Project(
        **body.model_dump(exclude={"target_model", "judge_model_default"}),
        target_model=body.target_model or settings.target_model,
        judge_model_default=body.judge_model_default or settings.judge_model,
    )
    s.add(p)
    s.commit()
    s.refresh(p)
    return p


@router.get("/projects/{pid}", response_model=Project)
def get_project(pid: str, s: Session = Depends(get_session)):
    p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


@router.post("/projects/{pid}/prompts", response_model=PromptVersion)
def add_prompt(pid: str, body: PromptIn, s: Session = Depends(get_session)):
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    pv = PromptVersion(project_id=pid, **body.model_dump())
    s.add(pv)
    s.commit()
    if body.label in ("old", "new"):
        stale.mark(s, pid, "prompts", f"the {body.label} prompt was changed")
    s.refresh(pv)  # last: stale.mark commits, which would expire the object we are about to return
    return pv


@router.get("/projects/{pid}/prompts", response_model=list[PromptVersion])
def list_prompts(pid: str, s: Session = Depends(get_session)):
    return s.exec(select(PromptVersion).where(PromptVersion.project_id == pid)).all()


class DraftIn(BaseModel):
    old_prompt: str
    change_request: str
    app_description: str = ""
    constraints: str = ""
    model: str | None = None  # agent model; default from settings


@router.post("/prompts/draft")
async def draft_prompt(body: DraftIn, client: LLMClient = Depends(get_client)):
    """Stage 0: 'describe the change' -> a DRAFT of the new prompt (not saved; the human reviews and edits it)."""
    if not body.old_prompt.strip() or not body.change_request.strip():
        raise HTTPException(422, "old_prompt and change_request are required")
    from app.agents.prompt_drafter import draft

    out = await draft(client, body.model or settings.judge_model, old_prompt=body.old_prompt, change_request=body.change_request,
                      app_description=body.app_description, constraints=body.constraints)
    return {"prompt": out.prompt, "notes": out.notes}


@router.get("/projects/{pid}/stale")
def get_stale(pid: str, s: Session = Depends(get_session)):
    """Stages whose upstream data changed after they were done: {stage: {reason, at}}."""
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    return stale.get(s, pid)


@router.post("/projects/{pid}/stale/{stage}/ack")
def ack_stale(pid: str, stage: str, s: Session = Depends(get_session)):
    """'I looked at it, it is still right': clears the mark without redoing the stage."""
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    if stage not in stale.STAGES:
        raise HTTPException(422, f"stage must be one of {stale.STAGES}")
    stale.clear(s, pid, stage)
    return stale.get(s, pid)
