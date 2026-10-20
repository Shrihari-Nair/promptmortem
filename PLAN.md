# Prompt Change Validator: Implementation Plan

Hand this file to Claude Code. Build milestone by milestone. Do not start a milestone until the previous one's acceptance criteria pass. Read sections 0, 1 and 14-17 before writing any code: they explain intent, scope, concrete data shapes, and how the agents should behave.

---

## 0. Product intent (read first)

**The problem.** Developers constantly edit prompts because the current one has a flaw. Today they check the fix on a few examples by eye, ship it, and discover later that something else broke. Real regression suites need a labeled dataset, and most teams don't have one. Existing tools either optimize a prompt from scratch or give you an eval harness you must fill yourself.

**The product.** A tool that answers one question honestly: *"I changed this prompt. Did it fix what I wanted, and did it break anything else?"* It does this without requiring a dataset, by having agents think through the application, plan test scenarios and metrics, and run old vs new prompts side by side, with the human steering at every planning step.

**Why the rules in this plan exist.** When the plan is silent, decide by these:
- *Human checkpoints everywhere in planning:* agents share blind spots with the model under test. The human is the only source of ground truth about what the app should do. Their feedback must change what agents do next (it is stored and fed back), not just be logged.
- *Review only top cases:* a human who must read 200 cases will stop reviewing. Show representatives, disputed, and high-impact cases; let rejections generalize to a whole cluster.
- *Budget shown after planning:* planning should never be warped by cost anxiety. The user sees the full picture, then trades coverage for cost knowingly, with the lost coverage made explicit.
- *Paired, noise-adjusted results:* LLM outputs vary run to run. Without measuring old-vs-old noise, "improvements" are often just variance. Trust in the report is the product.
- *Scope stamp on every report:* we cannot test "all scenarios." The report must always say what was and wasn't tested, how much was human-reviewed, and how much is verified (user-labeled) vs synthetic. Never overclaim.
- *Programmatic checks before LLM judges:* they are free, deterministic, and not subject to judge bias.
- *Dataset optional, never required:* a small dataset (10-20 items) greatly improves trust (seeds generation, calibrates the judge, forms a verified tier) but the tool must work without it.
- *Platforms are adapters:* eval platforms (Promptfoo first) are optional backends and export targets. The differentiators stay in our code: change-aware selection, paired analysis with noise, budget harness, human checkpoints.

**Who it's for.** Developers shipping LLM-powered apps who change prompts regularly and don't have labeled eval data.

**What success looks like.** A dev would ship or block a prompt change based on our report. Everything else is polish.

## 0.1 Scope decisions for v1 (defaults; change only if the user says so)

1. **Single-turn text prompts only.** One prompt template + one input in, one text/JSON output out. No tool calling, no multi-turn, no RAG retrieval in v1. Design the `Scenario.input` and runner interfaces so multi-turn can be added later (inputs are JSON, not strings), but do not build it now.
2. **Single-user local app.** No multi-tenant auth. A simple optional API token is enough. SQLite file per workspace.
3. **Providers:** any provider supported by LiteLLM; Anthropic and OpenAI tested first. Local models (Ollama) supported only via LiteLLM config, not specially tested.
4. **Traces:** generic JSONL import in v1 (`input`, optional `output`, optional `expected`). Langfuse/LangSmith connectors are v2.
5. **Languages:** English scenarios by default; multilingual category generated only if the app description mentions other languages.
6. **Programmatic metrics are declarative** (a JSON spec of checks), never arbitrary generated code execution, in v1.

---

## 1. What we're building

A tool for developers who want to **change a prompt and know whether the change helped or broke something**, without having a labeled dataset.

An agentic pipeline that:
1. Takes the app description, old prompt, new prompt, and (optionally) a small dataset or traces.
2. Plans an exhaustive set of test scenarios and metrics, with human review at each stage.
3. Estimates cost **after** planning, and lets the user trim by priority.
4. Runs old vs new prompts in a staged, budget-capped way.
5. Produces a paired, noise-adjusted report (fixed / broken / unchanged), clearly scoped to what was actually tested.
6. Optionally proposes a refined prompt (DSPy GEPA), validated on held-out scenarios.

### Design principles (do not violate)
- **Human checkpoints at every planning stage.** Humans can approve, edit, reject, or comment on scenarios, metrics, and results. Feedback is persisted and fed back to the agents so rejected items are not re-proposed.
- **Review top cases only.** Group by cluster; show representatives, disputed cases, and highest-impact cases. A rejection can generalize to a whole cluster.
- **Dataset is optional.** If provided it seeds generation, calibrates the judge, and forms a "verified" tier.
- **Budget is shown after planning, not before.** Planning is never cost-constrained.
- **Honesty about the score.** Every report shows verified vs synthetic tiers, unreviewed share, coverage map, and noise level. Never claim "no regressions" without scope.
- **Programmatic checks before LLM judges.** Free checks first.
- **Platforms are adapters, not the core.** Promptfoo export first. Core stays ours.

---

## 2. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.11+, FastAPI | async, Pydantic v2 models |
| LLM access | LiteLLM | provider-agnostic; per-call token and cost capture |
| Agents | Plain Python modules with typed inputs/outputs; DSPy signatures for scenario generator, rubric writer, judge | keeps GEPA optimization of these possible later |
| Storage | SQLite via SQLModel (Postgres-ready) | one DB file per workspace |
| Jobs | asyncio task runner with persisted job state (no Celery in v1) | resumable after restart |
| Streaming | Server-Sent Events for run progress | |
| Embeddings / clustering | sentence-transformers (local, small model) + HDBSCAN or agglomerative | for near-duplicate detection and cluster review |
| Stats | numpy, scipy | bootstrap CIs, McNemar, paired tests |
| Frontend | React + TypeScript + Vite, TanStack Query, Tailwind, shadcn/ui, Recharts | |
| Tests | pytest, pytest-asyncio, vitest, Playwright (smoke) | |

All model calls go through one `LLMClient` wrapper that records tokens, latency, cost, model, and caches by hash of (model, messages, params, seed) so reruns are cheap and deterministic where possible.

---

## 3. Repo structure

```
prompt-validator/
  backend/
    app/
      main.py
      api/                 # routers: projects, scenarios, metrics, plans, runs, reports, imports, exports
      core/
        llm.py             # LLMClient (LiteLLM), caching, cost tracking, hard caps
        db.py, models.py   # SQLModel tables
        jobs.py            # persisted async job runner
        events.py          # SSE
      agents/
        context_analyzer.py
        diff_analyzer.py
        scenario_generator.py
        scenario_critic.py     # dedupe, realism check, coverage gaps
        metric_designer.py
        judge.py
        reflector.py           # prompt rewrite (GEPA phase)
      pipeline/
        planner.py             # orchestrates stages 1-4
        clustering.py
        prioritizer.py
        cost_estimator.py
        trimmer.py
        runner.py              # staged execution
        analyzer.py            # paired stats, noise, calibration
        report.py
      adapters/
        dataset_import.py      # CSV/JSONL
        traces_import.py       # generic JSONL first; Langfuse/LangSmith later
        promptfoo_export.py
      optimize/
        gepa_runner.py
    tests/
  frontend/
    src/
      pages/ components/ api/ hooks/
  docs/
  PLAN.md
  README.md
```

---

## 4. Data model (SQLite tables)

- **project**: id, name, app_description, constraints (text), target_model, judge_model_default, created_at
- **prompt_version**: id, project_id, label (`old` | `new` | `candidate_n`), text, variables_schema (json), created_at
- **dataset_item**: id, project_id, input (json), expected (json, nullable), source (`user` | `trace`), label_notes
- **scenario**: id, project_id, cluster_id, category, input (json), rationale, expected_behavior (text), tags (json), origin (`generated` | `dataset` | `user`), status (`proposed` | `approved` | `rejected` | `edited`), pinned (bool), severity (1-5), diff_relevance (0-1), priority_score, reviewed (bool), tier (`verified` | `synthetic`)
- **scenario_cluster**: id, project_id, name, category, description, centroid_embedding (blob), status
- **metric**: id, project_id, name, kind (`programmatic` | `llm_judge`), definition (json: code spec or rubric text + scale), applies_to (category/tag filter), judge_model, weight, status, notes
- **feedback**: id, project_id, target_type, target_id, action (`approve` | `reject` | `edit` | `comment`), comment, created_at (agents read this when regenerating)
- **plan**: id, project_id, stage (`scenarios` | `metrics` | `budget` | `ready`), coverage_map (json), locked_at
- **cost_estimate**: id, plan_id, tier (`full` | `balanced` | `lean` | `custom`), n_scenarios, n_calls_target, n_calls_judge, repeats, low, expected, high, coverage_lost (json)
- **run**: id, plan_id, tier, budget_cap, status, spent, started_at, finished_at
- **run_result**: id, run_id, scenario_id, prompt_version_id, repeat_idx, output, latency, tokens_in, tokens_out, cost
- **score**: id, run_result_id, metric_id, value, rationale, judge_model, judge_repeat_idx
- **report**: id, run_id, summary (json), generated_at
- **calibration**: id, project_id, metric_id, n_labeled, agreement, details (json)

---

## 5. Pipeline stages

### Stage 0: Project setup
Inputs: app description, constraints/non-negotiables, target model, old prompt, new prompt (or "change request" text that the tool applies to produce the new prompt), optional dataset (CSV/JSONL), optional traces, optional "known failure" notes.

### Stage 1: Understand
- `context_analyzer`: extracts task, user types, inputs/outputs, risk areas, success criteria from the description and prompts. Output shown to the human for edit (a short "here's what I think your app does" card).
- `diff_analyzer`: structured diff of old vs new prompt (added/removed/changed instructions, tone, format, constraints). For each change, lists **behaviors it could plausibly affect**. This drives change-aware prioritization.

### Stage 2: Scenario planning (exhaustive, uncapped)
- `scenario_generator` runs per category, seeded by dataset items if present. Categories: happy path, edge cases, ambiguous input, long/noisy input, out-of-scope, adversarial / injection, format-sensitive, multilingual (if relevant), safety-critical (if relevant), plus **diff-targeted** scenarios (one set per diff change) and **regression guards** (behaviors the old prompt got right that must remain).
- `scenario_critic`: removes near-duplicates (embedding similarity), flags unrealistic cases, finds coverage gaps and asks generator to fill them. Loop up to N rounds (configurable).
- `clustering`: groups scenarios; produces the **coverage map** (category x cluster x counts).
- **Review selection** (what the human sees first): per cluster 1-2 representatives, cases flagged by critic as uncertain, highest `diff_relevance`. Human actions: approve, reject, edit, pin, comment. **Rejecting a representative asks "reject whole cluster?"**; if yes, cluster is regenerated using the comment as guidance. Unreviewed scenarios still run; they are labeled `reviewed=false` in the report.

### Stage 3: Metric design
- `metric_designer` proposes per-category metrics. **Prefer programmatic** (JSON schema validity, regex, length bounds, required/forbidden phrases, tool-call correctness) and fall back to LLM-judge rubrics (1-5 or pass/fail with explicit criteria and examples).
- Always include a **regression metric** (does the new output preserve the old output's correct behavior?), implemented as a pairwise judge with position-swap to cancel bias.
- Human edits rubric text, weights, thresholds, applies_to filters. Can test a metric on 3-5 sample outputs inline before approving (cheap preview).
- If the dataset has `expected` values, use them directly as metrics and **calibrate** judge metrics against them (store agreement).

### Stage 4: Cost estimate and trim
1. `cost_estimator` computes per-scenario cost = target calls (old+new) x repeats + judge calls x judge repeats, using average token counts from a tiny probe (run 5 scenarios on each prompt and measure). Output **low / expected / high** (high = p90 output length).
2. `prioritizer` scores each scenario:
   `priority = w1*diff_relevance + w2*severity + w3*uniqueness + w4*pinned_or_flagged + w5*disputed_potential`
   (weights configurable; defaults in config). Uniqueness = 1 - max cosine similarity to higher-priority items.
3. `trimmer` produces tiers **Full / Balanced / Lean** and supports a custom slider. Each tier shows cost range, case count, and **coverage lost** (category/cluster diff vs the full map).
4. **Floor rules:** pinned and severity>=4 scenarios are never auto-trimmed; user must explicitly confirm to drop them.
5. **Free cost levers offered first** as toggles with estimated savings: programmatic checks over LLM judges, cheaper judge for easy rubrics, adaptive repeats, early stopping.
6. User picks a tier and sets a **hard cap** (default = high estimate of chosen tier).

### Stage 5: Staged execution
- **Stage A: Smoke** (~10% of cases, 2 repeats). Validates prompts render, metrics run, judge outputs parse, and measures **noise** (old prompt vs itself variance). If noise is too high to detect meaningful change, warn and suggest more repeats.
- **Stage B: Main run** over the chosen tier. Adaptive repeats: cases stable after 2 repeats stop; unstable ones get up to k.
- **Stage C: Deep dive** automatically adds scenarios near any regression found (generate neighbors of broken cases) if budget remains; asks first if it would exceed cap.
- Early stopping per case when verdict (fixed/broken/unchanged) has clear margin.
- Hard cap enforcement in `LLMClient`: when spend reaches cap, the run **pauses** and asks the user (extend / stop and report partial). Never silently truncate.
- Results stream to the UI via SSE. Runs are resumable.

### Stage 6: Analysis and report
- **Paired analysis** per scenario and per metric: old vs new, same inputs, repeats aggregated.
  - Binary metrics: McNemar / exact paired test. Scalar: paired bootstrap CI on mean difference.
  - **Noise-adjusted:** a change is only "fixed/broken" if it exceeds the old-vs-old noise band.
- Verdict buckets: **Fixed, Broken, Unchanged, Inconclusive (noisy)**.
- Report sections:
  1. Headline: net effect with CI, counts per bucket, ship/block recommendation (advisory).
  2. **Scope stamp:** tier run, coverage map of tested vs dropped, % reviewed vs unreviewed, % verified vs synthetic, noise level.
  3. Regressions first (grouped by cluster, with side-by-side outputs and judge rationale).
  4. Improvements.
  5. Per-metric and per-category breakdown.
  6. Judge reliability: calibration agreement if dataset exists; judge disagreement rate otherwise.
  7. Cost: spent vs estimate.
- **Human review of results:** user can mark a verdict as wrong ("this is actually fine" / "this is actually broken"), edit a rubric, and **re-score without re-running generations** (scores cached by output hash).
- Export: JSON, Markdown, and Promptfoo config.

### Stage 7 (optional): Prompt refinement
- Uses **DSPy GEPA** with: approved scenarios as trainset, approved metrics as the metric function, reflection on failures from Stage 6.
- Hold out a scenario subset (e.g. 30%, stratified by cluster) the optimizer never sees. The candidate prompt is validated on held-out via the same paired analysis against the *new* prompt and the *old* prompt.
- Output: candidate prompt(s) with diff, held-out results, and cost. The human accepts, edits, or rejects. Loop allowed with feedback.

---

## 6. Backend API (REST + SSE)

```
POST   /projects                         GET /projects/{id}
POST   /projects/{id}/prompts            # old/new versions
POST   /projects/{id}/dataset/import     # CSV/JSONL
POST   /projects/{id}/traces/import
POST   /projects/{id}/analyze            # stage 1 (job)
POST   /projects/{id}/scenarios/generate # stage 2 (job)
GET    /projects/{id}/scenarios?cluster=&status=&review_queue=true
PATCH  /scenarios/{id}                   # approve/reject/edit/pin
POST   /clusters/{id}/reject             # with comment -> regenerate
POST   /projects/{id}/metrics/generate
PATCH  /metrics/{id}
POST   /metrics/{id}/preview             # run on sample outputs
POST   /projects/{id}/plan/estimate      # returns tiers + coverage lost
POST   /projects/{id}/plan/trim          # custom selection
POST   /projects/{id}/runs               # tier, cap, levers
GET    /runs/{id}/events                 # SSE
POST   /runs/{id}/resume | /extend | /stop
GET    /runs/{id}/report
POST   /reports/{id}/verdict-override
POST   /reports/{id}/rescore
POST   /projects/{id}/optimize           # GEPA phase
GET    /projects/{id}/export/promptfoo
POST   /feedback
```

Long operations return a `job_id`; the UI subscribes to progress.

---

## 7. Frontend (required inputs and screens)

A wizard with a persistent left stepper (Setup > Understand > Scenarios > Metrics > Budget > Run > Report > Refine). Each stage can be revisited; changing upstream data marks downstream as stale.

1. **Setup**
   - App description (textarea), constraints/non-negotiables, known failure notes.
   - Old prompt and new prompt (side-by-side editor with live diff). Option: "describe the change" and let the tool draft the new prompt.
   - Prompt variables / input schema editor.
   - Target model + judge model dropdowns, API key config.
   - Optional: upload dataset (CSV/JSONL) with column mapper (input, expected); optional traces import.
   - Optional: Promptfoo toggle.
2. **Understand**: card with the inferred task/risks (editable), and the structured prompt-diff with "could affect" behaviors (editable, add missing ones).
3. **Scenarios review**
   - Coverage map heatmap (category x cluster).
   - Review queue: representatives, disputed, highest-impact. Each card: input, expected behavior, rationale; buttons Approve / Edit / Reject / Pin / Comment.
   - Reject-cluster dialog with "what's wrong" comment, then Regenerate.
   - "Add my own scenario" form. "Generate more in category X" button.
   - Counters: approved / unreviewed / rejected.
4. **Metrics review**: list of metrics with kind badge, rubric editor, applies-to filter, weight, "preview on samples" panel, calibration score (if dataset).
5. **Budget**
   - Tier cards (Full / Balanced / Lean) with low/expected/high cost, case count, and **coverage lost** diff.
   - Cost-lever toggles with estimated savings.
   - Custom trim: sortable table by priority with checkboxes, locked rows for pinned/high-severity.
   - Hard cap input. Confirm button.
6. **Run**: live progress by stage (smoke, main, deep dive), spend vs cap meter, noise estimate as it emerges, early regressions surfaced live, pause/extend/stop controls. Cap-hit modal.
7. **Report**: headline card, scope stamp, regressions-first list with side-by-side outputs and rationale, per-metric charts with CIs, judge reliability panel, cost panel. Per-case "verdict is wrong" action and "re-score" button. Export buttons.
8. **Refine (optional)**: start optimization, show candidate prompt diff, held-out results, accept/edit/reject.

UX rules: autosave everything; every agent-generated item has a visible origin and status; every list has filters; no step requires reviewing everything.

---

## 8. Key algorithms (specifics)

- **Diff relevance:** LLM maps each scenario to diff changes it could be sensitive to (multi-label), plus embedding similarity between scenario and changed-text; combine into 0-1.
- **Uniqueness / dedupe:** cosine similarity on embeddings; greedy selection (max marginal relevance) when trimming.
- **Noise estimation:** run old prompt twice (or k times) on the smoke set; compute per-metric variance; define the noise band as the 95% interval of the paired old-vs-old difference.
- **Judge hygiene:** fixed rubric, structured JSON output, temperature 0, position-swap for pairwise, optional 2-judge ensemble on disputed rubrics. Log disagreement.
- **Early stopping:** sequential test per scenario after each repeat; stop when posterior/CI excludes the noise band or after max repeats.
- **Caching:** key on (model, prompt text hash, input hash, params, repeat idx). Re-scoring and resume must hit cache.

---

## 9. Milestones and acceptance criteria

**M0: Skeleton (1-2 days).** Repo, FastAPI, SQLite models, LLMClient with cost tracking/caching/hard caps, React shell with stepper, CI. *Accept:* create project, save prompts, an LLM call logs cost and is cached on rerun.

**M1: Understand + diff.** context_analyzer, diff_analyzer, Setup and Understand screens, dataset import. *Accept:* given two prompts, UI shows an editable list of changes with "could affect" behaviors.

**M2: Scenario planning and review.** generator, critic, clustering, coverage map, review queue, reject-cluster regeneration with feedback. *Accept:* generating 150+ scenarios yields clusters; rejecting a cluster with a comment regenerates different scenarios (verified by test using stored feedback); coverage heatmap renders.

**M3: Metrics.** metric_designer, programmatic metric engine (sandboxed spec, not arbitrary code exec in v1: use declarative checks), judge with calibration, preview. *Accept:* metrics editable and previewable; judge calibration reported when dataset present.

**M4: Cost, priority, trim.** probe, estimator, prioritizer, trimmer, Budget screen. *Accept:* three tiers with low/expected/high; coverage-lost diff correct; pinned/high-severity protected; estimates within ~25% of actual on test projects.

**M5: Runner.** staged execution, noise estimation, adaptive repeats, early stopping, cap pause/resume, SSE. *Accept:* run resumes after server restart; hitting the cap pauses and prompts; no duplicate paid calls on resume.

**M6: Analysis and report.** paired stats, buckets, scope stamp, report UI, verdict override, re-score, exports (JSON/MD). *Accept:* on a synthetic benchmark with a planted regression and a planted improvement, report flags both and marks a no-op prompt tweak as Unchanged (noise-adjusted).

**M7: Promptfoo export + trace import.** *Accept:* exported config runs in Promptfoo and reproduces scenario inputs and assertions for programmatic metrics.

**M8: GEPA refinement.** gepa_runner, held-out validation, Refine screen. *Accept:* candidate prompt evaluated on held-out with paired report vs old and new.

**M9: Hardening.** auth (simple token), secrets handling, docs, example project, Playwright smoke across the full flow.

---

## 10. Testing strategy

- **Unit:** prioritizer, trimmer, cost estimator, stats functions (known-answer tests), clustering determinism with fixed seeds.
- **Fake LLM mode:** a deterministic mock provider so the full pipeline runs in CI without spend. Include a mock that injects controllable noise and planted regressions.
- **Golden benchmark:** a small bundled project (e.g. support-bot prompt) with known planted regression/improvement/no-op changes. Used as the acceptance test for M6.
- **Prompt tests for agents:** agent outputs validated against Pydantic schemas; retry with repair on parse failure.
- **E2E:** Playwright through the wizard with the fake LLM.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| No ground truth; judge shares blind spots | Programmatic checks first; dataset calibration; tiering verified vs synthetic; explicit report language |
| Judge gaming / overfitting in GEPA | Held-out scenarios never shown to optimizer |
| Cost surprises | Probe-based estimate, p90 high bound, hard cap with pause |
| Human fatigue | Representatives + disputed + high-impact only; cluster-level rejection |
| Noise masks real changes | Old-vs-old noise band, Inconclusive bucket, adaptive repeats |
| "Exhaustive" overclaims | Coverage map always shown; scope stamp on report |
| Stale downstream data after edits | Dependency tracking; mark stages stale |

---

## 12. Decisions

Resolved by default in section 0.1. Still to confirm with the user before the relevant milestone: default target and judge model names (before M0), and whether a hosted multi-user mode is ever wanted (before M9).

---

## 13. Instructions for Claude Code

- Work milestone by milestone; after each, run tests and summarize what's done vs the acceptance criteria.
- Keep agent prompts in versioned files under `backend/app/agents/prompts/`, not inline.
- Every LLM call goes through `LLMClient`; no direct provider calls.
- Every agent output is a Pydantic model; validate and repair.
- Keep config (weights, thresholds, default repeats, cluster params) in one `config.py` with env overrides.
- Write a short `docs/` note for each stage describing inputs, outputs, and human actions.
- Ask before deviating from the principles in section 1.


---

## 14. Concrete data shapes and examples

These are normative examples. Implement Pydantic models matching them; keep field names.

### 14.1 Project setup input
```json
{
  "app_description": "Customer support bot for a SaaS billing product. Answers questions about invoices, refunds, plans. Must never promise refunds outside policy. Replies under 120 words.",
  "constraints": ["Never reveal internal policy IDs", "Always reply in the user's language", "Refund promises only per policy doc"],
  "known_failure_notes": "Current prompt is too verbose and sometimes over-promises refunds.",
  "target_model": "claude-sonnet-4-5",
  "judge_model": "claude-sonnet-4-5",
  "prompt_old": "You are a helpful billing assistant. ... {{user_message}}",
  "prompt_new": "You are a concise billing assistant. Answer in under 120 words. ... {{user_message}}",
  "input_schema": {"user_message": "string", "plan_tier": "string?"}
}
```
Prompts use `{{variable}}` placeholders filled from `Scenario.input`.

### 14.2 Prompt diff analysis (output of `diff_analyzer`)
```json
{
  "changes": [
    {
      "id": "chg_1",
      "type": "added_constraint",
      "old_text": null,
      "new_text": "Answer in under 120 words.",
      "intent": "reduce verbosity",
      "could_affect": ["length of answers", "completeness of multi-part answers", "refund explanations that need detail"],
      "risk": "medium"
    },
    {
      "id": "chg_2",
      "type": "tone_change",
      "old_text": "helpful",
      "new_text": "concise",
      "intent": "terser tone",
      "could_affect": ["empathy in complaint cases", "greeting behavior"],
      "risk": "low"
    }
  ]
}
```

### 14.3 Scenario
```json
{
  "id": "scn_0042",
  "cluster_id": "cl_07",
  "category": "diff_targeted",
  "input": {"user_message": "I was charged twice in March and also want to know how to downgrade. Can you explain both and what refund I can expect?", "plan_tier": "pro"},
  "rationale": "Multi-part question likely to be truncated by the new 120-word cap; checks completeness vs concision tradeoff.",
  "expected_behavior": "Addresses both the double charge and the downgrade, without promising a refund beyond policy, in roughly <=120 words.",
  "tags": ["multi_part", "refund", "length_sensitive"],
  "linked_changes": ["chg_1"],
  "origin": "generated",
  "severity": 4,
  "diff_relevance": 0.9,
  "status": "proposed",
  "pinned": false,
  "reviewed": false,
  "tier": "synthetic"
}
```
Scenario `category` enum: `happy_path, edge_case, ambiguous, long_noisy, out_of_scope, adversarial, format_sensitive, multilingual, safety_critical, diff_targeted, regression_guard`.

### 14.4 Metric: programmatic (declarative)
```json
{
  "id": "met_len",
  "name": "Under 120 words",
  "kind": "programmatic",
  "applies_to": {"categories": ["*"]},
  "definition": {
    "checks": [
      {"type": "max_words", "value": 120},
      {"type": "forbidden_regex", "pattern": "POL-\\d+", "message": "leaked internal policy id"}
    ],
    "aggregate": "all_pass"
  },
  "scale": "binary",
  "weight": 1.0,
  "status": "proposed"
}
```
Supported check types in v1: `max_words, min_words, max_chars, regex_required, forbidden_regex, contains_all, contains_none, json_valid, json_schema, equals, starts_with, language_is`.

### 14.5 Metric: LLM judge rubric
```json
{
  "id": "met_refund_policy",
  "name": "No refund over-promise",
  "kind": "llm_judge",
  "applies_to": {"tags": ["refund"]},
  "definition": {
    "rubric": "Pass only if the reply does NOT promise or imply a refund, credit, or timeline that is not stated in the policy. Offering to check eligibility is fine.",
    "pass_examples": ["I can look into whether the charge is eligible for a refund."],
    "fail_examples": ["You'll definitely get a full refund within 2 days."],
    "output": "binary"
  },
  "scale": "binary",
  "judge_model": "claude-sonnet-4-5",
  "weight": 2.0,
  "status": "proposed"
}
```

### 14.6 Judge output (must be strict JSON)
```json
{"verdict": "fail", "score": 0, "rationale": "Reply states 'you will get a full refund', which is not supported by policy.", "confidence": 0.85}
```

### 14.7 Pairwise regression judge output
```json
{"winner": "A", "preserved_correct_behavior": false, "rationale": "Output B drops the downgrade instructions that A covered correctly.", "confidence": 0.7}
```
Run twice with A/B positions swapped; if results disagree, mark the case `disputed`.

### 14.8 Cost estimate
```json
{
  "tiers": [
    {"tier": "full", "n_scenarios": 240, "repeats": 3, "cost": {"low": 11.2, "expected": 14.8, "high": 19.5}, "coverage_lost": {}},
    {"tier": "balanced", "n_scenarios": 140, "repeats": 3, "cost": {"low": 6.4, "expected": 8.5, "high": 11.3}, "coverage_lost": {"long_noisy": "-50%", "tone": "-40%"}},
    {"tier": "lean", "n_scenarios": 70, "repeats": 2, "cost": {"low": 2.1, "expected": 2.9, "high": 3.9}, "coverage_lost": {"long_noisy": "-100%", "multilingual": "-80%"}}
  ],
  "levers": [
    {"id": "programmatic_first", "saving_expected": 3.1, "enabled": true},
    {"id": "cheap_judge_easy_rubrics", "saving_expected": 1.8, "enabled": false},
    {"id": "adaptive_repeats", "saving_expected": 2.4, "enabled": true},
    {"id": "early_stopping", "saving_expected": 1.2, "enabled": true}
  ],
  "protected_scenarios": 31
}
```

### 14.9 Report summary
```json
{
  "headline": {"fixed": 18, "broken": 4, "unchanged": 96, "inconclusive": 12, "net_effect": {"mean_diff": 0.06, "ci95": [0.01, 0.11]}, "recommendation": "caution: 4 regressions in refund_policy cluster"},
  "scope": {
    "tier": "balanced", "tested": 130, "dropped": 110,
    "reviewed_share": 0.34, "verified_share": 0.0,
    "noise_band": {"met_len": 0.02, "met_refund_policy": 0.08},
    "coverage_lost": {"long_noisy": "-50%"}
  },
  "regressions": [{"scenario_id": "scn_0042", "metric_id": "met_refund_policy", "old_pass_rate": 1.0, "new_pass_rate": 0.33, "disputed": false}],
  "judge_reliability": {"calibration_agreement": null, "pairwise_disagreement_rate": 0.07},
  "cost": {"estimated": 8.5, "spent": 7.9}
}
```
`recommendation` is advisory text produced by rules (any severity>=4 regression => block/caution), not by free-form LLM opinion.

### 14.10 Feedback record
```json
{"target_type": "cluster", "target_id": "cl_07", "action": "reject", "comment": "Our users never write multi-part questions this long. Make them short and messy.", "created_at": "..."}
```
The generator receives all prior feedback for the project (most recent and cluster-relevant first) as "constraints learned from the reviewer" in its prompt.

---

## 15. Agent specifications

All agents: structured JSON output validated by Pydantic, auto-retry up to 2 times with a repair message on parse failure, prompts stored as versioned files in `backend/app/agents/prompts/`, temperature per agent as listed, and every call through `LLMClient`.

| Agent | Input | Output | Temp | Key rules |
|---|---|---|---|---|
| context_analyzer | app description, constraints, both prompts, dataset sample | `{task, user_types, input_types, output_format, risk_areas[], success_criteria[], assumptions[]}` | 0.2 | List assumptions explicitly so the human can correct them. Never invent policies not in the input. |
| diff_analyzer | old prompt, new prompt, context | diff analysis (14.2) | 0 | Be exhaustive about *implicit* changes (reordered instructions, removed examples), not just added text. |
| scenario_generator | context, one category, diff analysis, dataset seeds, prior feedback, n | list of scenarios (14.3) | 0.9 | Diverse by construction: vary length, tone, user expertise, typos, intent. For `diff_targeted`, one batch per change. For `regression_guard`, derive from behaviors the old prompt does well (ask the old prompt via probe if needed). Inputs must be realistic, not obviously test-like. Use dataset seeds as style anchors. |
| scenario_critic | scenarios, embeddings, coverage map | `{duplicates[], unrealistic[], gaps[{category, description}], uncertain[]}` | 0 | Gaps feed back to the generator; loop up to `max_critic_rounds` (default 2). |
| metric_designer | context, approved scenarios by category, dataset (if any) | metrics (14.4, 14.5) | 0.3 | Prefer programmatic. Every LLM-judge metric needs pass/fail examples. Always add one pairwise regression metric. Keep metric count small (target 5-12). |
| judge | rubric, input, output | judge output (14.6) | 0 | Strict JSON only; cite evidence from output in rationale. Never see which prompt version produced the output (blind). |
| pairwise_judge | rubric, input, output A, output B | 14.7 | 0 | Run both orders. |
| reflector (stage 7) | failures, current prompt, rubric | rewritten prompt + change notes | 0.7 | Used inside GEPA's reflection step; keep edits minimal and explain each. |

**Blindness rule:** judges must never be told which output came from old vs new.
**Determinism:** use fixed seeds where the provider supports them, and cache every call.

---

## 16. Default configuration (`config.py`)

```
scenarios_per_category_initial = 25
max_critic_rounds = 2
dedupe_cosine_threshold = 0.92
review_queue_max_per_cluster = 2
smoke_fraction = 0.10
smoke_repeats = 2
default_repeats = 3
max_repeats_adaptive = 5
priority_weights = {diff_relevance: 0.35, severity: 0.30, uniqueness: 0.20, pinned_or_flagged: 0.10, disputed_potential: 0.05}
severity_protected_threshold = 4
tiers = {full: 1.0, balanced: 0.6, lean: 0.3}   # fraction of priority-ranked scenarios
cost_high_percentile = 0.90
probe_scenarios = 5
heldout_fraction = 0.30
judge_temperature = 0
significance_alpha = 0.05
```
All overridable via env or per-project settings.

---

## 17. Glossary and definition of done

- **Scenario:** one test input plus the expected behavior description.
- **Cluster:** group of similar scenarios; unit of human rejection and of coverage reporting.
- **Coverage map:** category x cluster counts, before and after trimming.
- **Verified vs synthetic:** verified = backed by user-provided labeled data; synthetic = agent-generated.
- **Reviewed:** a human approved or edited the scenario.
- **Noise band:** variation expected between two runs of the *same* prompt; changes inside it are Inconclusive/Unchanged.
- **Disputed:** judge repeats or position-swapped judgments disagree.
- **Tier:** a preset trim level (Full/Balanced/Lean).

**The project is done when:** a user with no dataset can go from two prompts and an app description to a scoped, noise-adjusted report in the UI, with human edits at the scenario and metric stages, a visible cost estimate with trim options before spending, a hard-capped run, and a golden-benchmark test proving planted regressions are caught and no-op changes are not flagged.
