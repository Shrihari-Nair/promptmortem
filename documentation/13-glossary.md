# 13. Glossary

**A/A control.** Comparing the old prompt against itself. Whatever difference shows up is noise. Used to set a floor on how noisy a pairwise judge is.

**Adaptive repeats.** Running more repeats only for cases that look different from the old prompt. Cheaper than fixed repeats only when most cases are quiet.

**Agent.** A single model call with fixed instructions and a strict, validated output format. Not autonomous.

**Approved, proposed, rejected.** The review state of a scenario or metric. Unreviewed ones still run and are reported as unreviewed.

**Bonferroni correction.** Testing many things makes false alarms likely. The bar for significance is divided by the number of tests so the whole run has about a 5% chance of any false alarm.

**Bootstrap interval.** A confidence range found by resampling the cases many times and seeing how much the average moves.

**Broken / fixed / unchanged / inconclusive.** The four outcomes for a case on a metric. Inconclusive means it looks different but cannot be told apart from noise, or the design lacked power.

**Calibration.** Measuring how well a judge agrees with your own pass and fail labels.

**Cassette.** A JSON dump of recorded model calls (the `LLMCall` rows). Loaded into the cache it lets a test replay real model output with no network and no cost.

**Cap (hard cap).** The most you allow a run to spend. At the cap, the run pauses and asks.

**Cohen's kappa.** Agreement between a judge and your labels after subtracting the agreement expected by chance. 1 is perfect, 0 is no better than guessing.

**Cluster.** A group of similar scenarios. Rejecting one case can reject its cluster.

**Coverage lost.** What a cheaper plan gives up, per category and cluster. Shown for every tier.

**Dataset.** Optional rows of inputs with expected behaviour. They become verified scenarios.

**Deep dive.** After the main stage, the tool generates neighbours of broken cases to check how widespread the regression is.

**Diff relevance.** How strongly a scenario exercises something that changed between the prompts.

**Double bezel.** The UI panel style: an outer tray with an inner core.

**Ensemble (second judge).** An optional second, different model that also scores an output. Disagreements are shown but do not change the verdict.

**Fake model.** A deterministic offline stand-in for a real model (any name starting with `fake/`). Used in tests and demos.

**Fault injection.** Making a fake model misbehave on purpose (garbage or truncated JSON, timeouts) to check the tool copes.

**Feedback.** Every approve, reject, edit, comment or override you make. Stored and fed back into the agents.

**GEPA.** A reflective prompt-optimization method (from DSPy) used in the Refine step.

**Golden benchmark.** Fake projects with known truth (a planted regression, improvement and no-op) used to validate the statistics.

**Held-out set.** Scenarios the optimizer never sees, used to check candidate prompts fairly.

**Judge.** A model that scores an output against a rubric. Pointwise judges score one output. Pairwise judges pick the better of two. Judges are blind to which prompt wrote which output.

**Job.** A background task with a status and progress text. The browser polls it.

**Levers.** Switches that reduce cost: programmatic first, cheap judge for easy rubrics, adaptive repeats, early stopping.

**Nightly sweep.** The golden benchmark run over several noise levels and replicates, compared to a committed baseline of error rates.

**LiteLLM.** The library that gives one interface to many model providers.

**LLMClient.** The single place model calls happen. Caches, tracks cost, enforces the cap.

**McNemar test.** An exact test for pass and fail outcomes on the same cases, counting how many went each way.

**Metric.** A way to score one output. Programmatic (code) or LLM judge.

**Noise.** How much the same prompt disagrees with itself across repeats.

**Net effect.** The weighted average of per-case differences between new and old, with a confidence interval.

**Pairing.** Comparing old and new on the same input and the same repeat.

**Pinned.** A scenario you always want tested. Never dropped by a tier.

**Placeholder.** A `{{variable}}` in a prompt that gets filled with a scenario's input.

**Probe.** A small measured run used to learn real per-call costs before estimating.

**Programmatic first.** Code checks run before judges, and judges skip outputs that already failed a code check.

**Promptfoo.** An open-source prompt evaluation tool. Prompt Mortem can export its tests to a Promptfoo config.

**Recommendation.** The rule-based level in the report: ok, caution, block, incomplete or inconclusive. Advisory.

**Regression guard.** A scenario built from behaviours the old prompt really shows, to catch the new prompt losing them.

**Repeat.** One more run of the same input through the same prompt, used to see randomness.

**Review queue.** The short list of scenarios that most need your eyes: cluster representatives, critic-flagged cases and the most change-relevant case per cluster.

**Rubric.** The written instructions a judge scores against.

**Scenario.** One test case: an input, an expected behaviour, a category, a severity.

**Scope stamp.** The part of a report that states exactly what was tested and what was not.

**Severity.** How bad a failure on a scenario would be (1 to 5). Severity 4 or more is protected and can trigger a BLOCK.

**Smoke stage.** A small first run that catches setup mistakes early and measures noise.

**Stale.** A step whose upstream data changed after it was done. Flagged, never recomputed automatically.

**Synthetic vs verified.** Verified scenarios come from your labelled data. Synthetic ones were generated by the tool.

**Tier.** A plan size: Full, Balanced or Lean.

**Trace.** A real input (optionally with its output and a pass or fail label) from production. A real input, but not ground truth.

**Verdict override.** Marking a single case "actually fine", "broken" or "improved". Applied on every rebuild of the report.
