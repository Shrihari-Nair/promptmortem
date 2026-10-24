#!/usr/bin/env python3
"""End-to-end smoke test of a RUNNING server on the example project, with hard spending caps. Standard library only.

  # terminal 1 (a small, cheap configuration for the first real run):
  cd backend && PV_SCENARIOS_PER_CATEGORY_INITIAL=4 PV_SCENARIOS_PER_DIFF_CHANGE=3 PV_DEFAULT_REPEATS=3 uv run uvicorn app.main:app
  # terminal 2:
  python scripts/pipeline_smoke.py --model gemini/gemini-2.5-flash --judge gemini/gemini-2.5-flash --cap 0.50
  python scripts/pipeline_smoke.py --model fake/app --judge fake/judge --cap 5      # dry run, no API key, no spend

It stops (never silently continues) if the cap is hit; --optimize adds the GEPA refinement step with its own cap."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

EX = os.path.join(os.path.dirname(__file__), "..", "examples", "billing_bot")


class Api:
    def __init__(self, base: str, token: str | None):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:400]}")

    def job(self, job_id: str, label: str, timeout: int = 1800):
        t0, last = time.time(), ""
        while time.time() - t0 < timeout:
            j = self.call("GET", f"/jobs/{job_id}")
            if j["progress"] and j["progress"] != last:
                last = j["progress"]
                print(f"    {label}: {last}")
            if j["status"] not in ("pending", "running"):
                return j
            time.sleep(1.5)
        sys.exit(f"timed out waiting for {label}")


def step(n, text):
    print(f"\n[{n}] {text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--model", default="gemini/gemini-2.5-flash", help="target model (LiteLLM name; fake/app for a dry run)")
    ap.add_argument("--judge", default="gemini/gemini-2.5-flash", help="judge + agent model")
    ap.add_argument("--cap", type=float, default=0.50, help="hard cap (USD) for the comparison run")
    ap.add_argument("--tier", default="lean", choices=["full", "balanced", "lean"])
    ap.add_argument("--optimize", action="store_true", help="also run the GEPA refinement")
    ap.add_argument("--optimize-cap", type=float, default=0.50)
    ap.add_argument("--token", default=os.environ.get("PV_API_TOKEN"))
    a = ap.parse_args()
    api = Api(a.base, a.token)

    step(0, "server and keys")
    h = api.call("GET", "/health")
    print(f"    keys configured: {h['keys']}; default models: {h['models']}")
    family = "gemini" if a.model.startswith("gemini") else "openai" if a.model.startswith(("openai", "gpt")) else "anthropic" if "claude" in a.model else None
    if family and not h["keys"].get(family):
        sys.exit(f"no {family.upper()} API key is configured on the server: put it in .env (GEMINI_API_KEY=...) and restart it")

    step(1, "create the example project (Acme billing bot) and import its dataset and traces")
    meta = json.load(open(f"{EX}/project.json"))
    p = api.call("POST", "/projects", {**meta, "target_model": a.model, "judge_model_default": a.judge})
    pid = p["id"]
    for label in ("old", "new"):
        api.call("POST", f"/projects/{pid}/prompts", {"label": label, "text": open(f"{EX}/{label}_prompt.txt").read().strip()})
    print("    dataset:", api.call("POST", f"/projects/{pid}/dataset/import", {"format": "csv", "content": open(f"{EX}/dataset.csv").read()}))
    print("    traces: ", api.call("POST", f"/projects/{pid}/traces/import", {"format": "jsonl", "content": open(f"{EX}/traces.jsonl").read()}))

    step(2, "understand: what does the app do, what changed?")
    j = api.job(api.call("POST", f"/projects/{pid}/analyze")["job_id"], "analyze")
    assert j["status"] == "done", j
    an = api.call("GET", f"/projects/{pid}/analysis")
    print("    task:", an["context"]["data"]["task"])
    for c in an["diff"]["data"]["changes"]:
        print(f"    change {c['id']} [{c['risk']}] {c['intent']} -> could affect {c['could_affect'][:3]}")

    step(3, "scenarios (planning is never cost-capped; this is the main LLM-generation cost)")
    j = api.job(api.call("POST", f"/projects/{pid}/scenarios/generate")["job_id"], "scenarios")
    assert j["status"] == "done", j
    cov = api.call("GET", f"/projects/{pid}/coverage")
    print("    result:", j["result"], "| counts:", cov["counts"])

    step(4, "metrics (programmatic first, judges where needed, plus the pairwise regression metric)")
    j = api.job(api.call("POST", f"/projects/{pid}/metrics/generate")["job_id"], "metrics")
    assert j["status"] == "done", j
    for m in api.call("GET", f"/projects/{pid}/metrics"):
        mode = "pairwise" if m["definition"].get("mode") == "pairwise" else m["kind"]
        print(f"    - {m['name']} [{mode}] weight {m['weight']}")
    for m in [m for m in api.call("GET", f"/projects/{pid}/metrics") if m["kind"] == "llm_judge" and m["definition"].get("mode") != "pairwise"]:
        j = api.job(api.call("POST", f"/metrics/{m['id']}/calibrate")["job_id"], "calibrate")
        print(f"    calibration of '{m['name'][:40]}' on your labelled traces:", j["result"] or j["error"])

    step(5, "cost estimate (a small probe on real calls) and plan")
    est = api.call("POST", f"/projects/{pid}/plan/estimate")
    for t in est["tiers"]:
        c = t["cost"]
        print(f"    {t['tier']:8} {t['n_scenarios']:3} cases x {t['repeats']} repeats: ${c['low']:.4f} / ${c['expected']:.4f} / ${c['high']:.4f} (low/expected/high)")
    print(f"    probe cost: ${est['probe']['paid']:.4f}; protected cases (pinned or severity>=4): {est['protected_scenarios']}")
    for w in est.get('warnings', []):
        print('    WARNING:', w)
    plan = api.call("POST", f"/projects/{pid}/plan/confirm", {"tier": a.tier, "cap": a.cap})
    print(f"    confirmed tier {plan['tier']} with a HARD cap of ${plan['cap']:.4f}" + (f" ({plan['warning']})" if plan.get("warning") else ""))

    step(6, "run old vs new (smoke -> main -> deep dive), paired and noise-adjusted")
    r = api.call("POST", f"/projects/{pid}/runs")
    j = api.job(r["job_id"], "run")
    st = api.call("GET", f"/runs/{r['run_id']}")
    print(f"    status: {st['status']} stage: {st['stage']} spent ${st['spent']:.4f} of ${st['cap']:.4f}")
    if st["status"] == "paused":
        print(f"    PAUSED ({st['pause_reason']}). Nothing was dropped silently. Extend with POST /runs/{r['run_id']}/extend or stop with /stop.")
    rep = api.call("GET", f"/runs/{r['run_id']}/report")["summary"]
    h_, sc = rep["headline"], rep["scope"]
    print(f"    {h_['level'].upper()}: {h_['recommendation']}")
    print(f"    fixed {h_['fixed']} broken {h_['broken']} unchanged {h_['unchanged']} inconclusive {h_['inconclusive']} | net effect {h_['net_effect']}")
    print(f"    scope: {sc['tested']} tested, {sc['dropped']} dropped by the tier, reviewed {sc['reviewed_share']:.0%}, verified {sc['verified_share']:.0%}, complete={sc['complete']}")
    for w in rep["warnings"]:
        print("    WARNING:", w)
    for x in rep["regressions"][:3]:
        change = f"{x['old_pass_rate']} -> {x['new_pass_rate']}" if x.get("old_pass_rate") is not None else f"preference for new {x.get('preference_for_new')}"
        print(f"    regression [{x['cluster']}] severity {x['severity']} {x['metric_name']}: {change}")
    print(f"    cost: spent ${rep['cost']['spent']:.4f}; estimated ${rep['cost']['estimated']} (high {rep['cost']['estimated_high']})")
    print(f"    planning (outside the cap): ${rep['cost']['planning_spent']:.4f}; total for the project: ${rep['cost']['planning_spent'] + rep['cost']['spent']:.4f}")

    if a.optimize:
        step(7, f"GEPA refinement (hard cap ${a.optimize_cap})")
        o = api.call("POST", f"/projects/{pid}/optimize", {"cap": a.optimize_cap, "max_metric_calls": 40})
        j = api.job(o["job_id"], "optimize")
        res = api.call("GET", f"/optimizations/{o['optimization_id']}")
        print(f"    status {res['status']}, spent ${res['spent']:.4f}; {res['error']}")
        for c in res["result"]["candidates"]:
            print(f"    {c['label']}: validation {c['seed_val_score']} -> {c['val_score']}")
            for k, v in c["heldout"].items():
                print(f"      {k}: {v['level']} {v['counts']} | {v['recommendation']}")
    print(f"\nproject id: {pid} (open the UI and pick it up there; the example is stored like any other project)")


if __name__ == "__main__":
    main()
