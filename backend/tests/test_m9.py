import asyncio
import json
import os

import pytest
from sqlmodel import Session, select

from app.api.deps import get_client
from app.config import settings
from app.core.fake_llm import fake_provider
from app.core.llm import LLMClient
from app.core.models import Run
from app.core.secrets import configured_keys, redact
from app.main import app
from app.pipeline import runner
from tests.conftest import make_project, wait_job
from tests.golden import golden

KEY = "AIzaSyD-FAKE-KEY-0123456789abcdefghij"  # secret-scan: allow (a deliberately fake key used to test redaction)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "t0ken-for-tests")
    return "t0ken-for-tests"


def test_api_token_protects_everything_except_health(client, token):
    assert client.get("/health").status_code == 200  # open: the UI and scripts use it to see what is configured
    r = client.post("/projects", json={"name": "x"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer" and "token" in r.json()["detail"]
    assert client.post("/projects", json={"name": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/projects", json={"name": "x"}, headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/projects", json={"name": "x"}, headers={"X-API-Token": token}).status_code == 200
    pid = client.post("/projects", json={"name": "x"}, headers={"X-API-Token": token}).json()["id"]
    assert client.get(f"/projects/{pid}").status_code == 401
    assert client.get(f"/projects/{pid}", params={"token": token}).status_code == 200  # EventSource / download links cannot set headers
    assert client.get(f"/projects/{pid}", params={"token": token + "x"}).status_code == 401
    assert client.get("/jobs/nope").status_code == 401  # even unknown routes do not leak existence
    assert client.options("/projects").status_code != 401  # CORS preflight must pass


def test_no_token_configured_means_open(client):
    assert settings.api_token is None and client.post("/projects", json={"name": "x"}).status_code == 200
    assert client.get("/health").json()["auth_required"] is False


def test_health_reports_configuration_without_values(client, monkeypatch):
    for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    h = client.get("/health").json()
    assert h["keys"] == {"gemini": False, "openai": False, "anthropic": False} and h["models"]["target"] == settings.target_model
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    r = client.get("/health")
    assert r.json()["keys"]["gemini"] is True and KEY not in r.text  # a boolean, never the value
    monkeypatch.setenv("GEMINI_API_KEY", "short")
    assert configured_keys()["gemini"] is False  # too short to be a real key


def test_redaction(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "s3cr3t-value-123456")
    cases = [f"bad key {KEY} rejected", "url https://x.googleapis.com/v1?key=abc123SECRET&alt=json", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
             "openai sk-proj-abcdefghijklmnop1234", "echoed s3cr3t-value-123456 back", "api_key=hunter2hunter2 token=zzz", "{'api_key': 'dictrepr-secret-9876', 'model': 'x'}", "password: p4ssw0rd!"]  # secret-scan: allow (a deliberately fake key used to test redaction)
    for c in cases:
        out = redact(c)
        assert "[REDACTED]" in out, c
        for secret in (KEY, "abc123SECRET", "abcdefghijklmnopqrstuvwxyz012345", "sk-proj-abcdefghijklmnop1234", "s3cr3t-value-123456", "hunter2hunter2", "dictrepr-secret-9876", "p4ssw0rd"):  # secret-scan: allow (a deliberately fake key used to test redaction)
            assert secret not in out
    assert redact("ordinary error: 404 not found, retry in 5s") == "ordinary error: 404 not found, retry in 5s"
    assert redact("primary key violated; the key to success") == "primary key violated; the key to success"  # no false positives on prose
    assert redact("") == ""


def leaky(after=0):
    n = {"c": 0}

    async def provider(model, messages, params):
        n["c"] += 1
        if n["c"] > after:
            raise RuntimeError(f"401 from https://generativelanguage.googleapis.com/v1/models?key={KEY} (bad key {KEY})")
        return await fake_provider(model, messages, params)

    return provider


def test_provider_errors_are_scrubbed_before_they_are_stored_or_returned(client, engine):
    app.dependency_overrides[get_client] = lambda: LLMClient(engine, fake=leaky())
    try:
        p = client.post("/projects", json={"name": "x", "target_model": "fake/a", "judge_model_default": "fake/j", "app_description": "d"}).json()
        for label in ("old", "new"):
            client.post(f"/projects/{p['id']}/prompts", json={"label": label, "text": f"{label} {{{{user_message}}}}"})
        j = wait_job(client, client.post(f"/projects/{p['id']}/analyze").json()["job_id"])
    finally:
        app.dependency_overrides.pop(get_client, None)
    assert j["status"] == "failed" and "[REDACTED]" in j["error"] and KEY not in json.dumps(j)
    # a paused run keeps its reason, scrubbed
    pid = golden(client, engine, "noop")
    rid = runner.create_run(engine, pid)
    asyncio.run(runner.execute_run(engine, LLMClient(engine, fake=leaky(after=40)), rid))
    st = client.get(f"/runs/{rid}")
    assert st.json()["status"] == "paused" and "[REDACTED]" in st.json()["pause_reason"] and KEY not in st.text
    with Session(engine) as s:
        assert KEY not in (s.get(Run, rid).pause_reason or "")


def test_equals_metric_only_for_short_categorical_expected_values(client):
    from tests.test_m2 import generate_all

    long_gold = make_project(client)
    client.post(f"/projects/{long_gold['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nhow do I cancel,You can cancel any time from Settings > Billing. We will keep your data for 30 days after that.\n"})
    generate_all(client, long_gold["id"])
    wait_job(client, client.post(f"/projects/{long_gold['id']}/metrics/generate").json()["job_id"])
    ms = client.get(f"/projects/{long_gold['id']}/metrics").json()
    assert not [m for m in ms if m["kind"] == "programmatic" and m["definition"]["checks"][0].get("value") == "$expected"]  # free text: not an exact-match metric
    short = make_project(client)
    client.post(f"/projects/{short['id']}/dataset/import", json={"format": "csv", "content": "input,expected\nis this a refund request,yes\nis this spam,no\n"})
    generate_all(client, short["id"])
    wait_job(client, client.post(f"/projects/{short['id']}/metrics/generate").json()["job_id"])
    ms = client.get(f"/projects/{short['id']}/metrics").json()
    assert len([m for m in ms if m["kind"] == "programmatic" and m["definition"]["checks"][0].get("value") == "$expected"]) == 1


def test_the_test_suite_can_never_reach_a_real_provider():
    """Guard: default models in tests are fakes and the LLMClient routes fake/* to the mock, so no key is ever used or needed."""
    assert settings.target_model.startswith("fake/") and settings.judge_model.startswith("fake/")
