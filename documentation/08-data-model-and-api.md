# 8. Data model and API

Tables are defined in `backend/app/core/models.py` with SQLModel. IDs are short prefixed strings (`prj_...`, `scn_...`). JSON columns hold flexible data.

## Tables

### Project setup
| Table | Holds |
|-------|-------|
| `Project` | name, app description, constraints, known-failure notes, target model, default judge/agent model, whether to show the Promptfoo export |
| `PromptVersion` | the prompt text with a `label`: `old`, `new`, `candidate_N`, `new_prev_N`; plus the input variable schema |
| `DatasetItem` | an imported dataset row or trace: input (dict), expected, source (`user` or `trace`), notes |
| `ProjectAnalysis` | the two step 2 results (`context`, `diff`) as JSON, with an `edited` flag |

### Planning
| Table | Holds |
|-------|-------|
| `ScenarioCluster` | a group of similar scenarios: category, name, description, centroid embedding, status |
| `Scenario` | one test case: category, input, rationale, expected behaviour, tags, linked changes, origin (`generated`, `dataset`, `user`), status (`proposed`, `approved`, `rejected`, `edited`), pinned, severity 1 to 5, diff relevance, priority score, reviewed, tier (`verified` or `synthetic`), flagged, representative |
| `Metric` | name, kind (`programmatic` or `llm_judge`), JSON definition, `applies_to` filter, scale, optional pinned judge model, weight, status, notes |
| `Feedback` | every human action: target type (scenario, cluster, metric, verdict, candidate), target id, action (approve, reject, edit, comment), comment |
| `Calibration` | judge-versus-your-labels agreement per metric |

### Budget and runs
| Table | Holds |
|-------|-------|
| `Plan` | one per project: stage, coverage map, the cost probe, the chosen tier/cap/levers/frozen scenario ids, stale marks |
| `CostEstimate` | one per tier: counts of calls, repeats, low/expected/high cost, coverage lost, scenario ids |
| `Run` | tier, budget cap, status (`pending`, `running`, `paused`, `done`, `stopped`, `failed`), spend, stage (`smoke`, `main`, `deep_dive`, `done`), frozen config, measured noise, warnings, pause reason, error |
| `RunResult` | one model output: which scenario, which prompt version, which repeat, output text, latency, tokens, cost |
| `Score` | one metric score for one result: value, rationale, judge model, whether the A/B orders disagreed, details |
| `Report` | the report summary as JSON (always rebuildable from scores) |

### Plumbing
| Table | Holds |
|-------|-------|
| `LLMCall` | the model-call cache: key (hash), model, response, tokens, cost, latency, plus `run_id` (the run that paid), `project_id` and `tag` (agent name or `target`). This is also the cost ledger. |
| `Job` | a background job: kind, status, progress text, result, error |
| `Optimization` | a step 8 search: status, config (cap, split ids), result (scores, candidates, reflection log), spend, error |

## How the tables connect

```
Project -+- PromptVersion (old, new, candidates)
         +- DatasetItem
         +- ProjectAnalysis (context, diff)
         +- ScenarioCluster -- Scenario
         +- Metric -- Calibration
         +- Feedback
         +- Plan -+- CostEstimate
                  +- Run -- RunResult -- Score (to a Metric)
                            +- Report
         +- Optimization (candidates reference PromptVersion rows)
```

## API routes

All paths are under `/api` when served by `app.web` (the Vite dev server proxies the same prefix). Long operations return a job id; poll `GET /jobs/{id}`.

### Projects and prompts (`api/projects.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects` | create a project |
| `GET /projects/{pid}` | read it |
| `POST /projects/{pid}/prompts` | save a prompt version |
| `GET /projects/{pid}/prompts` | list versions |
| `POST /prompts/draft` | draft a new prompt from the old one and a request |
| `GET /projects/{pid}/stale` | which steps are out of date |
| `POST /projects/{pid}/stale/{stage}/ack` | mark a step as still right |

### Analysis and import (`api/analysis.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/dataset/import` | import a dataset (CSV or JSONL, with column mapping) |
| `POST /projects/{pid}/traces/import` | import traces |
| `GET /projects/{pid}/dataset` | list imported rows |
| `POST /projects/{pid}/analyze` | run both analysis agents (job) |
| `GET /projects/{pid}/analysis` | read the analysis |
| `PUT /projects/{pid}/analysis/{kind}` | save your edits (`context` or `diff`) |
| `GET /jobs/{jid}` | job status and result |

### Scenarios (`api/scenarios.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/scenarios/generate` | plan or extend scenarios (job) |
| `GET /projects/{pid}/scenarios` | list (`review_queue=true` for the review queue) |
| `POST /projects/{pid}/scenarios` | add your own |
| `PATCH /scenarios/{sid}` | approve, edit, reject, pin, comment |
| `POST /clusters/{cid}/reject` | reject a whole cluster and regenerate |
| `GET /projects/{pid}/coverage` | the coverage map |
| `POST /feedback` | record a feedback row directly |

### Metrics (`api/metrics.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/metrics/generate` | design metrics (job) |
| `GET /projects/{pid}/metrics` | list |
| `POST /projects/{pid}/metrics` | add your own (validated; invalid gives 422) |
| `PATCH /metrics/{mid}` | approve, reject, edit |
| `POST /metrics/{mid}/preview` | score on sample outputs |
| `POST /metrics/{mid}/calibrate` | agreement with your labels |
| `GET /projects/{pid}/calibration` | stored calibration results |

### Budget (`api/plans.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/plan/estimate` | probe if needed, then estimate all tiers |
| `POST /projects/{pid}/plan/trim` | estimate a custom selection (409 if protected cases are dropped without confirmation) |
| `POST /projects/{pid}/plan/confirm` | lock tier, cap and levers |
| `GET /projects/{pid}/plan` | read the plan |
| `GET /projects/{pid}/plan/ranking` | the priority ranking |

### Runs and reports (`api/runs.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/runs` | start a run from the confirmed plan |
| `GET /runs/{rid}` | run state |
| `GET /projects/{pid}/runs` | list runs |
| `POST /runs/{rid}/resume` | resume a paused run |
| `POST /runs/{rid}/extend` | raise the cap and continue |
| `POST /runs/{rid}/stop` | stop and report partial |
| `GET /runs/{rid}/events` | live Server-Sent Events |
| `GET /runs/{rid}/report` | the report (rebuilt each time) |
| `GET /projects/{pid}/spend` | everything the project has paid for: `planning` (analysis, scenarios, metrics, calibration, probe; outside any run cap), `in_runs`, `total`. Calls from endpoints keyed by metric, cluster, optimization or report id carry no project tag and are not counted |
| `GET /runs/{rid}/trace` | paid model calls and cost for this run, per agent (`target`, `judge`, `pairwise_judge`, ...). Cache hits are not logged, so a resumed run shows only what it paid for itself |
| `POST /reports/{rep_id}/verdict-override` | override a case verdict |
| `POST /reports/{rep_id}/rescore` | re-score with current metrics |
| `GET /reports/{rep_id}/export` | export (`format=md` or `json`) |

### Export (`api/exports.py`)
| Method and path | Purpose |
|-----------------|---------|
| `GET /projects/{pid}/export/promptfoo` | Promptfoo config (`format=yaml\|json`, `scope=plan\|all`) |

### Refinement (`api/optimize.py`)
| Method and path | Purpose |
|-----------------|---------|
| `POST /projects/{pid}/optimize` | start a search with a required cap (job) |
| `GET /projects/{pid}/optimizations` | list searches |
| `GET /optimizations/{oid}` | one search with candidates |
| `PATCH /optimizations/{oid}/candidates/{cid}` | edit a candidate |
| `POST /optimizations/{oid}/candidates/{cid}/accept` | accept (optionally promote) |
| `POST /optimizations/{oid}/candidates/{cid}/reject` | reject with a comment |
| `POST /optimizations/{oid}/candidates/{cid}/evaluate` | resume paused validation |

### System
| Method and path | Purpose |
|-----------------|---------|
| `GET /health` | which provider keys exist (true or false only), default models, whether a token is required. Never requires the token. |

## Authentication

If `PV_API_TOKEN` is set, every route except `/health` requires the token as `Authorization: Bearer <token>`, `X-API-Token: <token>`, or `?token=<token>`. The query form exists because browser event streams and download links cannot set headers. The check is constant-time. It is a single shared token, not user accounts.
