# 10. Testing, the fake model and the demo

## The fake model

`backend/app/core/fake_llm.py`. Any model name starting with `fake/` is served by a deterministic mock instead of a real provider. The tool's whole pipeline can therefore run for free, repeatably, in tests and demos.

- **Deterministic:** the output is a pure function of the messages and seed.
- **Agents:** agent prompts start with `<!-- agent: NAME vN -->`, and the fake returns canned, schema-valid JSON for each agent. Scenario text depends on the whole prompt, so reviewer feedback changes what is generated.
- **Fake application behaviour:** a prompt can carry a directive such as

  ```
  <!-- fake-app: {"verbose_on_explain": true, "overpromise": 0.0, "noise": 0.03} -->
  ```

  - `verbose_on_explain`: replies to "explain" questions run about 160 words instead of about 60.
  - `overpromise`: the chance a refund question gets a policy-breaking promise ("You will definitely get a full refund within 2 days").
  - `noise`: the chance that decision flips at random. This is the old-vs-old variance.
  - Randomness is seeded by prompt text plus the repeat index, so old and new are independent and repeats differ.
- **Cost:** a tiny fixed price per token, so budget logic can be tested.

These directives are how tests plant known regressions, improvements and no-ops.

## The golden benchmark

`backend/app/benchmark/golden.py` (with `tests/golden.py`). It builds fake projects with known truth:

- a **planted regression** (refund over-promising, severity 4)
- a **planted improvement** (verbose answers to explain questions get shorter)
- a **no-op** rewording
- adjustable noise

The tests check that the tool flags the regression, credits the improvement, does not flag the no-op beyond the expected false-alarm rate, and keeps spend inside the estimate. The statistics choices in [04](04-statistics-explained.md) were made by measuring against this benchmark. It gates the report milestone (M6).

## Backend tests

`cd backend && uv run pytest`. 139 passed and 1 skipped at last run, about 2.5 minutes. They use the fake model only, so no API key is needed.

| File | Covers |
|------|--------|
| `test_m0.py` | model client, cache, cost tracking, hard cap |
| `test_m1.py` | analysis agents, import, drafting |
| `test_m2.py` | scenario planning, review, feedback loop, clustering |
| `test_m3.py` | metrics engine, judges, calibration |
| `test_m4.py` | probe, estimate, tiers, floor rule |
| `test_m5.py` | staged execution, adaptive repeats, cap pauses, resume, crash safety |
| `test_m6.py` | the golden benchmark gate |
| `test_m7.py` | Promptfoo export, with a real Promptfoo run when `PROMPTFOO_BIN` is set |
| `test_m8.py` | GEPA: split, held-out isolation, validation, cap |
| `test_m9.py` | auth token, redaction, example project |
| `test_m10.py` | gap-closing against PLAN: drafting the new prompt, saved notes, optional variables, cheap judge, second judge, old-prompt probes for regression guards, verdict feedback, restart recovery, stale tracking |
| `test_harness.py` | cassette record/replay (`app/core/cassette.py`), faults injected into the model provider (fenced, truncated, garbage JSON, timeouts), and CLAUDE.md principles as asserts (no provider call outside `LLMClient`, blind judge prompts, complete scope stamp) |
| `test_verdicts.py` | known-answer tests for the statistics |
| `test_web.py` | single-port serving, SPA fallback, token behaviour |
| `test_no_secrets.py` | the secret scanner and that no key is in the tree |

Promptfoo parity: with `PROMPTFOO_BIN=/path/to/promptfoo`, a project built so every check type sees passing and failing outputs is run through real Promptfoo using its `echo` provider. 1,440 comparisons gave 0 disagreements with Prompt Mortem's own checks.

## Frontend tests

See [09](09-frontend.md): Vitest unit tests (5), and one Playwright test that walks the whole wizard.

## Real-model smoke test

`scripts/pipeline_smoke.py` drives the whole pipeline over HTTP against a running server, with hard caps.

```bash
python scripts/pipeline_smoke.py --model fake/app                              # dry run, free
python scripts/pipeline_smoke.py --model gemini/gemini-2.5-flash --cap 0.50    # real, small
```

It uses the billing-bot example.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: the secret scan, the backend tests (with real Promptfoo installed), the frontend build and unit tests, and the browser test. None of them needs an API key.

## The demo recorder

`frontend/demo/record.mjs`. A Playwright script that drives the real UI through the whole flow on the fake models and records a video.

- Injects an on-screen caption bar and a visible cursor.
- Types and clicks with a calm pace, and logs the time of every caption and of every wait on a model call.
- Writes a `.webm` and `segments.json`. A later step cuts the recording (captions at normal speed, routine actions faster, model waits much faster) and the result was composed with Hyperframes (title card, the recording, closing card, music) into the published video and GIF in `docs/media/`.

```bash
cd frontend
BASE_URL=http://localhost:8000 OUT=./demo-out node demo/record.mjs
```

The published demo (`DEMO_MODE=real`) is a real Gemini run of the billing-bot example: a concise new prompt checked against a wordy old one. It ended "Looks fine" with 25 cases improved. The offline mode plants a regression instead, so it ends in BLOCK.
