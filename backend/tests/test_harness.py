"""Test-harness tests: cassette replay, fault injection into the model provider, and the CLAUDE.md principles as asserts."""
import re
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.agents.base import AgentError, run_agent
from app.agents.base import render
from app.agents.schemas import JudgeOut
from app.core import cassette
from app.core.fake_llm import fake_provider
from app.core.llm import LLMClient
from app.core.models import LLMCall
from tests.conftest import make_project
from tests.golden import golden, run_to_end

APP = Path(__file__).resolve().parents[1] / "app"
JUDGE = {"rubric": "r", "pass_examples": [], "fail_examples": [], "scale": "binary", "input": {}, "output": "o"}


# ---- cassette ----
async def test_cassette_roundtrip_serves_everything_offline(engine, tmp_path):
    rec = LLMClient(engine)
    msgs = [{"role": "user", "content": "hello there"}]
    a = await rec.complete("fake/m", msgs)
    path = tmp_path / "c.json"
    assert cassette.dump(engine, path) == 1

    from sqlmodel import SQLModel, create_engine
    e2 = create_engine(f"sqlite:///{tmp_path}/replay.db")
    SQLModel.metadata.create_all(e2)
    assert cassette.load(e2, path) == 1 and cassette.load(e2, path) == 0  # idempotent
    off = LLMClient(e2, provider=cassette.offline, fake=cassette.offline)
    b = await off.complete("fake/m", msgs)
    assert b.cached and b.text == a.text
    with pytest.raises(cassette.CassetteMiss):
        await off.complete("fake/m", [{"role": "user", "content": "never recorded"}])


# ---- fault injection ----
def chaos(*faults):
    """Provider that misbehaves on its n-th call (None = behave), then behaves."""
    calls = iter(faults)

    async def provider(model, messages, params):
        text, tin, tout, cost = await fake_provider(model, messages, params)
        f = next(calls, None)
        if f == "error":
            raise TimeoutError("provider timeout")
        text = {"fence": f"Sure!\n```json\n{text}\n```", "garbage": "I cannot comply", "truncate": text[: len(text) // 2]}.get(f, text)
        return text, tin, tout, cost
    return provider


def judge(engine, *faults):
    return run_agent(LLMClient(engine, fake=chaos(*faults)), "fake/j", "judge", JUDGE, JudgeOut, temperature=0.0)


async def test_fenced_json_is_parsed_without_a_retry(engine):
    await judge(engine, "fence")
    with Session(engine) as s:
        assert len(s.exec(select(LLMCall)).all()) == 1


@pytest.mark.parametrize("fault", ["garbage", "truncate"])
async def test_bad_output_is_repaired_by_retry(engine, fault):
    assert (await judge(engine, fault)).verdict in ("pass", "fail")
    with Session(engine) as s:
        assert len(s.exec(select(LLMCall)).all()) == 2


async def test_persistent_garbage_raises_agent_error(engine):
    with pytest.raises(AgentError):
        await judge(engine, "garbage", "garbage", "garbage")


async def test_provider_error_propagates_and_leaves_nothing_cached(engine):
    with pytest.raises(TimeoutError):
        await judge(engine, "error")
    with Session(engine) as s:
        assert s.exec(select(LLMCall)).first() is None  # a retry pays and caches normally
    await judge(engine)


# ---- CLAUDE.md principles as asserts ----
def test_no_provider_call_outside_llmclient():  # principle: all LLM calls go through LLMClient
    bad = [str(p.relative_to(APP)) for p in APP.rglob("*.py") if p.name != "llm.py"
           and re.search(r"\b(acompletion|litellm\.completion|\.chat\.completions)\b", p.read_text())]
    assert not bad, bad


@pytest.mark.parametrize("agent", ["judge", "pairwise_judge"])
def test_judge_prompts_are_blind(agent):  # principle 6: judges never learn which prompt produced an output
    text = render(agent, {k: "" for k in [*JUDGE, "output_a", "output_b"]})
    assert not re.search(r"\b(old|new|before|after|baseline|candidate|version)\b", text, re.I), text


def test_report_scope_stamp_is_complete(client, engine):  # principle 5: every report says what was tested
    pid = golden(client, engine, "noop", n_each=5)
    rid, j = run_to_end(client, pid)
    assert j["result"]["status"] == "done", j
    scope = client.get(f"/runs/{rid}/report").json()["summary"]["scope"]
    need = {"judge_models", "tier", "tested", "untested_in_tier", "reviewed_share", "verified_share", "noise_band", "complete", "coverage_lost"}
    assert need <= set(scope), need - set(scope)


# ---- real Gemini output (recorded by scripts/pipeline_smoke.py + `python -m app.core.cassette dump`) ----
def test_real_gemini_judge_outputs_parse():
    from app.agents.base import extract_json
    from app.agents.schemas import PairOut
    path = Path(__file__).parent / "cassettes" / "billing_gemini.json"
    seen = {"verdict": 0, "winner": 0}
    for c in __import__("json").loads(path.read_text())["calls"]:
        for key, schema in (("verdict", JudgeOut), ("winner", PairOut)):
            if f'"{key}"' in c["response"]:
                schema.model_validate(extract_json(c["response"]))  # what run_agent would accept
                seen[key] += 1
    assert all(seen.values()), seen  # the cassette really contains both judge kinds


# ---- run tagging and trace ----
def test_run_trace_attributes_paid_calls_to_the_run_and_agent(client, engine):
    pid = golden(client, engine, "noop", n_each=5)
    rid, j = run_to_end(client, pid)
    assert j["result"]["status"] == "done", j
    t = client.get(f"/runs/{rid}/trace").json()
    assert t["calls"] > 0 and "target" in t["by_tag"] and "(untagged)" not in t["by_tag"], t["by_tag"].keys()
    assert t["cost"] == pytest.approx(sum(x["cost"] for x in t["by_tag"].values()))
    with Session(engine) as s:  # the run's own cost log never exceeds what the run says it spent
        assert t["cost"] <= s.get(__import__("app.core.models", fromlist=["Run"]).Run, rid).spent + 1e-9
    assert client.get("/runs/nope/trace").status_code == 404


def test_project_spend_separates_planning_from_runs(client, engine):
    p = make_project(client)  # stage-1 analysis runs agents: paid planning calls, no run yet
    s0 = client.get(f"/projects/{p['id']}/spend").json()
    assert s0["planning"] > 0 and s0["in_runs"] == 0 and s0["total"] == s0["planning"]
    pid = golden(client, engine, "noop", n_each=5)
    rid, _ = run_to_end(client, pid)
    s1 = client.get(f"/projects/{pid}/spend").json()
    cost = client.get(f"/runs/{rid}/report").json()["summary"]["cost"]
    assert s1["in_runs"] == pytest.approx(cost["spent"]) and cost["planning_spent"] == pytest.approx(s1["planning"])
    assert s1["total"] == pytest.approx(s1["planning"] + s1["in_runs"]) and s1["planning"] > 0  # the probe is planning spend
