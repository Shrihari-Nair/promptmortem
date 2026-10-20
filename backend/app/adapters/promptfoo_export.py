"""Export a project as a Promptfoo config (promptfooconfig.yaml): both prompts, the scenarios as tests, and the programmatic
metrics as assertions. Promptfoo is an optional backend: our differentiators (change-aware selection, noise-adjusted paired
analysis, budget, human checkpoints) stay in our code; this is an export so the cases and checks can be rerun elsewhere.

Mapping notes (checked against Promptfoo, see tests/test_m7.py):
- every assertion carries `metric: <our metric name>` and our weight, so Promptfoo reports per-metric scores;
- aggregate all_pass -> plain assertions; any_pass -> assert-set (threshold just above 0); mean -> assert-set (threshold 0);
- LLM-judge metrics -> llm-rubric, graded by the judge model; the pairwise regression metric -> select-best across the two prompts;
- not exportable: language_is (no Promptfoo built-in); Python-only regex syntax is passed through as written."""
import json

import yaml
from sqlmodel import Session, select

from app.config import settings
from app.core.models import Metric, Plan, Project, PromptVersion, Scenario, ScenarioCluster
from app.pipeline.metric_plan import is_pairwise
from app.pipeline.execute import fill_optional, project_optional
from app.pipeline.metrics_engine import metric_applies


def provider_id(model: str) -> tuple[str, str | None]:
    """LiteLLM model name -> Promptfoo provider id (+ a note when the env var differs or the mapping is a guess)."""
    prov, _, name = model.partition("/")
    if not name:
        prov, name = ("anthropic", model) if model.startswith("claude") else ("openai", model)
    table = {"gemini": ("google", "GOOGLE_API_KEY (Promptfoo's Google provider; LiteLLM uses GEMINI_API_KEY)"), "openai": ("openai", None),
             "anthropic": ("anthropic:messages", None), "vertex_ai": ("vertex", None), "ollama": ("ollama:chat", None),
             "fake": ("echo", "the 'fake' model is a test double: Promptfoo's echo provider returns the rendered prompt")}
    if prov in table:
        pf, note = table[prov]
        return ("echo" if prov == "fake" else f"{pf}:{name}"), note
    return f"{prov}:{name}", f"unknown provider '{prov}': check this id against Promptfoo's provider list"


def _js(expr: str, value: str | None = None) -> dict:
    return {"type": "javascript", "value": expr}


def check_assertion(c: dict, scenario: Scenario) -> dict | None:
    """One declarative check -> one Promptfoo assertion (None: not exportable)."""
    t, ic = c["type"], c.get("ignore_case", False)
    lit = json.dumps
    if t in ("max_words", "min_words"):
        n = c["value"]
        op = "<=" if t == "max_words" else ">="
        return _js(f"output.trim().split(/\\s+/).filter(Boolean).length {op} {n}")
    if t == "max_chars":
        return _js(f"[...output].length <= {c['value']}")
    if t in ("regex_required", "forbidden_regex"):
        neg = t == "forbidden_regex"
        if ic:
            return _js(f"{'!' if neg else ''}new RegExp({lit(c['pattern'])}, 'i').test(output)")
        return {"type": "not-regex" if neg else "regex", "value": c["pattern"]}
    if t in ("contains_all", "contains_none"):
        base = "icontains-any" if ic else "contains-any"
        return {"type": f"{'icontains' if ic else 'contains'}-all", "value": c["values"]} if t == "contains_all" else {"type": f"not-{base}", "value": c["values"]}
    if t == "json_valid":
        return {"type": "is-json"}
    if t == "json_schema":
        return {"type": "is-json", "value": c["schema"]}
    if t == "equals":
        want = scenario.expected_behavior if c["value"] == "$expected" else c["value"]
        return _js(f"output.trim().toLowerCase() === {lit(want.strip().lower())}") if ic else _js(f"output.trim() === {lit(want.strip())}")
    if t == "starts_with":
        v = c["value"]
        return _js(f"output.trimStart().toLowerCase().startsWith({lit(v.lower())})") if ic else _js(f"output.trimStart().startsWith({lit(v)})")
    return None  # language_is


def metric_assertions(m: Metric, scenario: Scenario, judge_pf: str, warnings: set[str]) -> list[dict]:
    d = m.definition
    if m.kind == "programmatic":
        subs = []
        for c in d["checks"]:
            a = check_assertion(c, scenario)
            if a is None:
                warnings.add(f"metric '{m.name}': check '{c['type']}' has no Promptfoo equivalent and was not exported")
            else:
                subs.append(a)
        if not subs:
            return []
        agg = d.get("aggregate", "all_pass")
        if agg == "all_pass":
            return [{**a, "metric": m.name, "weight": m.weight} for a in subs]
        return [{"type": "assert-set", "metric": m.name, "weight": m.weight, "threshold": 0.01 if agg == "any_pass" else 0, "assert": subs}]
    if is_pairwise(m):
        return [{"type": "select-best", "value": d["rubric"], "metric": m.name, "weight": m.weight}]
    examples = "".join(f"\nPASS example: {e}" for e in d.get("pass_examples", [])) + "".join(f"\nFAIL example: {e}" for e in d.get("fail_examples", []))
    a = {"type": "llm-rubric", "value": d["rubric"] + examples, "metric": m.name, "weight": m.weight}
    if m.judge_model:
        a["provider"] = provider_id(m.judge_model)[0]
    return [a]


def build_config(s: Session, project_id: str, scope: str = "plan") -> tuple[dict, list[str]]:
    p = s.get(Project, project_id)
    pv = {v.label: v for v in s.exec(select(PromptVersion).where(PromptVersion.project_id == project_id)).all()}
    if "old" not in pv or "new" not in pv:
        raise ValueError("save old and new prompts first")
    plan = s.exec(select(Plan).where(Plan.project_id == project_id)).first()
    scns = [x for x in s.exec(select(Scenario).where(Scenario.project_id == project_id)).all() if x.status != "rejected"]
    chosen = (plan.chosen or {}).get("scenario_ids") if plan else None
    if scope == "plan" and chosen:
        keep = set(chosen)
        scns = [x for x in scns if x.id in keep]
    scns.sort(key=lambda x: x.id)
    if not scns:
        raise ValueError("no scenarios to export yet")
    metrics = [m for m in s.exec(select(Metric).where(Metric.project_id == project_id)).all() if m.status != "rejected"]
    clusters = {c.id: c.name for c in s.exec(select(ScenarioCluster).where(ScenarioCluster.project_id == project_id)).all()}
    target_pf, note_t = provider_id(p.target_model)
    judge_pf, note_j = provider_id(p.judge_model_default)
    warnings = {n for n in (note_t, note_j) if n}
    optional = project_optional(s, project_id)
    tests = []
    for x in scns:
        asserts = [a for m in metrics if metric_applies(m.applies_to, x.category, x.tags) for a in metric_assertions(m, x, judge_pf, warnings)]
        tests.append({"description": f"{x.id} [{x.category}]", "vars": fill_optional(x.input, optional),
                      "metadata": {"scenario_id": x.id, "category": x.category, "cluster": clusters.get(x.cluster_id), "severity": x.severity,
                                   "tier": x.tier, "reviewed": x.reviewed, "tags": x.tags},
                      "assert": asserts})
    cfg = {
        "description": f"{p.name}: old vs new prompt (exported by Prompt Change Validator)",
        "prompts": [{"label": "old", "raw": pv["old"].text}, {"label": "new", "raw": pv["new"].text}],
        "providers": [{"id": target_pf, **({} if target_pf == "echo" else {"config": {"temperature": settings.target_temperature}})}],
        "defaultTest": {"options": {"provider": judge_pf}},
        "evaluateOptions": {"repeat": (plan.chosen or {}).get("repeats", settings.default_repeats) if plan else settings.default_repeats},
        "tests": tests,
    }
    return cfg, sorted(warnings)


def to_yaml(cfg: dict, warnings: list[str]) -> str:
    head = ["# Promptfoo config exported by Prompt Change Validator.",
            "# Run: npx promptfoo@latest eval -c promptfooconfig.yaml --no-cache   (--no-cache so repeats are real samples)",
            "# Assertions are tagged with `metric:` (our metric names). Noise-adjusted paired analysis is not part of Promptfoo."]
    head += [f"# NOTE: {w}" for w in warnings]
    return "\n".join(head) + "\n" + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=120)
