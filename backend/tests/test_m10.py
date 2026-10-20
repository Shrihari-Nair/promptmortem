"""Closing the gaps against PLAN.md: drafting the new prompt, persisted notes, optional variables, cheap judge, ensemble,
old-prompt probes for regression guards, verdict feedback, restart recovery, stale tracking."""
import asyncio
import json

import pytest
from sqlmodel import Session, select

from app.core.fake_llm import fake_provider
from app.core.llm import LLMClient
from app.config import settings
from app.core.models import Metric, Project, PromptVersion, Scenario
from app.pipeline.execute import ensure_placeholders, fill_optional, optional_vars, template_vars
from tests.conftest import make_project, wait_job
from tests.golden import golden, run_to_end
from tests.test_m2 import generate_all


def test_prompt_drafter_applies_the_change_and_keeps_placeholders(client):
    old = "You are a billing assistant. Be thorough.\n\nCustomer: {{user_message}} (plan: {{plan_tier}})"
    r = client.post("/prompts/draft", json={"old_prompt": old, "change_request": "Answer in under 120 words.", "app_description": "billing", "constraints": "never promise refunds"})
    assert r.status_code == 200
    d = r.json()
    assert "under 120 words" in d["prompt"] and old in d["prompt"] and d["notes"]  # minimal edit on top of the old prompt
    assert all(f"{{{{{v}}}}}" in d["prompt"] for v in ("user_message", "plan_tier"))
    assert client.post("/prompts/draft", json={"old_prompt": "", "change_request": "x"}).status_code == 422
    assert client.post("/prompts/draft", json={"old_prompt": "p {{x}}", "change_request": " "}).status_code == 422


async def test_drafter_restores_a_dropped_placeholder(engine):
    from app.agents.prompt_drafter import draft

    async def sloppy(model, messages, params):
        return json.dumps({"prompt": "Be concise but forget the input.", "notes": ["x"]}), 1, 1, 0.0

    out = await draft(LLMClient(engine, provider=sloppy), "x/m", old_prompt="Help. {{user_message}}", change_request="shorter")
    assert out.prompt.count("{{user_message}}") == 1


def test_known_failure_notes_are_persisted_and_used_by_the_analyzer(client, engine):
    seen = []

    async def spy(model, messages, params):
        seen.append(str(messages[0]["content"]))
        return await fake_provider(model, messages, params)

    from app.api.deps import get_client
    from app.main import app

    app.dependency_overrides[get_client] = lambda: LLMClient(engine, fake=spy)
    try:
        p = client.post("/projects", json={"name": "x", "app_description": "d", "known_failure_notes": "over-promises refunds", "target_model": "fake/a", "judge_model_default": "fake/j"}).json()
        for label in ("old", "new"):
            client.post(f"/projects/{p['id']}/prompts", json={"label": label, "text": f"{label} {{{{user_message}}}}"})
        assert wait_job(client, client.post(f"/projects/{p['id']}/analyze").json()["job_id"])["status"] == "done"
    finally:
        app.dependency_overrides.pop(get_client, None)
    assert client.get(f"/projects/{p['id']}").json()["known_failure_notes"] == "over-promises refunds"
    assert any("over-promises refunds" in s and "agent: context_analyzer" in s for s in seen)  # no query parameter needed any more


def test_optional_variables_render_empty_and_never_fail_the_smoke_check(client, engine):
    assert template_vars("a {{x}} {{ y }} {{x}}") == ["x", "y"] and optional_vars({"x": "string", "y": "string?"}) == {"y"}
    assert fill_optional({"x": "1"}, {"y"}) == {"y": "", "x": "1"} and fill_optional({"y": "set"}, {"y"}) == {"y": "set"}
    assert ensure_placeholders("hi", ["a"]).endswith("{{a}}")
    pid = golden(client, engine, "noop")
    with Session(engine) as s:
        for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == pid)).all():
            v.text += "\nPlan: {{plan_tier}}"
            v.variables_schema = {"user_message": "string", "plan_tier": "string?"}
            s.add(v)
        s.commit()
    rid, j = run_to_end(client, pid)
    assert j["result"]["status"] == "done"  # without the 'string?' declaration this fails the smoke test (tested in M5)
    with Session(engine) as s:
        from app.core.models import RunResult

        assert all("{{plan_tier}}" not in r.output for r in s.exec(select(RunResult)).all())
    cfg = client.get(f"/projects/{pid}/export/promptfoo", params={"format": "json", "scope": "all"}).json()["config"]
    assert all(t["vars"].get("plan_tier") == "" for t in cfg["tests"])  # exported too


# ---------------------------------------------------------------- cheap judge (a real lever, not just an estimate)
NO_PF = {"programmatic_first": False, "cheap_judge_easy_rubrics": True, "adaptive_repeats": True, "early_stopping": True}


def spy_models():
    seen = []

    async def spy(model, messages, params):
        c = str(messages[0]["content"])
        for a in ("judge", "pairwise_judge"):
            if f"agent: {a} " in c:
                seen.append((a, model))
        return await fake_provider(model, messages, params)

    return seen, spy


def test_cheap_judge_lever_really_switches_the_judge_for_easy_rubrics_only(client, engine):
    from app.pipeline import runner

    pid = golden(client, engine, "noop", levers=NO_PF)
    est = client.post(f"/projects/{pid}/plan/estimate", json={"levers": NO_PF}).json()
    lever = next(l for l in est["levers"] if l["id"] == "cheap_judge_easy_rubrics")
    assert lever["available"] and lever["enabled"] and "fake/cheap" in lever["note"] and lever["saving_expected"] > 0
    seen, spy = spy_models()
    rid = runner.create_run(engine, pid)
    assert asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=spy), rid))["status"] == "done"
    assert {m for a, m in seen if a == "judge"} == {"fake/cheap"}  # the short binary rubric
    assert {m for a, m in seen if a == "pairwise_judge"} == {"fake/judge"}  # the regression judge never downgrades
    rep = client.get(f"/runs/{rid}/report").json()["summary"]
    assert rep["scope"]["cheap_judge"]["model"] == "fake/cheap" and "No refund over-promise (judge)" in rep["scope"]["cheap_judge"]["metrics"]
    # lever off: everything uses the configured judge
    pid2 = golden(client, engine, "noop", levers={**NO_PF, "cheap_judge_easy_rubrics": False})
    seen2, spy2 = spy_models()
    rid2 = runner.create_run(engine, pid2)
    asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=spy2), rid2))
    assert {m for a, m in seen2 if a == "judge"} == {"fake/judge"}


def test_cheap_judge_lever_is_unavailable_when_no_cheaper_model_is_known(client, engine):
    from app.pipeline import cost_estimator as ce
    from app.pipeline.metric_plan import cheap_model_for

    assert cheap_model_for("gemini/gemini-2.5-pro") == "gemini/gemini-2.5-flash-lite" and cheap_model_for("openai/gpt-4o") == "openai/gpt-4o-mini"
    assert cheap_model_for("claude-sonnet-4-5").startswith("anthropic/") and cheap_model_for("weird/model-x") is None
    pid = golden(client, engine, "noop")
    with Session(engine) as s:
        p = s.get(Project, pid)
        p.judge_model_default = "weird/model-x"
        s.add(p)
        s.commit()
    with Session(engine) as s:  # estimate() reuses the stored probe, so no provider call is needed
        on = ce.estimate(s, pid, {"cheap_judge_easy_rubrics": True})
        off = ce.estimate(s, pid, {"cheap_judge_easy_rubrics": False})
    lever = next(l for l in on["levers"] if l["id"] == "cheap_judge_easy_rubrics")
    assert lever == {**lever, "available": False, "enabled": False, "saving_expected": 0.0} and "PV_CHEAP_JUDGE_MODEL" in lever["note"]
    assert on["tiers"][0]["cost"] == off["tiers"][0]["cost"]  # asking for an unavailable lever changes nothing: no phantom savings


# ---------------------------------------------------------------- 2-judge ensemble (optional, logs disagreement)
def test_judge_ensemble_logs_disagreement_without_changing_the_verdict(client, engine):
    from app.pipeline import runner

    pid = golden(client, engine, "noop", levers={**NO_PF, "cheap_judge_easy_rubrics": False, "programmatic_first": False})
    with Session(engine) as s:
        m = next(x for x in s.exec(select(Metric).where(Metric.project_id == pid)).all() if x.name == "No refund over-promise (judge)")
        d = dict(m.definition)
    r = client.patch(f"/metrics/{m.id}", json={"definition": {**d, "ensemble": True, "ensemble_model": "fake/second"}})
    assert r.status_code == 200 and r.json()["definition"]["ensemble"] is True

    async def contrarian(model, messages, params):  # the second judge always disagrees
        text, a, b, c = await fake_provider(model, messages, params)
        if model == "fake/second" and "agent: judge " in str(messages[0]["content"]):
            j = json.loads(text)
            j["verdict"], j["score"] = ("fail", 0) if j["verdict"] == "pass" else ("pass", 1)
            text = json.dumps(j)
        return text, a, b, c

    rid = runner.create_run(engine, pid)
    asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=contrarian), rid))
    from app.core.models import Score

    with Session(engine) as s:
        sc = [x for x in s.exec(select(Score).where(Score.metric_id == m.id)).all()]
    assert sc and all(x.disputed and "second_judge" in x.details and "verdict" not in x.details for x in sc)  # logged; not mistaken for a pairwise score
    # the primary judge's verdict stands; the contrarian second judge is always the opposite of it
    assert all((x.value == 1.0) == (x.details["second_judge"]["verdict"] == "fail") for x in sc) and {x.value for x in sc} <= {0.0, 1.0}
    rel = client.get(f"/runs/{rid}/report").json()["summary"]["judge_reliability"]
    assert rel["pointwise_disagreement_rate"] == 1.0 and rel["ensemble_judgments"] == len(sc)


def test_ensemble_flag_without_a_second_model_is_ignored_not_an_error(client, engine):
    from app.pipeline import runner

    pid = golden(client, engine, "noop", levers={**NO_PF, "cheap_judge_easy_rubrics": False})
    with Session(engine) as s:
        m = next(x for x in s.exec(select(Metric).where(Metric.project_id == pid)).all() if x.name == "No refund over-promise (judge)")
        d = dict(m.definition)
    client.patch(f"/metrics/{m.id}", json={"definition": {**d, "ensemble": True}})  # no ensemble_model, no global setting
    rid, j = run_to_end(client, pid)
    assert j["result"]["status"] == "done" and client.get(f"/runs/{rid}/report").json()["summary"]["judge_reliability"]["ensemble_judgments"] == 0


# ---------------------------------------------------------------- regression_guard from the old prompt's real behaviour
def test_regression_guards_are_derived_from_what_the_old_prompt_actually_did(client, engine):
    from app.config import settings
    from app.pipeline import planner

    p = make_project(client)
    pid = p["id"]
    client.post(f"/projects/{pid}/dataset/import", json={"format": "csv", "content": "input,expected\nhow do I update my card,go to settings\nwhy was I charged twice,apologise\nplease cancel,explain how\n"})
    calls = []

    async def spy(model, messages, params):
        calls.append(str(messages[0]["content"]))
        return await fake_provider(model, messages, params)

    asyncio.run(planner.plan_scenarios(engine, LLMClient(engine, fake=spy), pid))
    target = [c for c in calls if not c.startswith("<!-- agent:")]
    probes = [c for c in target if "helpful billing assistant" in c]  # the OLD prompt's text from make_project
    assert len(probes) == settings.regression_probe_n
    assert sum("how do I update my card" in c or "why was I charged twice" in c or "please cancel" in c for c in probes) == 3  # real inputs first
    gens = [c for c in calls if "agent: scenario_generator" in c]
    guard = [c for c in gens if "CATEGORY=regression_guard" in c]
    other = [c for c in gens if "CATEGORY=regression_guard" not in c]
    assert guard and all("old_prompt_reply" in c and "fake-response" in c for c in guard)  # the generator sees the old prompt's real replies
    assert other and all("old_prompt_reply" not in c for c in other)
    n = len(calls)
    wait_job(client, client.post(f"/projects/{pid}/scenarios/generate", json={"category": "regression_guard", "n": 4}).json()["job_id"])  # extending also probes
    assert client.get(f"/projects/{pid}/coverage").json()["counts"]["unreviewed"] > 0


# ---------------------------------------------------------------- verdict overrides reach the agents
def test_verdict_overrides_are_fed_back_to_the_generator_and_metric_designer(client, engine):
    from app.pipeline.feedback import feedback_text, metric_feedback_text

    pid = golden(client, engine, "planted")
    rid, _ = run_to_end(client, pid)
    rep = client.get(f"/runs/{rid}/report").json()
    reg = rep["summary"]["regressions"][0]
    client.post(f"/reports/{rep['id']}/verdict-override", json={"scenario_id": reg["scenario_id"], "verdict": "fine", "comment": "a polite offer to check is fine here"})
    with Session(engine) as s:
        g = feedback_text(s, pid, category=reg["category"])
        m = metric_feedback_text(s, pid)
    assert "actually fine" in g and "a polite offer to check is fine here" in g and "Customer" not in g.split("actually")[0][:5]
    assert "overruled a result" in m and "a polite offer to check is fine here" in m and "too strict or too lenient" in m


# ---------------------------------------------------------------- restart recovery of everything that was running
def test_restart_marks_orphaned_jobs_and_optimizations_failed_with_a_clear_message(client, engine):
    from app.core.models import Job, Optimization
    from app.pipeline.runner import recover_interrupted

    p = client.post("/projects", json={"name": "x"}).json()
    with Session(engine) as s:
        for st in ("running", "pending", "done"):
            s.add(Job(project_id=p["id"], kind="analyze", status=st))
        s.add(Optimization(project_id=p["id"], status="running"))
        s.add(Optimization(project_id=p["id"], status="done"))
        s.commit()
    recover_interrupted(engine)
    with Session(engine) as s:
        jobs = {j.status for j in s.exec(select(Job)).all()}
        assert jobs == {"failed", "done"}
        assert all("server restart" in j.error and "cache" in j.error for j in s.exec(select(Job)).all() if j.status == "failed")
        assert sorted(o.status for o in s.exec(select(Optimization)).all()) == ["done", "failed"]


# ---------------------------------------------------------------- stale tracking
def test_upstream_changes_mark_only_stages_that_were_done_and_redoing_clears_them(client, engine):
    pid = golden(client, engine, "noop")
    stale = lambda: set(client.get(f"/projects/{pid}/stale").json())
    assert stale() == set()
    rid, _ = run_to_end(client, pid)
    assert stale() == set()  # everything is consistent after a run
    with Session(engine) as s:
        sids = [x.id for x in s.exec(select(Scenario).where(Scenario.project_id == pid)).all()]
    client.patch(f"/scenarios/{sids[0]}", json={"action": "approve"})
    assert stale() == set()  # approving changes nothing the metrics, the budget or the run depend on
    client.patch(f"/scenarios/{sids[1]}", json={"input": {"user_message": "edited text"}})
    assert stale() == {"Metrics", "Budget", "Run", "Report"}  # Refine was never done, so it is not stale
    st = client.get(f"/projects/{pid}/stale").json()
    assert "scenario was edited" in st["Budget"]["reason"]
    assert client.post(f"/projects/{pid}/stale/Metrics/ack").json().keys() == {"Budget", "Run", "Report"}  # 'still right'
    client.post(f"/projects/{pid}/plan/estimate")
    assert stale() == {"Run", "Report"}  # estimating IS the budget step
    run_to_end(client, pid)
    assert stale() == set()  # a new run refreshes Run and Report
    with Session(engine) as s:
        mid = s.exec(select(Metric).where(Metric.project_id == pid)).first().id
    client.patch(f"/metrics/{mid}", json={"status": "approved"})
    assert stale() == set()  # approving is not a content change
    client.patch(f"/metrics/{mid}", json={"weight": 3.0})
    assert stale() == {"Budget", "Run", "Report"}
    client.post(f"/projects/{pid}/stale/Budget/ack")
    client.post(f"/projects/{pid}/stale/Run/ack")
    client.post(f"/projects/{pid}/stale/Report/ack")
    client.post(f"/projects/{pid}/prompts", json={"label": "new", "text": "a rewritten prompt {{user_message}}"})
    assert stale() == {"Understand", "Scenarios", "Metrics", "Budget", "Run", "Report"}
    assert client.post(f"/projects/{pid}/stale/Nope/ack").status_code == 422 and client.get("/projects/nope/stale").status_code == 404


def test_stale_marks_follow_analysis_edits_and_regeneration_and_never_touch_unfinished_stages(client, engine):
    p = make_project(client)
    pid = p["id"]
    assert set(client.get(f"/projects/{pid}/stale").json()) == set()
    d = client.get(f"/projects/{pid}/analysis").json()["diff"]["data"]
    client.put(f"/projects/{pid}/analysis/diff", json=d)
    assert set(client.get(f"/projects/{pid}/stale").json()) == set()  # nothing downstream exists yet
    generate_all(client, pid)
    wait_job(client, client.post(f"/projects/{pid}/metrics/generate").json()["job_id"])
    d["changes"][0]["could_affect"].append("something new")
    client.put(f"/projects/{pid}/analysis/diff", json=d)
    assert set(client.get(f"/projects/{pid}/stale").json()) == {"Scenarios", "Metrics"}
    generate_all(client, pid)  # regenerating the scenarios clears 'Scenarios' and invalidates what depends on them
    assert set(client.get(f"/projects/{pid}/stale").json()) == {"Metrics"}
    wait_job(client, client.post(f"/projects/{pid}/metrics/generate").json()["job_id"])
    assert set(client.get(f"/projects/{pid}/stale").json()) == set()


# ---------------------------------------------------------------- real-provider cost control (found by the first Gemini run)
def test_reasoning_control_only_touches_gemini_2_5_and_never_the_pro_off_switch():
    from app.core.reasoning import reasoning_params as r

    assert r("gemini/gemini-2.5-flash", "disable") == {"reasoning_effort": "disable"} and r("gemini/gemini-2.5-flash-lite", "disable") == {"reasoning_effort": "disable"}
    assert r("gemini/gemini-2.5-pro", "disable") == {"reasoning_effort": "low"}  # 2.5 Pro cannot switch thinking off
    for other in ("gemini/gemini-2.0-flash", "openai/gpt-4o", "anthropic/claude-sonnet-4-5", "fake/app", "ollama/llama3"):
        assert r(other, "disable") == {}, other  # unsupported parameters would error on other providers
    assert r("gemini/gemini-2.5-flash", "default") == {} and r("gemini/gemini-2.5-flash", None) == {}


def test_judges_and_generators_switch_thinking_off_but_the_target_is_left_alone(engine, monkeypatch):
    from app.agents import judge as judge_agent
    from app.agents.schemas import JudgeDef
    from app.pipeline.execute import run_target

    seen = []

    async def prov(model, messages, params):
        seen.append((model, dict(params)))
        agent = str(messages[0]["content"])
        reply = {"verdict": "pass", "score": 1, "rationale": "ok"} if "agent: judge " in agent else "hello"
        return (json.dumps(reply) if isinstance(reply, dict) else reply), 1, 1, 0.0

    c = LLMClient(engine, provider=prov)
    d = JudgeDef(rubric="Pass if polite.", pass_examples=["thanks"], fail_examples=["go away"])
    asyncio.run(judge_agent.judge(c, "gemini/gemini-2.5-flash", d, {"q": 1}, "hi"))
    asyncio.run(run_target(c, "gemini/gemini-2.5-flash", "Say hi {{q}}", {"q": 1}))
    judge_call, target_call = seen
    assert judge_call[1]["reasoning_effort"] == "disable" and "reasoning_effort" not in target_call[1]  # the app's behaviour is what we measure
    monkeypatch.setattr(settings, "target_reasoning", "low")
    asyncio.run(run_target(c, "gemini/gemini-2.5-flash", "Say hi again {{q}}", {"q": 2}))
    assert seen[-1][1]["reasoning_effort"] == "low"  # configurable to match how the app really runs


def test_estimate_warns_when_protected_cases_dominate(client, engine):
    pid = golden(client, engine, "noop")  # 20 of 60 golden cases are protected (33%): no warning
    assert client.post(f"/projects/{pid}/plan/estimate").json()["warnings"] == []
    with Session(engine) as s:
        for x in s.exec(select(Scenario).where(Scenario.project_id == pid)).all():
            x.severity = 5
            s.add(x)
        s.commit()
    est = client.post(f"/projects/{pid}/plan/estimate").json()
    assert len(est["warnings"]) == 1 and "100%" in est["warnings"][0] and "never auto-trimmed" in est["warnings"][0]
