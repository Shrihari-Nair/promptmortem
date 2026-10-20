import asyncio

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.core.embeddings import scenario_text
from app.core.llm import LLMClient
from app.core.models import CostEstimate, LLMCall, Metric, Plan, Project, PromptVersion, Scenario
from app.pipeline import cost_estimator as ce
from app.pipeline import trimmer
from app.pipeline.execute import run_target
from app.pipeline.metric_plan import evaluate, is_pairwise
from app.pipeline.metrics_engine import metric_applies
from app.pipeline.prioritizer import is_protected, rank
from tests.conftest import make_project, wait_job
from tests.test_m2 import generate_all


def ready(client, **kw):
    """Project through stages 1-3 on the fake model."""
    p = make_project(client, **kw)
    generate_all(client, p["id"])
    wait_job(client, client.post(f"/projects/{p['id']}/metrics/generate").json()["job_id"])
    return p


def S(pid, text, category="happy_path", sev=3, rel=0.5, **kw):
    return Scenario(project_id=pid, category=category, input={"user_message": text}, severity=sev, diff_relevance=rel, **kw)


def test_prioritizer_weights_and_uniqueness(engine):
    with Session(engine) as s:
        pid = Project(name="p", target_model="fake/t", judge_model_default="fake/j")
        s.add(pid)
        s.commit()
        a = S(pid.id, "why was I charged twice in march for my pro plan", sev=3, rel=0.9)
        dup = S(pid.id, "why was I charged twice in march for my pro plan?", sev=3, rel=0.89)  # near-copy, slightly lower
        b = S(pid.id, "zebra xylophone quantum harmonica volcano", sev=3, rel=0.85)
        low = S(pid.id, "hello there", sev=1, rel=0.0)
        s.add_all([a, dup, b, low])
        s.commit()
        order = [x.id for x in rank(s, pid.id)]
        assert order == [a.id, b.id, dup.id, low.id]  # b outranks the near-duplicate despite a lower base score
        w = settings.priority_weights
        assert s.get(Scenario, a.id).priority_score == pytest.approx(w["diff_relevance"] * 0.9 + w["severity"] * 3 / 5 + w["uniqueness"] * 1.0)
        pinned = S(pid.id, "hello there again", sev=1, rel=0.0, pinned=True)
        s.add(pinned)
        s.commit()
        assert [x.id for x in rank(s, pid.id)].index(pinned.id) < [x.id for x in rank(s, pid.id)].index(low.id)


def test_trimmer_protects_floor_and_reports_exact_coverage_lost(engine):
    with Session(engine) as s:
        p = Project(name="p", target_model="fake/t", judge_model_default="fake/j")
        s.add(p)
        s.commit()
        xs = [S(p.id, f"alpha {i} unique words {i * 7}", "happy_path", sev=2, rel=0.9 - i * 0.05) for i in range(6)]
        xs += [S(p.id, f"edge {i} other stuff {i * 13}", "edge_case", sev=2, rel=0.3 - i * 0.01) for i in range(4)]
        xs[7].severity = 5  # protected, low priority
        xs[0].pinned = True
        s.add_all(xs)
        s.commit()
        ranked = rank(s, p.id)
        lean = trimmer.select_tier(ranked, 0.3)  # round(10*0.3) = 3 slots
        ids = {x.id for x in lean}
        assert xs[7].id in ids and xs[0].id in ids and len(lean) == 3  # protected kept, one slot left for the best other
        full = trimmer.select_tier(ranked, 1.0)
        assert len(full) == 10 and trimmer.coverage_lost(s, ranked, full) == {"categories": {}, "clusters": {}, "clusters_dropped": []}
        lost = trimmer.coverage_lost(s, ranked, lean)["categories"]
        kept_h = sum(x.category == "happy_path" for x in lean)
        assert lost["happy_path"] == f"-{round(100 * (6 - kept_h) / 6)}%" and lost["edge_case"] == "-75%"  # 1 of 4 edge kept
        small = trimmer.select_tier(ranked, 0.0)  # protected only
        assert {x.id for x in small} == {xs[7].id, xs[0].id}


def test_estimate_three_tiers_with_ranges_and_coverage_lost(client, engine):
    p = ready(client)
    r = client.post(f"/projects/{p['id']}/plan/estimate")
    assert r.status_code == 200, r.text
    est = r.json()
    full, bal, lean = est["tiers"]
    assert [t["tier"] for t in est["tiers"]] == ["full", "balanced", "lean"]
    for t in est["tiers"]:
        c = t["cost"]
        assert 0 < c["low"] <= c["expected"] <= c["high"] and t["suggested_cap"] == c["high"]
    assert full["cost"]["expected"] > bal["cost"]["expected"] > lean["cost"]["expected"]
    assert full["n_scenarios"] > bal["n_scenarios"] > lean["n_scenarios"] and full["coverage_lost"]["categories"] == {}
    assert bal["coverage_lost"]["categories"] and lean["coverage_lost"]["categories"]
    assert lean["repeats"] == 2 and full["repeats"] == 3
    # pinned / severity>=4 scenarios are never auto-trimmed
    with Session(engine) as s:
        prot = {x.id for x in s.exec(select(Scenario)).all() if is_protected(x) and x.status != "rejected"}
    assert est["protected_scenarios"] == len(prot) > 0
    for t in est["tiers"]:
        assert prot <= set(t["scenario_ids"])
    assert {l["id"] for l in est["levers"]} == set(ce.LEVERS)
    with Session(engine) as s:  # persisted as cost_estimate rows
        assert {c.tier for c in s.exec(select(CostEstimate)).all()} == {"full", "balanced", "lean"}
        assert s.exec(select(Plan)).first().stage == "budget"


def test_levers_change_the_estimate_in_the_right_direction(client):
    p = ready(client)
    url = f"/projects/{p['id']}/plan/estimate"
    exp = lambda lv: client.post(url, json={"levers": lv}).json()["tiers"][0]["cost"]["expected"]
    off = {k: False for k in ce.LEVERS}
    none = exp(off)
    assert exp({**off, "early_stopping": True}) < none  # quiet cases stop at 2 of the planned 3
    assert exp({**off, "adaptive_repeats": True}) > none  # contested cases are confirmed with up to 5 repeats: costs more, and says so
    savings = {l["id"]: l["saving_expected"] for l in client.post(url, json={"levers": off}).json()["levers"]}
    assert savings["early_stopping"] > 0 and savings["adaptive_repeats"] < 0  # honest: confirmation is not free
    assert savings["programmatic_first"] >= 0 and savings["cheap_judge_easy_rubrics"] >= 0


def test_probe_is_reused_and_reprobed_when_inputs_change(client, engine):
    p = ready(client)
    url = f"/projects/{p['id']}/plan/estimate"
    client.post(url)
    with Session(engine) as s:
        n1 = len(s.exec(select(LLMCall)).all())
        fp1 = s.exec(select(Plan)).first().probe["fingerprint"]
    client.post(url)
    with Session(engine) as s:
        assert len(s.exec(select(LLMCall)).all()) == n1  # no new calls: probe reused
        m = next(m for m in s.exec(select(Metric)).all() if m.name == "Under 120 words")
    client.patch(f"/metrics/{m.id}", json={"definition": {"checks": [{"type": "max_words", "value": 50}]}})
    client.post(url)  # fingerprint changed -> reprobe (judge/target calls are cached, so cost is ~0 but the probe reruns)
    with Session(engine) as s:
        assert s.exec(select(Plan)).first().probe["fingerprint"] != fp1  # stale probe was replaced
    assert client.post(url, json={"reprobe": True}).status_code == 200


def test_probe_calls_are_cached_for_the_runner(client, engine):
    p = ready(client)
    client.post(f"/projects/{p['id']}/plan/estimate")
    with Session(engine) as s:
        pv = {v.label: v.text for v in s.exec(select(PromptVersion)).all()}
        xs = sorted(s.exec(select(Scenario).where(Scenario.status != "rejected")).all(), key=lambda x: (len(scenario_text(x.input)), x.id))
        first = xs[0]  # the probe always includes the shortest scenario
        proj = s.get(Project, p["id"])
    c = LLMClient(engine)
    assert asyncio.run(run_target(c, proj.target_model, pv["old"], first.input)).cached
    assert asyncio.run(run_target(c, proj.target_model, pv["new"], first.input)).cached


def test_estimate_matches_actual_within_25_percent(client, engine):
    """Simulate the full run (all repeats, both prompts, every applicable judge) and compare to the estimate (levers off)."""
    p = ready(client)
    est = client.post(f"/projects/{p['id']}/plan/estimate", json={"levers": {k: False for k in ce.LEVERS}}).json()
    tier = est["tiers"][1]  # balanced
    with Session(engine) as s:
        proj = s.get(Project, p["id"])
        pv = {v.label: v.text for v in s.exec(select(PromptVersion)).all()}
        metrics = ce.active_metrics(s, p["id"])
        chosen = [s.get(Scenario, i) for i in tier["scenario_ids"]]
    c = ce.Metered(LLMClient(engine))

    async def run():
        for x in chosen:
            for rep in range(tier["repeats"]):
                o = await run_target(c, proj.target_model, pv["old"], x.input, repeat_idx=rep)
                n = await run_target(c, proj.target_model, pv["new"], x.input, repeat_idx=rep)
                for m in metrics:
                    if m.kind == "llm_judge" and metric_applies(m.applies_to, x.category, x.tags):
                        await evaluate(c, proj.judge_model_default, m, inp=x.input, output=n.text, old_output=o.text)
                        if not is_pairwise(m):
                            await evaluate(c, proj.judge_model_default, m, inp=x.input, output=o.text)

    asyncio.run(run())
    actual, expected = sum(c.full), tier["cost"]["expected"]
    print(f"\nESTIMATE expected={expected:.6f} actual={actual:.6f} low={tier['cost']['low']:.6f} high={tier['cost']['high']:.6f}")
    assert abs(expected - actual) / actual < 0.25, (expected, actual)
    assert tier["cost"]["low"] <= actual <= tier["cost"]["high"] * 1.25


def test_trim_floor_rule_and_confirm(client):
    p = ready(client)
    pid = p["id"]
    rows = client.get(f"/projects/{pid}/plan/ranking").json()
    assert rows == sorted(rows, key=lambda r: -r["priority"]) and any(r["protected"] for r in rows)
    unprot = [r["id"] for r in rows if not r["protected"]][:10]
    r = client.post(f"/projects/{pid}/plan/trim", json={"scenario_ids": unprot})
    assert r.status_code == 409 and r.json()["detail"]["protected_dropped"]  # dropping protected needs explicit confirmation
    r = client.post(f"/projects/{pid}/plan/trim", json={"scenario_ids": unprot, "confirm_drop_protected": True, "repeats": 2})
    assert r.status_code == 200
    custom = r.json()["tiers"][-1]
    assert custom["tier"] == "custom" and custom["n_scenarios"] == 10 and custom["repeats"] == 2

    # confirm a standard tier: cap defaults to the tier's high estimate, selection is frozen
    c = client.post(f"/projects/{pid}/plan/confirm", json={"tier": "balanced"}).json()
    est = client.post(f"/projects/{pid}/plan/estimate").json()["tiers"][1]
    assert c["cap"] == est["cost"]["high"] and c["scenario_ids"] == est["scenario_ids"] and c["warning"] is None
    plan = client.get(f"/projects/{pid}").json()
    assert client.get(f"/projects/{pid}/plan").json()["stage"] == "ready"
    low_cap = client.post(f"/projects/{pid}/plan/confirm", json={"tier": "lean", "cap": 1e-9}).json()
    assert low_cap["warning"] and low_cap["cap"] == 1e-9
    assert client.post(f"/projects/{pid}/plan/confirm", json={"tier": "custom"}).status_code == 422
    assert client.post(f"/projects/{pid}/plan/confirm", json={"tier": "huge"}).status_code == 422
    assert client.post(f"/projects/{pid}/plan/confirm", json={"tier": "lean", "cap": 0}).status_code == 422


def test_estimate_requires_planning_first(client):
    p = make_project(client)  # analysed, but no scenarios
    r = client.post(f"/projects/{p['id']}/plan/estimate")
    assert r.status_code == 422 and "plan scenarios first" in r.json()["detail"]


def test_bad_json_schema_is_422_not_500(client):
    p = make_project(client)
    r = client.post(f"/projects/{p['id']}/metrics", json={"name": "s", "kind": "programmatic", "definition": {
        "checks": [{"type": "json_schema", "schema": {"type": "nonsense"}}]}})
    assert r.status_code == 422
