"""Helpers shared by the runner and benchmark tests."""
from sqlmodel import Session

from app.benchmark.golden import seed_golden
from tests.conftest import wait_job


def golden(client, engine, variant="planted", noise=0.05, n_each=20, cap=50.0, levers=None, tier="full"):
    pid = seed_golden(engine, variant, noise, n_each)
    r = client.post(f"/projects/{pid}/plan/estimate", json={"levers": levers} if levers else None)
    assert r.status_code == 200, r.text
    c = client.post(f"/projects/{pid}/plan/confirm", json={"tier": tier, "cap": cap, **({"levers": levers} if levers else {})})
    assert c.status_code == 200, c.text
    return pid


def start_run(client, pid, cap=None):
    r = client.post(f"/projects/{pid}/runs", json={"cap": cap} if cap else None)
    assert r.status_code == 200, r.text
    return r.json()


def run_to_end(client, pid, cap=None):
    r = start_run(client, pid, cap)
    j = wait_job(client, r["job_id"], timeout=120)
    return r["run_id"], j
