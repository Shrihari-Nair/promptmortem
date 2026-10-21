# 11. Security, deployment and settings

## Keeping the API key safe

The Gemini key must live only in your git-ignored `.env` file as `GEMINI_API_KEY`. Several layers make sure it never leaks.

1. **Never in code.** The app reads keys from the environment only (`core/llm.py` loads the project-root `.env`). Nothing stores a key in the database, and the API never returns one. `/health` returns only `true` or `false` per provider.
2. **Git ignores it.** `.gitignore` excludes `.env`, `.env.*` (except `.env.example`), key and credential files, databases, logs and per-machine tool settings. `.env.example` lists the variable names with empty values.
3. **A scanner.** `scripts/check_secrets.py` checks every file that could be pushed for the real key value (read from your `.env`) and for key-shaped patterns (`AIza...`, `sk-...`, bearer tokens). A line marked `secret-scan: allow` is exempt, which the test fixtures use for fake keys.
4. **A pre-commit hook.** `scripts/pre-commit` runs the scanner. Install it once with `scripts/install-git-hooks.sh`.
5. **A test and CI.** `tests/test_no_secrets.py` and a CI job run the scanner on every push.
6. **Redaction.** `core/secrets.py` scrubs key shapes, `?key=` and `api_key=` values, `token:` values and the literal value of every configured key from any error text that gets stored (job errors, run pause reasons, optimization errors).
7. **Not in Docker.** `.dockerignore` excludes `.env` and databases. The key is passed at run time by `docker compose`, never baked into the image. The built image was checked: no `.env`, no database, zero files containing the key, and no mention in the image history.

## Optional login token

Set `PV_API_TOKEN` and every route except `/health` requires it (`Authorization: Bearer`, `X-API-Token`, or `?token=`). The web app asks for it on the first 401 and remembers it in the browser. The comparison is constant-time. This is one shared secret, not user accounts, and is meant for a machine you share, not for the public internet.

## No arbitrary code execution

Custom metrics are declarative JSON (a list of check types and an aggregate). Regexes and JSON schemas are validated when saved. The tool never evaluates user-supplied code.

## Running it

### Docker (one command)

```bash
cp .env.example .env            # add GEMINI_API_KEY=... (or skip it and use fake/* models)
docker compose up --build       # then open http://localhost:8000
```

- **One image, one port.** `Dockerfile` has two stages: a Node stage builds the frontend, and a Python stage installs the backend with `uv` (locked versions), copies in the built frontend, and runs `uvicorn app.web:app` on port 8000.
- **Not root:** the app runs as user `app` (uid 1000).
- **Data:** SQLite lives at `/data/workspace.db` on the named volume `pv-data`, so it survives restarts and rebuilds. `docker compose down -v` deletes it.
- **Health check:** the container checks `/api/health`.
- **Options:** `PORT=8080 docker compose up --build` changes the port. `EMBEDDINGS=1` installs sentence-transformers (about 2 GB more because of PyTorch). Without it the app uses a lightweight hashed text-embedding fallback for clustering, and logs a warning.
- **Result:** the image is about 207 MB by default.

### Local development

```bash
cd backend  && uv sync && uv run uvicorn app.main:app --reload     # API on :8000
cd frontend && npm install && npm run dev                           # UI on :5173, proxies /api to :8000
```

For the single-port setup without Docker, build the frontend (`npm run build`) and run `uv run uvicorn app.web:app --port 8000` from `backend/`.

## Settings

All settings are in `backend/app/config.py`. Override any of them with an environment variable named `PV_<NAME>` (or in `.env`). Defaults follow PLAN section 16.

| Group | Setting (default) | Meaning |
|-------|-------------------|---------|
| Basics | `db_url` (`sqlite:///workspace.db`) | database location |
| | `api_token` (none) | optional shared token |
| | `static_dir` (frontend/dist) | where the built frontend is served from |
| Models | `target_model`, `judge_model` (`gemini/gemini-2.5-flash`) | default models; `fake/*` for the mock |
| | `judge_reasoning`, `generator_reasoning` (`disable`), `target_reasoning` (`default`) | provider thinking control |
| | `target_temperature` (0.7), `judge_temperature` (0.0) | sampling |
| | `llm_concurrency` (5) | calls in flight at once |
| Planning | `scenarios_per_category_initial` (25), `scenarios_per_diff_change` (8), `gap_fill_n` (5) | how many cases to generate |
| | `max_critic_rounds` (2), `dedupe_cosine_threshold` (0.92), `cluster_target_size` (8) | cleaning and grouping |
| | `review_queue_max_per_cluster` (2), `regression_probe_n` (6) | review and regression guards |
| | `embedding_model` (`all-MiniLM-L6-v2`; `hash` = fallback) | text embeddings |
| Budget | `tiers` (1.0, 0.6, 0.3), `tier_repeats` (3, 3, 2) | plan sizes |
| | `priority_weights`, `severity_protected_threshold` (4) | ranking and floor rule |
| | `probe_scenarios` (5), `cost_high_percentile` (0.90) | estimation |
| | `cheap_judge_model`, `cheap_judge_cost_ratio` (0.3), `easy_rubric_max_chars` (400), `adaptive_stable_share` (0.6) | cost levers |
| Run | `smoke_fraction` (0.10), `smoke_min` (3), `smoke_repeats` (2) | smoke stage |
| | `default_repeats` (3), `max_repeats_adaptive` (5), `confirm_flagged` (false) | repeats |
| | `deep_dive_per_regression` (3), `deep_dive_max_scenarios` (12) | deep dive |
| Statistics | `significance_alpha` (0.05), `noise_prior_var` (0.05), `noise_prior_n` (5), `noisy_band_cut` (0.5), `pair_noise_floor` (0.05), `bootstrap_n` (2000), `bootstrap_seed` (0) | tests and noise |
| Refine | `gepa_max_metric_calls` (120), `gepa_minibatch` (3), `gepa_val_fraction` (0.30), `gepa_min_pool` (10), `gepa_reviewed_min` (30), `heldout_fraction` (0.30) | GEPA |
| Other | `judge_ensemble_model` (none), `calibration_max_items` (30) | second judge, calibration |

## Continuous integration

`.github/workflows/ci.yml` has four jobs: the secret scan, the backend tests, the frontend build and unit tests, and the browser end-to-end test. See [10](10-testing-and-demo.md).
