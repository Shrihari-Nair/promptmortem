# 12. Decisions, lessons and known limits

## Why it is built this way

**Everything through one model client.** Cost tracking, caching and the hard cap are only trustworthy if no call can bypass them. So `LLMClient` is the single place a provider is called. Even the prompt optimizer's DSPy calls are routed through it.

**Cache by content.** The cache key is a hash of the model, messages, parameters, seed and repeat index. This makes resume, retry and re-scoring free, and it makes crash safety a side effect: re-running the same code never pays twice.

**Fake model first.** The deterministic fake model and the golden benchmark were built early and gate the report milestone. Statistics are only believable if you can plant a known regression and check the tool finds it, and plant a no-op and check it stays quiet.

**Exact tests over normal bands.** The first design used a 95% noise band. On the benchmark it flagged a no-op change as a regression in roughly a third of runs, because flips are rare and lopsided. Exact binomial tests with multiple-comparison correction replaced it.

**Never stop a flagged case early.** Stopping a case at 2 or 3 repeats the moment it looked different inflated false alarms. Flagged cases are now confirmed with more repeats instead.

**Programmatic checks before judges.** Code checks are free, exact and repeatable. Judges are the fallback, and are skipped on outputs that already failed a code check.

**Plan first, price later.** Letting cost shape the plan hides coverage loss. The plan is exhaustive, and then every tier states what it drops.

**Pause and ask at the cap.** A silently truncated run would produce a report that looks complete but is not. The cap pauses, and the report is labelled partial if you stop.

**Declarative metrics only.** Running user-supplied code is a security and reproducibility risk, and version 1 does not need it.

**Human feedback is data.** Rejections, comments and overrides are stored and fed back into the agents, so the tool gets closer to what you want instead of asking the same question twice.

## What testing on real Gemini taught us

Each of these was found by running against real Gemini and fixed.

| Finding | Fix |
|---------|-----|
| Hidden "thinking" tokens made judge calls about 9x more expensive than needed (350 versus 32 output tokens, same verdict). | Judges and generators run with thinking off. The target model is left as your app runs it. |
| An unprimed pairwise judge preferred longer answers and rated 18 compliant concise replies as regressions, producing a false BLOCK. | The regression judge is built from the app's constraints and intended changes and told not to reward length or polish. |
| The generator rated 56% of scenarios severity 4 or more, which made them all protected and the cheap tiers pointless. | Severity anchors in the generator prompt. The estimate warns when protected cases exceed 40%. |
| Calibration treated free-text "expected" descriptions as gold replies, so no judge could pass. | Calibration uses only explicit `pass` or `fail` labels with an observed output. |
| JSON `null` expected values were counted as labelled. | Fixed (JSON null versus SQL NULL). |
| Error text could expose key-shaped strings. | Redaction covers `api_key=`, bearer tokens and literal key values. |
| A handful of approved scenarios gave a 5/3/4 optimization split. | Approved-only is used only with at least 30 approved cases. |

## Bugs the checks caught

- A normal-approximation false-alarm rate (replaced by exact tests).
- Estimator and cost-lever double counting.
- Database objects expiring after a stale-mark commit (mark before refresh).
- Tests accidentally calling real Gemini (fakes are now pinned in tests).
- The auth middleware blocking `/api/health` once the API was mounted under `/api` (it now strips the mount prefix first).
- A mobile layout overflow caused by absolutely positioned screen-reader text.
- Focus moving on first page load, which broke the skip link as the first Tab stop.

## Known limits

- **Version 1 scope:** single-turn text prompts, one user, local only.
- **Synthetic benchmark:** the quoted error rates describe the benchmark's noise model, not every real model.
- **Judge blind spots:** judges can share weaknesses with the model under test. Calibration and the optional second judge help but do not remove this.
- **Repeat budget:** noisy pairwise judges may not reach significance for single cases within the repeat limit. They are then reported as inconclusive, and the metric-level tests still apply.
- **One model per run:** a result for one target model does not carry over to another.
- **Refinement is not resumable:** GEPA cannot resume, but retrying replays paid work from the cache for free.
- **Database migrations:** startup only adds missing columns. Renames and drops would need a real migration.
- **In-memory events:** the live event history is not persisted. After a restart the database state is the source of truth.
- **DSPy pin:** `LLMClientLM` uses an API that DSPy 3.5 removes, so DSPy stays pinned below 3.5.
- **Promptfoo export:** `language_is` cannot be exported, and Python-only regex syntax may differ in JavaScript. The export header lists these as warnings.

## Harness decisions

- **A cassette is the cache.** The cache table already stores everything a replay needs, so recording is a JSON dump and replay is seeding the cache. No new storage format.
- **Real output in tests.** The fake model returns perfectly valid JSON, so real-model failure shapes (fences, truncation, refusals) are covered by fault injection and by a recorded Gemini cassette.
- **Error rates are pinned.** The nightly sweep fails if power falls, false alarms rise or spend passes the high estimate, so a change to the statistics code cannot quietly make the verdicts worse.
- **Kappa next to agreement.** Raw agreement flatters a judge that agrees by default; kappa does not.
- **Seeded loops, not Hypothesis.** The property tests use stdlib `random` with fixed seeds to avoid a new dependency; the cost is no automatic shrinking of a failing case.

## Ideas that were deliberately left out

- Multi-turn conversations, tool calling and agent traces.
- User accounts and sharing.
- A hosted version.
- Running arbitrary custom code as a metric.
- Claude Code hooks and a milestone acceptance checklist for the development process (proposed as harness item 9, not built).
