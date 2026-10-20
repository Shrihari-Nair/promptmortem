from sqlmodel import Session, select

from app.config import settings
from app.core.embeddings import embed, scenario_text
from app.core.llm import LLMClient
from app.core.models import Feedback, Scenario, ScenarioCluster
from tests.conftest import make_project, wait_job


def generate_all(client, pid):
    j = wait_job(client, client.post(f"/projects/{pid}/scenarios/generate").json()["job_id"])
    assert j["status"] == "done", j
    return j["result"]


def test_generates_150_plus_clustered_scenarios_with_coverage(client, engine):
    p = make_project(client)
    stats = generate_all(client, p["id"])
    assert stats["scenarios"] >= 150 and stats["clusters"] > 8
    with Session(engine) as s:
        scns = s.exec(select(Scenario)).all()
        assert all(x.cluster_id for x in scns)  # nothing left unclustered
        assert {"diff_targeted", "regression_guard", "adversarial"} <= {x.category for x in scns}
        assert "multilingual" not in {x.category for x in scns}  # description doesn't mention other languages
    cov = client.get(f"/projects/{p['id']}/coverage").json()
    assert sum(sum(r.values()) for r in cov["categories"].values()) == stats["scenarios"]
    assert cov["counts"]["unreviewed"] == stats["scenarios"]
    assert all(c in cov["clusters"] for r in cov["categories"].values() for c in r)  # heatmap columns resolvable


def test_review_queue_is_small_and_covers_every_cluster(client, engine):
    p = make_project(client)
    stats = generate_all(client, p["id"])
    q = client.get(f"/projects/{p['id']}/scenarios", params={"review_queue": "true"}).json()
    assert 0 < len(q) < stats["scenarios"] / 2  # humans never review everything
    assert {x["cluster_id"] for x in q} == {c["id"] for c in _clusters(engine)}
    assert any(x["flagged"] for x in q)  # critic-uncertain cases surface


def _clusters(engine):
    with Session(engine) as s:
        return [c.model_dump() for c in s.exec(select(ScenarioCluster)).all()]


def test_reject_cluster_with_comment_regenerates_different_scenarios(client, engine):
    p = make_project(client)
    generate_all(client, p["id"])
    with Session(engine) as s:
        cl = next(c for c in s.exec(select(ScenarioCluster)).all() if c.category == "long_noisy")
        old_scn = s.exec(select(Scenario).where(Scenario.cluster_id == cl.id)).all()
        keep = old_scn[0]
        old_texts = [scenario_text(x.input) for x in old_scn]
        cid, keep_id, n_old = cl.id, keep.id, len(old_scn)
    client.patch(f"/scenarios/{keep_id}", json={"action": "approve"})  # human-approved ones survive

    comment = "Our users never write long essays. Make them short and messy."
    j = wait_job(client, client.post(f"/clusters/{cid}/reject", json={"comment": comment}).json()["job_id"])
    assert j["status"] == "done" and j["result"]["rejected"] == n_old - 1 and j["result"]["added"] > 0

    with Session(engine) as s:
        fb = s.exec(select(Feedback).where(Feedback.target_type == "cluster")).one()
        assert fb.comment == comment and fb.target_id == cid  # persisted
        assert s.get(Scenario, keep_id).status == "approved"
        new = [x for x in s.exec(select(Scenario).where(Scenario.category == "long_noisy", Scenario.status == "proposed")).all()
               if x.cluster_id != cid]
        assert new, "regeneration produced nothing"
        # new scenarios are not near-copies of rejected ones
        rej = embed(old_texts)
        for x in new:
            assert float((rej @ embed([scenario_text(x.input)])[0]).max()) < settings.dedupe_cosine_threshold
    # rejected cluster's proposed scenarios left the coverage map; approved one remains
    cov = client.get(f"/projects/{p['id']}/coverage").json()
    assert cov["counts"]["rejected"] == n_old - 1


def test_feedback_reaches_generator_prompt(client, engine):
    """The stored comment must appear in the next generation prompt (cluster-relevant first)."""
    from app.pipeline.feedback import feedback_text

    p = make_project(client)
    generate_all(client, p["id"])
    with Session(engine) as s:
        cid = next(c for c in s.exec(select(ScenarioCluster)).all() if c.category == "adversarial").id
    wait_job(client, client.post(f"/clusters/{cid}/reject", json={"comment": "too obviously test-like"}).json()["job_id"])
    with Session(engine) as s:
        txt = feedback_text(s, p["id"], category="adversarial")
        assert "too obviously test-like" in txt and "Rejected examples" in txt
        other = feedback_text(s, p["id"], category="happy_path")
        assert "too obviously test-like" in other  # still included, just lower priority


def test_same_comment_changes_output_vs_no_comment(engine, client):
    """Different feedback => different generator prompt => different scenarios (fake LLM is prompt-deterministic)."""
    import asyncio

    from app.agents import scenario_generator
    from app.agents.schemas import ContextAnalysis, DiffAnalysis

    ctx, diff = ContextAnalysis(task="t"), DiffAnalysis(changes=[])
    c = LLMClient(engine)

    def run(fb):
        return asyncio.run(scenario_generator.generate(c, "fake/m", context=ctx, diff=diff, category="happy_path", n=5,
                                                       seeds=[], feedback=fb, existing=[], variation=0))

    a, b, a2 = run(""), run("make them short"), run("")
    assert [x.input for x in a] != [x.input for x in b] and [x.input for x in a] == [x.input for x in a2]


def test_patch_scenario_actions_and_feedback(client, engine):
    p = make_project(client)
    generate_all(client, p["id"])
    with Session(engine) as s:
        sid = s.exec(select(Scenario)).first().id
    r = client.patch(f"/scenarios/{sid}", json={"action": "approve", "pinned": True}).json()
    assert r["status"] == "approved" and r["reviewed"] and r["pinned"]
    r = client.patch(f"/scenarios/{sid}", json={"input": {"user_message": "edited!"}, "comment": "shorter"}).json()
    assert r["status"] == "edited" and r["input"]["user_message"] == "edited!"
    r = client.patch(f"/scenarios/{sid}", json={"action": "reject", "comment": "unrealistic"}).json()
    assert r["status"] == "rejected"
    with Session(engine) as s:
        assert [f.action for f in s.exec(select(Feedback).where(Feedback.target_id == sid)).all()] == ["approve", "edit", "reject"]
    assert client.patch("/scenarios/nope", json={}).status_code == 404
    assert client.patch(f"/scenarios/{sid}", json={"action": "explode"}).status_code == 422


def test_dataset_becomes_verified_and_user_scenario_and_more_in_category(client, engine):
    p = make_project(client)
    client.post(f"/projects/{p['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nwhy was I charged twice,explain\n"})
    generate_all(client, p["id"])
    with Session(engine) as s:
        ds = s.exec(select(Scenario).where(Scenario.origin == "dataset")).all()
        assert len(ds) == 1 and ds[0].tier == "verified" and ds[0].status == "approved" and ds[0].reviewed
    generate_all(client, p["id"])  # rerun must not import the dataset item twice
    with Session(engine) as s:
        assert len(s.exec(select(Scenario).where(Scenario.origin == "dataset")).all()) == 1
    u = client.post(f"/projects/{p['id']}/scenarios", json={"category": "edge_case", "input": {"user_message": "mine"}}).json()
    assert u["origin"] == "user" and u["reviewed"]
    before = client.get(f"/projects/{p['id']}/coverage").json()["counts"]["unreviewed"]
    j = wait_job(client, client.post(f"/projects/{p['id']}/scenarios/generate", json={"category": "edge_case", "n": 6}).json()["job_id"])
    assert j["status"] == "done" and j["result"]["added"] > 0
    assert client.get(f"/projects/{p['id']}/coverage").json()["counts"]["unreviewed"] > before
    assert client.post(f"/projects/{p['id']}/scenarios/generate", json={"category": "nope"}).status_code == 422


def test_generate_without_analysis_fails_cleanly(client):
    p = client.post("/projects", json={"name": "x", "judge_model_default": "fake/j"}).json()
    j = wait_job(client, client.post(f"/projects/{p['id']}/scenarios/generate").json()["job_id"])
    assert j["status"] == "failed" and "analyze" in j["error"]


def test_reviewer_comment_is_in_the_regeneration_prompt(client, engine):
    import asyncio

    from app.core.fake_llm import fake_provider
    from app.pipeline import planner

    p = make_project(client)
    generate_all(client, p["id"])
    with Session(engine) as s:
        cid = next(c for c in s.exec(select(ScenarioCluster)).all() if c.category == "happy_path").id
    prompts = []

    async def spy(model, messages, params):
        if "agent: scenario_generator" in messages[0]["content"]:
            prompts.append(messages[0]["content"])
        return await fake_provider(model, messages, params)

    asyncio.run(planner.reject_cluster(engine, LLMClient(engine, fake=spy), cid, "ALWAYS-MAKE-THEM-TERSE"))
    assert prompts and all("ALWAYS-MAKE-THEM-TERSE" in x for x in prompts)
    assert any("Reviewer rejected a whole happy_path cluster" in x for x in prompts)


def test_unlabelled_traces_are_real_inputs_but_not_verified(client, engine):
    p = make_project(client)
    client.post(f"/projects/{p['id']}/traces/import", json={"format": "jsonl", "content": '{"input":"why was I double billed"}\n{"input":"cancel please","expected":"confirm cancellation"}'})
    generate_all(client, p["id"])
    with Session(engine) as s:
        rows = {x.input["user_message"]: x for x in s.exec(select(Scenario).where(Scenario.origin == "dataset")).all()}
    plain, labelled = rows["why was I double billed"], rows["cancel please"]
    assert (plain.tier, plain.status, plain.reviewed) == ("synthetic", "proposed", False) and "trace" in plain.tags
    assert (labelled.tier, labelled.status, labelled.reviewed) == ("verified", "approved", True) and labelled.expected_behavior == "confirm cancellation"


def test_pass_fail_trace_labels_calibrate_judges_but_are_not_scenario_expectations(client, engine):
    p = make_project(client)
    client.post(f"/projects/{p['id']}/traces/import", json={"format": "jsonl", "content": '{"input":"refund me now","output":"You will get a full refund.","expected":"fail"}'})
    generate_all(client, p["id"])
    with Session(engine) as s:
        x = next(x for x in s.exec(select(Scenario).where(Scenario.origin == "dataset")).all() if x.input["user_message"] == "refund me now")
    assert (x.tier, x.status, x.reviewed, x.expected_behavior) == ("synthetic", "proposed", False, "")  # the label 'fail' is not a behaviour to test for
