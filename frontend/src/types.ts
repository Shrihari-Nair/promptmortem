export type Project = { id: string; name: string; target_model: string; judge_model_default: string; known_failure_notes?: string; promptfoo_enabled?: boolean }
export type Job = { id: string; status: 'pending' | 'running' | 'done' | 'failed' | 'paused'; progress: string; error: string; result: Record<string, number> | null }
export type Context = { task: string; user_types: string[]; input_types: string[]; output_format: string; risk_areas: string[]; success_criteria: string[]; assumptions: string[] }
export type Change = { id: string; type: string; old_text: string | null; new_text: string | null; intent: string; could_affect: string[]; risk: 'low' | 'medium' | 'high' }
export type Analysis = { context?: { data: Context; edited: boolean }; diff?: { data: { changes: Change[] }; edited: boolean } }
export type Scenario = {
  project_id: string
  id: string; cluster_id: string | null; category: string; input: Record<string, unknown>; rationale: string
  expected_behavior: string; tags: string[]; status: string; pinned: boolean; severity: number; diff_relevance: number
  reviewed: boolean; tier: string; origin: string; flagged: boolean; representative: boolean
}
export type Coverage = {
  categories: Record<string, Record<string, number>>
  clusters: Record<string, { name: string; category: string }>
  counts: { approved: number; unreviewed: number; rejected: number }
}
export const CATEGORIES = ['happy_path', 'edge_case', 'ambiguous', 'long_noisy', 'out_of_scope', 'adversarial', 'format_sensitive', 'multilingual', 'safety_critical', 'diff_targeted', 'regression_guard']
export type Metric = {
  id: string; name: string; kind: 'programmatic' | 'llm_judge'; definition: Record<string, any>; applies_to: Record<string, string[]>
  scale: string; weight: number; judge_model: string | null; status: string; notes: string
}
export type Calibration = { metric_id: string; n_labeled: number; agreement: number | null; details?: { by_label?: Record<string, { n: number; agree: number }> } | null }
export type Tier = {
  tier: string; n_scenarios: number; repeats: number; cost: { low: number; expected: number; high: number }
  coverage_lost: { categories: Record<string, string>; clusters: Record<string, string>; clusters_dropped: string[] }
  suggested_cap: number; scenario_ids: string[]
}
export type Lever = { id: string; enabled: boolean; available?: boolean; saving_expected: number; note: string }
export type Estimate = { tiers: Tier[]; levers: Lever[]; protected_scenarios: number; warnings?: string[]; basis: string; probe: { paid: number } }
export type RankRow = { id: string; category: string; text: string; severity: number; pinned: boolean; priority: number; protected: boolean }
export type RunState = {
  id: string; status: 'pending' | 'running' | 'paused' | 'done' | 'stopped' | 'failed'; stage: string; spent: number; cap: number
  pause_reason: string; error: string; warnings: string[]; noise: { band?: Record<string, number>; source?: string } | null; total: number; tier: string
}
export type RunEvent = { type: string; [k: string]: any }
export type ReportRow = {
  scenario_id: string; cluster: string | null; category: string; severity: number; metric_name: string; old_pass_rate?: number | null
  new_pass_rate?: number | null; preference_for_new?: number | null; disputed?: boolean; input?: Record<string, unknown>
  old_output?: string | null; new_output?: string | null; rationale?: string; override?: { verdict: string; comment: string }
}
export type MetricRow = {
  metric_id: string; name: string; kind: string; test_kind: string; n: number; applicable: number; old_mean: number; new_mean: number
  mean_diff: number; ci95: [number, number]; noise_band: number | null; detection_threshold: number | null; counts: Record<string, number>
  test?: { name: string; p: number }; disputed_rate?: number | null
}
export type Report = {
  headline: {
    fixed: number; broken: number; unchanged: number; inconclusive: number; level: string; recommendation: string
    net_effect: { mean_diff: number; ci95: [number, number] }; metric_regressions: { name: string; mean_diff: number; p: number }[]
  }
  scope: {
    tier: string; tested: number; dropped: number; untested_in_tier: number; deep_dive_added: number; reviewed_share: number; verified_share: number
    noise_band: Record<string, number>; detection_threshold: Record<string, number | null>; comparisons: number; judge_calls_skipped: number
    repeats: number; coverage_lost: Record<string, string>; clusters_dropped: string[]; complete: boolean; run_status: string
    cheap_judge?: { model: string | null; metrics: string[] }
  }
  regressions: ReportRow[]; improvements: ReportRow[]; inconclusive: { scenario_id: string; cluster: string | null }[]
  per_metric: MetricRow[]; per_category: { name: string; fixed: number; broken: number; unchanged: number; inconclusive: number; n: number }[]
  judge_reliability: { calibration_agreement: number | null; pairwise_disagreement_rate: number | null; pairwise_judgments: number; pointwise_disagreement_rate?: number | null; ensemble_judgments?: number }
  cost: { estimated: number | null; estimated_high: number | null; spent: number; cap: number; probe_spent: number | null }
  warnings: string[]; overrides: Record<string, { verdict: string; comment: string }>
}
export type HeldOut = {
  run_id: string; status: string; level: string; recommendation: string; counts: Record<string, number>
  net_effect: { mean_diff: number; ci95: [number, number] }; scope: { tested: number; complete: boolean }
}
export type Candidate = {
  id: string; version_id: string; label: string; text: string; status: string; val_score: number | null; seed_val_score: number | null
  notes: string[]; diff_vs_new: string; diff_vs_old: string; edited_from?: string; feedback?: string
  heldout: { vs_new?: HeldOut; vs_old?: HeldOut }
}
export type Optimization = {
  id: string; status: string; spent: number; error: string; created_at: string
  config: { cap: number; heldout_ids: string[]; train_ids: string[]; val_ids: string[]; trained_on_reviewed: boolean } | null
  result: { candidates: Candidate[]; warnings: string[]; seed_val_score: number | null; best_val_score: number | null; metric_calls: number; note?: string; stopped?: string } | null
}
export type Stale = Record<string, { reason: string; at: string }>
export type Health = { ok: boolean; auth_required: boolean; keys: Record<string, boolean>; models: { target: string; judge: string } }
