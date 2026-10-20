import { ArrowRight, Brain, Coins, EyeSlash, Faders, Gauge, GitDiff, Play, Ruler, Scales, ShieldCheck, Sparkle, Stamp, TreeStructure } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { Reveal } from '../lib/reveal'
import { cn } from '../lib/utils'

/** Pill button with the arrow in its own circle (button-in-button). The circle drifts on hover. */
export function CtaButton({ children, onClick, href, tone = 'solid' }: { children: ReactNode; onClick?: () => void; href?: string; tone?: 'solid' | 'quiet' }) {
  const cls = cn(
    'group inline-flex items-center gap-3 whitespace-nowrap rounded-full py-2 pl-6 pr-2 text-sm font-medium transition-[transform,background-color,box-shadow] duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] active:scale-[0.98]',
    tone === 'solid' ? 'bg-ink text-canvas hover:shadow-[0_10px_40px_-8px_var(--glow)]' : 'text-ink ring-1 ring-line-strong hover:bg-sunken',
  )
  const dot = (
    <span className={cn('flex size-8 items-center justify-center rounded-full transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:-translate-y-px group-hover:translate-x-1 group-hover:scale-105', tone === 'solid' ? 'bg-canvas/15' : 'bg-ink/10')}>
      <ArrowRight size={16} weight="light" aria-hidden />
    </span>
  )
  return href ? <a href={href} className={cls}>{children}{dot}</a> : <button type="button" onClick={onClick} className={cls}>{children}{dot}</button>
}

const Eyebrow = ({ children }: { children: ReactNode }) => (
  <span className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-[10px] font-medium uppercase tracking-[0.2em] text-muted ring-1 ring-line-strong">
    <span aria-hidden className="size-1 rounded-full bg-accent" />{children}
  </span>
)

/** A mock of the report's verdict so visitors see the output before they run anything. Numbers are from the bundled billing-bot example. */
function VerdictPreview() {
  const rows = [
    { name: 'Replies under 120 words', was: 31, now: 94, tag: 'Fixed', tone: 'text-good bg-good-soft' },
    { name: 'Never promises a refund', was: 88, now: 90, tag: 'Unchanged', tone: 'text-muted bg-sunken' },
    { name: 'Answers in the customer’s language', was: 92, now: 71, tag: 'Broken', tone: 'text-bad bg-bad-soft' },
  ]
  return (
    <div className="relative">
      <div className="bezel">
        <div className="core p-6 md:p-7">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-[0.2em] text-muted">Verdict</span>
            <span className="rounded-full bg-warn-soft px-2.5 py-1 text-[11px] font-medium text-warn">Do not ship yet</span>
          </div>
          <p className="mt-4 text-[22px] font-semibold leading-tight tracking-tight">Fixed the wordiness. Broke Spanish replies.</p>
          <ul className="mt-6 space-y-5">
            {rows.map((r, i) => (
              <li key={r.name}>
                <div className="flex items-baseline justify-between gap-3 text-[13px]">
                  <span>{r.name}</span>
                  <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', r.tone)}>{r.tag}</span>
                </div>
                <div className="mt-2 space-y-1" style={{ ['--d' as string]: `${i * 140}ms` }}>
                  <div className="flex items-center gap-2"><span className="w-7 text-[10px] uppercase tracking-wider text-muted">old</span><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken"><div className="bar h-full rounded-full bg-line-strong" style={{ width: `${r.was}%` }} /></div><span className="tnum w-8 text-right text-xs text-muted">{r.was}%</span></div>
                  <div className="flex items-center gap-2"><span className="w-7 text-[10px] uppercase tracking-wider text-muted">new</span><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken"><div className={cn('bar h-full rounded-full', r.tag === 'Broken' ? 'bg-bad' : r.tag === 'Fixed' ? 'bg-good' : 'bg-accent')} style={{ width: `${r.now}%` }} /></div><span className="tnum w-8 text-right text-xs">{r.now}%</span></div>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-6 border-t border-line pt-4 text-[11px] text-muted">Paired old-vs-new on the same inputs, adjusted for run-to-run noise. Illustration of the report.</p>
        </div>
      </div>
      <div className="float-a absolute -right-3 -top-5 hidden rotate-3 rounded-2xl bg-surface px-3.5 py-2.5 text-[11px] shadow-panel ring-1 ring-line md:block">
        <span className="text-muted">Scope stamp</span><br /><span className="font-medium">Full tier · 86% coverage · noise ± 3.1%</span>
      </div>
      <div className="float-b absolute -bottom-5 -left-4 hidden -rotate-2 items-center gap-2 rounded-2xl bg-surface px-3.5 py-2.5 text-[11px] shadow-panel ring-1 ring-line md:flex">
        <EyeSlash size={16} weight="light" aria-hidden className="text-accent" /><span>Judges never see which prompt wrote the answer</span>
      </div>
    </div>
  )
}

const STEPS = [
  { n: '01', t: 'Setup', d: 'Paste the old prompt and the new one, or describe the change and let it draft the new prompt.', i: Faders, span: 'md:col-span-7' },
  { n: '02', t: 'Understand', d: 'It reads your app and the diff, and tells you what the change will touch.', i: Brain, span: 'md:col-span-5' },
  { n: '03', t: 'Scenarios', d: 'Test inputs for the goal, for edge cases and for things you did not touch. You review the top ones.', i: TreeStructure, span: 'md:col-span-4' },
  { n: '04', t: 'Metrics', d: 'Programmatic checks first, then blind LLM judges. Nothing is a vague “quality score”.', i: Ruler, span: 'md:col-span-4' },
  { n: '05', t: 'Budget', d: 'Exhaustive plan first, then an estimate and a trim. Every tier shows the coverage it loses.', i: Coins, span: 'md:col-span-4' },
  { n: '06', t: 'Run', d: 'Old and new on the same inputs, with old-vs-old repeats to measure the noise. Stops and asks at the cap.', i: Play, span: 'md:col-span-5' },
  { n: '07', t: 'Report', d: 'A verdict you can act on, with a scope stamp that says exactly what was tested.', i: Scales, span: 'md:col-span-7' },
  { n: '08', t: 'Refine', d: 'If something broke, it proposes a fixed prompt and validates it the same way.', i: Sparkle, span: 'md:col-span-12' },
]

const PRINCIPLES = [
  { t: 'Noise-adjusted', d: 'Models vary between runs. We measure old-vs-old first, so a wobble is not reported as a regression.', i: Gauge },
  { t: 'Blind judges', d: 'A judge sees one output, or two in random order. It never learns which prompt produced which.', i: EyeSlash },
  { t: 'Checks before judges', d: 'Length, format, regex and schema checks run in code. LLM judges only handle what code cannot.', i: ShieldCheck },
  { t: 'Never claims more than tested', d: 'Every report carries tier, coverage, reviewed share, verified vs synthetic cases and noise.', i: Stamp },
]

export default function Home({ start }: { start: () => void }) {
  return (
    <div className="relative">
      <div aria-hidden className="hairline-grid pointer-events-none absolute inset-x-0 top-0 -z-10 h-[46rem]" />

      <section aria-labelledby="hero" className="mx-auto grid min-h-[100dvh] max-w-[1280px] items-center gap-16 px-5 pb-24 pt-36 md:grid-cols-[1.05fr_0.95fr] md:px-8">
        <div>
          <Reveal><Eyebrow>Prompt change validation</Eyebrow></Reveal>
          <Reveal delay={80}>
            <h1 id="hero" className="mt-6 text-[2.75rem] font-semibold leading-[1.02] tracking-tighter sm:text-6xl lg:text-[4.6rem]">
              Did the prompt change fix it, or <span className="text-gradient sheen pb-1">break something else?</span>
            </h1>
          </Reveal>
          <Reveal delay={160}>
            <p className="mt-7 max-w-[52ch] text-lg leading-relaxed text-muted">
              Give it the old prompt and the new one. It builds the tests, runs both versions side by side, subtracts the noise and tells you what got better and what got worse. No labelled dataset needed.
            </p>
          </Reveal>
          <Reveal delay={240} className="mt-10 flex flex-wrap items-center gap-4">
            <CtaButton onClick={start}>Start validating</CtaButton>
            <CtaButton href="#pipeline" tone="quiet">See how it works</CtaButton>
          </Reveal>
          <Reveal delay={320}>
            <dl className="mt-14 grid max-w-xl grid-cols-3 gap-6 border-t border-line pt-6 text-[13px]">
              {[['Paired', 'old vs new, same inputs'], ['Human', 'checkpoints at every stage'], ['Capped', 'hard budget, it asks first']].map(([k, v]) => (
                <div key={k}><dt className="text-base font-semibold tracking-tight">{k}</dt><dd className="mt-1 text-muted">{v}</dd></div>
              ))}
            </dl>
          </Reveal>
        </div>
        <Reveal delay={200}><VerdictPreview /></Reveal>
      </section>

      <section id="pipeline" aria-labelledby="pipe-h" className="mx-auto max-w-[1280px] scroll-mt-24 px-5 py-28 md:px-8 md:py-40">
        <Reveal className="max-w-2xl">
          <Eyebrow>Eight steps</Eyebrow>
          <h2 id="pipe-h" className="mt-6 text-4xl font-semibold leading-[1.05] tracking-tighter md:text-6xl">One pass, from the diff to a verdict.</h2>
          <p className="mt-5 max-w-[56ch] text-muted">You stay in control. It pauses for your review at scenarios, metrics, budget and results, and learns from what you reject.</p>
        </Reveal>
        <ol className="mt-16 grid grid-cols-1 gap-5 md:grid-cols-12">
          {STEPS.map((s, i) => (
            <Reveal as="li" key={s.n} delay={(i % 3) * 90} className={cn('list-none', s.span)}>
              <div className="bezel h-full">
                <div className="core group flex h-full min-h-48 flex-col justify-between gap-10 p-6 transition-transform duration-700 ease-[cubic-bezier(0.32,0.72,0,1)] hover:-translate-y-1">
                  <div className="flex items-start justify-between">
                    <span className="tnum font-mono text-xs text-muted">{s.n}</span>
                    <span className="flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent transition-transform duration-700 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:scale-110"><s.i size={20} weight="light" aria-hidden /></span>
                  </div>
                  <div>
                    <h3 className="text-xl font-semibold tracking-tight">{s.t}</h3>
                    <p className="mt-2 max-w-[48ch] text-[13px] leading-relaxed text-muted">{s.d}</p>
                  </div>
                </div>
              </div>
            </Reveal>
          ))}
        </ol>
      </section>

      <section id="principles" aria-labelledby="pr-h" className="mx-auto max-w-[1280px] scroll-mt-24 px-5 py-24 md:px-8 md:py-32">
        <div className="grid gap-14 md:grid-cols-[0.8fr_1.2fr]">
          <Reveal>
            <Eyebrow>Why you can trust it</Eyebrow>
            <h2 id="pr-h" className="mt-6 text-4xl font-semibold leading-[1.05] tracking-tighter md:text-5xl">It would rather say “not sure” than guess.</h2>
            <p className="mt-5 max-w-[46ch] text-muted">Most prompt tests tell you a score moved. This tells you whether the move is bigger than the noise, and on how much of your app.</p>
          </Reveal>
          <ul className="grid gap-5 sm:grid-cols-2">
            {PRINCIPLES.map((p, i) => (
              <Reveal as="li" key={p.t} delay={i * 90} className="list-none">
                <div className="bezel h-full"><div className="core h-full p-6">
                  <p.i size={26} weight="light" aria-hidden className="text-accent" />
                  <h3 className="mt-6 text-lg font-semibold tracking-tight">{p.t}</h3>
                  <p className="mt-2 text-[13px] leading-relaxed text-muted">{p.d}</p>
                </div></div>
              </Reveal>
            ))}
          </ul>
        </div>
      </section>

      <section aria-labelledby="cta-h" className="mx-auto max-w-[1280px] px-5 pb-32 pt-16 md:px-8">
        <Reveal>
          <div className="bezel"><div className="core relative overflow-hidden px-6 py-20 text-center md:py-28">
            <div aria-hidden className="absolute left-1/2 top-0 size-[40rem] -translate-x-1/2 -translate-y-1/2 rounded-full opacity-60 blur-3xl" style={{ background: 'radial-gradient(closest-side, var(--glow), transparent)' }} />
            <GitDiff size={32} weight="light" aria-hidden className="relative mx-auto text-accent" />
            <h2 id="cta-h" className="relative mx-auto mt-6 max-w-3xl text-4xl font-semibold leading-[1.05] tracking-tighter md:text-6xl">Check your next prompt change before it ships.</h2>
            <p className="relative mx-auto mt-5 max-w-[48ch] text-muted">Try the bundled billing-bot example, or paste your own prompts. A dry run costs nothing.</p>
            <div className="relative mt-10 flex justify-center"><CtaButton onClick={start}>Start validating</CtaButton></div>
          </div></div>
        </Reveal>
        <p className="mt-10 text-center text-xs text-muted">Prompt Change Validator. Runs locally; your keys stay on your machine.</p>
      </section>
    </div>
  )
}
