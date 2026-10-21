import asyncio
import json
import re

import pytest
from sqlmodel import Session, select

from app.agents import judge as judge_agent
from app.agents.schemas import Check, JudgeDef, MetricSpec
from app.core.llm import LLMClient
from app.core.models import Calibration, Feedback, Metric
from app.pipeline import metric_plan
from app.pipeline.feedback import metric_feedback_text
from app.pipeline.metrics_engine import metric_applies, run_check, run_programmatic
from tests.conftest import make_project, wait_job
from tests.test_m2 import generate_all


def C(**kw):
    return Check.model_validate(kw)


@pytest.mark.parametrize("check,output,ok", [
    (dict(type="max_words", value=3), "one two three", True),
    (dict(type="max_words", value=3), "one two three four", False),
    (dict(type="min_words", value=2), "one", False),
    (dict(type="max_chars", value=5), "abcdef", False),
    (dict(type="regex_required", pattern=r"\d{3}"), "call 555", True),
    (dict(type="forbidden_regex", pattern=r"POL-\d+"), "see POL-12", False),
    (dict(type="forbidden_regex", pattern=r"pol-\d+", ignore_case=True), "see POL-12", False),
    (dict(type="contains_all", values=["a", "b"]), "a and b", True),
    (dict(type="contains_all", values=["a", "z"]), "a and b", False),
    (dict(type="contains_none", values=["refund"]), "we will refund", False),
    (dict(type="json_valid"), '{"a": 1}', True),
    (dict(type="json_valid"), "```json\n{}\n```", False),
    (dict(type="json_schema", schema={"type": "object", "required": ["a"]}), '{"a": 1}', True),
    (dict(type="json_schema", schema={"type": "object", "required": ["a"]}), '{"b": 1}', False),
    (dict(type="equals", value="Yes", ignore_case=True), " yes ", True),
    (dict(type="starts_with", value="Hello"), "Hello there", True),
    (dict(type="starts_with", value="Hello"), "Well hello", False),
    (dict(type="language_is", value="en"), "Thank you very much for contacting our support team today.", True),
    (dict(type="language_is", value="en"), "Muchas gracias por contactar con nuestro equipo de soporte hoy.", False),
])
def test_each_check_type(check, output, ok):
    assert run_check(C(**check), output)[0] is ok


def test_equals_expected_aggregate_and_applicability():
    d = {"checks": [{"type": "max_words", "value": 2}, {"type": "contains_all", "values": ["x"]}], "aggregate": "mean"}
    assert run_programmatic(d, "x y z")[0] == 0.5
    assert run_programmatic({**d, "aggregate": "any_pass"}, "x y z")[0] == 1.0
    v, why = run_programmatic({"checks": [{"type": "equals", "value": "$expected"}]}, "42", expected="42")
    assert v == 1.0
    assert metric_applies({"categories": ["*"]}, "x", [])
    assert metric_applies({"tags": ["refund"]}, "any", ["refund", "a"]) and not metric_applies({"tags": ["refund"]}, "any", ["b"])
    assert not metric_applies({"categories": ["edge_case"]}, "happy_path", [])


def test_schema_rejects_bad_specs():
    for bad in (dict(type="forbidden_regex", pattern="("), dict(type="max_words"), dict(type="max_words", value=-1),
                dict(type="json_schema", schema={"type": "nonsense"}), dict(type="contains_all"), dict(type="equals", value=3)):
        with pytest.raises(ValueError):
            C(**bad)
    with pytest.raises(ValueError):
        JudgeDef(rubric="a long enough rubric", pass_examples=["ok"])  # no fail example
    with pytest.raises(ValueError):
        MetricSpec(name="x", kind="programmatic", definition={"checks": []})


def test_design_adds_regression_metric_prefers_programmatic_and_replaces_proposals(client, engine):
    p = make_project(client)
    generate_all(client, p["id"])
    j = wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    assert j["status"] == "done" and j["result"]["programmatic"] >= 2 and j["result"]["llm_judge"] >= 2
    ms = client.get(f"/projects/{p['id']}/metrics").json()
    assert sum(m["definition"].get("mode") == "pairwise" for m in ms) == 1  # always exactly one regression metric
    judge_ms = [m for m in ms if m["kind"] == "llm_judge" and m["definition"]["mode"] == "pointwise"]
    assert all(m["definition"]["pass_examples"] and m["definition"]["fail_examples"] for m in judge_ms)
    keep = next(m for m in ms if m["name"] == "Under 120 words")
    rej = next(m for m in ms if m["name"] == "No refund over-promise")
    client.patch(f"/metrics/{keep['id']}", json={"status": "approved"})
    client.patch(f"/metrics/{rej['id']}", json={"status": "rejected", "comment": "refund is handled elsewhere"})
    wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    after = client.get(f"/projects/{p['id']}/metrics").json()
    assert sum(m["name"] == "Under 120 words" for m in after) == 1  # approved one not duplicated
    assert sum(m["definition"].get("mode") == "pairwise" for m in after) == 1
    with Session(engine) as s:
        assert "refund is handled elsewhere" in metric_feedback_text(s, p["id"])
        assert s.exec(select(Feedback).where(Feedback.target_type == "metric")).all()


def test_dataset_expected_values_become_a_metric(client):
    p = make_project(client)
    client.post(f"/projects/{p['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nhi,Hello!\n"})
    generate_all(client, p["id"])
    wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    ms = client.get(f"/projects/{p['id']}/metrics").json()
    exp = [m for m in ms if m["kind"] == "programmatic" and m["definition"]["checks"][0].get("value") == "$expected"]
    assert len(exp) == 1 and exp[0]["applies_to"] == {"tags": ["dataset"]}


def test_edit_metric_validates_and_marks_edited(client):
    p = make_project(client)
    generate_all(client, p["id"])
    wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    m = next(m for m in client.get(f"/projects/{p['id']}/metrics").json() if m["name"] == "Under 120 words")
    r = client.patch(f"/metrics/{m['id']}", json={"definition": {"checks": [{"type": "max_words", "value": 80}]}, "weight": 3}).json()
    assert r["status"] == "edited" and r["definition"]["checks"][0]["value"] == 80 and r["weight"] == 3
    assert client.patch(f"/metrics/{m['id']}", json={"definition": {"checks": [{"type": "max_words"}]}}).status_code == 422
    assert client.patch("/metrics/nope", json={}).status_code == 404
    mine = client.post(f"/projects/{p['id']}/metrics", json={"name": "mine", "kind": "llm_judge", "definition": {"rubric": "too short"}})
    assert mine.status_code == 422


def test_preview_runs_all_three_kinds(client):
    p = make_project(client)
    generate_all(client, p["id"])
    wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    ms = {m["name"]: m for m in client.get(f"/projects/{p['id']}/metrics").json()}
    prog = client.post(f"/metrics/{ms['Under 120 words']['id']}/preview", json={"n": 3}).json()
    assert len(prog) == 3 and all(x["result"]["value"] in (0.0, 1.0) and x["output"] for x in prog)
    jd = client.post(f"/metrics/{ms['No refund over-promise']['id']}/preview", json={"n": 4}).json()
    assert len(jd) == 4 and all(x["result"]["verdict"] in ("pass", "fail") for x in jd)
    pair = [m for m in ms.values() if m["definition"].get("mode") == "pairwise"][0]
    pw = client.post(f"/metrics/{pair['id']}/preview", json={"n": 3}).json()
    assert all(x["result"]["verdict"] in ("old", "new", "tie") and "old_output" in x for x in pw)
    own = client.post(f"/metrics/{ms['Under 120 words']['id']}/preview", json={"samples": [{"input": {}, "output": "a b c"}]}).json()
    assert own[0]["result"]["value"] == 1.0
    assert client.post(f"/metrics/{pair['id']}/preview", json={"samples": [{"input": {}, "output": "x"}]}).status_code == 422


def scripted(fn):
    seen = []

    async def provider(model, messages, params):
        prompt = messages[0]["content"]
        seen.append(prompt)
        return json.dumps(fn(prompt)), 1, 1, 0.0

    return provider, seen


PAIR = JudgeDef(mode="pairwise", rubric="Which output is better overall?")


async def test_pairwise_position_swap_detects_position_bias_and_stays_blind(engine):
    # a judge that ALWAYS says "A" is purely positional -> orders disagree -> disputed
    prov, seen = scripted(lambda p: {"winner": "A", "preserved_correct_behavior": True, "rationale": "r"})
    r = await judge_agent.pairwise(LLMClient(engine, provider=prov), "x/m", PAIR, {"q": 1}, "OLDTEXT", "NEWTEXT")
    assert r.disputed and r.verdict == "tie" and len(seen) == 2
    # the judge sees both orders, and never any hint of which prompt produced which output
    assert "## Output A\nOLDTEXT" in seen[0] and "## Output A\nNEWTEXT" in seen[1]
    assert not any(re.search(r"\b(old|new)\b", re.sub(r"OLDTEXT|NEWTEXT", "", x), re.I) for x in seen)


async def test_pairwise_consistent_judge_maps_back_to_old_new(engine):
    prefer_new = lambda p: {"winner": "A" if "## Output A\nNEWTEXT" in p else "B", "preserved_correct_behavior": True, "rationale": "r"}
    prov, _ = scripted(prefer_new)
    r = await judge_agent.pairwise(LLMClient(engine, provider=prov), "x/m", PAIR, {}, "OLDTEXT", "NEWTEXT")
    assert r.verdict == "new" and not r.disputed
    prefer_old = lambda p: {"winner": "A" if "## Output A\nOLDTEXT" in p else "B", "preserved_correct_behavior": False, "rationale": "r"}
    prov, _ = scripted(prefer_old)  # different model name: identical prompts would otherwise hit the cache
    r = await judge_agent.pairwise(LLMClient(engine, provider=prov), "x/m2", PAIR, {}, "OLDTEXT", "NEWTEXT")
    assert r.verdict == "old" and not r.preserved_correct_behavior


async def test_pointwise_judge_prompt_is_blind_and_cached(engine):
    prov, seen = scripted(lambda p: {"verdict": "pass", "score": 1, "rationale": "r"})
    d = JudgeDef(rubric="Pass if polite.", pass_examples=["thanks"], fail_examples=["go away"])
    c = LLMClient(engine, provider=prov)
    await judge_agent.judge(c, "x/m", d, {"q": "hi"}, "hello")
    await judge_agent.judge(c, "x/m", d, {"q": "hi"}, "hello")
    assert len(seen) == 1  # re-scoring identical output hits the cache
    assert not re.search(r"\b(old|new|version)\b", seen[0], re.I)


def test_calibration_agreement_uses_only_explicit_labels(client, engine):
    p = make_project(client)
    pid = p["id"]
    # free-text expected values are descriptions of wanted behaviour, NOT gold replies: they must not be calibrated on
    client.post(f"/projects/{pid}/dataset/import", json={"format": "csv", "content": "input,expected\nq1,Apologise and offer to check eligibility\nq2,Explain where to download invoices\n"})
    lines = [{"input": f"good{i}", "output": f"fine reply {i}", "expected": "pass"} for i in range(4)] + [{"input": f"bad{i}", "output": f"bad reply {i}", "expected": "fail"} for i in range(2)]
    client.post(f"/projects/{pid}/traces/import", json={"format": "jsonl", "content": "\n".join(json.dumps(l) for l in lines)})
    m = client.post(f"/projects/{pid}/metrics", json={
        "name": "j", "kind": "llm_judge", "judge_model": "x/m",
        "definition": {"rubric": "Pass if helpful.", "pass_examples": ["yes"], "fail_examples": ["no"]}}).json()
    prov, _ = scripted(lambda p: {"verdict": "pass", "score": 1, "rationale": "r"})  # a lenient judge passes everything
    res = asyncio.run(metric_plan.calibrate(engine, LLMClient(engine, provider=prov), m["id"]))
    assert res["n_labeled"] == 6 and abs(res["agreement"] - 4 / 6) < 1e-9  # the 2 descriptions are ignored; it misses both labelled fails
    with Session(engine) as s:
        cal = s.exec(select(Calibration)).one()
        assert cal.n_labeled == 6 and {x["label"] for x in cal.details["disagreements"]} == {"fail"}
        assert cal.details["by_label"] == {"pass": {"n": 4, "agree": 4}, "fail": {"n": 2, "agree": 0}}  # a lenient judge is visible per label
    assert client.get(f"/projects/{pid}/calibration").json()[0]["agreement"] == pytest.approx(4 / 6)
    # nothing labelled => a clear failure; programmatic/pairwise metrics are not calibratable
    q = make_project(client)
    client.post(f"/projects/{q['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nq,some description\n"})
    mm = client.post(f"/projects/{q['id']}/metrics", json={"name": "j", "kind": "llm_judge", "definition": {"rubric": "Pass if helpful.", "pass_examples": ["y"], "fail_examples": ["n"]}}).json()
    j = wait_job(client, client.post(f"/metrics/{mm['id']}/calibrate").json()["job_id"])
    assert j["status"] == "failed" and "no labelled examples" in j["error"]


def test_unlabelled_traces_do_not_count_as_labelled_data(client, engine):
    """Regression: JSON-null `expected` rows leaked into calibration (as the string 'None') and the exact-match metric."""
    p = make_project(client)
    pid = p["id"]
    client.post(f"/projects/{pid}/traces/import", json={"format": "jsonl", "content": '{"input":"t1"}\n{"input":"t2"}\n{"input":"t3","output":"fine","expected":"pass"}'})
    generate_all(client, pid)
    wait_job(client, client.post(f"/projects/{pid}/metrics/generate").json()["job_id"])
    ms = client.get(f"/projects/{pid}/metrics").json()
    assert not [m for m in ms if m["kind"] == "programmatic" and m["definition"]["checks"][0].get("value") == "$expected"]  # no gold values at all
    m = client.post(f"/projects/{pid}/metrics", json={"name": "j", "kind": "llm_judge", "judge_model": "x/m",
                                                      "definition": {"rubric": "Pass if helpful.", "pass_examples": ["yes"], "fail_examples": ["no"]}}).json()
    prov, _ = scripted(lambda p: {"verdict": "pass", "score": 1, "rationale": "r"})
    res = asyncio.run(metric_plan.calibrate(engine, LLMClient(engine, provider=prov), m["id"]))
    assert res["n_labeled"] == 1  # only the one trace that carries a label


def _labelled_judge_project(client):
    pid = make_project(client)["id"]
    lines = [{"input": f"good{i}", "output": f"fine reply {i}", "expected": "pass"} for i in range(4)] + [{"input": f"bad{i}", "output": f"bad reply {i}", "expected": "fail"} for i in range(2)]
    client.post(f"/projects/{pid}/traces/import", json={"format": "jsonl", "content": "\n".join(json.dumps(l) for l in lines)})
    return client.post(f"/projects/{pid}/metrics", json={"name": "j", "kind": "llm_judge", "judge_model": "x/m",
                                                         "definition": {"rubric": "Pass if helpful.", "pass_examples": ["yes"], "fail_examples": ["no"]}}).json()["id"]


def test_calibration_reports_chance_corrected_kappa(client, engine):
    mid = _labelled_judge_project(client)
    prov, _ = scripted(lambda p: {"verdict": "pass", "score": 1, "rationale": "r"})  # says pass to everything: 4/6 raw agreement
    res = asyncio.run(metric_plan.calibrate(engine, LLMClient(engine, provider=prov), mid))
    assert res["agreement"] == pytest.approx(4 / 6) and res["kappa"] == pytest.approx(0.0)  # ...but no skill at all


def test_compare_judge_versions_scores_each_prompt_file_against_labels(client, engine, tmp_path, monkeypatch):
    from app.agents import base

    mid = _labelled_judge_project(client)
    base_text = (base.PROMPTS / "judge.v1.md").read_text()
    (tmp_path / "judge.v1.md").write_text(base_text)
    (tmp_path / "judge.v2.md").write_text(base_text + "\nSTRICT-V2\n")
    monkeypatch.setattr(base, "PROMPTS", tmp_path)
    # v1 passes everything; v2 (marked prompt) fails anything whose output says "bad"
    prov, _ = scripted(lambda p: {"verdict": "fail" if "STRICT-V2" in p and "bad reply" in p else "pass", "score": 0, "rationale": "r"})
    out = asyncio.run(metric_plan.compare_judge_versions(engine, LLMClient(engine, provider=prov), mid, ["v1", "v2"]))
    assert out["v1"]["agreement"] == pytest.approx(4 / 6) and out["v1"]["kappa"] == pytest.approx(0.0)
    assert out["v2"]["agreement"] == 1.0 and out["v2"]["kappa"] == pytest.approx(1.0)
