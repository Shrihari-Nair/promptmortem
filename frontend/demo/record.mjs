// Records an end-to-end walkthrough of the real UI (video + captions) against a running Prompt Mortem.
// Usage: BASE_URL=http://localhost:8000 OUT=./demo-out node demo/record.mjs
// Uses the offline fake models, so it is free and deterministic: the old prompt is wordy but safe, the new one is concise but over-promises refunds.
import { chromium } from 'playwright'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const BASE = process.env.BASE_URL ?? 'http://localhost:8000'
const OUT = process.env.OUT ?? './demo-out'
const EX = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'examples', 'billing_bot')
const D = (o) => `<!-- fake-app: ${JSON.stringify(o)} -->`
const OLD = readFileSync(join(EX, 'old_prompt.txt'), 'utf8').trim() + `\n${D({ verbose_on_explain: true, overpromise: 0.0, noise: 0.03 })}`
const NEW = readFileSync(join(EX, 'new_prompt.txt'), 'utf8').trim() + `\n${D({ verbose_on_explain: false, overpromise: 0.9, noise: 0.03 })}`

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, colorScheme: 'dark', recordVideo: { dir: OUT, size: { width: 1280, height: 720 } } })
await ctx.addInitScript(() => {
  addEventListener('DOMContentLoaded', () => {
    const css = document.createElement('style')
    css.textContent = `
      #pm-cap{position:fixed;left:50%;bottom:26px;transform:translate(-50%,14px);z-index:9999;max-width:min(920px,92vw);padding:12px 22px 12px 14px;border-radius:999px;display:flex;gap:12px;align-items:center;
        background:rgba(10,10,12,.82);backdrop-filter:blur(14px);box-shadow:0 0 0 1px rgba(255,255,255,.14),0 18px 50px -12px rgba(0,0,0,.7);color:#f2f2f4;font:500 17px/1.35 "Geist Variable",system-ui,sans-serif;opacity:0;
        transition:opacity .5s cubic-bezier(.32,.72,0,1),transform .5s cubic-bezier(.32,.72,0,1);pointer-events:none}
      #pm-cap.on{opacity:1;transform:translate(-50%,0)}
      #pm-cap b{flex:none;font:600 12px/1 "Geist Mono Variable",monospace;letter-spacing:.14em;text-transform:uppercase;padding:7px 11px;border-radius:999px;background:#86a3f2;color:#0b1020}
      #pm-cur{position:fixed;left:0;top:0;width:22px;height:22px;margin:-4px 0 0 -4px;z-index:10000;pointer-events:none;transition:transform .08s linear}
      #pm-cur::before{content:"";position:absolute;inset:0;border-radius:50%;background:rgba(134,163,242,.28);box-shadow:0 0 0 2px #86a3f2}
      #pm-cur.down::before{background:rgba(134,163,242,.7);transform:scale(.8)}`
    document.head.append(css)
    const cap = Object.assign(document.createElement('div'), { id: 'pm-cap' }); cap.innerHTML = '<b></b><span></span>'
    const cur = Object.assign(document.createElement('div'), { id: 'pm-cur' })
    document.body.append(cap, cur)
    addEventListener('mousemove', (e) => { cur.style.transform = `translate(${e.clientX}px,${e.clientY}px)` }, true)
    addEventListener('mousedown', () => cur.classList.add('down'), true); addEventListener('mouseup', () => cur.classList.remove('down'), true)
  })
})
const T0 = Date.now()
const segs = []  // [start, end] seconds of dead time (model calls); post-production plays these faster
const page = await ctx.newPage()
const wait = (ms) => page.waitForTimeout(ms)
const fast = async (fn) => { const a = (Date.now() - T0) / 1000; await fn(); const b = (Date.now() - T0) / 1000; if (b - a > 2.5) segs.push([+(a + 0.8).toFixed(2), +b.toFixed(2)]) }
const PACE = 1.15  // < 1 shortens every caption hold
const marks = []  // every caption with its start time, used to cut the short GIF
const say = async (tag, text, ms = 2600) => {
  marks.push({ t: +((Date.now() - T0) / 1000).toFixed(2), tag, text, hold: +(ms * PACE / 1000).toFixed(2) })
  await page.evaluate(([t, x]) => { const c = document.getElementById('pm-cap'); c.querySelector('b').textContent = t; c.querySelector('span').textContent = x; c.classList.add('on') }, [tag, text])
  await wait(ms * PACE)
}
const hush = () => page.evaluate(() => document.getElementById('pm-cap')?.classList.remove('on'))
const glide = async (loc) => {
  await loc.scrollIntoViewIfNeeded()
  const b = await loc.boundingBox(); if (!b) return
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 38 }); await wait(450)
}
const click = async (loc) => { await glide(loc); await loc.click(); await wait(900) }
const type = async (loc, text, delay = 22) => { await glide(loc); await loc.click(); await loc.pressSequentially(text, { delay }); await wait(300) }
const fill = async (loc, text) => { await glide(loc); await loc.fill(text); await wait(800) }
const show = async (loc, ms = 2200) => {
  await loc.scrollIntoViewIfNeeded()
  await page.evaluate((el) => scrollBy({ top: el.getBoundingClientRect().top - 130, behavior: 'smooth' }), await loc.elementHandle())
  await wait(ms * PACE + 500)
}
const approveAll = async (max) => {  // click every visible Approve button quickly
  const a = page.getByRole('button', { name: 'Approve', exact: true })
  for (let i = 0; i < max && (await a.count()) > 0; i++) { await a.first().scrollIntoViewIfNeeded(); await a.first().click(); await wait(520) }
}
const step = (n) => page.getByRole('button', { name: new RegExp(`^\\d+\\s+${n}\\b`) })
const scrollBy = async (px, ms = 1600) => { await page.evaluate((y) => scrollBy({ top: y, behavior: 'smooth' }), px); await wait(ms) }

// 0. Home (hook)
await page.goto(BASE); await wait(900)
await say('Prompt Mortem', 'You changed a prompt. Did it fix the problem, or break something else?', 3600)
await scrollBy(820, 1500)
await say('Eight steps', 'You review the key cases at every checkpoint. Nothing is hidden.', 2800)
await scrollBy(-3000, 900); await hush()
await click(page.getByRole('button', { name: 'Start validating' }).first())

// 1. Setup
await say('01 Setup', 'Describe the app. Paste the old and the new prompt.', 1800)
await type(page.getByLabel('Project name'), 'Acme billing bot', 55)
await type(page.getByLabel('What the app does'), 'Customer support bot for a SaaS billing product. Replies under 120 words.', 22)
await type(page.getByLabel('Non-negotiable constraints'), 'Never promise a refund outside policy.', 28)
await fill(page.getByLabel('Target model'), 'fake/app'); await fill(page.getByLabel('Judge and agent model'), 'fake/judge')
await fill(page.getByLabel('Old prompt', { exact: true }), OLD)
await fill(page.getByLabel('New prompt', { exact: true }), NEW)
await show(page.getByLabel('{{user_message}}'), 1400)
await say('01 Setup', 'The input variable and the exact diff are detected. No dataset needed.', 2600)
await page.getByLabel('Dataset (CSV or JSON)'.replace('JSON', 'JSONL')).setInputFiles(join(EX, 'dataset.csv')); await wait(500)
await page.getByLabel('Production traces (JSONL)').setInputFiles(join(EX, 'traces.jsonl')); await wait(500)
await hush()
await click(page.getByRole('button', { name: 'Save and continue' }))

// 2. Understand
await say('02 Understand', 'Agents read the app and the diff, and predict what could break.', 1600)
await click(page.getByRole('button', { name: 'Analyze prompts' }))
await fast(() => page.getByRole('heading', { name: 'What changed and what it could affect' }).waitFor())
await show(page.getByRole('heading', { name: 'What changed and what it could affect' }), 2200); await hush()

// 3. Scenarios
await click(step('Scenarios'))
await click(page.getByRole('button', { name: 'Generate scenario plan' }))
await fast(() => page.getByText('Unreviewed', { exact: true }).waitFor()); await wait(600)
await say('03 Scenarios', 'Test cases for the goal, edge cases and everything you did not touch. You review only the top ones.', 3400)
await approveAll(6)
await wait(800)
await click(page.getByRole('button', { name: 'Reject', exact: true }).nth(1))
await fill(page.getByLabel('What is wrong?'), 'Our users write short, messy messages.')
await say('03 Scenarios', 'Reject one case and the whole cluster goes. Your reason is fed back to the agents.', 2800)
await click(page.getByRole('button', { name: 'Reject whole cluster and regenerate' })); await wait(1200); await hush()

// 4. Metrics
await click(step('Metrics'))
await click(page.getByRole('button', { name: 'Design metrics' }))
await fast(() => page.getByText('Regression judge (pairwise)').waitFor()); await wait(500)
await say('04 Metrics', 'Metrics come from your rules. Code checks run first, blind LLM judges second.', 3200)
await approveAll(10); await hush()

// 5. Budget
await click(step('Budget'))
await click(page.getByRole('button', { name: 'Estimate cost' }))
await fast(() => page.getByRole('columnheader', { name: 'Coverage lost' }).waitFor()); await wait(500)
await show(page.getByRole('columnheader', { name: 'Coverage lost' }), 600)
await say('05 Budget', 'Cost is estimated after planning. Every tier shows the coverage it gives up.', 3400)
await click(page.getByRole('radio', { name: 'Balanced tier' }))
await click(page.getByRole('button', { name: /^Confirm balanced plan/ }))
await fast(() => page.getByText(/Plan locked: cap/).waitFor()); await hush()

// 6. Run
await click(step('Run'))
await click(page.getByRole('button', { name: 'Start run' }))
await say('06 Run', 'Old and new on the same inputs, plus old-vs-old repeats to measure noise. A hard cap pauses and asks.', 3400)
const done = page.getByRole('button', { name: 'View the report' })
const asks = page.getByRole('heading', { name: /Hard cap reached|deep dive would exceed/ })
for (let i = 0; i < 6 && !(await done.isVisible()); i++) {
  await fast(() => done.or(asks).waitFor({ timeout: 120_000 }))
  if (await asks.isVisible()) { await fill(page.getByLabel('Extend by (USD)'), '5'); await click(page.getByRole('button', { name: 'Extend and continue' })) }
}
await hush(); await click(done)

// 7. Report (the outcome)
await fast(() => page.getByRole('heading', { name: 'Scope stamp' }).waitFor()); await wait(600)
await show(page.getByRole('region', { name: 'Verdict' }), 600)
const vt = (await page.getByRole('region', { name: 'Verdict' }).innerText()).split('\n').map((x) => x.trim()).filter(Boolean)
console.log('VERDICT TEXT:', JSON.stringify(vt))
const reason = (vt[1] ?? '').replace(/^block:\s*/, '').replace(/\s*on severity.*$/, '').replace(/\s*\(p=.*?\)/, '')
await say('07 Verdict', `${vt[0].toUpperCase()}. ${reason}.`, 5200)
await show(page.getByRole('img', { name: /Difference per metric/ }), 600)
await say('07 Verdict', 'Per metric: shorter answers are fixed, quality regressed. Paired and noise-adjusted.', 4200)
await show(page.getByRole('heading', { name: 'Scope stamp' }), 600)
await say('07 Verdict', 'The scope stamp says exactly what was tested. It never claims more.', 4200); await hush()

// 8. Refine
await click(step('Refine'))
await fill(page.getByLabel(/^Hard cap/), '5'); await fill(page.getByLabel(/^Search size/), '30')
await click(page.getByRole('button', { name: 'Start optimization' }))
await fast(() => page.getByRole('heading', { name: /Optimization [a-z0-9]{6}/ }).waitFor({ timeout: 120_000 }))
await fast(() => page.locator('span', { hasText: /^(done|paused)$/ }).first().waitFor({ timeout: 120_000 })); await wait(500)
await show(page.getByRole('heading', { name: /Optimization [a-z0-9]{6}/ }), 600)
await say('08 Refine', 'If something broke, it searches for a better prompt and validates it on held-out cases.', 3600); await hush(); await wait(300)

const vid = page.video(); await ctx.close(); await browser.close()
console.log('video:', await vid.path())
import('node:fs').then((f) => f.writeFileSync(join(OUT, 'segments.json'), JSON.stringify({ segs, marks })))
