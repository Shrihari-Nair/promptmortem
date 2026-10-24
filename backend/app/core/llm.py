"""LLMClient: the only place model calls happen. Cost tracking, cache, hard cap."""
import hashlib
import json
import time
from contextvars import ContextVar
from pathlib import Path
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.models import LLMCall

load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # the project-root .env (git-ignored), wherever the server is started from


current_run: ContextVar[str | None] = ContextVar("current_run", default=None)  # set by the runner; tags paid calls
current_project: ContextVar[str | None] = ContextVar("current_project", default=None)  # set by start_job


class BudgetExceeded(Exception):
    """Raised BEFORE a paid call when spend has reached the cap. Caller pauses the run and asks the user."""

    def __init__(self, spent: float, cap: float):
        super().__init__(f"budget cap reached: spent {spent:.4f} of {cap:.4f}")
        self.spent, self.cap = spent, cap


@dataclass
class Budget:
    cap: float
    spent: float = 0.0

    def extend(self, extra: float) -> None:
        self.cap += extra


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    cost: float
    latency: float
    cached: bool
    full_cost: float = 0.0  # what the call cost when first paid (cache hits have cost 0)


# (model, messages, params) -> (text, tokens_in, tokens_out, cost)
Provider = Callable[[str, list[dict], dict], Awaitable[tuple[str, int, int, float]]]


async def litellm_provider(model: str, messages: list[dict], params: dict):
    import litellm

    r = await litellm.acompletion(model=model, messages=messages, num_retries=2, **params)
    u = r.usage
    try:
        cost = litellm.completion_cost(completion_response=r)
    except Exception:  # unknown pricing for a model: record 0 rather than fail the call
        cost = 0.0
    return r.choices[0].message.content or "", u.prompt_tokens, u.completion_tokens, cost


def cache_key(model: str, messages: list[dict], params: dict, seed: int, repeat_idx: int) -> str:
    blob = json.dumps([model, messages, params, seed, repeat_idx], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class LLMClient:
    def __init__(self, engine: Engine, provider: Provider = litellm_provider, fake: Provider | None = None):
        from app.core.fake_llm import fake_provider

        self.engine, self.provider, self.fake = engine, provider, fake or fake_provider

    async def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        seed: int = 0,
        repeat_idx: int = 0,
        budget: Budget | None = None,
        tag: str | None = None,
        **params,
    ) -> LLMResult:
        params = {"temperature": temperature, **params}
        key = cache_key(model, messages, params, seed, repeat_idx)
        with Session(self.engine) as s:
            hit = s.exec(select(LLMCall).where(LLMCall.cache_key == key)).first()
        if hit:  # cache hits are free and never blocked by the cap
            return LLMResult(hit.response, hit.tokens_in, hit.tokens_out, 0.0, 0.0, True, hit.cost)

        if budget and budget.spent >= budget.cap:
            raise BudgetExceeded(budget.spent, budget.cap)

        call = self.fake if model.startswith("fake/") else self.provider
        t0 = time.perf_counter()
        # seed goes to the provider only; Gemini via LiteLLM accepts it, fake uses it for determinism
        text, tin, tout, cost = await call(model, messages, {**params, "seed": seed + repeat_idx})
        latency = time.perf_counter() - t0
        with Session(self.engine) as s:
            s.add(LLMCall(cache_key=key, model=model, response=text, tokens_in=tin,
                          tokens_out=tout, cost=cost, latency=latency, run_id=current_run.get(), project_id=current_project.get(), tag=tag))
            s.commit()
        if budget:
            budget.spent += cost
        return LLMResult(text, tin, tout, cost, latency, False, cost)
