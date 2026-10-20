from app.agents.base import run_agent
from app.agents.schemas import ContextAnalysis
from app.core.llm import LLMClient


async def analyze_context(client: LLMClient, model: str, *, app_description: str, constraints: str,
                          known_failure_notes: str, prompt_old: str, prompt_new: str,
                          dataset_sample: list[dict]) -> ContextAnalysis:
    return await run_agent(client, model, "context_analyzer", {
        "app_description": app_description, "constraints": constraints or "(none)",
        "known_failure_notes": known_failure_notes or "(none)", "prompt_old": prompt_old,
        "prompt_new": prompt_new, "dataset_sample": dataset_sample or "(none)",
    }, ContextAnalysis, temperature=0.2)
