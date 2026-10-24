import asyncio
import json
from collections import Counter

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.core.fake_llm import fake_provider
from app.core.llm import LLMClient
from app.core.models import LLMCall, Metric, PromptVersion, Run, RunResult, Score
from app.pipeline import runner
from tests.conftest import wait_job
from tests.golden import golden, run_to_end, start_run

OFF = {"programmatic_first": False, "cheap_judge_easy_rubrics": False, "adaptive_repeats": False, "early_stopping": False}


def no_duplicate_paid_calls(engine):
    with Session(engine) as s:
        keys = [x.cache_key for x in s.exec(select(LLMCall)).all()]
        rr = [(r.run_id, r.scenario_id, r.prompt_version_id, r.repeat_idx) for r in s.exec(select(RunResult)).all()]
    assert len(keys) == len(set(keys)), "a call was paid twice"
    assert len(rr) == len(set(rr)), "a result row was written twice"


def paid_total(engine):
    with Session(engine) as s:
        return sum(x.cost for x in s.exec(select(LLMCall)).all())


def test_run_requires_a_confirmed_plan(client, engine):
    from app.benchmark.golden import seed_golden

    pid = seed_golden(engine)
    r = client.post(f"/projects/{pid}/runs")
    assert r.status_code == 409 and "budget" in r.json()["detail"]


def test_full_run_lifecycle_noise_and_spend(client, engine):
    pid = golden(client, engine, "noop")
    before = paid_total(engine)  # the probe already paid for some calls
    rid, j = run_to_end(client, pid)
    assert j["status"] == "done" and j["result"] == {"status": "done"}
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "done" and st["stage"] == "done" and 0 < st["spent"] <= st["cap"]
    assert st["spent"] == pytest.approx(paid_total(engine) - before)  # run spend == what was actually paid during the run
    assert st["noise"]["source"] == "pooled" and set(st["noise"]["metrics"]) and all(v["var"] > 0 for v in st["noise"]["metrics"].values())
    with Session(engine) as s:  # smoke measured A/A controls for the pairwise metric
        assert len(s.exec(select(Score).where(Score.judge_repeat_idx == 1)).all()) == 6  # 10% of 60 cases
    no_duplicate_paid_calls(engine)


def test_hard_cap_pauses_then_extend_resumes_without_duplicate_paid_calls(client, engine):
    pid = golden(client, engine, "noop")
    cap = 0.03
    rid, j = run_to_end(client, pid, cap=cap)
    assert j["result"]["status"] == "paused" and j["result"]["reason"] == "cap"
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "paused" and st["pause_reason"] == "cap"
    assert cap <= st["spent"] < cap + 0.01  # paused at the cap (overshoot bounded by in-flight calls); never silently truncated
    assert client.post(f"/runs/{rid}/extend", json={"amount": 0}).status_code == 422
    r = client.post(f"/runs/{rid}/extend", json={"amount": 5})
    assert wait_job(client, r.json()["job_id"], 120)["result"]["status"] == "done"
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "done" and st["cap"] == pytest.approx(cap + 5)
    no_duplicate_paid_calls(engine)
    # the paused part of the run was not paid for again: spend == sum of every paid call, probe excluded
    with Session(engine) as s:
        run_cost = sum(x.cost for x in s.exec(select(RunResult)).all())
    assert st["spent"] >= run_cost
    assert client.post(f"/runs/{rid}/resume").status_code == 409  # done runs cannot be resumed


def test_partial_report_after_stop(client, engine):
    pid = golden(client, engine, "planted")
    rid, j = run_to_end(client, pid, cap=0.03)
    assert j["result"]["reason"] == "cap"
    assert client.post(f"/runs/{rid}/stop").status_code == 200
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "stopped"
    rep = client.get(f"/runs/{rid}/report").json()["summary"]
    assert rep["scope"]["complete"] is False and rep["scope"]["untested_in_tier"] > 0
    assert rep["headline"]["level"] != "ok"  # a partial run never says "all clear"
    assert client.post(f"/runs/{rid}/stop").status_code == 409


def flaky(after):
    n = {"calls": 0}

    async def provider(model, messages, params):
        n["calls"] += 1
        if n["calls"] > after:
            raise RuntimeError("provider down")
        return await fake_provider(model, messages, params)

    return provider


def test_provider_crash_pauses_keeps_state_and_resumes(client, engine):
    pid = golden(client, engine, "noop")
    rid = runner.create_run(engine, pid)
    res = asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=flaky(120)), rid))
    assert res["status"] == "paused" and res["reason"] == "error"
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.status == "paused" and run.pause_reason.startswith("error:") and run.spent > 0
        done_rows = len(s.exec(select(RunResult)).all())
    assert done_rows > 0
    res = asyncio.run(runner.execute_run(engine, LLMClient(engine), rid))  # provider is back
    assert res["status"] == "done"
    no_duplicate_paid_calls(engine)


def test_run_resumes_after_server_restart(client, engine):
    pid = golden(client, engine, "noop")
    rid = runner.create_run(engine, pid)
    asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=flaky(100)), rid))
    with Session(engine) as s:  # a hard crash leaves the row 'running' with no task behind it
        run = s.get(Run, rid)
        run.status = "running"
        s.add(run)
        s.commit()
    assert runner.recover_interrupted(engine) == 1
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "paused" and "restart" in st["pause_reason"]
    r = client.post(f"/runs/{rid}/resume")
    assert wait_job(client, r.json()["job_id"], 120)["result"]["status"] == "done"
    no_duplicate_paid_calls(engine)


def test_smoke_fails_fast_on_a_broken_template_before_spending(client, engine):
    pid = golden(client, engine, "noop")
    with Session(engine) as s:
        pv = s.exec(select(PromptVersion).where(PromptVersion.project_id == pid, PromptVersion.label == "new")).one()
        pv.text += "\nPlan: {{plan_tier}}"
        s.add(pv)
        s.commit()
    rid, j = run_to_end(client, pid)
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "failed" and "plan_tier" in st["error"]
    with Session(engine) as s:
        assert len(s.exec(select(RunResult)).all()) == 0  # failed before any paid call


def rows_per_scenario(engine, rid):
    with Session(engine) as s:
        return Counter(r.scenario_id for r in s.exec(select(RunResult).where(RunResult.run_id == rid)).all())


def test_adaptive_repeats_and_early_stopping_save_calls(client, engine):
    off = golden(client, engine, "noop", levers=OFF)
    rid_off, _ = run_to_end(client, off)
    assert set(rows_per_scenario(engine, rid_off).values()) == {6}  # 3 repeats x 2 prompts, exactly, for every case
    on = golden(client, engine, "noop")
    rid_on, _ = run_to_end(client, on)
    counts = rows_per_scenario(engine, rid_on)
    assert sum(counts.values()) < sum(rows_per_scenario(engine, rid_off).values()) * 0.8  # stable cases stopped after 2 repeats
    assert min(counts.values()) == 4 and max(counts.values()) <= 2 * settings.max_repeats_adaptive


def test_clear_regressions_stop_early_and_are_reported_live(client, engine):
    pid = golden(client, engine, "planted")
    r = start_run(client, pid)
    evs = []
    with client.stream("GET", f"/runs/{r['run_id']}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
                if evs[-1]["type"] in ("done", "paused", "failed", "stopped"):
                    break
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "snapshot" and kinds[-1] == "done", kinds[-10:]  # shows what a flaky run saw
    for k in ("noise", "progress", "regression"):
        assert k in kinds, (k, kinds[:10], len(kinds))
    stages = [e["stage"] for e in evs if e["type"] == "stage"]
    assert stages.index("smoke") < stages.index("main") and "deep_dive" in stages
    assert kinds.index("noise") < kinds.index("regression")  # noise is known before regressions are surfaced
    reg = [e for e in evs if e["type"] == "regression"]
    assert all(e["severity"] >= 1 and e["new"] < e["old"] for e in reg)
    prog = [e for e in evs if e["type"] == "progress"]
    assert all(b["spent"] >= a["spent"] for a, b in zip(prog, prog[1:])) and all(e["spent"] <= e["cap"] + 0.01 for e in prog)
    # quiet cases stop after 2 repeats; a case already decided by the exact, corrected test stops too (confirm_flagged is off)
    with Session(engine) as s:
        from app.core.models import Scenario

        by = {x.id: x for x in s.exec(select(Scenario)).all() if "deep_dive" not in x.tags}
    counts = rows_per_scenario(engine, r["run_id"])
    chores = [counts[i] for i, x in by.items() if "account" in x.tags]
    refund = [counts[i] for i, x in by.items() if "refund" in x.tags]
    assert set(chores) == {4}  # 2 repeats x 2 prompts
    assert sum(refund) / len(refund) < 2 * settings.max_repeats_adaptive  # decided cases did not all run to the maximum


def test_confirm_flagged_setting_keeps_running_flagged_cases_to_the_maximum(client, engine, monkeypatch):
    monkeypatch.setattr(settings, "confirm_flagged", True)
    pid = golden(client, engine, "planted")
    rid, _ = run_to_end(client, pid)
    with Session(engine) as s:
        from app.core.models import Scenario

        refund = {x.id for x in s.exec(select(Scenario)).all() if "refund" in x.tags and "deep_dive" not in x.tags}
    counts = rows_per_scenario(engine, rid)
    assert max(counts[i] for i in refund) == 2 * settings.max_repeats_adaptive  # the old, costlier behaviour is still available


def test_deep_dive_asks_before_exceeding_the_cap(client, engine):
    pid = golden(client, engine, "planted")
    rid, j = run_to_end(client, pid, cap=0.1)
    seen = []
    for _ in range(40):  # keep nudging the cap until the main run is paid for; then the deep dive must ASK
        st = client.get(f"/runs/{rid}").json()
        seen.append(st["pause_reason"])
        if st["status"] != "paused" or st["pause_reason"] == "deep_dive_needs_budget":
            break
        j = wait_job(client, client.post(f"/runs/{rid}/extend", json={"amount": 0.004}).json()["job_id"], 120)
    st = client.get(f"/runs/{rid}").json()
    assert st["status"] == "paused" and st["pause_reason"] == "deep_dive_needs_budget", seen
    assert st["stage"] == "main"  # the main run finished; nothing from the deep dive was spent
    with Session(engine) as s:
        assert "deep_dive_ids" not in s.get(Run, rid).config or s.get(Run, rid).config["deep_dive_ids"] == []
    j = wait_job(client, client.post(f"/runs/{rid}/extend", json={"amount": 5}).json()["job_id"], 120)
    assert j["result"]["status"] == "done"
    rep = client.get(f"/runs/{rid}/report").json()["summary"]
    assert rep["scope"]["deep_dive_added"] > 0
    no_duplicate_paid_calls(engine)
