from pydantic import BaseModel, Field

from app.agents.base import run_agent
from app.core.llm import Budget, LLMClient


class ReflectOut(BaseModel):
    prompt: str = Field(min_length=10)
    notes: list[str] = []


def leaks(text: str, forbidden: list[str]) -> list[str]:
    """Training inputs copied verbatim into the prompt (overfitting)."""
    low = text.lower()
    return [f for f in forbidden if len(f) >= 25 and f.lower() in low]


async def reflect(client: LLMClient, model: str, *, app_description: str, constraints: str, current_prompt: str, variables: list[str],
                  examples: list[dict], known_failures: list[dict], human_feedback: str, forbidden: list[str],
                  budget: Budget | None = None, variation: int = 0) -> ReflectOut:
    vars_ = {"app_description": app_description or "(not given)", "constraints": constraints or "(none stated)", "current_prompt": current_prompt,
             "variables": ", ".join("{{" + v + "}}" for v in variables) or "(none)", "examples": examples or "(none)",
             "known_failures": known_failures or "(none)", "human_feedback": human_feedback or "(none yet)"}
    out = await run_agent(client, model, "reflector", vars_, ReflectOut, temperature=0.7, seed=variation, budget=budget)
    bad = leaks(out.prompt, forbidden)
    if bad:  # one retry: copied example text overfits the training cases
        vars_["human_feedback"] += "\nYour previous attempt copied example inputs into the prompt. Do NOT include any example text; describe the behaviour instead."
        out = await run_agent(client, model, "reflector", vars_, ReflectOut, temperature=0.7, seed=variation + 1, budget=budget)
        if leaks(out.prompt, forbidden):
            raise ValueError("the reflector copied training inputs into the prompt twice")
    return out
