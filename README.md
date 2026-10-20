# Prompt Mortem

You changed a prompt. Did it fix what you wanted, and did it break anything else? This tool answers that without a labelled dataset: agents plan scenarios and metrics (you steer at every step), run the old and new prompt side by side, and report paired, noise-adjusted results scoped to what was actually tested. Optionally it proposes a refined prompt (DSPy GEPA) and validates it on held-out scenarios.

See `PLAN.md` (intent and design) and `CLAUDE.md` (principles). Per-stage notes are in `docs/`.

## Quick start (Docker, one command)

```bash
cp .env.example .env        # put GEMINI_API_KEY=... in .env (optional: skip to use the fake model)
docker compose up --build   # builds frontend + backend into one image
```

Open http://localhost:8000. UI and API share that one port (API under `/api`, docs at `/api/docs`).

- Port taken? `PORT=8080 docker compose up --build`.
- Real sentence embeddings (bigger image): `EMBEDDINGS=1 docker compose up --build`.
- Data lives in the `pv-data` volume and survives restarts. `docker compose down` keeps it, `docker compose down -v` wipes it.
- The key is read from `.env` at runtime only. It is never copied into the image (`.dockerignore`) or git (`.gitignore`, `scripts/check_secrets.py`).

## Run it (local dev, without Docker)

```bash
cp .env.example .env            # then set GEMINI_API_KEY=...   (keys live only here; never stored or returned)
cd backend && uv sync && uv run uvicorn app.main:app --reload          # API on :8000 (SQLite file: workspace.db)
cd frontend && npm install && npm run dev                               # UI on :5173, proxies /api to :8000
```

Check the setup: `curl localhost:8000/health` shows which provider keys are configured (booleans) and the default models
(`gemini/gemini-2.5-flash`; change with `PV_TARGET_MODEL` / `PV_JUDGE_MODEL`, or per project in Setup). Models are LiteLLM names;
`fake/*` is a deterministic mock (no key, no spend) used by all tests. Optional: `uv sync --extra embeddings` for real sentence
embeddings (otherwise a hashed n-gram fallback is used and a warning is logged). Optional: `PV_API_TOKEN=...` to require a token.

## Your first real (Gemini) run, cheaply

Every stage has a hard cap or is cheap by construction; the UI shows cost before you spend and pauses at the cap, never truncating silently.

```bash
# small configuration for the first run (fewer generated scenarios):
cd backend && PV_SCENARIOS_PER_CATEGORY_INITIAL=4 PV_SCENARIOS_PER_DIFF_CHANGE=3 uv run uvicorn app.main:app
python scripts/pipeline_smoke.py --model gemini/gemini-2.5-flash --judge gemini/gemini-2.5-flash --cap 0.50            # whole pipeline, hard cap $0.50
python scripts/pipeline_smoke.py --model fake/app --judge fake/judge --cap 5 --optimize                                 # dry run, no key needed
```

Or use the UI: Setup (load `examples/billing_bot/*`: description, constraints, both prompts, `dataset.csv`, `traces.jsonl`) then Understand,
Scenarios, Metrics, Budget, Run, Report, Refine. Things to look at on a real run: that the generated scenarios look like your users, that the
metric rubrics say what you mean (preview them on samples before approving), the cost probe vs your expectations, the noise bands in the
scope stamp, and the judge calibration on the example's labelled traces.

## What the report promises

Every report carries a scope stamp (tier, tested vs dropped, reviewed vs unreviewed, verified vs synthetic, noise, repeats, skipped judge calls).
Verdicts are per case and metric, old vs new on the same repeats, and only called *fixed* or *broken* beyond what noise alone explains (exact tests,
corrected for the number of comparisons; contested cases are confirmed with extra repeats). The recommendation is rule-based and advisory.
Details and measured error rates: `docs/stage6-report.md`.

## Tests

```bash
cd backend && uv run pytest                                  # ~110 tests, fake LLM, no key
PROMPTFOO_BIN=/path/to/promptfoo uv run pytest tests/test_m7.py   # also verifies the Promptfoo export against the real tool
cd frontend && npm test && npm run build                     # unit test + type-check
cd frontend && npx playwright install chromium && npm run e2e     # full wizard in a real browser (uses ports 8000 and 5174)
```

The golden benchmark (`backend/app/benchmark/golden.py`) plants a regression, an improvement and a no-op change with injectable noise; it is
the M6 gate (`tests/test_m6.py`).

## Notes for the first real run

- Your provider key can live in this project's `.env`; LiteLLM also loads any `.env` it finds up the directory tree (for example `~/.env`), so an existing `GEMINI_API_KEY` there is picked up automatically. The tests are pinned to fake models and can never use a key.
- The "cheap judge" cost lever uses a cheaper judge model for short pass/fail rubrics (`PV_CHEAP_JUDGE_MODEL`, default derived from your provider); a second judge can be enabled per rubric (`PV_JUDGE_ENSEMBLE_MODEL`).
- Real-run cost: Gemini 2.5 bills hidden "thinking" tokens as output. Judges and scenario generation run with thinking off (9x cheaper per judge call, same verdicts); your app's model is measured as configured. If your app calls it with thinking off, set `PV_TARGET_REASONING=disable`. About 70% of a real run's cost is the model under test.
- Calibrate the judges on your own labelled traces (`examples/billing_bot/traces.jsonl` shows the format: an observed `output` and `expected` of `pass` or `fail`). Agreement is shown per metric; a rubric that does not match what your labels are about will agree poorly.
- If you edit something upstream (a scenario, a metric, a prompt) the later steps get an "out of date" marker; nothing is recomputed behind your back.

## Scope (v1)

Single-turn text prompts; single-user local app; declarative programmatic metrics (no generated code is ever executed); Promptfoo export and
generic JSONL traces (Langfuse/LangSmith connectors and multi-turn are v2).
