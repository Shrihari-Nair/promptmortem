"""SQLModel tables, PLAN.md section 4. String ids like 'scn_ab12cd34' match the shapes in section 14."""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str):
    return Field(default_factory=lambda: new_id(prefix), primary_key=True)


def J(default: Any = None):
    return Field(default=default, sa_column=Column(JSON))


class Project(SQLModel, table=True):
    id: str = _id("prj")
    name: str
    app_description: str = ""
    constraints: str = ""
    target_model: str
    judge_model_default: str
    known_failure_notes: str = ""
    promptfoo_enabled: bool = True  # show the Promptfoo export in the UI
    created_at: datetime = Field(default_factory=now)


class PromptVersion(SQLModel, table=True):
    id: str = _id("pv")
    project_id: str = Field(foreign_key="project.id", index=True)
    label: str  # old | new | candidate_n
    text: str
    variables_schema: dict | None = J()
    created_at: datetime = Field(default_factory=now)


class DatasetItem(SQLModel, table=True):
    id: str = _id("ds")
    project_id: str = Field(foreign_key="project.id", index=True)
    input: dict = J({})
    expected: Any = J()
    source: str = "user"  # user | trace
    label_notes: str = ""


class ScenarioCluster(SQLModel, table=True):
    id: str = _id("cl")
    project_id: str = Field(foreign_key="project.id", index=True)
    name: str = ""
    category: str = ""
    description: str = ""
    centroid_embedding: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    status: str = "proposed"


class Scenario(SQLModel, table=True):
    id: str = _id("scn")
    project_id: str = Field(foreign_key="project.id", index=True)
    cluster_id: str | None = Field(default=None, foreign_key="scenariocluster.id", index=True)
    category: str
    input: dict = J({})
    rationale: str = ""
    expected_behavior: str = ""
    tags: list = J([])
    linked_changes: list = J([])
    origin: str = "generated"  # generated | dataset | user
    status: str = "proposed"  # proposed | approved | rejected | edited
    pinned: bool = False
    severity: int = 3
    diff_relevance: float = 0.0
    priority_score: float = 0.0
    reviewed: bool = False
    tier: str = "synthetic"  # verified | synthetic
    flagged: bool = False  # critic marked uncertain -> goes to review queue
    representative: bool = False  # cluster representative -> goes to review queue


class Metric(SQLModel, table=True):
    id: str = _id("met")
    project_id: str = Field(foreign_key="project.id", index=True)
    name: str
    kind: str  # programmatic | llm_judge
    definition: dict = J({})
    applies_to: dict = J({"categories": ["*"]})
    scale: str = "binary"
    judge_model: str | None = None
    weight: float = 1.0
    status: str = "proposed"
    notes: str = ""


class Feedback(SQLModel, table=True):
    id: str = _id("fb")
    project_id: str = Field(foreign_key="project.id", index=True)
    target_type: str  # scenario | cluster | metric | verdict ...
    target_id: str
    action: str  # approve | reject | edit | comment
    comment: str = ""
    created_at: datetime = Field(default_factory=now)


class Plan(SQLModel, table=True):
    id: str = _id("plan")
    project_id: str = Field(foreign_key="project.id", index=True)
    stage: str = "scenarios"  # scenarios | metrics | budget | ready
    coverage_map: dict | None = J()
    probe: dict | None = J()  # measured per-call costs (stage 4)
    chosen: dict | None = J()  # confirmed tier, cap, levers, frozen scenario_ids
    stale: dict | None = J()  # {stage: {reason, at}} stages whose upstream data changed after they were done
    locked_at: datetime | None = None


class CostEstimate(SQLModel, table=True):
    id: str = _id("ce")
    plan_id: str = Field(foreign_key="plan.id", index=True)
    tier: str  # full | balanced | lean | custom
    n_scenarios: int = 0
    n_calls_target: int = 0
    n_calls_judge: int = 0
    repeats: int = 1
    low: float = 0.0
    expected: float = 0.0
    high: float = 0.0
    coverage_lost: dict | None = J()
    scenario_ids: list | None = J()


class Run(SQLModel, table=True):
    id: str = _id("run")
    plan_id: str = Field(foreign_key="plan.id", index=True)
    tier: str = "balanced"
    budget_cap: float = 0.0
    status: str = "pending"  # pending | running | paused | done | stopped | failed
    spent: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stage: str = "pending"  # pending | smoke | main | deep_dive | done
    config: dict | None = J()  # frozen scenario_ids, repeats, levers, coverage_lost
    noise: dict | None = J()  # old-vs-old noise measured in the smoke stage
    warnings: list | None = J()
    pause_reason: str = ""  # cap | interrupted | error: ... | deep_dive_needs_budget
    error: str = ""


class RunResult(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("run_id", "scenario_id", "prompt_version_id", "repeat_idx"),)
    id: str = _id("rr")
    run_id: str = Field(foreign_key="run.id", index=True)
    scenario_id: str = Field(foreign_key="scenario.id", index=True)
    prompt_version_id: str = Field(foreign_key="promptversion.id")
    repeat_idx: int = 0
    output: str = ""
    latency: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


class Score(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("run_result_id", "metric_id", "judge_repeat_idx"),)
    id: str = _id("sc")
    run_result_id: str = Field(foreign_key="runresult.id", index=True)
    metric_id: str = Field(foreign_key="metric.id", index=True)
    value: float = 0.0
    rationale: str = ""
    judge_model: str | None = None
    judge_repeat_idx: int = 0
    disputed: bool = False  # pairwise: the two A/B orders disagreed
    details: dict | None = J()  # pairwise: verdict, preserved_correct_behavior, confidence


class Report(SQLModel, table=True):
    id: str = _id("rep")
    run_id: str = Field(foreign_key="run.id", index=True)
    summary: dict = J({})
    generated_at: datetime = Field(default_factory=now)


class Calibration(SQLModel, table=True):
    id: str = _id("cal")
    project_id: str = Field(foreign_key="project.id", index=True)
    metric_id: str = Field(foreign_key="metric.id", index=True)
    n_labeled: int = 0
    agreement: float | None = None
    details: dict | None = J()


class LLMCall(SQLModel, table=True):
    """Every LLM call: cost log + response cache (keyed by cache_key)."""
    id: str = _id("llm")
    cache_key: str = Field(index=True)
    model: str
    response: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    latency: float = 0.0
    run_id: str | None = Field(default=None, index=True)  # run that paid for the call (cache hits are not logged)
    project_id: str | None = Field(default=None, index=True)
    tag: str | None = None  # agent name, or "target" for the application under test
    created_at: datetime = Field(default_factory=now)


class ProjectAnalysis(SQLModel, table=True):
    """Stage 1 outputs (context card, prompt diff). Human-editable; `edited` marks human changes."""
    id: str = _id("an")
    project_id: str = Field(foreign_key="project.id", index=True)
    kind: str  # context | diff
    data: dict = J({})
    edited: bool = False


class Job(SQLModel, table=True):
    id: str = _id("job")
    project_id: str | None = Field(default=None, index=True)
    kind: str
    status: str = "pending"  # pending | running | done | failed | paused
    progress: str = ""
    result: dict | None = J()
    error: str = ""
    created_at: datetime = Field(default_factory=now)


class Optimization(SQLModel, table=True):
    """Stage 7 (GEPA) run: the held-out split, the candidates (with their held-out validation) and what it cost."""
    id: str = _id("opt")
    project_id: str = Field(foreign_key="project.id", index=True)
    status: str = "pending"  # pending | running | done | failed | paused
    config: dict | None = J()  # cap, max_metric_calls, split ids, seed version
    result: dict | None = J()  # seed/best scores, reflection log, candidates[]
    spent: float = 0.0
    error: str = ""
    created_at: datetime = Field(default_factory=now)
