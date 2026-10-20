"""A project engineered so every exportable check type sees both passing and failing outputs (used by the Promptfoo parity test).
With prompts that are just '{{user_message}}' and Promptfoo's `echo` provider, the output IS the scenario input, so our engine
and Promptfoo judge exactly the same strings."""
from sqlmodel import Session

from app.core import models as M

INPUTS = ["Hello there, this is a short polite message.", "POL-123 leaked id and the word refund appears here", '{"a": 1, "name": "x"}',
          '```json\n{"a": 1}\n```', "yes", "  Yes  ", "Hello", "well hello",
          "one two three four five six seven eight nine ten eleven twelve", "alpha beta gamma", "ÜBER Ünïcode text with ÄÖÜ", "x" * 40]
METRICS = [
    ("max_words", {"checks": [{"type": "max_words", "value": 8}]}),
    ("min_words", {"checks": [{"type": "min_words", "value": 3}]}),
    ("max_chars", {"checks": [{"type": "max_chars", "value": 30}]}),
    ("regex_required", {"checks": [{"type": "regex_required", "pattern": "^Hel+o\\b"}]}),
    ("regex_required_i", {"checks": [{"type": "regex_required", "pattern": "^hello", "ignore_case": True}]}),
    ("forbidden", {"checks": [{"type": "forbidden_regex", "pattern": "POL-\\d+"}]}),
    ("forbidden_i", {"checks": [{"type": "forbidden_regex", "pattern": "refund", "ignore_case": True}]}),
    ("contains_all", {"checks": [{"type": "contains_all", "values": ["alpha", "gamma"]}]}),
    ("contains_all_i", {"checks": [{"type": "contains_all", "values": ["HELLO", "polite"], "ignore_case": True}]}),
    ("contains_none", {"checks": [{"type": "contains_none", "values": ["refund", "alpha"]}]}),
    ("contains_none_i", {"checks": [{"type": "contains_none", "values": ["HELLO"], "ignore_case": True}]}),
    ("json_valid", {"checks": [{"type": "json_valid"}]}),
    ("json_schema", {"checks": [{"type": "json_schema", "schema": {"type": "object", "required": ["name"]}}]}),
    ("equals", {"checks": [{"type": "equals", "value": "yes"}]}),
    ("equals_i", {"checks": [{"type": "equals", "value": "YES", "ignore_case": True}]}),
    ("starts_with", {"checks": [{"type": "starts_with", "value": "Hello"}]}),
    ("starts_with_i", {"checks": [{"type": "starts_with", "value": "hello", "ignore_case": True}]}),
    ("all_pass", {"checks": [{"type": "max_words", "value": 8}, {"type": "contains_none", "values": ["refund"]}], "aggregate": "all_pass"}),
    ("any_pass", {"checks": [{"type": "max_words", "value": 3}, {"type": "contains_all", "values": ["alpha"]}], "aggregate": "any_pass"}),
    ("mean", {"checks": [{"type": "max_words", "value": 8}, {"type": "min_words", "value": 3}, {"type": "contains_all", "values": ["alpha"]}], "aggregate": "mean"}),
]


def seed_parity(engine) -> str:
    with Session(engine) as s:
        p = M.Project(name="parity", target_model="fake/app", judge_model_default="fake/judge")
        s.add(p)
        s.commit()
        s.add_all([M.PromptVersion(project_id=p.id, label="old", text="{{user_message}}"), M.PromptVersion(project_id=p.id, label="new", text="{{user_message}}")])
        s.add_all([M.Scenario(project_id=p.id, category="happy_path", input={"user_message": t}, status="approved") for t in INPUTS])
        s.add_all([M.Metric(project_id=p.id, name=n, kind="programmatic", definition={"aggregate": "all_pass", **d}, status="approved") for n, d in METRICS])
        s.commit()
        return p.id
