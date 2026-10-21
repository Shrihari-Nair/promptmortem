"""The ONE way target calls are made. The probe (M4) and the runner (M5) both use it, so cache keys match
and a probed call is never paid twice."""
import re

from sqlmodel import Session, select

from app.config import settings
from app.core import reasoning
from app.core.llm import Budget, LLMClient, LLMResult
from app.core.models import PromptVersion

_PH = r"\{\{\s*(\w+)\s*\}\}"


def template_vars(*texts: str) -> list[str]:
    seen: list[str] = []
    for t in texts:
        for v in re.findall(_PH, t):
            if v not in seen:
                seen.append(v)
    return seen


def ensure_placeholders(text: str, variables: list[str]) -> str:
    """A prompt that dropped an input placeholder would never show the model its input: put it back at the end."""
    missing = [v for v in variables if not re.search(r"\{\{\s*" + re.escape(v) + r"\s*\}\}", text)]
    return text if not missing else text.rstrip() + "".join(f"\n\n{v}: {{{{{v}}}}}" for v in missing)


def optional_vars(schema: dict | None) -> set[str]:
    """Variables declared optional in a prompt's variables_schema (type ending in '?', e.g. 'string?')."""
    return {k for k, v in (schema or {}).items() if str(v).endswith("?")}


def fill_optional(inp: dict, optional: set[str]) -> dict:
    """Optional variables a scenario does not set render as empty text instead of a literal {{placeholder}}."""
    return {**{k: "" for k in optional if k not in inp}, **inp}


def project_optional(s: Session, project_id: str) -> set[str]:
    out: set[str] = set()
    for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all():
        out |= optional_vars(v.variables_schema)
    return out


def render_prompt(text: str, inp: dict) -> str:
    for k, v in inp.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


async def run_target(client: LLMClient, model: str, prompt_text: str, inp: dict, *, repeat_idx: int = 0,
                     budget: Budget | None = None) -> LLMResult:
    return await client.complete(model, [{"role": "user", "content": render_prompt(prompt_text, inp)}],
                                 temperature=settings.target_temperature, repeat_idx=repeat_idx, budget=budget, tag="target", **reasoning.for_target(model))
