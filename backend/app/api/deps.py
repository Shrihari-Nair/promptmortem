from fastapi import Depends, Request
from sqlmodel import Session

from app.core.db import get_session
from app.core.llm import LLMClient, current_project


async def get_client(request: Request, s: Session = Depends(get_session)) -> LLMClient:
    # async so the contextvar reaches the endpoint; tags calls made in requests under /projects/{pid}/...
    # ponytail: endpoints keyed by metric/cluster/optimization/report id (not pid) stay untagged; resolve their project if that matters
    current_project.set(request.path_params.get("pid"))
    return LLMClient(s.get_bind())
