from fastapi import Depends
from sqlmodel import Session

from app.core.db import get_session
from app.core.llm import LLMClient


def get_client(s: Session = Depends(get_session)) -> LLMClient:
    return LLMClient(s.get_bind())
