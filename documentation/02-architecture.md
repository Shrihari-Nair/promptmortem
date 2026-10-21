# 2. Architecture

## The big picture

```
   Browser (React app)
        |  HTTP + Server-Sent Events (live run progress)
        v
   FastAPI server  --------------------------+
   (routers: projects, analysis, scenarios,  |
    metrics, plans, runs, exports, optimize) |
        |                                    |
        |  starts background jobs            |  reads / writes
        v                                    v
   Pipeline modules                      SQLite database (WAL mode)
   (planner, metric plan, estimator,     projects, prompts, scenarios, metrics,
    runner, verdicts, report, GEPA)      plans, runs, results, scores, reports,
        |                                feedback, jobs, cached model calls
        |  every model call goes through
        v
   LLMClient  ---- cache hit? return free ----> (database: LLMCall table)
        |  no: check the hard cap, then call
        v
   LiteLLM  ->  Gemini / OpenAI / Anthropic        or     fake model (tests, demos)
```

Two ideas hold the design together:

1. **One door for model calls.** Every call, from every agent, judge, run and optimizer, goes through `LLMClient` (`backend/app/core/llm.py`). That one place caches answers, tracks cost, and refuses to spend past the hard cap. No other file talks to a model provider.
2. **The database is the memory.** Almost all state (scenarios, scores, run progress, feedback) is in SQLite. Because of that, a run can pause, crash or restart and pick up without paying twice.

## Tech stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | Python 3.12, FastAPI | Async, typed, easy streaming |
| Data | SQLModel on SQLite (WAL mode) | Single-user local app; no server to run |
| Model access | LiteLLM | One interface for Gemini, OpenAI, Anthropic and others |
| Agent outputs | Pydantic models | Every agent answer is validated, with repair-retry |
| Prompt search | DSPy GEPA (pinned below 3.5) | Reflective prompt optimization for step 8 |
| Maths | numpy, scipy | Exact binomial tests, bootstrap, clustering |
| Embeddings | sentence-transformers, or a built-in hash fallback | Dedupe and cluster scenarios |
| Frontend | React 19, TypeScript, Vite, Tailwind v4 | |
| UI parts | Radix Dialog, shadcn-style components, Phosphor icons, TanStack Query, Recharts | |
| Packaging | Docker (two-stage build), docker compose | One command, one port |

## Repository layout

```
backend/
  app/
    main.py            the API app (routers, optional token check, /health)
    web.py             single-port entry: mounts the API under /api and serves the built frontend
    config.py          every tunable setting (env prefix PV_)
    core/              db, models (tables), llm (the one door), fake_llm, embeddings,
                       jobs (background tasks), events (live progress bus), secrets (redaction), reasoning
    agents/            the AI agents, their Pydantic schemas, and prompts/*.md (versioned prompt files)
    pipeline/          planner, clustering, prioritizer, metric_plan, metrics_engine, cost_estimator,
                       trimmer, runner, collect, verdicts, report, feedback, stale
    optimize/          gepa_runner, dspy_bridge, split (step 8)
    adapters/          dataset_import, promptfoo_export
    api/               the route files, one per area
    benchmark/         golden.py: the planted-regression benchmark
  tests/               the pytest suite
frontend/
  src/                 App shell, pages (one per step plus Home), ui components, lib helpers
  e2e/                 Playwright browser test of the whole wizard
  demo/record.mjs      scripted demo recorder
examples/billing_bot/  sample prompts, dataset, traces, project description
scripts/               secret scanner, pre-commit hook, real-model smoke script
docs/                  short per-stage notes, demo media
documentation/         this folder
Dockerfile, docker-compose.yml, .dockerignore
```

## How one request flows

Take "Generate scenario plan" in step 3.

1. The browser calls `POST /api/projects/{id}/scenarios/generate`.
2. The route starts a **job** (`core/jobs.py`) and returns a job id right away. Long work never blocks a request.
3. The job runs the planner in the background. The browser polls `GET /api/jobs/{id}` (about every 0.7 seconds) and shows progress text.
4. The planner calls agents. Each agent call renders a versioned prompt file, calls `LLMClient`, validates the reply against a Pydantic model, and retries with the error message if it is invalid (up to two repairs).
5. Results are written to the database. The job row is marked done, and the browser reloads the scenario list.

Runs (step 6) work the same way, but progress is pushed over **Server-Sent Events** (`GET /api/runs/{id}/events`) instead of polled, because a run lasts longer and has richer events.

## Background jobs and live events

- **Jobs** (`core/jobs.py`): an asyncio task plus a `Job` row holding status (`pending`, `running`, `done`, `failed`, `paused`), a progress message and the result. A `BudgetExceeded` error turns into `paused`, not `failed`. Error text is passed through the secret redactor before it is stored.
- **Events** (`core/events.py`): an in-memory bus per run with the last 500 events replayed to late subscribers. It is not persisted. After a restart, the run's state comes from the database instead.
- **Restart recovery** (`recover_interrupted`): runs left `running` by a crash become `paused (interrupted)`. They never restart themselves, so nothing is spent without being asked. Orphaned job and optimization rows are marked failed with a clear message. Cached calls replay for free, so retrying is cheap.

## The single-port web entry

`backend/app/web.py` builds one Starlette app:

- `/api/*` is the FastAPI app (so the browser calls `/api/projects`; in development the Vite server forwards `/api` to port 8000 and removes the prefix).
- `/` serves the built React app. Unknown paths return `index.html` so client-side navigation works, while a missing file under `assets/` stays a 404.
- If the frontend has not been built, `/` returns a clear 503 message instead of a blank page.
- The optional token check strips the `/api` prefix before exempting `/health`, so the health check stays open.

In Docker the command is `uvicorn app.web:app`. In development you can run `app.main:app` for the API alone and `npm run dev` for the UI.

## Where the data lives

- The database file is `workspace.db` by default, or the path in `PV_DB_URL`. In Docker it is `/data/workspace.db` on the `pv-data` volume.
- On startup `init_db` creates missing tables and adds any missing columns. It does not rename or drop columns, which would need a real migration.
- The model-call cache lives in the same database (`LLMCall` table), keyed by a hash of the model, messages, parameters, seed and repeat index. Each paid call also records the run that paid for it (`run_id`) and a `tag` (the agent name, or `target` for the application under test); `GET /runs/{id}/trace` sums these per agent. `app/core/cassette.py` dumps and reloads these rows as a JSON "cassette" (see [10](10-testing-and-demo.md)).
