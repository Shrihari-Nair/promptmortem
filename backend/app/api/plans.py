from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import get_client
from app.core.db import get_session
from app.core.llm import LLMClient
from app.core.models import Scenario
from app.pipeline import cost_estimator as ce
from app.pipeline import stale, trimmer
from app.pipeline.planner import PlanError
from app.pipeline.prioritizer import is_protected, rank
from app.core.embeddings import scenario_text

router = APIRouter()


class EstimateIn(BaseModel):
    levers: dict[str, bool] | None = None
    reprobe: bool = False


async def _estimate(pid: str, s: Session, client: LLMClient, levers=None, reprobe=False, custom=None) -> dict:
    try:
        await ce.ensure_probe(s.get_bind(), client, pid, reprobe)
        s.expire_all()
        return ce.estimate(s, pid, levers, custom)
    except PlanError as e:
        raise HTTPException(422, str(e))


@router.post("/projects/{pid}/plan/estimate")
async def estimate(pid: str, body: EstimateIn | None = None, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    body = body or EstimateIn()
    est = await _estimate(pid, s, client, body.levers, body.reprobe)
    stale.clear(s, pid, "Budget")  # estimating IS the budget step
    return est


class TrimIn(BaseModel):
    scenario_ids: list[str]
    repeats: int | None = None
    levers: dict[str, bool] | None = None
    confirm_drop_protected: bool = False


def _check_protected(s: Session, pid: str, ids: list[str], confirm: bool) -> None:
    dropped = [x.id for x in rank(s, pid) if is_protected(x) and x.id not in set(ids)]
    if dropped and not confirm:
        raise HTTPException(409, {"error": "pinned or high-severity scenarios would be dropped; confirm explicitly",
                                  "protected_dropped": dropped})


@router.post("/projects/{pid}/plan/trim")
async def trim(pid: str, body: TrimIn, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    _check_protected(s, pid, body.scenario_ids, body.confirm_drop_protected)
    return await _estimate(pid, s, client, body.levers, custom={"scenario_ids": body.scenario_ids, "repeats": body.repeats})


class ConfirmIn(BaseModel):
    tier: str  # full | balanced | lean | custom
    cap: float | None = None  # default = the tier's high estimate
    levers: dict[str, bool] | None = None
    scenario_ids: list[str] | None = None
    repeats: int | None = None
    confirm_drop_protected: bool = False


@router.post("/projects/{pid}/plan/confirm")
async def confirm(pid: str, body: ConfirmIn, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    if body.tier == "custom":
        if not body.scenario_ids:
            raise HTTPException(422, "custom tier needs scenario_ids")
        _check_protected(s, pid, body.scenario_ids, body.confirm_drop_protected)
    custom = {"scenario_ids": body.scenario_ids, "repeats": body.repeats} if body.tier == "custom" else None
    est = await _estimate(pid, s, client, body.levers, custom=custom)
    tier = next((t for t in est["tiers"] if t["tier"] == body.tier), None)
    if not tier:
        raise HTTPException(422, "tier must be full, balanced, lean or custom")
    cap = body.cap if body.cap is not None else tier["suggested_cap"]
    if cap <= 0:
        raise HTTPException(422, "cap must be positive")
    plan = ce.get_plan(s, pid)
    from datetime import datetime, timezone
    plan.chosen = {"tier": body.tier, "cap": cap, "levers": {l["id"]: l["enabled"] for l in est["levers"]},
                   "repeats": tier["repeats"], "scenario_ids": tier["scenario_ids"], "expected": tier["cost"]["expected"],
                   "high": tier["cost"]["high"], "coverage_lost": tier["coverage_lost"],
                   "warning": "cap is below the expected cost: the run will likely pause" if cap < tier["cost"]["expected"] else None}
    plan.stage, plan.locked_at = "ready", datetime.now(timezone.utc)
    s.add(plan)
    s.commit()
    stale.clear(s, pid, "Budget")
    stale.mark(s, pid, "plan", "a new budget plan was confirmed")
    return plan.chosen


@router.get("/projects/{pid}/plan")
def get_plan(pid: str, s: Session = Depends(get_session)):
    p = ce.get_plan(s, pid)
    return {"stage": p.stage, "chosen": p.chosen, "coverage_map": p.coverage_map, "probe": p.probe}


@router.get("/projects/{pid}/plan/ranking")
def ranking(pid: str, s: Session = Depends(get_session)):
    """Priority-sorted table for the custom trim UI. `protected` rows are locked unless explicitly confirmed."""
    return [{"id": x.id, "category": x.category, "cluster_id": x.cluster_id, "text": scenario_text(x.input)[:200],
             "severity": x.severity, "pinned": x.pinned, "priority": round(x.priority_score, 4), "protected": is_protected(x),
             "status": x.status} for x in rank(s, pid)]
