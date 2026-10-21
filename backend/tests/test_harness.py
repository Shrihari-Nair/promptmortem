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
    need = {"tier", "tested", "untested_in_tier", "reviewed_share", "verified_share", "noise_band", "complete", "coverage_lost"}
    assert need <= set(scope), need - set(scope)
