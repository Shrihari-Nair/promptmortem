"""M8 acceptance: a candidate prompt is evaluated on held-out scenarios with a paired report vs the old AND the new prompt."""
import asyncio
import json

import pytest
from sqlmodel import Session, select

from app.agents import reflector
from app.benchmark.golden import seed_golden
from app.core.fake_llm import fake_provider
from app.core.llm import LLMClient
from app.core.models import Feedback, LLMCall, Optimization, PromptVersion, Run, Scenario
from app.optimize.dspy_bridge import ensure_placeholders, template_vars
from app.optimize.gepa_runner import optimize
from app.optimize.split import stratified_split
from tests.conftest import wait_job

CALLS = 60  # small GEPA budget: the fake reflector needs a single proposal


def start(client, pid, cap=5.0, **kw):
    r = client.post(f"/projects/{pid}/optimize", json={"cap": cap, "max_metric_calls": CALLS, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def run_opt(client, pid, **kw):
    r = start(client, pid, **kw)
    j = wait_job(client, r["job_id"], 180)
    return r["optimization_id"], j


def opt(client, oid):
    return client.get(f"/optimizations/{oid}").json()


def test_stratified_split_properties():
    items = [(f"r{i}", "refund") for i in range(20)] + [(f"e{i}", "explain") for i in range(20)] + [(f"a{i}", "chores") for i in range(20)] + [("solo", "x")]
    rest, held = stratified_split(items, 0.3)
    assert not set(rest) & set(held) and len(rest) + len(held) == 61
    assert sum(h.startswith("r") for h in held) == sum(h.startswith("e") for h in held) == sum(h.startswith("a") for h in held) == 6  # stratified
    assert "solo" in rest  # a stratum of one never goes entirely to held-out
    assert stratified_split(items, 0.3) == (rest, held)  # deterministic
    assert stratified_split(items, 0.3, seed=7)[1] != held  # but seedable


def test_placeholders_are_enforced():
    assert template_vars("a {{user_message}} b {{ plan }} {{user_message}}") == ["user_message", "plan"]
    assert ensure_placeholders("Be brief.", ["user_message"]) == "Be brief.\n\nuser_message: {{user_message}}"
    ok = "Be brief. {{ user_message }}"
    assert ensure_placeholders(ok, ["user_message"]) == ok


def test_gate_candidate_beats_the_prompt_and_is_validated_on_held_out_vs_new_and_old(client, engine):
    pid = seed_golden(engine, "planted", 0.02)
    oid, j = run_opt(client, pid)
    assert j["status"] == "done" and j["result"]["candidates"] == 1
    o = opt(client, oid)
    r, cfg = o["result"], o["config"]
    assert o["status"] == "done" and 0 < o["spent"] <= cfg["cap"]
    assert r["best_val_score"] > r["seed_val_score"]  # GEPA found something better than the starting (new) prompt
    assert set(cfg["heldout_ids"]).isdisjoint(cfg["train_ids"]) and set(cfg["heldout_ids"]).isdisjoint(cfg["val_ids"])
    assert abs(len(cfg["heldout_ids"]) - 18) <= 3 and cfg["trained_on_reviewed"] is True
    c = r["candidates"][0]
    assert c["label"] == "candidate_1" and "{{user_message}}" in c["text"] and c["status"] == "proposed"
    assert '"overpromise": 0.0' in c["text"] and '"overpromise": 0.9' in c["diff_vs_new"]  # the diff shows the fix
    assert any("refund" in n.lower() for n in c["notes"])  # the reflector explained the change
    # held-out validation: paired, noise-adjusted, against BOTH prompts
    vn, vo = c["heldout"]["vs_new"], c["heldout"]["vs_old"]
    assert vn["status"] == vo["status"] == "done"
    assert vn["counts"]["fixed"] > 0 and vn["counts"]["broken"] == 0  # repairs the new prompt's refund regression
    assert vo["counts"]["broken"] == 0 and vo["counts"]["fixed"] > 0  # and keeps the new prompt's length improvement over old
    assert vn["level"] == "ok" and vo["level"] == "ok"
    assert vn["scope"]["tested"] == vo["scope"]["tested"] == len(cfg["heldout_ids"]) and vn["scope"]["complete"]
    full = client.get(f"/runs/{vn['run_id']}/report").json()["summary"]  # the complete stage-6 report is available for each
    assert full["compare"] == {"base": "new", "test": "candidate_1"} and full["scope"]["tier"] == "heldout" and full["scope"]["deep_dive_added"] == 0
    md = client.get(f"/reports/{client.get(f'/runs/{vo['run_id']}/report').json()['id']}/export", params={"format": "md"}).text
    assert md.startswith("# Prompt change report: candidate_1 vs old")
    with Session(engine) as s:
        assert s.exec(select(PromptVersion).where(PromptVersion.label == "candidate_1")).one().text == c["text"]
        assert {r_.tier for r_ in s.exec(select(Run)).all()} == {"heldout"}


def test_optimizer_never_sees_held_out_scenarios(engine):
    pid = seed_golden(engine, "planted", 0.02)
    calls, phase = [], {"p": "setup"}

    async def spy(model, messages, params):
        calls.append((phase["p"], "\n".join(str(m["content"]) for m in messages)))
        return await fake_provider(model, messages, params)

    asyncio.run(optimize(engine, LLMClient(engine, fake=spy), pid, cap=5.0, max_metric_calls=CALLS, on_phase=lambda p: phase.update(p=p)))
    with Session(engine) as s:
        cfg = s.exec(select(Optimization)).one().config
        held = [str(v) for i in cfg["heldout_ids"] for v in s.get(Scenario, i).input.values()]
        train = [str(v) for i in cfg["train_ids"] for v in s.get(Scenario, i).input.values()]
    gepa = "\n".join(c for ph, c in calls if ph == "gepa")
    after = "\n".join(c for ph, c in calls if ph == "heldout")
    assert len(gepa) > 5000 and any(t in gepa for t in train)  # non-vacuous: the search really ran on training cases
    assert not [h for h in held if h in gepa]  # not one held-out input appeared in ANY call (target, judge or reflector) during the search
    assert any(h in after for h in held)  # ...and they are used afterwards, for validation only


def test_reflector_that_drops_a_placeholder_is_repaired(engine):
    pid = seed_golden(engine, "planted", 0.02)

    async def sloppy(model, messages, params):
        text, a, b, c = await fake_provider(model, messages, params)
        if "agent: reflector" in str(messages[0]["content"]):
            d = json.loads(text)
            d["prompt"] = d["prompt"].replace("{{user_message}}", "")
            text = json.dumps(d)
        return text, a, b, c

    asyncio.run(optimize(engine, LLMClient(engine, fake=sloppy), pid, cap=5.0, max_metric_calls=CALLS))
    with Session(engine) as s:
        c = s.exec(select(Optimization)).one().result["candidates"][0]
    assert c["text"].count("{{user_message}}") == 1 and c["text"].rstrip().endswith("{{user_message}}")  # restored, so it is a valid template
    assert c["heldout"]["vs_new"]["status"] == "done"


async def test_reflector_cannot_copy_training_inputs_into_the_prompt(engine):
    leaked = "Can I get a refund for invoice 7? I was charged after cancelling."
    replies = [json.dumps({"prompt": f"Answer politely. Example: {leaked} {{{{user_message}}}}", "notes": ["x"]}),
               json.dumps({"prompt": "Answer politely. {{user_message}}", "notes": ["y"]})]
    seen = []

    async def prov(model, messages, params):
        seen.append(messages[-1]["content"])
        return replies[min(len(seen) - 1, 1)], 1, 1, 0.0

    kw = dict(app_description="", constraints="", current_prompt="p " * 10, variables=["user_message"], examples=[], known_failures=[], human_feedback="", forbidden=[leaked])
    out = await reflector.reflect(LLMClient(engine, provider=prov), "x/m", **kw)
    assert len(seen) == 2 and "copied example inputs" in seen[1] and leaked not in out.prompt  # retried once, with the reason

    async def always(model, messages, params):
        return replies[0], 1, 1, 0.0

    with pytest.raises(ValueError, match="copied training inputs"):
        await reflector.reflect(LLMClient(engine, provider=always), "x/other", **kw)


def test_hard_cap_pauses_the_search_and_a_retry_replays_from_cache(client, engine):
    pid = seed_golden(engine, "planted", 0.02)
    oid, j = run_opt(client, pid, cap=0.004)
    o = opt(client, oid)
    assert j["status"] == "done" and j["result"]["status"] == "paused" and o["status"] == "paused" and "cap" in o["error"]
    assert o["spent"] >= 0.004 and o["spent"] < 0.004 + 0.02  # paused at the cap, overshoot bounded by in-flight calls
    with Session(engine) as s:
        assert not s.exec(select(Run)).all()  # nothing was validated on held-out data after the cap
    oid2, j2 = run_opt(client, pid, cap=5.0)  # the retry: same seed and prompts, so the first part replays from the cache
    assert opt(client, oid2)["status"] == "done"
    with Session(engine) as s:
        keys = [x.cache_key for x in s.exec(select(LLMCall)).all()]
    assert len(keys) == len(set(keys))  # no call was paid twice


def test_rejecting_with_a_comment_reaches_the_next_reflection(client, engine):
    pid = seed_golden(engine, "planted", 0.02)
    oid, _ = run_opt(client, pid)
    cid = opt(client, oid)["result"]["candidates"][0]["id"]
    r = client.post(f"/optimizations/{oid}/candidates/{cid}/reject", json={"comment": "never discuss internal policy ids"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    with Session(engine) as s:
        assert s.exec(select(Feedback).where(Feedback.target_type == "candidate")).one().comment == "never discuss internal policy ids"
    seen = []

    async def spy(model, messages, params):
        if "agent: reflector" in str(messages[0]["content"]):
            seen.append(str(messages[0]["content"]))
        return await fake_provider(model, messages, params)

    asyncio.run(optimize(engine, LLMClient(engine, fake=spy), pid, cap=5.0, max_metric_calls=CALLS))
    assert seen and all("never discuss internal policy ids" in p and "REJECTED" in p and "Candidate began" in p for p in seen)


def test_human_can_edit_revalidate_and_accept(client, engine):
    pid = seed_golden(engine, "planted", 0.02)
    oid, _ = run_opt(client, pid)
    c = opt(client, oid)["result"]["candidates"][0]
    assert client.patch(f"/optimizations/{oid}/candidates/{c['id']}", json={"text": "No placeholder at all"}).status_code == 422
    e = client.patch(f"/optimizations/{oid}/candidates/{c['id']}", json={"text": c["text"] + "\nAlways be polite.", "comment": "add politeness"})
    assert e.status_code == 200
    ed = e.json()
    assert ed["status"] == "edited" and ed["edited_from"] == c["id"] and ed["heldout"] == {} and ed["label"] == "candidate_2" and "+Always be polite." in ed["diff_vs_new"]
    j = wait_job(client, client.post(f"/optimizations/{oid}/candidates/{ed['id']}/evaluate", json={"cap": 2.0}).json()["job_id"], 180)
    assert j["status"] == "done" and j["result"]["statuses"] == "done,done"
    cands = {x["id"]: x for x in opt(client, oid)["result"]["candidates"]}
    assert cands[ed["id"]]["heldout"]["vs_new"]["counts"]["broken"] == 0 and cands[c["id"]]["status"] == "proposed"  # the original is untouched
    r = client.post(f"/optimizations/{oid}/candidates/{ed['id']}/accept", json={"promote": True, "comment": "ship it"})
    assert r.status_code == 200 and r.json()["label"] == "new" and "re-run" in r.json()["note"]
    with Session(engine) as s:
        pv = {v.label: v.text for v in s.exec(select(PromptVersion)).all()}
    assert pv["new"] == ed["text"] and "new_prev_1" in pv and '"overpromise": 0.9' in pv["new_prev_1"]  # the previous 'new' is kept
    assert client.post(f"/optimizations/{oid}/candidates/nope/accept", json={}).status_code == 404


def test_a_prompt_that_cannot_be_improved_says_so_and_validates_nothing(client, engine):
    pid = seed_golden(engine, "planted", 0.0)
    with Session(engine) as s:
        v = s.exec(select(PromptVersion).where(PromptVersion.project_id == pid, PromptVersion.label == "new")).one()
        v.text = v.text.replace('"overpromise": 0.9', '"overpromise": 0.0')  # already as good as the metrics can see
        s.add(v)
        s.commit()
    oid, j = run_opt(client, pid)
    o = opt(client, oid)
    assert o["status"] == "done" and o["result"]["candidates"] == [] and "no candidate beat" in o["result"]["note"]
    with Session(engine) as s:
        assert not s.exec(select(Run)).all()


def test_preconditions_are_explicit(client, engine):
    pid = seed_golden(engine, "planted")
    assert client.post(f"/projects/{pid}/optimize", json={}).status_code == 422  # a hard cap is required, never implicit
    assert client.post(f"/projects/{pid}/optimize", json={"cap": 0}).status_code == 422
    assert client.post("/projects/nope/optimize", json={"cap": 1}).status_code == 404
    small = seed_golden(engine, "planted", n_each=2)  # 6 scenarios: no room for a held-out set
    oid, j = run_opt(client, small)
    assert j["status"] == "failed" and "at least 10 scenarios" in j["error"] and opt(client, oid)["status"] == "failed"
    with Session(engine) as s:
        from app.core.models import Metric

        for m in s.exec(select(Metric).where(Metric.project_id == pid)).all():
            m.status = "rejected"
            s.add(m)
        s.commit()
    oid, j = run_opt(client, pid)
    assert j["status"] == "failed" and "no metrics" in j["error"]


def test_a_handful_of_approved_scenarios_does_not_shrink_the_split_to_nothing(client, engine):
    """Found on real data: 12 approved dataset rows used to become the whole pool (5 train / 3 val / 4 held out)."""
    pid = seed_golden(engine, "planted", 0.02, n_each=20)
    with Session(engine) as s:
        xs = s.exec(select(Scenario).where(Scenario.project_id == pid)).all()
        for x in xs[12:]:
            x.status, x.reviewed = "proposed", False  # only 12 stay approved
            s.add(x)
        s.commit()
    oid, j = run_opt(client, pid)
    o = opt(client, oid)
    assert o["config"]["trained_on_reviewed"] is False and len(o["config"]["heldout_ids"]) >= 15  # all 60 scenarios, not 12
    assert any("approved (12)" in w and "too small" in w for w in o["result"]["warnings"])
