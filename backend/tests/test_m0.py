import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import models
from app.core.db import get_session
from app.core.llm import Budget, BudgetExceeded, LLMClient
from app.main import app


async def test_llm_call_logged_and_cached(engine):
    c = LLMClient(engine)
    msgs = [{"role": "user", "content": "hello there"}]
    a = await c.complete("fake/m", msgs)
    b = await c.complete("fake/m", msgs)
    assert not a.cached and b.cached and a.text == b.text and a.cost > 0 and b.cost == 0
    with Session(engine) as s:
        assert len(s.exec(select(models.LLMCall)).all()) == 1  # one paid call logged
    # different seed or repeat_idx => different cache entry
    assert not (await c.complete("fake/m", msgs, repeat_idx=1)).cached


async def test_hard_cap_raises_before_paid_call_but_allows_cache(engine):
    c = LLMClient(engine)
    budget = Budget(cap=0.000001)
    m1 = [{"role": "user", "content": "one two three"}]
    await c.complete("fake/m", m1, budget=budget)  # crosses the cap
    assert budget.spent >= budget.cap
    with pytest.raises(BudgetExceeded):
        await c.complete("fake/m", [{"role": "user", "content": "new"}], budget=budget)
    assert (await c.complete("fake/m", m1, budget=budget)).cached  # resume-safe
    budget.extend(1.0)
    await c.complete("fake/m", [{"role": "user", "content": "new"}], budget=budget)


def test_project_and_prompts_roundtrip(engine):
    def sess():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    cl = TestClient(app)
    p = cl.post("/projects", json={"name": "bot", "app_description": "billing"}).json()
    assert p["target_model"] == "fake/app"  # the test default (see conftest); the product default is gemini/gemini-2.5-flash
    for label in ("old", "new"):
        assert cl.post(f"/projects/{p['id']}/prompts", json={"label": label, "text": f"{label} {{{{user_message}}}}"}).status_code == 200
    got = cl.get(f"/projects/{p['id']}/prompts").json()
    assert {g["label"] for g in got} == {"old", "new"}
    assert cl.get("/projects/nope").status_code == 404
    app.dependency_overrides.clear()


def test_init_db_adds_columns_missing_from_an_older_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.core.db import init_db

    e = create_engine(f"sqlite:///{tmp_path}/old.db")
    with e.begin() as c:  # a `plan` table as it was before M3 (no probe/chosen columns), with a row in it
        c.execute(text("CREATE TABLE plan (id VARCHAR PRIMARY KEY, project_id VARCHAR, stage VARCHAR, coverage_map JSON, locked_at DATETIME)"))
        c.execute(text("INSERT INTO plan (id, project_id, stage) VALUES ('plan_1', 'prj_1', 'scenarios')"))
    init_db(e)
    cols = {c["name"] for c in inspect(e).get_columns("plan")}
    assert {"probe", "chosen"} <= cols
    with e.connect() as c:
        assert c.execute(text("SELECT id, probe FROM plan")).one() == ("plan_1", None)  # data kept
