import { expect, test } from '@playwright/test'

// Planted behaviour for the fake app (see backend/app/benchmark/golden.py): old = verbose + safe, new = concise + refund over-promising.
const DIRECTIVE = (o: object) => `<!-- fake-app: ${JSON.stringify(o)} -->`
const OLD = `You are a billing support assistant.\n${DIRECTIVE({ verbose_on_explain: true, overpromise: 0.0, noise: 0.03 })}\n\nCustomer: {{user_message}}`
const NEW = `You are a concise billing assistant.\n${DIRECTIVE({ verbose_on_explain: false, overpromise: 0.9, noise: 0.03 })}\n\nCustomer: {{user_message}}`

test('the whole wizard works in a browser: setup to refine', async ({ page, request }) => {
  const problems: string[] = []
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && !/favicon|status of 422/.test(m.text()) && problems.push(`console: ${m.text()}`))  // (422: the test submits an invalid metric on purpose)
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 500 && problems.push(`HTTP ${r.status()} ${r.url()}`))
  const shot = async (name: string) => { if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/${process.env.E2E_THEME ?? 'light'}-${name}.png`, fullPage: true }) }
  const shotEl = async (name: string, el: import('@playwright/test').Locator) => { if (process.env.E2E_SHOTS) await el.screenshot({ path: `${process.env.E2E_SHOTS}/${process.env.E2E_THEME ?? 'light'}-${name}.png` }) }
  const step = (n: string) => page.getByRole('button', { name: new RegExp(`^\\d+\\s+${n}\\b`) })

  // 1. Setup: every field has a visible label; key status is shown (never the key)
  await page.goto('/')
  await page.getByRole('button', { name: 'Start validating' }).first().click()
  if (process.env.E2E_THEME) await page.getByLabel('Theme').selectOption(process.env.E2E_THEME)
  await expect(page).toHaveTitle('Prompt Mortem')
  await page.getByLabel('Project name').fill('E2E billing bot')
  await page.getByLabel('What the app does').fill('Billing support bot. Never promise refunds. Replies under 120 words.')
  await page.getByLabel('Non-negotiable constraints').fill('Never promise refunds outside policy.')
  await page.getByLabel('Target model').fill('fake/app')
  await page.getByLabel('Judge and agent model').fill('fake/judge')
  await expect(page.getByText(/Provider keys on the server/)).toBeVisible()
  await page.getByLabel('Old prompt', { exact: true }).fill(OLD)
  // stage 0: describe the change and let the tool draft the new prompt (then the human edits it)
  await page.getByLabel(/^Or describe the change/).fill('Answer in under 120 words.')
  await page.getByRole('button', { name: 'Draft new prompt' }).click()
  await expect(page.getByLabel('New prompt', { exact: true })).toHaveValue(/Additional instruction: Answer in under 120 words\./)
  await expect(page.getByLabel('{{user_message}}')).toBeVisible()  // the input schema is detected from the prompts
  await page.getByLabel('New prompt', { exact: true }).fill(NEW)
  await expect(page.getByLabel(/Show the Promptfoo export/)).toBeChecked()
  await expect(page.locator('pre').filter({ hasText: 'overpromise' }).first()).toBeVisible()  // the live difference
  await shot('1-setup')
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // 2. Understand
  await page.getByRole('button', { name: 'Analyze prompts' }).click()
  await expect(page.getByRole('heading', { name: 'Your app, as understood' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What changed and what it could affect' })).toBeVisible()
  await expect(page.getByText('chg_1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Add a change the agent missed' }).click()
  await expect(page.getByText('chg_3', { exact: true })).toBeVisible()  // edits are saved through the API and re-rendered
  await shot('2-understand')

  // 3. Scenarios: generate, heatmap, approve, reject a whole cluster with a comment
  await step('Scenarios').click()
  await page.getByRole('button', { name: 'Generate scenario plan' }).click()
  await expect(page.getByText('Unreviewed', { exact: true })).toBeVisible()
  const cells = page.locator('button[title*="#"]')
  await expect(cells.first()).toBeVisible()
  expect(await cells.count()).toBeGreaterThan(8)  // the heatmap has a cell per cluster
  await expect(page.locator('span:has-text("Approved") b')).toHaveText('0')
  await page.getByRole('button', { name: 'Approve' }).first().click()  // an approved case leaves the review queue and counts as approved
  await expect(page.locator('span:has-text("Approved") b')).toHaveText('1')
  await page.getByRole('button', { name: 'Reject', exact: true }).nth(1).click()
  await page.getByLabel('What is wrong?').fill('Our users write short, messy messages.')
  await page.getByRole('button', { name: 'Reject whole cluster and regenerate' }).click()
  await expect(page.locator('span:has-text("Rejected") b')).not.toHaveText('0')
  await page.getByLabel('Add my own scenario').fill('Please explain how proration works.')
  await page.getByRole('button', { name: 'Add scenario' }).click()
  // a comment on a case is asked for in the app's own dialog (never window.prompt)
  await page.getByRole('button', { name: 'Comment' }).first().click()
  await page.getByLabel('Your comment').fill('Real users never write this long.')
  await page.getByRole('button', { name: 'Save comment' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await shot('3-scenarios')

  // 4. Metrics: design, preview, approve, add my own
  await step('Metrics').click()
  await page.getByRole('button', { name: 'Design metrics' }).click()
  await expect(page.getByText('Under 120 words').first()).toBeVisible()
  await expect(page.getByText('Regression judge (pairwise)')).toBeVisible()  // the regression metric is always present
  await page.getByRole('button', { name: 'Preview on samples' }).first().click()
  await expect(page.locator('th', { hasText: 'Result' }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).first().click()
  await page.getByLabel('Metric name').fill('My own: no policy ids')  // add-your-own metric (declarative checks only)
  await page.getByLabel('Metric definition (JSON)').fill('{"checks": [{"type": "forbidden_regex", "pattern": "POL-\\\\\\\\d+"}], "aggregate": "all_pass"}')
  await page.getByRole('button', { name: 'Add metric' }).click()
  await expect(page.getByText('My own: no policy ids').first()).toBeVisible()
  await page.getByLabel('Metric definition (JSON)').fill('{"checks": []}')
  await page.getByLabel('Metric name').fill('Broken one')
  await page.getByRole('button', { name: 'Add metric' }).click()
  await expect(page.getByRole('alert').filter({ hasText: /422/ })).toBeVisible()  // invalid definitions are rejected with a clear message
  await shot('4-metrics')

  // 5. Budget: estimate, compare tiers with coverage lost, confirm
  await step('Budget').click()
  await page.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(page.getByRole('columnheader', { name: 'Coverage lost' })).toBeVisible()
  await expect(page.getByRole('radio')).toHaveCount(3)  // full, balanced, lean
  await page.getByRole('radio', { name: 'Lean tier' }).check()
  await page.getByRole('button', { name: /^Confirm lean plan/ }).click()
  await expect(page.getByText(/Plan locked: cap/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Export Promptfoo config' })).toBeVisible()
  await shot('5-budget')

  // 6. Run: live progress, handle the cap prompts like a user would
  await step('Run').click()
  await page.getByRole('button', { name: 'Start run' }).click()
  const done = page.getByRole('button', { name: 'View the report' })
  const asks = page.getByRole('heading', { name: /Hard cap reached|deep dive would exceed/ })
  for (let i = 0; i < 6 && !(await done.isVisible()); i++) {
    await expect(done.or(asks)).toBeVisible({ timeout: 120_000 })
    if (await asks.isVisible()) {
      await page.getByLabel('Extend by (USD)').fill('5')
      await page.getByRole('button', { name: 'Extend and continue' }).click()
    }
  }
  await expect(page.getByText(/^Spent \$/)).toBeVisible()
  await expect(page.getByRole('progressbar', { name: 'Spend against the hard cap' })).toBeVisible()
  await shot('6-run')
  await done.click()

  // 7. Report: verdict, scope stamp, per-metric table and chart, exports
  await expect(page.getByRole('heading', { name: 'Scope stamp' })).toBeVisible()
  await expect(page.getByText('Advisory, produced by rules')).toBeVisible()
  await expect(page.getByText('Net effect (mean paired difference)')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Per metric' })).toBeVisible()
  await expect(page.getByRole('img', { name: /Difference per metric/ })).toBeVisible()
  await expect(page.getByText(/Reviewed by a human:/)).toBeVisible()
  await shot('7-report')
  await shotEl('7a-verdict', page.getByRole('region', { name: 'Verdict' }))
  await shotEl('7b-chart', page.getByRole('img', { name: /Difference per metric/ }))
  const exportLink = await page.getByRole('link', { name: /Promptfoo config/ }).getAttribute('href')
  const yaml = await request.get(exportLink!)
  expect(yaml.status()).toBe(200)
  expect(await yaml.text()).toContain('prompts:')

  // 7b. Out-of-date tracking: changing scenarios after the run marks the downstream steps, and acknowledging clears them
  await step('Scenarios').click()
  await page.getByLabel('Add my own scenario').fill('A late scenario that changes the plan.')
  await page.getByRole('button', { name: 'Add scenario' }).click()
  await step('Budget').click()
  await expect(page.getByRole('status').filter({ hasText: 'may be out of date' })).toBeVisible()
  await expect(step('Budget')).toHaveAccessibleName('5 Budget (out of date)')  // announced properly to screen readers
  await page.getByRole('button', { name: 'Mark as reviewed' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'may be out of date' })).toHaveCount(0)

  // 8. Refine (GEPA) with a hard cap
  await step('Refine').click()
  await page.getByLabel(/^Hard cap/).fill('5')
  await page.getByLabel(/^Search size/).fill('30')
  await page.getByRole('button', { name: 'Start optimization' }).click()
  await expect(page.getByRole('heading', { name: /Optimization [a-z0-9]{6}/ })).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('span', { hasText: /^(done|paused)$/ }).first()).toBeVisible({ timeout: 120_000 })
  await shot('8-refine')

  // responsive: at phone width no step may force a horizontal page scroll (wide tables scroll inside their own container)
  await page.setViewportSize({ width: 390, height: 844 })
  for (const s of ['Understand', 'Scenarios', 'Metrics', 'Budget', 'Run', 'Report', 'Refine']) {
    await step(s).click()
    await page.waitForTimeout(400)
    if (s === 'Budget' || s === 'Report') await shot(`mobile-${s}`)
    const { overflow, culprits } = await page.evaluate(() => {
      const w = window.innerWidth
      const chain = (e: Element) => { const out: string[] = []; for (let x: Element | null = e; x && x !== document.body && out.length < 4; x = x.parentElement) out.push(`${x.tagName.toLowerCase()}${x.id ? '#' + x.id : ''}.${String(x.className).split(' ').slice(0, 3).join('.')}`); return out.join(' < ') }
      const r = (e: Element | null) => (e ? `${Math.round(e.getBoundingClientRect().left)}..${Math.round(e.getBoundingClientRect().right)}` : 'none')
      const outside = [...document.querySelectorAll('body *')].filter((e) => !e.closest('nav')).map((e) => [e, e.getBoundingClientRect().right] as const).filter(([, x]) => x > w + 1).sort((p, q) => q[1] - p[1]).slice(0, 3).map(([e, x]) => `${chain(e)} right=${Math.round(x)}`)
      const bad = [`html.scrollWidth=${document.documentElement.scrollWidth} body.scrollWidth=${document.body.scrollWidth}`, `header=${r(document.querySelector('header'))} headerInner=${r(document.querySelector('header > div'))} nav=${r(document.querySelector('nav'))} main=${r(document.querySelector('main'))}`, ...outside]
      return { overflow: document.documentElement.scrollWidth - w, culprits: bad }
    })
    expect(overflow, `${s} overflows the viewport by ${overflow}px at 390px wide. Culprits: ${culprits.join(' | ')}`).toBeLessThanOrEqual(0)
  }
  await page.setViewportSize({ width: 1280, height: 720 })

  // keyboard: the skip link is the first tab stop and moves focus to the main content
  await page.reload()
  await expect(page.getByRole('navigation', { name: 'Steps' })).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()

  expect(problems, problems.join('\n')).toEqual([])
})
