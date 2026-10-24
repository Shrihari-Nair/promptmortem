# 6. Cost, tiers and the hard cap

The rule: **plan everything first, then price it, then trim.** Cost never shapes the plan. Code: `pipeline/cost_estimator.py`, `prioritizer.py`, `trimmer.py`, and the cap in `core/llm.py` and `pipeline/runner.py`.

## Why cost is estimated by measuring

Per-call prices vary with prompt length, reply length and model. So the tool measures instead of guessing.

**The probe.** A few scenarios (`probe_scenarios`, default 5), spread evenly over input length, are run on both prompts through the same function the real run uses. Those calls are cached, so the run reuses them for free. A few judge calls (pointwise and pairwise) are made too. The probe records the mean, 10th and 90th percentile cost per call, and the share of outputs that pass the programmatic checks. It is reused until prompts, models or metrics change, or you ask to measure again.

**The estimate.** For each scenario: `repeats x (old call + new call)` plus judge calls for each applicable judge metric (a pairwise judge is two calls per pair, one per order). Programmatic metrics cost nothing. Low, expected and high use the 10th percentile, the mean and the 90th percentile call cost.

## Ranking the scenarios

`prioritizer.py` gives each scenario a priority:

```
priority = 0.35 x diff relevance + 0.30 x severity/5 + 0.20 x uniqueness
         + 0.10 x (pinned or flagged) + 0.05 x disputed potential
```

(weights from `config.py`). Items are ranked greedily, so "uniqueness" is measured only against items already ranked above. "Disputed potential" is a category guess (ambiguous, adversarial, safety critical) until real runs measure disagreement.

## Tiers

`trimmer.py` builds three tiers from the ranking:

| Tier | Share of cases | Repeats |
|------|----------------|---------|
| Full | 100% | 3 |
| Balanced | 60% | 3 |
| Lean | 30% | 2 |

**Floor rule.** Pinned cases and severity 4 or higher are always included. Trying to drop them in a custom selection returns an error until you send `confirm_drop_protected`. The estimate warns when protected cases are over 40% of the plan, since the cheaper tiers then cannot be much smaller. (This once happened on real Gemini, which rated 56% of cases severity 4 or more. Anchors in the generator prompt now keep most cases at 1 to 3.)

**Coverage lost.** For every tier the tool lists what it gives up, per category and per cluster, plus clusters dropped entirely. You see this before you commit.

## Cost levers

Switches you can turn on or off. Defaults follow PLAN section 14.8.

| Lever | Effect |
|-------|--------|
| `programmatic_first` | LLM judges only run on outputs that pass the programmatic checks. |
| `cheap_judge_easy_rubrics` | Easy rubrics (pointwise, binary, short) use a cheaper judge model. The pairwise regression judge is never downgraded. |
| `adaptive_repeats` | More repeats only where cases look different. |
| `early_stopping` | Quiet cases stop after 2 repeats. |

Notes:
- Savings are estimates from assumed rates in `config.py`, and the response says so.
- **Adaptive repeats can cost more** than fixed repeats when many cases differ, so its saving can be negative. Early stopping is what saves money. They are modelled as one factor.
- The cheap judge is real: `PV_CHEAP_JUDGE_MODEL`, or derived from the judge's provider (Gemini flash-lite, gpt-4o-mini, Claude Haiku). If no cheaper model is known, the lever shows as unavailable, is forced off, and claims no savings. The report names the metrics judged by the cheaper model.
- low, expected and high assume 90%, 60% and 0% of cases are quiet. The default cap equals the **high** estimate, so it covers a run where every case is contested.
- Measured on the golden benchmark, actual spend stayed inside `[expected x 0.8, high]` in 34 of 34 runs. It was about 1.05x expected for no-op changes and 1.5 to 1.7x when many cases regress. The deep dive is not in the estimate; it asks before spending.

## The hard cap

- You confirm a plan with a cap (default: the tier's high estimate). A warning appears if the cap is below the expected cost.
- `LLMClient.complete()` checks spend against the cap **before every paid call** and raises `BudgetExceeded` when it is reached.
- The run catches this and **pauses** with reason `cap`. The UI shows **Extend and continue** (raise the cap and carry on) or **Stop and report partial** (a report flagged partial).
- Calls already in flight finish, so spend can pass the cap by a few calls.
- **Cache hits are free and never blocked.**
- A provider error pauses the run with the error text. It never silently drops cases.
- The deep dive needs its own headroom. If it would exceed the cap, the run pauses with `deep_dive_needs_budget` and asks.

## Why runs are cheap to retry

Calls are cached in the database by a hash of (model, messages, parameters, seed, repeat index). Resuming a run executes the same code, and every call already paid for returns instantly. Tests confirm there are no duplicate paid calls after cap pauses, provider crashes or a simulated restart.

## Real-model cost findings

- Target-model replies are about 70% of real run cost, since judges run with thinking off.
- Hidden reasoning tokens are billed as output on Gemini 2.5, so turning them off for judges and generators was the biggest saving.
- **Planning is outside the cap.** The cap governs the comparison run only. Analysis, scenario generation, metric design, judge calibration and the cost probe are paid before it and are not capped. On the real smoke run with `--cap 0.25`, planning added about $0.27 (total about $0.53). The report's cost block and `GET /projects/{pid}/spend` show planning spend next to run spend, and `scripts/pipeline_smoke.py` prints both.
- A typical full demo on the offline model costs nothing. A real small run on Gemini Flash costs cents. The real-model smoke script is normally run with a small cap such as `--cap 0.50`.
