from app.agents.base import run_agent
from app.agents.schemas import ContextAnalysis, DiffAnalysis, MetricDesign
from app.core.llm import LLMClient


async def design(client: LLMClient, model: str, *, context: ContextAnalysis, diff: DiffAnalysis,
                 scenarios: dict[str, list[dict]], dataset: list[dict], feedback: str) -> MetricDesign:
    return await run_agent(client, model, "metric_designer", {
        "context": context.model_dump(), "diff": diff.model_dump(), "scenarios": scenarios,
        "dataset": dataset or "(none)", "feedback": feedback or "(none yet)",
    }, MetricDesign, temperature=0.3)
