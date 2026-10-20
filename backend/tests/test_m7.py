import json
import os
import shutil
import subprocess

import pytest
import yaml
from sqlmodel import Session, select

from app.adapters.promptfoo_export import check_assertion, provider_id
from app.core.models import Metric, PromptVersion, Scenario
from app.pipeline.metrics_engine import run_programmatic
from tests.golden import golden
from tests.parity import INPUTS, METRICS, seed_parity


def test_provider_mapping():
    assert provider_id("gemini/gemini-2.5-flash")[0] == "google:gemini-2.5-flash" and "GOOGLE_API_KEY" in provider_id("gemini/gemini-2.5-flash")[1]
    assert provider_id("openai/gpt-4o")[0] == "openai:gpt-4o" and provider_id("gpt-4o")[0] == "openai:gpt-4o"
    assert provider_id("anthropic/claude-sonnet-4-5")[0] == "anthropic:messages:claude-sonnet-4-5" and provider_id("claude-sonnet-4-5")[0] == "anthropic:messages:claude-sonnet-4-5"
    assert provider_id("ollama/llama3")[0] == "ollama:chat:llama3" and provider_id("fake/app")[0] == "echo"
    pid, note = provider_id("weird/model-x")
    assert pid == "weird:model-x" and "unknown provider" in note


def test_export_shape_for_the_golden_project(client, engine):
    pid = golden(client, engine, "planted")
    r = client.get(f"/projects/{pid}/export/promptfoo")
    assert r.status_code == 200 and "filename=" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("# Promptfoo config exported by Prompt Mortem.") and "promptfoo@latest eval" in text and "--no-cache" in text
    cfg = yaml.safe_load(text)
    assert json.loads(json.dumps(cfg)) == client.get(f"/projects/{pid}/export/promptfoo", params={"format": "json"}).json()["config"]
    with Session(engine) as s:
        pv = {v.label: v.text for v in s.exec(select(PromptVersion)).all()}
        scn = {x.id: x for x in s.exec(select(Scenario)).all()}
    assert [(p["label"], p["raw"]) for p in cfg["prompts"]] == [("old", pv["old"]), ("new", pv["new"])]  # {{var}} syntax is shared with Promptfoo
    assert cfg["providers"] == [{"id": "echo"}] and cfg["defaultTest"]["options"]["provider"] == "echo" and cfg["evaluateOptions"]["repeat"] == 3
    assert len(cfg["tests"]) == 60
    for t in cfg["tests"]:  # scenario inputs are reproduced exactly
        x = scn[t["metadata"]["scenario_id"]]
        assert t["vars"] == x.input and t["metadata"]["severity"] == x.severity and t["metadata"]["tier"] == "synthetic" or t["metadata"]["tier"] == x.tier
    by = lambda tag: next(t for t in cfg["tests"] if tag in t["metadata"]["tags"])
    refund, explain, chores = by("refund"), by("length_sensitive"), by("account")
    names = lambda t: [a["metric"] for a in t["assert"]]
    assert set(names(refund)) == {"Under 120 words", "No refund over-promise (regex)", "No refund over-promise (judge)", "No regression vs previous output"}
    assert set(names(chores)) == {"Under 120 words", "No regression vs previous output"}  # applies_to filters are honoured per test
    types = {a["metric"]: a for a in refund["assert"]}
    assert types["Under 120 words"] == {"type": "javascript", "value": "output.trim().split(/\\s+/).filter(Boolean).length <= 120", "metric": "Under 120 words", "weight": 1.0}
    assert types["No refund over-promise (regex)"]["type"] == "javascript" and "new RegExp(\"full refund\", 'i')" in types["No refund over-promise (regex)"]["value"] and types["No refund over-promise (regex)"]["weight"] == 2.0
    judge = types["No refund over-promise (judge)"]
    assert judge["type"] == "llm-rubric" and "does not promise a refund" in judge["value"] and "PASS example" in judge["value"] and "FAIL example" in judge["value"]
    assert types["No regression vs previous output"]["type"] == "select-best"  # pairwise regression metric compares the two prompts' outputs


def test_scope_plan_vs_all_and_errors(client, engine):
    pid = golden(client, engine, "planted", tier="lean")
    n = lambda scope: len(client.get(f"/projects/{pid}/export/promptfoo", params={"format": "json", "scope": scope}).json()["config"]["tests"])
    assert n("plan") == 20 and n("all") == 60  # lean tier keeps the protected + top-priority cases
    with Session(engine) as s:
        sid = s.exec(select(Scenario)).first().id
        x = s.get(Scenario, sid)
        x.status = "rejected"
        s.add(x)
        s.commit()
    assert n("all") == 59  # rejected scenarios are never exported
    assert client.get("/projects/nope/export/promptfoo").status_code == 404
    assert client.get(f"/projects/{pid}/export/promptfoo", params={"format": "xml"}).status_code == 422
    empty = client.post("/projects", json={"name": "x"}).json()["id"]
    assert client.get(f"/projects/{empty}/export/promptfoo").status_code == 422  # no prompts yet


def test_language_is_is_reported_not_silently_dropped(client, engine):
    pid = seed_parity(engine)
    client.post(f"/projects/{pid}/metrics", json={"name": "english", "kind": "programmatic", "definition": {"checks": [{"type": "language_is", "value": "en"}, {"type": "max_words", "value": 50}]}})
    j = client.get(f"/projects/{pid}/export/promptfoo", params={"format": "json", "scope": "all"}).json()
    assert any("language_is" in w and "english" in w for w in j["warnings"])
    assert "# NOTE: metric 'english': check 'language_is'" in client.get(f"/projects/{pid}/export/promptfoo", params={"scope": "all"}).text
    t = j["config"]["tests"][0]
    assert [a["metric"] for a in t["assert"]].count("english") == 1  # the other check of that metric IS exported
    assert check_assertion({"type": "language_is", "value": "en"}, None) is None


def promptfoo_bin():
    for c in (os.environ.get("PROMPTFOO_BIN"), shutil.which("promptfoo")):
        if c and os.path.exists(c):
            return c
    return None


@pytest.mark.skipif(promptfoo_bin() is None, reason="set PROMPTFOO_BIN=/path/to/promptfoo (npm i promptfoo) to run the parity check")
def test_exported_config_runs_in_promptfoo_and_agrees_with_our_engine(client, engine, tmp_path):
    """M7 acceptance: the exported config runs in Promptfoo and reproduces scenario inputs and the assertions of every
    programmatic metric. Promptfoo's echo provider returns the rendered prompt, so both sides judge the same strings."""
    pid = seed_parity(engine)
    (tmp_path / "promptfooconfig.yaml").write_text(client.get(f"/projects/{pid}/export/promptfoo", params={"scope": "all"}).text)
    env = {**os.environ, "PROMPTFOO_DISABLE_TELEMETRY": "1", "PROMPTFOO_DISABLE_UPDATE": "1"}
    subprocess.run([promptfoo_bin(), "eval", "-c", "promptfooconfig.yaml", "--no-cache", "--output", "out.json", "--no-progress-bar"],
                   cwd=tmp_path, env=env, timeout=300, capture_output=True)  # exit code 100 just means some assertions failed
    results = json.loads((tmp_path / "out.json").read_text())["results"]["results"]
    assert len(results) == len(INPUTS) * 2 * 3  # tests x prompts x repeats
    # The rendered prompt (echoed back) is exactly our scenario input, JSON text included. (Promptfoo's results file shows `vars`
    # in a compacted form for JSON-looking strings; that is only how it reports them, not what the model would receive.)
    assert {r["response"]["output"] for r in results} == set(INPUTS)
    outcomes: dict[str, set] = {}
    for r in results:
        out = r["response"]["output"]
        comps = r["gradingResult"]["componentResults"]
        for name, d in METRICS:
            ours, _ = run_programmatic({"aggregate": "all_pass", **d}, out)
            agg = d.get("aggregate", "all_pass")
            if agg == "mean":
                assert r["namedScores"][name] == pytest.approx(ours), (name, out)
            elif agg == "any_pass":
                assert (r["namedScores"][name] > 0) == (ours == 1.0), (name, out)
            else:
                theirs = all(c["pass"] for c in comps if (c.get("assertion") or {}).get("metric") == name)
                assert theirs == (ours == 1.0), (name, out)
            outcomes.setdefault(name, set()).add(round(ours, 2))
    assert all(len(v) > 1 for v in outcomes.values())  # every metric saw passing AND failing outputs: the agreement is not vacuous
