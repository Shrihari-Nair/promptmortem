"""M6 acceptance: on the golden benchmark the report flags a planted regression AND a planted improvement, and marks a
no-op prompt tweak Unchanged (noise-adjusted)."""
import pytest
from sqlmodel import Session, select

from app.core.models import LLMCall, Metric, Report, RunResult, Scenario
from tests.conftest import wait_job
from tests.golden import golden, run_to_end


def report(client, engine, variant, noise=0.05, **kw):
    pid = golden(client, engine, variant, noise, **kw)
    rid, j = run_to_end(client, pid)
    assert j["result"]["status"] == "done", j
    r = client.get(f"/runs/{rid}/report").json()
    return rid, r["id"], r["summary"]


def test_gate_planted_regression_and_improvement_are_both_flagged(client, engine):
    rid, rep_id, rep = report(client, engine, "planted")
    h = rep["headline"]
    # the planted regression: refund over-promising, severity 4, grouped in its cluster, with side-by-side evidence
    assert h["broken"] >= 15 and {r["cluster"] for r in rep["regressions"]} == {"refund questions"}
    with Session(engine) as s:  # deep-dive neighbours are generated (random severities); the evidence below is about the benchmark's own cases
        own = {x.id for x in s.exec(select(Scenario)).all() if "deep_dive" not in x.tags}
    reg = next(r for r in rep["regressions"] if r["scenario_id"] in own and r["metric_name"] == "No refund over-promise (regex)")
    assert "full refund" in reg["new_output"].lower() and "full refund" not in reg["old_output"].lower() and reg["severity"] == 4
    assert reg["old_pass_rate"] is not None and reg["new_pass_rate"] < reg["old_pass_rate"] or reg["preference_for_new"] < 0
    assert h["level"] == "block" and h["recommendation"].startswith("block") and "refund questions" in h["recommendation"]
    # the planted improvement: 'explain' answers got shorter than 120 words
    assert h["fixed"] >= 15 and {r["cluster"] for r in rep["improvements"]} == {"explain questions"}
    assert all(r["metric_name"] in ("Under 120 words", "No regression vs previous output") for r in rep["improvements"])
    # untouched behaviour stays unchanged
    chores = next(g for g in rep["per_cluster"] if g["name"] == "account chores")
    assert chores["fixed"] == chores["broken"] == 0 and chores["unchanged"] == 20
    # aggregate tests agree with the per-case flags
    pm = {m["name"]: m for m in rep["per_metric"]}
    assert pm["Under 120 words"]["test"]["p"] < 0.01 and pm["Under 120 words"]["mean_diff"] > 0.2
    assert pm["No refund over-promise (regex)"]["test"]["p"] < 0.01 and pm["No refund over-promise (regex)"]["mean_diff"] < -0.5
    # honest scope stamp
    sc = rep["scope"]
    assert sc["tier"] == "full" and sc["complete"] and sc["tested"] >= 60 and sc["verified_share"] == 0.0
    assert 0.8 <= sc["reviewed_share"] < 1.0  # the deep-dive neighbours were not reviewed by a human
    assert set(sc["noise_band"]) == set(sc["detection_threshold"]) and sc["comparisons"] > 100
    assert all(x is None or 0 < x <= 1 for k, x in sc["detection_threshold"].items() if pm[k]["test_kind"] != "continuous")
    assert sc["judge_calls_skipped"] > 0  # programmatic_first skipped judging outputs that already failed a check, and says so
    refund_judge = pm["No refund over-promise (judge)"]
    assert refund_judge["n"] < refund_judge["applicable"]  # gated pairs are dropped, not silently counted as 'unchanged'
    assert rep["cost"]["spent"] > 0 and rep["cost"]["estimated"] > 0 and rep["judge_reliability"]["pairwise_judgments"] > 0


def test_gate_noop_tweak_is_unchanged(client, engine):
    _, _, rep = report(client, engine, "noop")
    h = rep["headline"]
    assert h["fixed"] == 0 and h["broken"] == 0 and h["unchanged"] >= 57 and h["level"] == "ok"  # noise may leave a case 'suggestive'
    assert h["unchanged"] + h["inconclusive"] == 60
    lo, hi = h["net_effect"]["ci95"]
    assert lo <= 0 <= hi  # no effect: the interval covers zero
    assert not rep["regressions"] and not rep["improvements"]
    assert all(m["test"]["p"] > 0.05 for m in rep["per_metric"] if "test" in m)


def test_noise_is_injected_and_measured_old_vs_old(client, engine):
    """Same prompts, only the injected noise differs: the measured old-vs-old band must grow with it."""
    _, _, quiet = report(client, engine, "noop", noise=0.0)
    _, _, loud = report(client, engine, "noop", noise=0.2)
    name = "No refund over-promise (regex)"
    assert loud["scope"]["noise_band"][name] > quiet["scope"]["noise_band"][name]
    assert quiet["headline"]["broken"] == loud["headline"]["broken"] == 0  # and noise alone is still not a regression


def test_pure_noise_is_inconclusive_with_a_warning_not_a_verdict(client, engine):
    rid, _, rep = report(client, engine, "noop", noise=0.5)  # refund decisions are coin flips in both prompts
    assert any("Noise is too high" in w and "repeats" in w for w in rep["warnings"])
    assert rep["headline"]["broken"] == rep["headline"]["fixed"] == 0 and rep["headline"]["inconclusive"] > 0
    assert rep["scope"]["noise_band"]["No refund over-promise (regex)"] >= 0.5


def test_human_verdict_override_changes_the_report_and_is_stored(client, engine):
    rid, rep_id, rep = report(client, engine, "planted")
    sid, before = rep["regressions"][0]["scenario_id"], rep["headline"]["broken"]
    r = client.post(f"/reports/{rep_id}/verdict-override", json={"scenario_id": sid, "verdict": "fine", "comment": "customer asked politely, ok"})
    assert r.status_code == 200
    new = r.json()["summary"]
    assert new["headline"]["broken"] == before - 1 and sid not in {x["scenario_id"] for x in new["regressions"]}
    assert new["overrides"][sid]["comment"] == "customer asked politely, ok"
    assert client.post(f"/reports/{rep_id}/verdict-override", json={"scenario_id": sid, "verdict": "meh"}).status_code == 422
    assert client.get(f"/runs/{rid}/report").json()["summary"]["headline"]["broken"] == before - 1  # persists across rebuilds
    sid2 = new["improvements"][0]["scenario_id"]
    broken = client.post(f"/reports/{rep_id}/verdict-override", json={"scenario_id": sid2, "verdict": "broken"}).json()["summary"]
    assert broken["headline"]["broken"] == before and sid2 in {x["scenario_id"] for x in broken["regressions"]}


def test_rescore_uses_edited_metrics_without_regenerating(client, engine):
    rid, rep_id, rep = report(client, engine, "planted")
    with Session(engine) as s:
        n_results, n_calls = len(s.exec(select(RunResult)).all()), len(s.exec(select(LLMCall)).all())
        m = next(m for m in s.exec(select(Metric)).all() if m.name == "Under 120 words")
    assert rep["per_metric"][[x["name"] for x in rep["per_metric"]].index("Under 120 words")]["new_mean"] == 1.0
    client.patch(f"/metrics/{m.id}", json={"definition": {"checks": [{"type": "max_words", "value": 30}]}})  # now nothing passes
    j = wait_job(client, client.post(f"/reports/{rep_id}/rescore").json()["job_id"])
    assert j["status"] == "done" and j["result"]["rescored"] > 0
    with Session(engine) as s:
        assert len(s.exec(select(RunResult)).all()) == n_results  # no output was regenerated
        assert len(s.exec(select(LLMCall)).all()) <= n_calls  # and nothing new was paid for (programmatic edit; judges cached)
    new = client.get(f"/runs/{rid}/report").json()["summary"]
    row = next(x for x in new["per_metric"] if x["name"] == "Under 120 words")
    assert row["new_mean"] == 0.0 and row["old_mean"] == 0.0
    assert client.post("/reports/nope/rescore").status_code == 404


def test_rescore_is_atomic_when_it_needs_budget_it_does_not_have(client, engine):
    """An edited judge rubric needs fresh judge calls; if the cap blocks them, the existing scores must stay intact."""
    rid, rep_id, _ = report(client, engine, "planted")
    with Session(engine) as s:
        before = len(s.exec(select(RunResult)).all())
        from app.core.models import Run, Score

        run = s.get(Run, rid)
        run.budget_cap = run.spent  # nothing left
        s.add(run)
        s.commit()
        n_scores = len(s.exec(select(Score)).all())
        m = next(m for m in s.exec(select(Metric)).all() if m.name == "No regression vs previous output")
    r = client.patch(f"/metrics/{m.id}", json={"definition": {"mode": "pairwise", "output": "binary", "pass_examples": [], "fail_examples": [],
                                                              "rubric": "Which output is more polite and complete? Penalise anything that is missing."}})
    assert r.status_code == 200 and "polite" in r.json()["definition"]["rubric"]  # the edit really was accepted
    j = wait_job(client, client.post(f"/reports/{rep_id}/rescore").json()["job_id"])
    assert j["status"] == "paused"  # budget error, surfaced
    with Session(engine) as s:
        assert len(s.exec(select(Score)).all()) == n_scores  # untouched


def test_exports_markdown_and_json(client, engine):
    rid, rep_id, rep = report(client, engine, "planted")
    md = client.get(f"/reports/{rep_id}/export", params={"format": "md"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    text = md.text
    for needle in ("## Scope stamp", "## Regressions", "## Improvements", "## Per metric", "## Judge reliability", "refund questions",
                   "block:", "Reviewed by a human", "detection threshold"):
        assert needle in text, needle
    assert text.index("## Regressions") < text.index("## Improvements")  # regressions first
    js = client.get(f"/reports/{rep_id}/export").json()
    assert js["headline"] == rep["headline"] and js["scope"]["tier"] == "full"
    assert client.get(f"/reports/{rep_id}/export", params={"format": "pdf"}).status_code == 422


def test_report_exists_for_a_run_with_no_results_yet(client, engine):
    from app.pipeline import runner

    pid = golden(client, engine, "noop")
    rid = runner.create_run(engine, pid)
    rep = client.get(f"/runs/{rid}/report").json()["summary"]
    assert rep["scope"]["tested"] == 0 and rep["headline"]["level"] == "incomplete"


def test_real_regression_is_not_reported_ok_when_cases_are_only_suggestive(client, engine):
    """At 15% noise, single cases are mostly 'inconclusive'; the pooled per-metric evidence must still stop an 'ok'."""
    _, _, rep = report(client, engine, "planted", noise=0.15, cap=50.0)
    h = rep["headline"]
    assert h["level"] in ("block", "caution") and h["level"] != "ok"
    assert h["inconclusive"] > 0  # individual cases were not all conclusive...
    assert any(m["name"] == "No refund over-promise (regex)" for m in h["metric_regressions"])  # ...but the aggregate test was
    assert h["fixed"] >= 15  # the deterministic improvement is still found


def test_estimate_range_covers_the_actual_spend(client, engine):
    rid, _, rep = report(client, engine, "noop")
    c = rep["cost"]
    assert 0.5 * c["estimated"] <= c["spent"] <= c["estimated_high"]
