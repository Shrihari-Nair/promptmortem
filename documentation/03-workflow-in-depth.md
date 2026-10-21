# 3. The workflow, step by step

For each step: what you do, what the system does, what is stored, and where the code is. Endpoint paths are relative to `/api`.

The steps are linked. If you change something upstream after a later step is done, the later steps are flagged "out of date" (see [Staleness](#staleness-tracking)).

---

## Step 1. Setup

**What you do**
- Name the project and describe the app, its non-negotiable constraints, and (optionally) what is wrong with the current prompt.
- Choose the target model (the one your app calls) and the judge/agent model. Empty means the server default (`gemini/gemini-2.5-flash`).
- Paste the old prompt. Paste the new prompt, or describe the change and click **Draft new prompt**. If you give no description, it uses your "what is wrong" note, or tightens the prompt against your constraints.
- Optionally upload a dataset (CSV or JSONL) and production traces (JSONL).

**What the system does**
- **Placeholders:** it finds `{{variables}}` in the prompts and builds an input schema (`string`, `string?`, `number`, `json`). A `string?` variable is optional: a scenario that omits it renders as empty text everywhere instead of failing.
- **Diff:** the screen shows a line-by-line difference between the prompts (`frontend/src/lib/setup.ts`).
- **Drafting** (`POST /prompts/draft`, agent `prompt_drafter`): applies only the requested change, keeps every placeholder and constraint, and restores any placeholder it dropped. The draft goes into the new-prompt box for you to edit. Nothing is saved until you continue.
- **Importing** (`adapters/dataset_import.py`): you map CSV columns or JSONL keys to prompt variables and to `expected`. Duplicate inputs are skipped and reported.
- **Dataset vs traces.** A dataset row with an expected value becomes a **verified**, pre-approved scenario. A trace is a real input but not ground truth: without an expected value it becomes a **synthetic**, unreviewed scenario that still seeds generation. Traces with an observed output and a `pass` or `fail` label are used later to calibrate judges.

**Stored:** `Project`, two `PromptVersion` rows (`old`, `new`), `DatasetItem` rows.

---

## Step 2. Understand

**What you do:** click **Analyze prompts**, read the result, correct it if needed, continue.

**What the system does** (`POST /projects/{id}/analyze`, a job; code in `api/analysis.py`)
- **`context_analyzer`** writes a context card: the task, who uses it, output format, risks, and explicit assumptions.
- **`diff_analyzer`** lists every explicit and implicit change between the prompts. Each change has an intent, a risk level and a `could_affect` list (for example, "removed the refund rule" could affect refund handling).
- Both agents use the project's judge/agent model and your "what is wrong" notes.

**Your edits matter.** `PUT /projects/{id}/analysis/{kind}` validates your edit against the same Pydantic model and saves it with an `edited` flag. Later steps build on your corrected version. Missed changes you add here get their own test cases in step 3.

**Stored:** two `ProjectAnalysis` rows (`context`, `diff`).

---

## Step 3. Scenarios

A scenario is one test input with an expected behaviour, a category, a severity (1 to 5) and a link to the changes it exercises.

**What the system does** (`pipeline/planner.py`; planning is never limited by cost)
1. **Import:** dataset rows become verified, pre-approved scenarios (once).
2. **Generate per category.** The scenario generator runs once per category: happy path, edge case, ambiguous, long noisy, out of scope, adversarial, format sensitive, plus multilingual and safety critical when your description or the risks mention them. It also runs one `diff_targeted` batch per change from step 2.
3. **Regression guards from real behaviour.** These are generated last. The tool runs the old prompt on a handful of realistic inputs (`regression_probe_n`, default 6) and shows the generator the actual replies. Guards then exercise behaviours the old prompt really has.
4. **Remove near-duplicates** by embedding similarity (cosine at or above `dedupe_cosine_threshold`, default 0.92).
5. **Critic rounds** (up to `max_critic_rounds`, default 2). The `scenario_critic` removes semantic duplicates and unrealistic cases, flags uncertain ones, and names coverage gaps. Gaps are generated and fed back.
6. **Diff relevance:** 0.7 times the generator's own score plus 0.3 times the embedding similarity to the changed text.
7. **Cluster** each category (`pipeline/clustering.py`): average-linkage on cosine distance, about `cluster_target_size` (8) scenarios per cluster. This is deterministic. A coverage map (category by cluster, with counts) is stored on the plan.

**Review queue** (`GET /projects/{id}/scenarios?review_queue=true`, `pipeline/review.py`): the cluster representative (closest to the centre, up to `review_queue_max_per_cluster`), any case the critic flagged, and the most change-relevant case per cluster. Unreviewed scenarios still run later and are reported as unreviewed.

**What you can do** (`PATCH /scenarios/{id}`)
- Approve, edit, reject, pin (always include in any plan) or comment on a case.
- Add your own scenario, or generate more in a category.
- Reject a whole cluster (`POST /clusters/{id}/reject`). Proposed cases in it are rejected. Approved, pinned and edited ones survive. Your reason is stored and the category is regenerated.

**Feedback loop** (`pipeline/feedback.py`): every action is a `Feedback` row. When the generator runs again, the relevant feedback (cluster-specific first, then recent, with the rejected examples) is added to its prompt as "constraints learned from the reviewer". Regeneration also avoids duplicating rejected cases.

**Stored:** `Scenario`, `ScenarioCluster`, `Feedback`, and the coverage map on `Plan`.

---

## Step 4. Metrics

A metric says how to score one output. There are two kinds.

- **Programmatic:** a declarative list of checks run in code. Check types: `max_words`, `min_words`, `max_chars`, `regex_required`, `forbidden_regex`, `contains_all`, `contains_none`, `json_valid`, `json_schema`, `equals`, `starts_with`, `language_is`. Aggregates: `all_pass`, `any_pass`, `mean`. No code is executed. Bad regexes or schemas are rejected at save time with a clear error (`pipeline/metrics_engine.py`).
- **LLM judge:** a rubric scored by a model. Pointwise (one output, pass or fail) or pairwise (two outputs, which is better).

**What the system does** (`POST /projects/{id}/metrics/generate`, `pipeline/metric_plan.py`)
- The `metric_designer` proposes a small set (capped at 12). It is told to prefer programmatic checks, and every judge metric must come with pass and fail examples (a validator rejects specs without them, which triggers a repair-retry).
- The code always adds one **pairwise regression metric** ("is the new output worse than the old one?"), built from your constraints and the intended changes, and told not to reward length or polish.
- If your dataset has gold `expected` values, it adds an `equals` metric (verified tier).

**What you can do**
- Approve, reject (with a comment fed to the next design round), or edit any metric: definition, what it applies to, weight.
- Add your own metric as JSON, for example `{"checks":[{"type":"max_words","value":100}],"aggregate":"all_pass"}`.
- **Preview** a metric on 3 to 5 sample outputs before trusting it.
- **Calibrate** a judge against your own labels (`POST /metrics/{id}/calibrate`). Only explicit labels count: a trace or dataset row with an observed output and `expected` of `pass` or `fail`. Agreement is stored overall and per label.
- Optionally enable a **second judge** on a rubric. It is shown as a disagreement rate and never overrules the primary verdict.

**Stored:** `Metric`, `Calibration`.

---

## Step 5. Budget

Cost is handled only now, after the exhaustive plan exists. See [06-cost-budget-and-caps.md](06-cost-budget-and-caps.md) for the detail.

**What you do:** click **Estimate cost**, compare the three tiers, pick one, set the hard cap, optionally switch cost levers on or off, optionally choose cases by hand, and confirm.

**What the system does**
- **Probes:** it runs a few cases on both prompts plus a few judge calls to measure real per-call cost.
- **Estimates** low, expected and high cost per tier.
- **Ranks** scenarios by priority, then builds the tiers: Full (100%), Balanced (60%), Lean (30%).
- **Floor rule:** pinned cases and severity 4 or higher are always kept. Dropping them in a custom selection needs explicit confirmation.
- **Coverage lost** is listed for every tier, per category and cluster, including clusters dropped entirely.
- **Confirming** freezes the scenario ids, repeats, levers and the cap on the plan.

**Stored:** `CostEstimate` rows and the `chosen` plan (tier, cap, levers, frozen ids).

---

## Step 6. Run

See [04-statistics-explained.md](04-statistics-explained.md) for how results are judged. The execution itself (`pipeline/runner.py`):

1. **Smoke stage** (10% of cases, at least 3, 2 repeats). It fails early if a placeholder has no value or a judge reply cannot be parsed. It runs an A/A control (old against old) for pairwise metrics, measures old-vs-old noise, and warns if noise is too high to call individual cases.
2. **Main stage:** for each case, several repeats of old and new. Code checks run first, then judges. A case stops after 2 repeats only if every metric that can still be decided is decided, or is unchanged with identical outputs. A flagged case is never stopped early: it is confirmed with more repeats (up to `max_repeats_adaptive`, default 5).
3. **Deep dive:** neighbours of broken cases are generated (up to 3 per regression, 12 in total) and run. If that would exceed the cap, the run pauses and asks.

**The cap.** `LLMClient` raises before any paid call once spend reaches the cap. The run pauses, and the UI offers **Extend and continue** or **Stop and report partial**. Calls already in flight finish, so spend can pass the cap by a few calls. Cache hits are free and never blocked.

**Resume and crash safety.** Every result row is looked up before a call and calls are cached, so resuming is just running the same code again. A provider error pauses the run with the error text and keeps all state.

**Live view.** `GET /runs/{id}/events` streams: snapshot, stage changes, noise, progress (spend against cap), regressions as they are found, warnings, pauses and done.

**Stored:** `Run`, `RunResult` (one per case, prompt version and repeat), `Score` (one per result and metric).

---

## Step 7. Report

`GET /runs/{id}/report` is always rebuilt from stored scores, so even a paused or stopped run gets a report (flagged partial).

**What you see**
- **Verdict:** a level (`ok`, `caution`, `block`, `incomplete`, `inconclusive`), a plain sentence, and counts of fixed, broken, unchanged and inconclusive cases, plus the net effect with a confidence interval.
- **Scope stamp:** tier, complete or partial, tested versus dropped cases, reviewed and verified shares, noise per metric, per-case detection thresholds, coverage lost, skipped judge calls, warnings.
- **Per metric:** a chart and table of the paired difference with 95% intervals and test p-values, plus a per-category table.
- **Regressions and improvements:** examples with old and new output side by side and the judge's reasoning.

**What you can do**
- Override a single verdict ("this is actually fine" or "broken" or "improved"). It is stored as feedback and applied on every rebuild, and it is fed to the metric designer.
- **Re-score** stored outputs with the current metrics (judge calls are cached by content, and the new scores replace the old ones in one transaction only if all succeed).
- Export Markdown, JSON, or a Promptfoo config.

**Stored:** `Report` (summary JSON).

---

## Step 8. Refine (optional)

See [07-prompt-refinement.md](07-prompt-refinement.md). You give a hard cap. The tool searches for an improved new prompt, validates each candidate on held-out cases using the same paired, noise-adjusted analysis, and shows full reports. You accept, edit or reject each candidate. It only offers a candidate that beats your prompt.

---

## Staleness tracking

`pipeline/stale.py`. Changing upstream data marks the stages that were already done as out of date:

- prompts changed: everything downstream
- analysis changed: scenarios onward
- scenarios changed: metrics onward
- metrics changed: budget onward
- a new plan: run and report

Approving a scenario or metric does not count as a change. Redoing a stage clears its own mark. `GET /projects/{id}/stale` lists marks, and `POST /projects/{id}/stale/{stage}/ack` means "I checked, it is still right". The UI shows a dot on the step and a banner. Nothing is recomputed automatically.
