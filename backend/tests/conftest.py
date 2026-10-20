import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["PV_EMBEDDING_MODEL"] = "hash"  # no model download in tests
# Tests must NEVER spend: even if a real key is discoverable (litellm loads any .env up the directory tree), the default models are fakes.
os.environ["PV_TARGET_MODEL"] = "fake/app"
os.environ["PV_JUDGE_MODEL"] = "fake/judge"
os.environ["PV_DB_URL"] = f"sqlite:///{_tmp}/app.db"

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.core import models  # noqa: E402,F401
from app.core.db import get_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def client(engine):
    def sess():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    with TestClient(app) as c:  # `with` keeps the event loop alive for background jobs
        yield c
    app.dependency_overrides.clear()


def wait_job(client, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] not in ("pending", "running"):
            return j
        time.sleep(0.05)
    raise TimeoutError(job_id)


def make_project(client, **kw):
    """Project on the fake model with both prompts, analysed (stage 1 done)."""
    body = {"name": "bot", "app_description": "Billing support bot", "target_model": "fake/t",
            "judge_model_default": "fake/j", **kw}
    p = client.post("/projects", json=body).json()
    for label, text in (("old", "You are a helpful billing assistant.\n{{user_message}}"),
                        ("new", "You are a concise billing assistant. Answer in under 120 words.\n{{user_message}}")):
        client.post(f"/projects/{p['id']}/prompts", json={"label": label, "text": text})
    j = wait_job(client, client.post(f"/projects/{p['id']}/analyze").json()["job_id"])
    assert j["status"] == "done", j
    return p
