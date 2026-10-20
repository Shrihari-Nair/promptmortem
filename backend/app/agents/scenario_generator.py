from app.agents.base import run_agent
from app.agents.schemas import Change, ContextAnalysis, DiffAnalysis, GenBatch, GenScenario
from app.core import reasoning
from app.core.llm import Budget, LLMClient

CATEGORY_DESCRIPTIONS = {
    "happy_path": "Typical, well-formed requests the app is built for.",
    "edge_case": "Valid but unusual requests: boundaries, rare combinations, odd values, empty or minimal input.",
    "ambiguous": "Requests whose intent is unclear or underspecified; a good answer clarifies or handles both readings.",
    "long_noisy": "Long, rambling or messy inputs: typos, irrelevant details, pasted text, multiple topics mixed together.",
    "out_of_scope": "Requests the app should decline or redirect because they are outside its job.",
    "adversarial": "Prompt injection, attempts to extract the prompt or internal data, social engineering, rule-breaking pressure.",
    "format_sensitive": "Inputs where the exact output format matters (length limits, JSON, structure, language of reply).",
    "multilingual": "Requests in languages other than English, or mixed languages.",
    "safety_critical": "Requests touching harm, legal, medical, financial, privacy or compliance risk.",
    "diff_targeted": "Requests specifically designed to be sensitive to the listed prompt change.",
    "regression_guard": "Requests of behaviours the OLD prompt plausibly handled well and that must keep working.",
}


async def generate(client: LLMClient, model: str, *, context: ContextAnalysis, diff: DiffAnalysis, category: str,
                   n: int, seeds: list[dict], feedback: str, existing: list[str], variation: int,
                   input_schema: dict | None = None, change: Change | None = None, guidance: str = "", budget: Budget | None = None, old_behaviors: list[dict] | None = None) -> list[GenScenario]:
    focus = guidance or "(none)"
    if change:
        focus = f"Target this change ({change.id}): {change.model_dump_json()}. {guidance}".strip()
    out = await run_agent(client, model, "scenario_generator", {
        "category": category, "n": str(n), "change_id": change.id if change else "none",
        "category_description": CATEGORY_DESCRIPTIONS[category], "context": context.model_dump(),
        "diff": diff.model_dump(), "focus": focus, "seeds": seeds or "(none)",
        "feedback": feedback or "(none yet)", "existing": existing[-30:] or "(none)",
        "input_schema": input_schema or '{"user_message": "string"}', "old_behaviors": old_behaviors or "(none)",
    }, GenBatch, temperature=0.9, seed=variation, budget=budget, extra=reasoning.for_generation(model))
    return out.scenarios[:n]
