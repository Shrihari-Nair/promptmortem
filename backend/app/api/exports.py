from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.adapters import promptfoo_export as pf
from app.core.db import get_session
from app.core.models import Project

router = APIRouter()


@router.get("/projects/{pid}/export/promptfoo")
def export_promptfoo(pid: str, format: str = "yaml", scope: str = "plan", s: Session = Depends(get_session)):
    """Promptfoo config with both prompts, the scenarios as tests and the programmatic metrics as assertions.
    scope=plan: the confirmed budget plan's scenarios (all non-rejected ones if none is confirmed); scope=all: every non-rejected scenario."""
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    if format not in ("yaml", "json") or scope not in ("plan", "all"):
        raise HTTPException(422, "format must be yaml or json; scope must be plan or all")
    try:
        cfg, warnings = pf.build_config(s, pid, scope)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if format == "json":
        return {"config": cfg, "warnings": warnings}
    return PlainTextResponse(pf.to_yaml(cfg, warnings), media_type="application/x-yaml",
                             headers={"Content-Disposition": 'attachment; filename="promptfooconfig.yaml"'})
