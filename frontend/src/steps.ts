export const STEPS = ['Setup', 'Understand', 'Scenarios', 'Metrics', 'Budget', 'Run', 'Report', 'Refine'] as const
export type Step = (typeof STEPS)[number]
