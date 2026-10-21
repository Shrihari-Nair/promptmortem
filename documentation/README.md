# Prompt Mortem: project documentation

Prompt Mortem tells you whether a change to an LLM prompt fixed what you wanted, and whether it broke anything else. You do not need a labelled dataset. The tool plans the tests, runs the old and new prompt side by side, subtracts the model's own randomness, and gives you a verdict with a clear statement of what was and was not tested.

This folder explains the whole project: what it does, how a run flows from start to finish, and how each part is built.

## Read in this order

| # | File | Read it when you want to know |
|---|------|-------------------------------|
| 1 | [01-what-it-does.md](01-what-it-does.md) | What problem this solves, the principles it never breaks, and the eight steps in plain words. |
| 2 | [02-architecture.md](02-architecture.md) | The pieces, how they connect, the tech stack, and where every file lives. |
| 3 | [03-workflow-in-depth.md](03-workflow-in-depth.md) | Each of the eight steps: what you do, what the system does, what gets stored, which code does it. |
| 4 | [04-statistics-explained.md](04-statistics-explained.md) | How "fixed", "broken" and "not sure" are decided, and how the verdict is reached. |
| 5 | [05-agents-and-prompts.md](05-agents-and-prompts.md) | The AI agents, what each one does, and how their output is checked. |
| 6 | [06-cost-budget-and-caps.md](06-cost-budget-and-caps.md) | How cost is estimated, how tiers trim the plan, and how the hard cap works. |
| 7 | [07-prompt-refinement.md](07-prompt-refinement.md) | The optional step that searches for a better prompt (GEPA). |
| 8 | [08-data-model-and-api.md](08-data-model-and-api.md) | The database tables and every API route. |
| 9 | [09-frontend.md](09-frontend.md) | The web app: pages, design system, how it talks to the server. |
| 10 | [10-testing-and-demo.md](10-testing-and-demo.md) | The fake model, the golden benchmark, the test suites, and the demo recorder. |
| 11 | [11-security-and-deployment.md](11-security-and-deployment.md) | Keeping the API key safe, optional login token, Docker, CI, and every setting. |
| 12 | [12-decisions-and-lessons.md](12-decisions-and-lessons.md) | Why things are built the way they are, what real runs taught us, and known limits. |
| 13 | [13-glossary.md](13-glossary.md) | Plain-language definitions of the terms used everywhere. |

## The project in one minute

- **Input:** your old prompt, your new prompt, a short description of the app and its rules. Optionally a dataset and real traces.
- **Output:** a report with a verdict (ok, caution, block, inconclusive or incomplete), a list of what got better and worse, and a **scope stamp** that says exactly what was tested.
- **How:** eight steps (Setup, Understand, Scenarios, Metrics, Budget, Run, Report, Refine). A person reviews the key decisions at the scenario, metric, budget and result stages, and the tool learns from what they reject.
- **Stack:** Python (FastAPI, SQLModel and SQLite, LiteLLM, DSPy) for the backend; React, TypeScript, Vite and Tailwind for the frontend; Docker for one-command use.

## Where to find things

| You want | Look here |
|----------|-----------|
| Start the app | `README.md` at the repo root (Docker quick start) |
| The original design | `PLAN.md` and `CLAUDE.md` at the repo root |
| Short per-stage notes | `docs/stage1-...` to `docs/stage9-...` |
| Demo video and GIF | `docs/media/` |
| Example inputs | `examples/billing_bot/` |
| Backend code | `backend/app/` |
| Frontend code | `frontend/src/` |
