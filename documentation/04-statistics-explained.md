# 4. How the verdict is decided

This is the part that makes the tool trustworthy. The code is in `backend/app/pipeline/verdicts.py` (pure maths, no database, no model calls), `collect.py` (gathers scores) and `report.py` (builds the verdict). It has known-answer tests in `tests/test_verdicts.py`.

## The core problem: randomness

Ask the same model the same question twice and you can get different answers. So if the new prompt scores 4 passes out of 5 and the old one scores 5 out of 5, that might be a real regression, or it might be chance. The tool needs to separate the two.

## Idea 1: compare the same things (pairing)

For every test case and every metric, the old prompt and the new prompt are scored **on the same repeats** of the same input. The comparison is per case, so differences between easy and hard cases do not drown out the change. If a repeat is missing on one side, it is dropped from both.

Scores are oriented so that higher is always better. A pairwise judge's preference runs from -1 (old is better) to +1 (new is better), and when the judge's two A/B orders disagree the case is marked `disputed` and counts as a tie.

## Idea 2: measure the noise first (old against old)

The tool runs the old prompt more than once on each case. How much the old prompt disagrees with itself is the noise level. Details:

- Noise is taken from the **first two repeats** of every case, which keeps it unbiased even though some cases stop early.
- It is estimated **per cluster**, and shrunk toward the global estimate. LLM noise is uneven (borderline cases flip, easy ones do not), so one global number would under-state noise where it matters.
- A small prior stops "no flips seen in a small sample" from being read as "zero noise" (`noise_prior_var`, `noise_prior_n`).
- For pairwise judges, an **A/A control** (old compared against old) sets a floor on the noise.

## Idea 3: an exact test, not a bell-curve band

For a pass/fail metric, the tool counts how many of the new prompt's repeats **deviate from the old prompt's usual outcome** and asks: how likely is that many deviations from noise alone? This uses the exact binomial distribution at the cluster's measured flip rate.

Why not a normal 95% band? It was tried and measured on the golden benchmark. Rare flips are lopsided, and a normal band flagged a no-op change as a regression in about a third of runs. The exact test, with confirmation repeats, flags about 5% (1 of 24 runs at 5% injected noise, 0 of 10 at 15%).

## Idea 4: correct for many comparisons

A run tests many case and metric pairs. Test enough of them and some will look significant by luck. The tool uses a **Bonferroni correction**: with `m` comparisons, the bar for "significant" is `alpha / m` (alpha is 0.05). That keeps the chance of any false alarm in the whole run near 5%.

## The four outcomes for each case and metric

| Outcome | Meaning |
|---------|---------|
| **fixed** | Significantly better, after correction. |
| **broken** | Significantly worse, after correction. |
| **inconclusive** | Looks different but not significant after correction, **or** the design had too little power to tell noise from change (too few repeats, or very noisy). |
| **unchanged** | No evidence of change, and enough power to have seen one. |

Two safeguards against false comfort:
- If even a complete flip could not reach significance at this noise level and number of repeats, any difference or instability is called **inconclusive**, never "unchanged".
- Continuous metrics (averages of checks, Likert scales) use a noise band corrected for multiple comparisons. Too-noisy metrics give inconclusive for small differences.

A case's overall bucket takes the worst first: **broken**, then fixed, then inconclusive, then unchanged. Your manual overrides apply on top.

## Whole-metric tests

Single cases can be too noisy to call, but many small signs add up. So each metric also gets an aggregate test across all cases:

- **McNemar exact test** for pass/fail metrics (how many cases went pass to fail versus fail to pass).
- **Sign test** for pairwise preferences.
- **Paired bootstrap** (2000 resamples of the cases, fixed seed) for a 95% confidence interval of the mean difference.
- **Net effect:** the weighted mean of per-case differences with a bootstrap interval. This is the number on the report.

## The recommendation (rules, not opinion)

`recommend()` in `report.py` turns the results into a level. The rules run in this order and stop at the first match:

1. **block:** any broken case with severity 4 or higher.
2. **block:** a metric got significantly worse overall on severity 4 or higher cases (Bonferroni-corrected over metrics, interval excludes zero), even if single cases are only inconclusive.
3. **caution:** any other broken case.
4. **caution:** a metric got significantly worse overall, with no single case conclusive.
5. **caution:** net effect is negative and its interval excludes zero.
6. **incomplete:** the run was partial, or planned cases were left untested, and no regressions were found among those tested.
7. **inconclusive:** more than 30% of cases are within the noise band. Rerun with more repeats.
8. **ok:** no regressions, with the number of improved cases if any.

The text is always labelled advisory.

## Reading a report that says "BLOCK" next to "184 fixed"

The counts and the verdict come from different evidence. "Fixed" counts cases and metrics where a clear improvement was proven, for example answers that now fit the word limit. A BLOCK can come from a pairwise regression judge that found a clear overall drop in quality, even when single cases are too noisy to call. Both can be true at once. Read the per-metric chart and table to see which metric drove the verdict.

## The scope stamp

Every report states what it covers, because a result is only as good as the testing behind it:

- tier (full, balanced, lean) and whether the run was complete or partial
- cases tested, dropped by the tier, planned but untested, added by the deep dive
- share reviewed by a human, and share verified by your labelled data (the rest is synthetic)
- repeats per case
- coverage lost, per category and cluster
- noise per metric (old against old)
- the per-case detection threshold, and a note when repeats are too few to detect a change
- judge calls skipped, and the metrics judged by a cheaper model

## Limits to keep in mind

- The benchmark that validates these error rates uses synthetic behaviours, so the rates describe that model of noise, not every real model.
- Judges can share blind spots with the model being tested.
- Per-case calls need enough repeats (5 by default for contested cases).
- The tool measures one model at one temperature setting (`target_temperature`, default 0.7). If your app runs differently, set it to match.
