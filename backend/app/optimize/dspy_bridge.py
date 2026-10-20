"""Glue between DSPy/GEPA and our stack, so the optimizer obeys the same rules as everything else:
- LLMClientLM: every DSPy language-model call goes through LLMClient (cache, cost tracking, hard cap);
- TemplateAdapter: the candidate instruction IS our prompt template, rendered with `{{var}}` exactly like the runner does,
  so GEPA scores what we would actually send (not DSPy's own message format)."""
import asyncio
from types import SimpleNamespace

import dspy
from dspy.adapters.base import Adapter

from app.config import settings
from app.core import reasoning
from app.core.llm import Budget, BudgetExceeded, LLMClient
from app.pipeline.execute import ensure_placeholders, render_prompt, template_vars  # noqa: F401  (re-exported)

def run_sync(coro):
    """Run a coroutine from a worker thread (DSPy/GEPA are synchronous and multi-threaded)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("run_sync called on the event loop thread")


class OptState:
    """Shared by the LM, the metric and the stop callback."""

    def __init__(self, budget: Budget, progress=lambda m: None):
        self.budget, self.progress, self.hit, self.metric_calls = budget, progress, False, 0


class LLMClientLM(dspy.BaseLM):
    def __init__(self, client: LLMClient, model: str, state: OptState, temperature: float | None = None, is_target: bool = True):
        super().__init__(model=model, cache=False, temperature=temperature)
        self._client, self._state, self._is_target = client, state, is_target

    def forward(self, prompt=None, messages=None, **kwargs):
        msgs = messages or [{"role": "user", "content": prompt}]
        temp = kwargs.get("temperature", self.kwargs.get("temperature"))
        try:
            r = run_sync(self._client.complete(self.model, msgs, temperature=settings.target_temperature if temp is None else temp, budget=self._state.budget, **(reasoning.for_target(self.model) if self._is_target else {})))
        except BudgetExceeded:
            self._state.hit = True
            raise
        msg = SimpleNamespace(content=r.text, tool_calls=None, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop", index=0)], model=self.model, cache_hit=r.cached,
                               usage={"prompt_tokens": r.tokens_in, "completion_tokens": r.tokens_out, "total_tokens": r.tokens_in + r.tokens_out})


class TemplateAdapter(Adapter):
    def __init__(self, variables: list[str]):
        super().__init__()
        self.variables = variables

    def format(self, signature, demos, inputs):
        text = ensure_placeholders(signature.instructions, self.variables)
        return [{"role": "user", "content": render_prompt(text, {k: str(v) for k, v in inputs.items()})}]

    def parse(self, signature, completion):
        return {"output": completion}


class BudgetStop:
    """GEPA stop callback: end the search at the hard cap (GEPA then returns its best candidate so far)."""

    def __init__(self, state: OptState):
        self.state = state

    def __call__(self, gepa_state) -> bool:
        return self.state.hit or self.state.budget.spent >= self.state.budget.cap
