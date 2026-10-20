import json

import pytest

from app.adapters.dataset_import import parse_records, to_items
from app.agents.base import AgentError, run_agent
from app.agents.schemas import ContextAnalysis
from app.core.llm import LLMClient
from tests.conftest import make_project

GOOD = json.dumps({"task": "t"})


def scripted(replies):
    calls = []

    async def provider(model, messages, params):
        calls.append(messages)
        return replies[min(len(calls) - 1, len(replies) - 1)], 1, 1, 0.0

    return provider, calls


async def test_agent_repairs_invalid_output(engine):
    prov, calls = scripted(["not json", '{"nope": 1}', "```json\n" + GOOD + "\n```"])
    out = await run_agent(LLMClient(engine, provider=prov), "x/m", "context_analyzer",
                          {k: "" for k in ("app_description", "constraints", "known_failure_notes", "prompt_old", "prompt_new", "dataset_sample")},
                          ContextAnalysis, temperature=0.2)
    assert out.task == "t" and len(calls) == 3
    assert "invalid" in calls[1][-1]["content"]  # repair message carries the error


async def test_agent_gives_up_after_two_repairs(engine):
    prov, calls = scripted(["bad"])
    with pytest.raises(AgentError):
        await run_agent(LLMClient(engine, provider=prov), "x/m", "context_analyzer", {}, ContextAnalysis, temperature=0)
    assert len(calls) == 3


def test_import_csv_with_mapping_and_jsonl_default():
    rows = parse_records("msg,tier,want\nhi,pro,ok\n", "csv")
    it = to_items(rows, {"input": {"user_message": "msg", "plan_tier": "tier"}, "expected": "want"})
    assert it == [{"input": {"user_message": "hi", "plan_tier": "pro"}, "expected": "ok", "output": None}]
    jl = to_items(parse_records('{"input": "hello", "output": "o"}\n{"input": {"user_message": "x"}}\n', "jsonl"))
    assert jl[0]["input"] == {"user_message": "hello"} and jl[0]["output"] == "o" and jl[1]["input"] == {"user_message": "x"}
    with pytest.raises(ValueError):
        to_items([{"foo": "bar"}])
    with pytest.raises(ValueError):
        to_items([{"input": " "}])


def test_analyze_then_edit_via_api(client):
    p = make_project(client)
    a = client.get(f"/projects/{p['id']}/analysis").json()
    assert a["context"]["data"]["task"] and len(a["diff"]["data"]["changes"]) == 2
    assert a["diff"]["data"]["changes"][0]["could_affect"]
    d = a["diff"]["data"]
    d["changes"][0]["could_affect"].append("something I added")
    assert client.put(f"/projects/{p['id']}/analysis/diff", json=d).status_code == 200
    got = client.get(f"/projects/{p['id']}/analysis").json()["diff"]
    assert got["edited"] and "something I added" in got["data"]["changes"][0]["could_affect"]
    assert client.put(f"/projects/{p['id']}/analysis/diff", json={"changes": [{"nope": 1}]}).status_code == 422


def test_analyze_requires_both_prompts(client):
    p = client.post("/projects", json={"name": "x", "judge_model_default": "fake/j"}).json()
    assert client.post(f"/projects/{p['id']}/analyze").status_code == 422


def test_dataset_and_trace_import_api(client):
    p = client.post("/projects", json={"name": "x"}).json()
    r = client.post(f"/projects/{p['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nhi,yo\nbye,\n"})
    assert r.json() == {"imported": 2, "skipped_duplicates": 0}
    r = client.post(f"/projects/{p['id']}/traces/import", json={"format": "jsonl", "content": '{"input":"a","output":"b"}'})
    assert r.json() == {"imported": 1, "skipped_duplicates": 0}
    again = client.post(f"/projects/{p['id']}/traces/import", json={"format": "jsonl", "content": '{"input":"a"}\n{"input":"c"}\n{"input":"c"}'})
    assert again.json() == {"imported": 1, "skipped_duplicates": 2}  # "a" existed; "c" repeated
    items = client.get(f"/projects/{p['id']}/dataset").json()
    assert {i["source"] for i in items} == {"user", "trace"} and len(items) == 4
    assert client.post(f"/projects/{p['id']}/dataset/import", json={"format": "csv", "content": "a\nb"}).status_code == 422
