"""Per-run cost trace from the LLMCall log: where the money and the model calls went. Paid calls only (cache hits are not
logged), so a resumed run shows just the calls it paid for itself; a repair retry shows as an extra call under its agent."""
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.models import LLMCall


def project_spend(engine: Engine, project_id: str) -> dict:
    """Everything a project has paid for. Planning (analysis, scenarios, metrics, calibration, the cost probe) is outside any run
    cap, so `planning` is shown next to `in_runs`, which is what the caps govern."""
    with Session(engine) as s:
        rows = s.exec(select(LLMCall).where(LLMCall.project_id == project_id)).all()
    runs = sum(r.cost for r in rows if r.run_id)
    plan = sum(r.cost for r in rows if not r.run_id)
    return {"project_id": project_id, "planning": round(plan, 6), "in_runs": round(runs, 6), "total": round(plan + runs, 6)}


def run_trace(engine: Engine, run_id: str) -> dict:
    with Session(engine) as s:
        rows = s.exec(select(LLMCall).where(LLMCall.run_id == run_id)).all()
    by: dict[str, dict] = {}
    for r in rows:
        t = by.setdefault(r.tag or "(untagged)", {"calls": 0, "cost": 0.0, "tokens_in": 0, "tokens_out": 0, "latency": 0.0})
        t["calls"] += 1
        t["cost"] += r.cost
        t["tokens_in"] += r.tokens_in
        t["tokens_out"] += r.tokens_out
        t["latency"] += r.latency
    for t in by.values():
        t["mean_latency"], t["cost"] = round(t.pop("latency") / t["calls"], 4), round(t["cost"], 6)
    return {"run_id": run_id, "calls": len(rows), "cost": round(sum(r.cost for r in rows), 6), "by_tag": dict(sorted(by.items(), key=lambda kv: -kv[1]["cost"]))}
