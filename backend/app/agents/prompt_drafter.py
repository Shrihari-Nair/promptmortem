from app.agents.base import run_agent
from app.agents.reflector import ReflectOut
from app.core.llm import LLMClient
from app.pipeline.execute import ensure_placeholders, template_vars


async def draft(client: LLMClient, model: str, *, old_prompt: str, change_request: str, app_description: str = "", constraints: str = "") -> ReflectOut:
    variables = template_vars(old_prompt)
    out = await run_agent(client, model, "prompt_drafter", {
        "app_description": app_description or "(not given)", "constraints": constraints or "(none stated)", "old_prompt": old_prompt,
        "change_request": change_request, "variables": ", ".join("{{" + v + "}}" for v in variables) or "(none)"}, ReflectOut, temperature=0.2)
    return ReflectOut(prompt=ensure_placeholders(out.prompt, variables), notes=out.notes)
