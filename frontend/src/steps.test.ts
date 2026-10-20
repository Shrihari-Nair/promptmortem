import { expect, test } from 'vitest'
import { STEPS } from './steps'

test('stepper matches PLAN section 7', () => {
  expect(STEPS).toEqual(['Setup', 'Understand', 'Scenarios', 'Metrics', 'Budget', 'Run', 'Report', 'Refine'])
})
