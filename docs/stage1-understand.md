# Stage 1: Understand (M1)

**Inputs:** app description, constraints, known-failure notes, old/new prompt, optional dataset/traces (CSV or JSONL).
**Agents:** `context_analyzer` (task, users, risks, explicit assumptions) and `diff_analyzer` (every explicit and implicit change, each with `could_affect`). Prompts: `backend/app/agents/prompts/*.v1.md`. Both use the project's `judge_model_default`.
**Outputs:** `project_analysis` rows (`context`, `diff`), stored with an `edited` flag.
**Human actions (Understand screen):** edit the context card, edit/remove changes, change risk, add missed changes and "could affect" behaviours. `PUT /projects/{id}/analysis/{kind}` validates against the Pydantic model (422 on bad shape).
**API:** `POST /projects/{id}/dataset/import`, `POST /projects/{id}/traces/import` (JSON body: `format`, `content`, optional `mapping`), `POST /projects/{id}/analyze` (job), `GET /projects/{id}/analysis`, `GET /jobs/{id}`.
**Not yet:** "describe the change and let the tool draft the new prompt"; downstream stale-marking.

**Stage 0 additions:** `POST /prompts/draft {old_prompt, change_request, app_description?, constraints?, model?}` drafts the new prompt from a change request (agent `prompt_drafter`: apply only the requested change, keep placeholders and constraints; a dropped `{{placeholder}}` is restored). It is a draft: the Setup screen puts it in the new-prompt box for you to edit. `known_failure_notes` are stored with the project and used by every analysis (no query parameter). The prompt-variables schema (`variables_schema`, detected from `{{placeholders}}`, editable in Setup) types each input; a variable declared `string?` is optional: a scenario that omits it renders as empty text everywhere a prompt is rendered (runs, probe, preview, GEPA, Promptfoo export) instead of failing the smoke test. The dataset and traces uploaders map CSV columns or JSONL keys to prompt variables and `expected`.
