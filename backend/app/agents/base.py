"""Shared agent runner: load versioned prompt, call LLMClient, validate against Pydantic, repair-retry."""
import json
import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.core.llm import Budget, LLMClient

PROMPTS = Path(__file__).parent / "prompts"
MAX_REPAIRS = 2


class AgentError(Exception):
    pass


def render(agent: str, variables: dict, version: str = "v1") -> str:
    text = (PROMPTS / f"{agent}.{version}.md").read_text()
    for k, v in variables.items():
        text = text.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False))
    return text


def extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return json.loads(m.group(1) if m else text)


async def run_agent[T: BaseModel](client: LLMClient, model: str, agent: str, variables: dict, out: type[T],
                                  temperature: float, seed: int = 0, version: str = "v1",
                                  budget: Budget | None = None, extra: dict | None = None) -> T:
    messages = [{"role": "user", "content": render(agent, variables, version)}]
    for _ in range(MAX_REPAIRS + 1):
        r = await client.complete(model, messages, temperature=temperature, seed=seed,
                                  response_format={"type": "json_object"}, budget=budget, **(extra or {}))
        try:
            return out.model_validate(extract_json(r.text))
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            messages = messages + [
                {"role": "assistant", "content": r.text},
                {"role": "user", "content": f"Your output was invalid: {e}\nReturn ONLY corrected JSON matching the schema."},
            ]
    raise AgentError(f"{agent}: no valid output after {MAX_REPAIRS} repairs")
