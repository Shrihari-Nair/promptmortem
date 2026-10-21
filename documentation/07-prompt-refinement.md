# 7. Prompt refinement (step 8)

Optional. After a report, the tool can search for a better version of the new prompt. Code: `backend/app/optimize/` (`gepa_runner.py`, `dspy_bridge.py`, `split.py`) and `api/optimize.py`.

## What it uses

**GEPA** is a reflective prompt-optimization method from the DSPy library. It repeatedly: runs the current prompt on a small batch, reads what went wrong, asks a model to propose an edit, tests it, and keeps the better candidates.

Prompt Mortem plugs its own parts into GEPA so that the search is scored the same way as a real run.

| GEPA needs | What Prompt Mortem supplies |
|------------|-----------------------------|
| A program to optimize | A one-predictor DSPy program whose instruction **is** your prompt template. |
| A way to call the model | A custom adapter that fills `{{variables}}` exactly like the runner does, so GEPA scores what you would really send. |
| An LM | `LLMClientLM`, which routes every DSPy call through `LLMClient` (cache, cost, hard cap). |
| A metric | Your approved metrics. Programmatic checks first; judges only if those pass. It returns the score and a text explanation of why it was low. |
| A proposer | The `reflector` agent: minimal edits, constraints and placeholders kept, no copying of example inputs. A dropped `{{placeholder}}` is appended back. |

## The data split

Held-out data is the safeguard against fooling yourself.

1. Use approved scenarios if there are at least `gepa_reviewed_min` (30). Otherwise use all non-rejected scenarios and show a warning. (Using only a handful of approved cases once produced a 5/3/4 split on real data, too small to validate anything.)
2. Hold out 30% (`heldout_fraction`), **stratified by cluster**. Every cluster with two or more items puts at least one on each side.
3. Split the remainder into GEPA's own train and validation (70/30).

**The optimizer never sees held-out cases.** This is tested by spying on every target, judge and reflector call during a search. Regressions from the last report are shown to the reflector only for train and validation cases.

## Validation

Each candidate is saved as a prompt version (`candidate_N`) and compared with both the **new** prompt and the **old** prompt on the held-out cases. This uses the ordinary step 6 and 7 machinery: paired, noise-adjusted, with a full report per comparison (no deep dive). So the claim "this candidate is better" is held to the same standard as the main verdict.

## The hard cap

A cap is **required** and covers both the search and the validation. GEPA stops at the cap through a stop callback, the optimization is marked `paused`, and no validation runs. GEPA itself cannot resume, but the cache is keyed by content and the search is seeded, so retrying replays the paid part for free. If the cap is hit during validation, `POST /optimizations/{id}/candidates/{cid}/evaluate` resumes the paused comparisons.

## Your part

For each candidate:
- **Accept** it, optionally promoting it to be the new prompt (the previous new prompt is kept as `new_prev_N`).
- **Edit** it. Your edit is saved as a new candidate and validated like the rest (placeholders must be kept).
- **Reject** it with a comment.

Every action is stored as feedback and given to the reflector next time, along with the opening text of rejected candidates.

## What a "no result" means

The screen can say "no candidate beat the starting prompt on the validation set". That is the tool refusing to claim a win it did not measure. It happens when the starting prompt already scores as well as the metrics can tell, or when the pool is too small to separate candidates. The app also warns when fewer than 30 scenarios are approved.

## Known debt

`LLMClientLM` subclasses DSPy's `BaseLM.forward`, which is deprecated in DSPy 3.4 and removed in 3.5. DSPy is pinned below 3.5. Migrate to the engine interface when upgrading.
