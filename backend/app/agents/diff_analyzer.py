import difflib

from app.agents.base import run_agent
from app.agents.schemas import ContextAnalysis, DiffAnalysis
from app.core.llm import LLMClient


async def analyze_diff(client: LLMClient, model: str, *, context: ContextAnalysis, prompt_old: str,
                       prompt_new: str) -> DiffAnalysis:
    udiff = "\n".join(difflib.unified_diff(prompt_old.splitlines(), prompt_new.splitlines(), "old", "new", lineterm="", n=1))
    return await run_agent(client, model, "diff_analyzer", {
        "context": context.model_dump(), "prompt_old": prompt_old, "prompt_new": prompt_new,
        "unified_diff": udiff or "(no line differences)",
    }, DiffAnalysis, temperature=0.0)
