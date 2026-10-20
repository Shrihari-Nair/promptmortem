from app.agents.base import run_agent
from app.agents.schemas import ContextAnalysis, CriticOut
from app.core import reasoning
from app.core.llm import LLMClient


async def critique(client: LLMClient, model: str, *, context: ContextAnalysis, scenarios: list[str],
                   coverage: dict[str, int], round: int) -> CriticOut:
    return await run_agent(client, model, "scenario_critic", {
        "context": context.model_dump(), "scenarios": "\n".join(scenarios), "coverage": coverage,
        "round": str(round),
    }, CriticOut, temperature=0.0, extra=reasoning.for_generation(model))
