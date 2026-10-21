"""Blind judges. Neither prompt says which prompt version produced an output."""
from dataclasses import dataclass

from app.agents.base import run_agent
from app.agents.schemas import JudgeDef, JudgeOut, PairOut
from app.config import settings
from app.core import reasoning
from app.core.llm import Budget, LLMClient


async def judge(client: LLMClient, model: str, d: JudgeDef, inp: dict, output: str, *, repeat_idx: int = 0,
                budget: Budget | None = None, version: str = "v1") -> JudgeOut:
    return await run_agent(client, model, "judge", {
        "rubric": d.rubric, "pass_examples": d.pass_examples, "fail_examples": d.fail_examples,
        "scale": "binary: score 1 = pass, 0 = fail" if d.output == "binary" else "1 (worst) to 5 (best); verdict pass if >= 4",
        "input": inp, "output": output,
    }, JudgeOut, temperature=settings.judge_temperature, seed=repeat_idx, budget=budget, version=version, extra=reasoning.for_judge(model))


@dataclass
class PairResult:
    verdict: str  # old | new | tie  (tie also when disputed)
    disputed: bool  # the two A/B orders disagree
    preserved_correct_behavior: bool  # from the order where A = old output
    rationale: str
    confidence: float


async def _pair(client, model, d, inp, a, b, repeat_idx, budget) -> PairOut:
    return await run_agent(client, model, "pairwise_judge", {
        "rubric": d.rubric, "input": inp, "output_a": a, "output_b": b,
    }, PairOut, temperature=settings.judge_temperature, seed=repeat_idx, budget=budget, extra=reasoning.for_judge(model))


async def pairwise(client: LLMClient, model: str, d: JudgeDef, inp: dict, old: str, new: str, *,
                   repeat_idx: int = 0, budget: Budget | None = None) -> PairResult:
    """Run both orders (position-swap). Orders that disagree => disputed."""
    r1 = await _pair(client, model, d, inp, old, new, repeat_idx, budget)  # A=old, B=new
    r2 = await _pair(client, model, d, inp, new, old, repeat_idx, budget)  # A=new, B=old

    def who(r: PairOut, a_is: str) -> str:
        b_is = "new" if a_is == "old" else "old"
        return {"A": a_is, "B": b_is, "tie": "tie"}[r.winner]

    w1, w2 = who(r1, "old"), who(r2, "new")
    disputed = w1 != w2
    return PairResult("tie" if disputed else w1, disputed, r1.preserved_correct_behavior,
                      f"[A=old] {r1.rationale} | [A=new] {r2.rationale}", min(r1.confidence, r2.confidence))
