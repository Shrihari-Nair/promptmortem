"""Golden benchmark: a billing-support bot with PLANTED behaviour (see fake_llm._app_reply). Used as the M6 gate and in CI.

variant="planted": the new prompt (a) is concise: fixes verbose answers to 'explain' questions (planted improvement) and
                   (b) over-promises refunds (planted regression).
variant="noop":    the new prompt is a pure rewording; identical behaviour (must be reported Unchanged).
`noise` is the injectable chance that a refund decision flips at random in ANY call (old-vs-old variance)."""
import json

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.models import Metric, Project, ProjectAnalysis, PromptVersion, Scenario, ScenarioCluster

OLD = {"verbose_on_explain": True, "overpromise": 0.0}
NEW = {"verbose_on_explain": False, "overpromise": 0.9}
TEMPLATE = "You are a billing support assistant for a SaaS product.\n<!-- fake-app: {params} -->\n\nCustomer: {{{{user_message}}}}"
NOOP_TEMPLATE = "You are a billing support assistant for a SaaS product. Help the customer.\n<!-- fake-app: {params} -->\n\nCustomer: {{{{user_message}}}}\n"

CLUSTERS = [  # name, category, tags, severity, message template
    ("refund questions", "regression_guard", ["refund"], 4, "Can I get a refund for invoice {i}? I was charged after cancelling."),
    ("explain questions", "diff_targeted", ["length_sensitive"], 2, "Please explain how proration works when I change plans (case {i})."),
    ("account chores", "happy_path", ["account"], 2, "How do I update the billing address on my account (case {i})?"),
]


def seed_golden(engine: Engine, variant: str = "planted", noise: float = 0.05, n_each: int = 20) -> str:
    old = TEMPLATE.format(params=json.dumps({**OLD, "noise": noise}))
    new = (TEMPLATE if variant == "planted" else NOOP_TEMPLATE).format(params=json.dumps({**(NEW if variant == "planted" else OLD), "noise": noise}))
    with Session(engine) as s:
        p = Project(name=f"golden-{variant}", app_description="Billing support bot", target_model="fake/app", judge_model_default="fake/judge")
        s.add(p)
        s.commit()
        s.add_all([PromptVersion(project_id=p.id, label="old", text=old), PromptVersion(project_id=p.id, label="new", text=new)])
        for name, cat, tags, sev, msg in CLUSTERS:
            cl = ScenarioCluster(project_id=p.id, name=name, category=cat)
            s.add(cl)
            s.commit()
            s.add_all([Scenario(project_id=p.id, cluster_id=cl.id, category=cat, tags=tags, severity=sev, status="approved", reviewed=True,
                                input={"user_message": msg.format(i=i)}, expected_behavior="Correct, concise answer within policy.",
                                diff_relevance=0.8 if cat == "diff_targeted" else 0.4) for i in range(n_each)])
        s.add_all([  # stage-1 output (the deep dive needs it to write neighbours of regressions)
            ProjectAnalysis(project_id=p.id, kind="context", data={"task": "Answer billing questions", "risk_areas": ["refund over-promising"]}),
            ProjectAnalysis(project_id=p.id, kind="diff", data={"changes": [{"id": "chg_1", "type": "tone_change", "new_text": "concise",
                                                                              "intent": "shorter answers", "could_affect": ["refund wording"], "risk": "medium"}]})])
        s.add_all([
            Metric(project_id=p.id, name="Under 120 words", kind="programmatic", status="approved", definition={"checks": [{"type": "max_words", "value": 120}], "aggregate": "all_pass"}),
            Metric(project_id=p.id, name="No refund over-promise (regex)", kind="programmatic", status="approved", weight=2.0, applies_to={"tags": ["refund"]},
                   definition={"checks": [{"type": "forbidden_regex", "pattern": "full refund", "ignore_case": True}], "aggregate": "all_pass"}),
            Metric(project_id=p.id, name="No refund over-promise (judge)", kind="llm_judge", status="approved", weight=2.0, applies_to={"tags": ["refund"]},
                   definition={"mode": "pointwise", "rubric": "Pass only if the reply does not promise a refund beyond policy.", "output": "binary",
                               "pass_examples": ["I can check whether this is eligible."], "fail_examples": ["You will get a full refund."]}),
            Metric(project_id=p.id, name="No regression vs previous output", kind="llm_judge", status="approved", weight=2.0,
                   definition={"mode": "pairwise", "rubric": "Which output answers better and follows the app's constraints?", "output": "binary", "pass_examples": [], "fail_examples": []}),
        ])
        s.commit()
        return p.id
