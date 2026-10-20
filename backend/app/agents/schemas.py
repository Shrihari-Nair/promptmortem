"""Pydantic outputs for agents (PLAN.md sections 14-15)."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CATEGORIES = ["happy_path", "edge_case", "ambiguous", "long_noisy", "out_of_scope", "adversarial",
              "format_sensitive", "multilingual", "safety_critical", "diff_targeted", "regression_guard"]


class ContextAnalysis(BaseModel):
    task: str
    user_types: list[str] = []
    input_types: list[str] = []
    output_format: str = ""
    risk_areas: list[str] = []
    success_criteria: list[str] = []
    assumptions: list[str] = []


class Change(BaseModel):
    id: str
    type: str
    old_text: str | None = None
    new_text: str | None = None
    intent: str = ""
    could_affect: list[str] = []
    risk: Literal["low", "medium", "high"] = "low"


class DiffAnalysis(BaseModel):
    changes: list[Change]


class GenScenario(BaseModel):
    input: dict
    rationale: str = ""
    expected_behavior: str = ""
    tags: list[str] = []
    linked_changes: list[str] = []
    severity: int = Field(3, ge=1, le=5)
    diff_relevance: float = Field(0.0, ge=0, le=1)

    @field_validator("input", mode="before")
    @classmethod
    def _str_input(cls, v):  # models sometimes return the bare message
        return {"user_message": v} if isinstance(v, str) else v


class GenBatch(BaseModel):
    scenarios: list[GenScenario]


class Gap(BaseModel):
    category: str
    description: str


class CriticOut(BaseModel):
    duplicates: list[str] = []
    unrealistic: list[str] = []
    gaps: list[Gap] = []
    uncertain: list[str] = []


# ---- metrics (PLAN 14.4-14.7) ----
import json as _json
import re as _re
from typing import Any

import jsonschema
from pydantic import model_validator

CHECK_TYPES = ["max_words", "min_words", "max_chars", "regex_required", "forbidden_regex", "contains_all",
               "contains_none", "json_valid", "json_schema", "equals", "starts_with", "language_is"]


class Check(BaseModel):
    type: Literal["max_words", "min_words", "max_chars", "regex_required", "forbidden_regex", "contains_all",
                  "contains_none", "json_valid", "json_schema", "equals", "starts_with", "language_is"]
    value: Any = None  # number | string | list[str]; "$expected" for equals = the scenario's expected output
    pattern: str | None = None
    values: list[str] | None = None
    schema_: dict | None = Field(default=None, alias="schema")
    ignore_case: bool = False
    message: str = ""
    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _shape(self):
        t = self.type
        if t in ("max_words", "min_words", "max_chars"):
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or self.value < 0:
                raise ValueError(f"{t} needs a non-negative numeric 'value'")
        elif t in ("regex_required", "forbidden_regex"):
            if not self.pattern:
                raise ValueError(f"{t} needs 'pattern'")
            try:
                _re.compile(self.pattern)
            except _re.error as e:
                raise ValueError(f"bad regex: {e}")
        elif t in ("contains_all", "contains_none"):
            self.values = self.values or (self.value if isinstance(self.value, list) else None)
            if not self.values:
                raise ValueError(f"{t} needs a non-empty 'values' list")
        elif t == "json_schema":
            if not self.schema_:
                raise ValueError("json_schema needs 'schema'")
            try:
                jsonschema.Draft202012Validator.check_schema(self.schema_)
            except jsonschema.SchemaError as e:
                raise ValueError(f"invalid JSON schema: {e.message}")
        elif t in ("equals", "starts_with", "language_is"):
            if not isinstance(self.value, str) or not self.value:
                raise ValueError(f"{t} needs a string 'value'")
        return self


class ProgDef(BaseModel):
    checks: list[Check] = Field(min_length=1)
    aggregate: Literal["all_pass", "any_pass", "mean"] = "all_pass"


class JudgeDef(BaseModel):
    mode: Literal["pointwise", "pairwise"] = "pointwise"
    rubric: str = Field(min_length=10)
    pass_examples: list[str] = []
    fail_examples: list[str] = []
    output: Literal["binary", "likert5"] = "binary"
    ensemble: bool = False  # also ask a second judge; a disagreement is logged (Score.disputed), the primary verdict stays
    ensemble_model: str | None = None  # default: settings.judge_ensemble_model

    @model_validator(mode="after")
    def _examples(self):
        if self.mode == "pointwise" and not (self.pass_examples and self.fail_examples):
            raise ValueError("every LLM-judge metric needs at least one pass example and one fail example")
        return self


def validate_definition(kind: str, definition: dict) -> dict:
    """Validate a metric definition against its kind; returns the normalised dict."""
    if kind == "programmatic":
        return ProgDef.model_validate(definition).model_dump(by_alias=True, exclude_none=True)
    if kind == "llm_judge":
        return JudgeDef.model_validate(definition).model_dump()
    raise ValueError("kind must be programmatic or llm_judge")


class MetricSpec(BaseModel):
    name: str
    kind: Literal["programmatic", "llm_judge"]
    applies_to: dict = {"categories": ["*"]}
    definition: dict
    scale: Literal["binary", "likert5"] = "binary"
    weight: float = Field(1.0, ge=0)
    judge_model: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _def(self):
        self.definition = validate_definition(self.kind, self.definition)
        return self


class MetricDesign(BaseModel):
    metrics: list[MetricSpec]


class JudgeOut(BaseModel):
    verdict: Literal["pass", "fail"]
    score: float
    rationale: str
    confidence: float = Field(1.0, ge=0, le=1)


class PairOut(BaseModel):
    winner: Literal["A", "B", "tie"]
    preserved_correct_behavior: bool
    rationale: str
    confidence: float = Field(1.0, ge=0, le=1)
