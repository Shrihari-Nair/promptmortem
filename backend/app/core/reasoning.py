"""Control of provider 'thinking' (Gemini 2.5 bills hidden reasoning tokens as output). Judges and scenario generation do not need it
(measured: a judge call went from 350 output tokens to 32 with the same verdict, 9x cheaper), so they run with it off. The TARGET
model is left as the app runs it by default: we measure what your app does."""
from app.config import settings


def reasoning_params(model: str, mode: str | None) -> dict:
    """LiteLLM params for `mode` in {default, disable, low, medium, high}. Only the Gemini 2.5 family thinks; everything else gets {}."""
    if not mode or mode == "default" or not model.startswith(("gemini/", "vertex_ai/gemini")):
        return {}
    name = model.split("/", 1)[1]
    if "2.5" not in name:
        return {}
    if "pro" in name and mode == "disable":
        return {"reasoning_effort": "low"}  # 2.5 Pro cannot switch thinking off
    return {"reasoning_effort": mode}


def for_judge(model: str) -> dict:
    return reasoning_params(model, settings.judge_reasoning)


def for_generation(model: str) -> dict:
    return reasoning_params(model, settings.generator_reasoning)


def for_target(model: str) -> dict:
    return reasoning_params(model, settings.target_reasoning)
