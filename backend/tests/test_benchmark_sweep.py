"""Nightly benchmark sweep (PV_SWEEP=1): the golden benchmark over several noise levels and independent replicates, with the
measured error rates compared to a committed baseline. Catches silent drift in the statistics (false-alarm rate, power, cost
estimate). Refresh the baseline on purpose with PV_SWEEP_UPDATE=1 and commit the diff."""
import json
import os
from pathlib import Path

import pytest

from tests.golden import golden, run_to_end

BASELINE = Path(__file__).parent / "benchmark_baseline.json"
GRID = {"planted": [0.0, 0.05, 0.15], "noop": [0.0, 0.05, 0.2]}
REPLICATES = 3
TOL = 0.05  # allowed worsening per rate, on top of the baseline

pytestmark = pytest.mark.skipif(not os.environ.get("PV_SWEEP"), reason="slow nightly sweep; set PV_SWEEP=1")


def measure(client, engine) -> dict:
    planted, noop, cost = [], [], []
    for variant, noises in GRID.items():
        for noise in noises:
            for r in range(REPLICATES):
                pid = golden(client, engine, variant, noise, salt=f"\nreplicate {r}")
                rid, j = run_to_end(client, pid)
                assert j["result"]["status"] == "done", j
                rep = client.get(f"/runs/{rid}/report").json()["summary"]
                h, c = rep["headline"], rep["cost"]
                cost.append(c["spent"] / c["estimated_high"])
                if variant == "planted":
                    planted.append({"detected": h["level"] in ("block", "caution"), "found_fix": h["fixed"] >= 15})
                else:
                    n = h["fixed"] + h["broken"] + h["unchanged"] + h["inconclusive"]
                    noop.append({"flagged_share": (h["fixed"] + h["broken"]) / max(n, 1), "not_ok": h["level"] != "ok"})
    mean = lambda xs: round(sum(xs) / len(xs), 4)  # noqa: E731
    return {"planted_detect_rate": mean([p["detected"] for p in planted]), "planted_fix_found_rate": mean([p["found_fix"] for p in planted]),
            "noop_case_false_flag_rate": mean([n["flagged_share"] for n in noop]), "noop_run_not_ok_rate": mean([n["not_ok"] for n in noop]),
            "cost_spent_over_high_estimate_max": round(max(cost), 3), "runs": len(planted) + len(noop)}


def test_sweep_matches_committed_baseline(client, engine):
    got = measure(client, engine)
    if os.environ.get("PV_SWEEP_UPDATE"):
        BASELINE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip(f"baseline rewritten: {got}")
    base = json.loads(BASELINE.read_text())
    assert got["planted_detect_rate"] >= base["planted_detect_rate"] - TOL, (got, base)  # power must not fall
    assert got["planted_fix_found_rate"] >= base["planted_fix_found_rate"] - TOL, (got, base)
    assert got["noop_case_false_flag_rate"] <= base["noop_case_false_flag_rate"] + TOL, (got, base)  # false alarms must not rise
    assert got["noop_run_not_ok_rate"] <= base["noop_run_not_ok_rate"] + TOL, (got, base)
    assert got["cost_spent_over_high_estimate_max"] <= 1.0, (got, base)  # never overspend the high estimate
