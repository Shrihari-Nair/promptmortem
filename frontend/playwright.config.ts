import { defineConfig } from '@playwright/test'

// Full-flow smoke through the real UI, real API server and the deterministic fake LLM (no API key, no spend).
// E2E_BASE_URL=http://localhost:8000 runs the same flow against an already running app (e.g. the Docker container).
const external = process.env.E2E_BASE_URL

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 240_000,
  expect: { timeout: 45_000 },
  workers: 1,
  reporter: 'list',
  use: { baseURL: external ?? 'http://localhost:5174', headless: true, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: external ? undefined : [
    {
      command: 'rm -f /tmp/pv_e2e.db* && PV_DB_URL=sqlite:////tmp/pv_e2e.db PV_EMBEDDING_MODEL=hash PV_SCENARIOS_PER_CATEGORY_INITIAL=5 PV_SCENARIOS_PER_DIFF_CHANGE=3 uv run uvicorn app.main:app --port 8000',
      cwd: '../backend',
      url: 'http://localhost:8000/health',
      timeout: 60_000,
      reuseExistingServer: false,
    },
    { command: 'npm run dev -- --port 5174 --strictPort', url: 'http://localhost:5174', timeout: 60_000, reuseExistingServer: false },
  ],
})
