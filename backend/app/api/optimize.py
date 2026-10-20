from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_client
from app.core.db import get_session
from app.core.jobs import start_job
from app.core.llm import LLMClient
from app.core.models import Feedback, Optimization, Project, PromptVersion
from app.optimize import gepa_runner as gr
from app.pipeline import stale
from app.optimize.dspy_bridge import ensure_placeholders, template_vars

router = APIRouter()


class OptimizeIn(BaseModel):
    cap: float = Field(gt=0)  # hard cap in USD for the whole optimization (search + held-out validation): required, never implicit
    max_metric_calls: int | None = Field(default=None, ge=6, le=2000)
    n_candidates: int = Field(default=1, ge=1, le=3)


@router.post("/projects/{pid}/optimize")
async def optimize(pid: str, body: OptimizeIn, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    """GEPA refinement of the NEW prompt, validated on held-out scenarios the optimizer never sees."""
    if not s.get(Project, pid):
        raise HTTPException(404, "project not found")
    engine = s.get_bind()
    o = Optimization(project_id=pid)
    s.add(o)
    s.commit()
    s.refresh(o)
    oid = o.id
    stale.clear(s, pid, "Refine")

    async def job(progress):
        try:
            res = await gr.optimize(engine, client, pid, cap=body.cap, max_metric_calls=body.max_metric_calls, n_candidates=body.n_candidates,
                                    progress=progress, optimization_id=oid)
        except Exception as e:  # precondition errors (no metrics, too few scenarios) and crashes: never leave the row 'pending'
            gr._save(engine, oid, status="failed", error=f"{type(e).__name__}: {e}")  # _save redacts
            raise
        return {k: v for k, v in res.items() if isinstance(v, (int, str, float))}

    return {"optimization_id": oid, "job_id": start_job(engine, pid, "optimize", job)}


def _opt(s: Session, oid: str) -> Optimization:
    o = s.get(Optimization, oid)
    if not o:
        raise HTTPException(404, "optimization not found")
    return o


def _cand(o: Optimization, cid: str) -> dict:
    for c in (o.result or {}).get("candidates", []):
        if c["id"] == cid:
            return c
    raise HTTPException(404, "candidate not found")


def _write(s: Session, o: Optimization, result: dict) -> None:
    o.result = {**result}  # a new dict, so the JSON column change is detected
    s.add(o)
    s.commit()


@router.get("/projects/{pid}/optimizations")
def list_optimizations(pid: str, s: Session = Depends(get_session)):
    return s.exec(select(Optimization).where(Optimization.project_id == pid)).all()


@router.get("/optimizations/{oid}", response_model=Optimization)
def get_optimization(oid: str, s: Session = Depends(get_session)):
    return _opt(s, oid)


class RejectIn(BaseModel):
    comment: str = ""


@router.post("/optimizations/{oid}/candidates/{cid}/reject")
def reject(oid: str, cid: str, body: RejectIn, s: Session = Depends(get_session)):
    """Rejecting with a comment is stored and fed to the reflector in the next optimization (the loop)."""
    o = _opt(s, oid)
    res = {**o.result}
    cand = _cand(o, cid)
    cand["status"], cand["feedback"] = "rejected", body.comment
    s.add(Feedback(project_id=o.project_id, target_type="candidate", target_id=cid, action="reject", comment=body.comment))
    _write(s, o, res)
    return cand


class AcceptIn(BaseModel):
    promote: bool = False  # make it the project's 'new' prompt (the previous 'new' is kept under 'new_prev_N')
    comment: str = ""


@router.post("/optimizations/{oid}/candidates/{cid}/accept")
def accept(oid: str, cid: str, body: AcceptIn, s: Session = Depends(get_session)):
    o = _opt(s, oid)
    res = {**o.result}
    cand = _cand(o, cid)
    cand["status"] = "accepted"
    s.add(Feedback(project_id=o.project_id, target_type="candidate", target_id=cid, action="approve", comment=body.comment))
    if body.promote:
        pvs = s.exec(select(PromptVersion).where(PromptVersion.project_id == o.project_id)).all()
        cur = next((v for v in pvs if v.label == "new"), None)
        if cur:
            cur.label = f"new_prev_{sum(v.label.startswith('new_prev_') for v in pvs) + 1}"
            s.add(cur)
        v = s.get(PromptVersion, cand["version_id"])
        v.label, cand["label"] = "new", "new"
        s.add(v)
        s.commit()
        stale.mark(s, o.project_id, "prompts", "a refined prompt was promoted to 'new'")
    _write(s, o, res)
    return {**cand, "note": "promoted to 'new'; earlier analysis, plan and runs refer to the previous 'new' prompt, so re-run them" if body.promote else None}


class EditIn(BaseModel):
    text: str
    comment: str = ""


@router.patch("/optimizations/{oid}/candidates/{cid}")
def edit(oid: str, cid: str, body: EditIn, s: Session = Depends(get_session)):
    """The human edits a candidate: stored as a NEW candidate (the original stays), to be validated on held-out like the others."""
    o = _opt(s, oid)
    res = {**o.result}
    src = _cand(o, cid)
    variables = (o.config or {}).get("variables", [])
    if ensure_placeholders(body.text, variables) != body.text:
        raise HTTPException(422, f"the prompt must keep every placeholder: {', '.join('{{' + v + '}}' for v in variables)}")
    n = len([v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == o.project_id)).all() if v.label.startswith("candidate_")]) + 1
    v = PromptVersion(project_id=o.project_id, label=f"candidate_{n}", text=body.text)
    s.add(v)
    s.commit()
    s.refresh(v)
    new_text = next((pv.text for pv in s.exec(select(PromptVersion).where(PromptVersion.project_id == o.project_id)).all() if pv.id == (o.config or {}).get("seed_version_id")), "")
    old_text = next((pv.text for pv in s.exec(select(PromptVersion).where(PromptVersion.project_id == o.project_id, PromptVersion.label == "old")).all()), "")
    cand = {"id": f"cand_{v.id[-8:]}", "version_id": v.id, "label": v.label, "text": body.text, "status": "edited", "edited_from": cid, "val_score": None,
            "seed_val_score": src.get("seed_val_score"), "notes": [body.comment] if body.comment else [], "heldout": {},
            "diff_vs_new": gr._udiff(new_text, body.text, "new", v.label), "diff_vs_old": gr._udiff(old_text, body.text, "old", v.label)}
    res["candidates"] = [*res["candidates"], cand]
    s.add(Feedback(project_id=o.project_id, target_type="candidate", target_id=cid, action="edit", comment=body.comment or "edited by the reviewer"))
    _write(s, o, res)
    return cand


class EvalIn(BaseModel):
    cap: float = Field(gt=0)  # per comparison (vs new, vs old)


@router.post("/optimizations/{oid}/candidates/{cid}/evaluate")
async def evaluate(oid: str, cid: str, body: EvalIn, s: Session = Depends(get_session), client: LLMClient = Depends(get_client)):
    """(Re-)validate a candidate on the held-out scenarios; resumes paused comparisons."""
    o = _opt(s, oid)
    _cand(o, cid)
    engine, pid = s.get_bind(), o.project_id

    async def job(progress):
        with Session(engine) as s2:
            o2 = s2.get(Optimization, oid)
            held, seed = o2.config["heldout_ids"], o2.config["seed_version_id"]
            old = next(v for v in s2.exec(select(PromptVersion).where(PromptVersion.project_id == pid)).all() if v.label == "old")
            cand = dict(_cand(o2, cid))
        spent = await gr.validate_candidate(engine, client, oid, cand, base_ids={"new": seed, "old": old.id}, held=held, cap=body.cap, progress=progress)
        with Session(engine) as s2:  # merge into the CURRENT result (it may have changed while this ran)
            o2 = s2.get(Optimization, oid)
            res = {**o2.result}
            res["candidates"] = [cand if c["id"] == cid else c for c in res["candidates"]]
            o2.spent += spent
            if all(h.get("status") == "done" for h in cand["heldout"].values()) and o2.status == "paused":
                o2.status, o2.error = "done", ""
            _write(s2, o2, res)
        return {"spent": spent, "statuses": ",".join(h["status"] for h in cand["heldout"].values())}

    return {"job_id": start_job(engine, pid, "evaluate_candidate", job)}
