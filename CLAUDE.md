# CLAUDE.md

Project: **Prompt Change Validator.** Agentic tool that tells a developer whether a prompt change fixed what they wanted and whether it broke anything else, without requiring a labeled dataset.

**Read `PLAN.md` fully before coding.** Sections 0, 0.1, 1, 14, 15, 16 and 17 are normative.

## Non-negotiable principles
1. Human checkpoints at scenario, metric, budget and result stages. Human feedback is persisted and fed back into the agents.
2. Humans review only top cases (representatives, disputed, high-impact); rejecting a case can reject its cluster.
3. Cost estimate and trim happen AFTER exhaustive planning, never before. Show coverage lost for every tier.
4. Results are paired old-vs-new and noise-adjusted. Always measure old-vs-old noise.
5. Every report carries a scope stamp (tier, coverage, reviewed share, verified vs synthetic, noise). Never claim more than was tested.
6. Programmatic checks before LLM judges. Judges are blind to which prompt produced an output.
7. Dataset is optional. Eval platforms are optional adapters.
8. Hard budget cap pauses and asks; it never silently truncates.

## Stack
Python 3.11, FastAPI, SQLModel/SQLite, LiteLLM (all LLM calls through `LLMClient`), DSPy (agent signatures, GEPA in stage 7), numpy/scipy, sentence-transformers. Frontend: React + TypeScript + Vite + Tailwind + shadcn/ui + TanStack Query + Recharts.

## Working rules
- Build one milestone at a time (M0 to M9 in PLAN.md). After each, run tests and report against its acceptance criteria. Stop and ask before starting the next.
- v1 scope: single-turn text prompts, single-user local app, declarative programmatic metrics (no arbitrary code execution).
- Every agent output is a Pydantic model matching the examples in PLAN.md section 14; validate and repair-retry.
- Agent prompts live in `backend/app/agents/prompts/` as versioned files.
- Config values live in `config.py` (defaults in PLAN.md section 16).
- Build the fake-LLM mode (deterministic, with injectable noise and planted regressions) and the golden benchmark early; they gate M6.
- Ask before deviating from the principles above.

## Commands (fill in as built)
- Backend (from `backend/`): `uv sync`, then `uv run uvicorn app.main:app --reload`
- Tests (from `backend/`): `uv run pytest`
- Frontend (from `frontend/`): `npm run dev` (proxies `/api` to :8000), `npm test`, `npm run build`
- Real-model smoke (server running): `python scripts/pipeline_smoke.py --model gemini/gemini-2.5-flash --cap 0.50` (`--model fake/app` = dry run)
- Browser smoke: `cd frontend && npm run e2e` (needs `npx playwright install chromium`); Promptfoo parity: `PROMPTFOO_BIN=... uv run pytest tests/test_m7.py`
- Models: LiteLLM names, e.g. `gemini/gemini-2.5-flash`; key in `.env` as `GEMINI_API_KEY`; `fake/*` = deterministic mock
