"""Persisted asyncio jobs. Long operations return a job_id; the UI polls GET /jobs/{id}.
ponytail: not resumed after a server restart (M5 adds that for runs)."""
import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.llm import BudgetExceeded
from app.core.secrets import redact
from app.core.models import Job

_tasks: set[asyncio.Task] = set()


def start_job(engine: Engine, project_id: str | None, kind: str,
              fn: Callable[[Callable[[str], None]], Awaitable[dict]]) -> str:
    with Session(engine) as s:
        job = Job(project_id=project_id, kind=kind)
        s.add(job)
        s.commit()
        jid = job.id

    def update(**kw):
        with Session(engine) as s:
            j = s.get(Job, jid)
            for k, v in kw.items():
                setattr(j, k, v)
            s.add(j)
            s.commit()

    async def run():
        update(status="running")
        try:
            update(status="done", result=await fn(lambda msg: update(progress=msg)))
        except BudgetExceeded as e:
            update(status="paused", error=redact(str(e)))
        except Exception as e:  # surfaced to the UI via the job row
            update(status="failed", error=redact(f"{type(e).__name__}: {e}"))

    t = asyncio.create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return jid
