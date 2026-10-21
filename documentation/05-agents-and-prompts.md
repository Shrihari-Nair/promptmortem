# 5. The agents and their prompts

An "agent" here is a single, focused model call with fixed instructions and a strict output format. They run one at a time within a step. They are not autonomous and do not call each other. During a run (step 6) there are no agents: just target-model calls, code checks and judge calls.

## The agent runner

All agents go through `run_agent` in `backend/app/agents/base.py`:

1. **Load a versioned prompt file** from `backend/app/agents/prompts/`, for example `scenario_generator.v1.md`, and fill in `{{variables}}`. The first line records the agent name and version (`<!-- agent: NAME vN -->`). Versioned files make prompt changes visible in git.
2. **Call `LLMClient`** with JSON output requested.
3. **Validate the reply** against a Pydantic model (`agents/schemas.py`). Code fences around the JSON are stripped first.
4. **Repair-retry:** if validation fails, the invalid reply and the error message are sent back with "return ONLY corrected JSON". This repeats up to two times, then an `AgentError` is raised.

Every agent output is therefore a validated object, never free text that code has to guess at.

## The agents

| Agent | Step | What it does | Temperature |
|-------|------|--------------|-------------|
| `prompt_drafter` | 1 | Applies a requested change to the old prompt, keeping placeholders and constraints. Lists each edit. | 0.2 |
| `context_analyzer` | 2 | Writes the context card: task, users, output format, risks, assumptions. | 0.2 |
| `diff_analyzer` | 2 | Lists every explicit and implicit prompt change with what each could affect. | 0.0 |
| `scenario_generator` | 3 | Produces test inputs for one category or one change, with severity and expected behaviour. | 0.9 |
| `scenario_critic` | 3 | Removes duplicates and unrealistic cases, flags uncertain ones, names coverage gaps. | 0.0 |
| `metric_designer` | 4 | Proposes programmatic checks and judge rubrics, with examples. | 0.3 |
| `judge` | 6 | Scores one output against a rubric (pass or fail with reasoning). | 0.0 |
| `pairwise_judge` | 6 | Decides which of two outputs is better. | 0.0 |
| `reflector` | 8 | Proposes an improved prompt from the failures it is shown. | 0.7 |

The model used is the project's judge/agent model, except where a metric pins its own judge model or a cheaper one is used (see [06](06-cost-budget-and-caps.md)).

## Output shapes (`agents/schemas.py`)

- `ContextAnalysis`, `DiffAnalysis` with `Change` entries (intent, old and new text, risk, `could_affect`).
- `GenBatch` of `GenScenario` (input, rationale, expected behaviour, tags, severity, diff relevance).
- `CriticOut` with removals, flags and `Gap` entries.
- `MetricDesign` of `MetricSpec`, each either a `ProgDef` (list of `Check`) or a `JudgeDef` (rubric, scale, pass and fail examples).
- `JudgeOut` and `PairOut` for judge replies.
- `ReflectOut` (a prompt plus notes) for the drafter and reflector.

Validators enforce the important rules in code, for example "a judge metric must have pass and fail examples", and bad regexes or JSON schemas are rejected.

## Judges: how they stay honest

- **Blind.** Judge prompts say "output A" and "output B" and never mention old or new. The code decides the order.
- **Both orders.** The pairwise judge runs with A/B swapped. If the two orders disagree, the case is `disputed` and counts as a tie.
- **Code first.** A pointwise judge is skipped on any output that already failed a programmatic check (the `programmatic_first` lever). That pair is dropped from that metric rather than counted, and the report says how many calls were skipped.
- **Cached.** Judge calls are cached by content, so re-scoring identical outputs costs nothing.
- **Thinking off.** Judges run with the provider's hidden reasoning disabled where possible. This cut a judge call from 350 output tokens to 32 with the same verdict on Gemini 2.5.
- **Calibration.** You can measure a judge against your own pass/fail labels. A lenient judge shows up as low agreement on the `fail` label.
- **Verbosity bias guard.** The regression judge is built from the app's constraints and the intended changes, and is told not to reward length or polish. On real Gemini an unprimed judge rated 18 compliant, concise replies as regressions, which would have blocked a good change.
- **Optional second judge.** A rubric can opt in to a second, different model. Disagreements are stored and shown, but the primary verdict stands.

## The feedback loop into agents

`pipeline/feedback.py` turns stored `Feedback` rows into text for the next agent call.

- **Scenario generator:** "constraints learned from the reviewer", built from your rejections and comments, cluster-relevant first, with examples of rejected cases.
- **Metric designer:** your metric rejections, plus verdict overrides ("this metric may be too strict or too lenient").
- **Reflector (step 8):** your accept, edit and reject decisions on candidates, with the opening text of rejected ones.

## Provider "thinking" control

`core/reasoning.py`. Gemini 2.5 bills hidden reasoning tokens as output. Judges, the scenario generator and the critic run with thinking off (`judge_reasoning`, `generator_reasoning`, default `disable`). Gemini 2.5 Pro cannot turn it off, so it uses `low`. The model under test is left as your app runs it (`target_reasoning`, default `default`), because the tool measures what your app does. If your app disables thinking, set `PV_TARGET_REASONING=disable` to match and to cut the cost.
