import { CaretRight, DownloadSimple, ListMagnifyingGlass } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ErrorBar, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, withToken } from '../api'
import { Badge, levelTone } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { useAsk } from '../components/ui/ask'
import { DefList, DefRow, EmptyState, Notice, PageHeader, Section, Skeleton } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import type { MetricRow, Project, Report, ReportRow } from '../types'
import { runKey } from './Run'

const pct = (x: number | null | undefined) => (x == null ? 'n/a' : `${Math.round(x * 100)}%`)
const txt = (o?: Record<string, unknown>) => Object.values(o ?? {}).join(' | ')
const LEVEL = { block: 'Block', caution: 'Caution', ok: 'Looks fine', incomplete: 'Incomplete', inconclusive: 'Inconclusive' } as Record<string, string>

function MetricChart({ rows }: { rows: MetricRow[] }) {
  // mean paired difference (new minus old) per metric with its 95% interval; left of 0 = worse, right = better
  const data = rows.map((m) => ({ name: m.name, diff: m.mean_diff, err: [m.mean_diff - m.ci95[0], m.ci95[1] - m.mean_diff] }))
  return (
    <div role="img" aria-label="Difference per metric with 95% confidence intervals. Left of zero is worse, right is better." style={{ width: '100%', height: 64 + 40 * rows.length }}>
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" domain={[-1, 1]} tickCount={9} fontSize={11} stroke="var(--line-strong)" tick={{ fill: 'var(--muted)' }} />
          <YAxis type="category" dataKey="name" width={220} fontSize={12} stroke="var(--line-strong)" tick={{ fill: 'var(--ink)' }} />
          <Tooltip cursor={{ fill: 'var(--sunken)' }} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 6, color: 'var(--ink)', fontSize: 12 }} formatter={(v) => (typeof v === 'number' ? v.toFixed(3) : String(v))} />
          <ReferenceLine x={0} stroke="var(--muted)" />
          <Bar dataKey="diff" isAnimationActive={false} barSize={14} radius={[0, 3, 3, 0]}>
            {data.map((d, i) => <Cell key={i} fill={d.diff < 0 ? 'var(--bad)' : 'var(--good)'} />)}
            <ErrorBar dataKey="err" direction="x" stroke="var(--ink)" width={6} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function CaseItem({ r, repId, onChange, kind }: { r: ReportRow; repId: string; onChange: () => void; kind: 'regression' | 'improvement' }) {
  const { ask } = useAsk()
  const mark = async (verdict: string) => {
    const comment = await ask({ title: `Mark this case as actually ${verdict}`, description: 'Your verdict replaces the measured one in this report and is fed back to the planning agents.', label: 'Why?', multiline: true, confirmLabel: 'Save verdict' })
    if (comment === null) return
    await api(`/reports/${repId}/verdict-override`, { method: 'POST', body: JSON.stringify({ scenario_id: r.scenario_id, verdict, comment }) })
    onChange()
  }
  return (
    <li className="py-5">
      <div className="flex flex-wrap items-center gap-2.5 text-[13px]">
        <span className="font-medium">{r.metric_name}</span>
        <Badge tone={r.severity >= 4 ? 'bad' : 'neutral'}>severity {r.severity}</Badge>
        {r.old_pass_rate != null && <span className="tnum text-muted">{pct(r.old_pass_rate)} to {pct(r.new_pass_rate)}</span>}
        {r.preference_for_new != null && <span className="tnum text-muted">preference for new {r.preference_for_new.toFixed(2)}</span>}
        {r.disputed && <Badge tone="warn">judge orders disagreed</Badge>}
        {r.override && <Badge tone="accent">you said: {r.override.verdict}</Badge>}
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => mark(kind === 'regression' ? 'fine' : 'broken')}>{kind === 'regression' ? 'This is actually fine' : 'This is actually broken'}</Button>
      </div>
      <p className="mt-2 max-w-[80ch] text-[13px] text-muted"><span className="font-medium text-ink">Input:</span> {txt(r.input)}</p>
      {r.old_output != null && (
        <div className="mt-3 grid gap-3 text-[13px] leading-relaxed md:grid-cols-2">
          <div className="rounded-md bg-sunken p-3"><p className="mb-1 text-xs font-medium text-muted">Old output</p>{r.old_output}</div>
          <div className={`rounded-md p-3 ${kind === 'regression' ? 'bg-bad-soft' : 'bg-good-soft'}`}><p className="mb-1 text-xs font-medium text-muted">New output</p>{r.new_output}</div>
        </div>
      )}
      {r.rationale && <p className="mt-2 max-w-[80ch] text-xs text-muted">Judge: {r.rationale}</p>}
    </li>
  )
}

function Cases({ title, rows, repId, onChange, kind, openByDefault }: { title: string; rows: ReportRow[]; repId: string; onChange: () => void; kind: 'regression' | 'improvement'; openByDefault: boolean }) {
  const byCluster = rows.reduce<Record<string, ReportRow[]>>((a, r) => ({ ...a, [r.cluster ?? r.category]: [...(a[r.cluster ?? r.category] ?? []), r] }), {})
  return (
    <Section title={`${title} (${rows.length})`}>
      {rows.length === 0 && <p className="text-[13px] text-muted">{kind === 'regression' ? 'No regressions were found in the tested scope.' : 'No improvements were found in the tested scope.'}</p>}
      <div className="divide-y divide-line">
        {Object.entries(byCluster).map(([c, rs]) => (
          <details key={c} open={openByDefault} className="group py-3 first:pt-0">
            <summary className="flex cursor-pointer list-none items-center gap-2 py-1 text-[14px] font-medium [&::-webkit-details-marker]:hidden">
              <CaretRight size={14} aria-hidden className="text-muted transition-transform duration-150 group-open:rotate-90" />{c}<span className="tnum text-[13px] font-normal text-muted">{rs.length} {rs.length === 1 ? 'case' : 'cases'}</span>
            </summary>
            <ul className="divide-y divide-line">{rs.slice(0, 25).map((r, i) => <CaseItem key={r.scenario_id + r.metric_name + i} r={r} repId={repId} onChange={onChange} kind={kind} />)}</ul>
            {rs.length > 25 && <p className="text-xs text-muted">{rs.length - 25} more are in the export.</p>}
          </details>
        ))}
      </div>
    </Section>
  )
}

export default function ReportPage({ project }: { project: Project }) {
  const qc = useQueryClient()
  const runId = localStorage.getItem(runKey(project.id))
  const [jobId, setJobId] = useState<string | null>(null)
  const q = useQuery({ queryKey: ['report', runId], enabled: !!runId, queryFn: () => api<{ id: string; summary: Report }>(`/runs/${runId}/report`) })
  const refresh = () => qc.invalidateQueries({ queryKey: ['report', runId] })
  const job = useJob(jobId, refresh)
  if (!runId) return <div className="max-w-[880px]"><PageHeader title="Report" /><EmptyState icon={<ListMagnifyingGlass size={28} />} title="There is no run to report on yet">Start a run in the Run step. The report appears here, and a stopped or paused run gets a report that says it is partial.</EmptyState></div>
  if (!q.data) return <div aria-busy="true" className="space-y-4"><Skeleton className="h-8 w-1/4" /><Skeleton className="h-40" /><Skeleton className="h-24" /></div>
  const { id, summary: r } = q.data
  const h = r.headline, s = r.scope
  const exp = (fmt: string) => withToken(`/api/reports/${id}/export?format=${fmt}`)
  return (
    <div className="max-w-[1000px]">
      <PageHeader
        title="Report"
        description="Old versus new on the same inputs and repeats, adjusted for noise, and scoped to what was actually tested."
        actions={<>
          <JobStatus job={job} />
          <Button variant="secondary" size="sm" onClick={async () => setJobId((await api<{ job_id: string }>(`/reports/${id}/rescore`, { method: 'POST' })).job_id)}>Re-score with current metrics</Button>
          <Button variant="secondary" size="sm" asChild><a href={exp('md')} target="_blank" rel="noreferrer"><DownloadSimple size={14} aria-hidden />Markdown</a></Button>
          <Button variant="secondary" size="sm" asChild><a href={exp('json')} target="_blank" rel="noreferrer"><DownloadSimple size={14} aria-hidden />JSON</a></Button>
          {project.promptfoo_enabled !== false && <Button variant="secondary" size="sm" asChild><a href={withToken(`/api/projects/${project.id}/export/promptfoo`)} download="promptfooconfig.yaml"><DownloadSimple size={14} aria-hidden />Promptfoo config</a></Button>}
        </>}
      />

      <section aria-label="Verdict" className="rounded-lg border border-line bg-surface p-6">
        <div className="flex items-start gap-3">
          <Badge tone={levelTone(h.level)} className="mt-1 px-2.5 py-1 text-[13px]">{LEVEL[h.level] ?? h.level}</Badge>
          <p className="max-w-[62ch] text-lg font-medium leading-snug">{h.recommendation}</p>
        </div>
        <p className="mt-1.5 text-xs text-muted">Advisory, produced by rules. Never a claim beyond what was tested.</p>
        <dl className="mt-6 flex flex-wrap gap-x-12 gap-y-4 border-t border-line pt-5">
          {([['fixed', 'text-good', 'Fixed'], ['broken', 'text-bad', 'Broken'], ['unchanged', 'text-ink', 'Unchanged'], ['inconclusive', 'text-muted', 'Inconclusive']] as const).map(([k, c, label]) => (
            <div key={k}><dd className={`tnum text-3xl font-semibold tracking-tight ${h[k] === 0 && k !== 'unchanged' ? 'text-muted' : c}`}>{h[k]}</dd><dt className="text-xs text-muted">{label}</dt></div>
          ))}
        </dl>
        <p className="tnum mt-5 text-[13px]">Net effect (mean paired difference): <b>{h.net_effect.mean_diff}</b>, 95% interval {h.net_effect.ci95[0]} to {h.net_effect.ci95[1]}.</p>
      </section>

      <Section title="Scope stamp" description="What this verdict does and does not cover." className="mt-10">
        <DefList>
          <DefRow term="Tier">{s.tier}, {s.complete ? 'complete' : <span className="font-semibold text-bad">partial (run {s.run_status})</span>}</DefRow>
          <DefRow term="Cases">{s.tested} tested, {s.dropped} dropped by the tier{s.untested_in_tier ? `, ${s.untested_in_tier} planned but not tested` : ''}{s.deep_dive_added ? `, ${s.deep_dive_added} added near regressions` : ''}</DefRow>
          <DefRow term="Who checked">Reviewed by a human: {pct(s.reviewed_share)}. Verified by your labelled data: {pct(s.verified_share)}. The rest is synthetic.</DefRow>
          <DefRow term="Repeats">{s.repeats} per case, adaptive</DefRow>
          <DefRow term="Coverage lost">{Object.keys(s.coverage_lost).length ? Object.entries(s.coverage_lost).map(([c, p]) => `${c.replaceAll('_', ' ')} ${p}`).join(', ') : 'none'}{s.clusters_dropped.length ? `. Clusters dropped entirely: ${s.clusters_dropped.join(', ')}` : ''}</DefRow>
          <DefRow term="Noise, old vs old">{Object.entries(s.noise_band).map(([k, v]) => `${k} ±${v}`).join('; ') || 'not measured yet'}</DefRow>
          <DefRow term="Per-case threshold">A single case counts as fixed or broken only past a threshold corrected for {s.comparisons} comparisons: {Object.entries(s.detection_threshold).map(([k, v]) => `${k} ${v == null ? 'not detectable at this many repeats' : v}`).join('; ')}.</DefRow>
          {!!s.judge_calls_skipped && <DefRow term="Judge calls skipped">{s.judge_calls_skipped}, because the output had already failed a programmatic check</DefRow>}
          {s.cheap_judge && <DefRow term="Cheaper judge">{s.cheap_judge.metrics.join(', ') || 'no metric qualified'}, judged by {s.cheap_judge.model} instead of the configured judge</DefRow>}
        </DefList>
        {r.warnings.map((w, i) => <Notice key={i} tone="warn" className="mt-3">{w}</Notice>)}
      </Section>

      <Cases title="Regressions" rows={r.regressions} repId={id} onChange={refresh} kind="regression" openByDefault />
      <Cases title="Improvements" rows={r.improvements} repId={id} onChange={refresh} kind="improvement" openByDefault={false} />

      <Section title="Per metric" description="Mean difference (new minus old) with a 95% interval. Left of zero is worse.">
        <MetricChart rows={r.per_metric} />
        <div className="mt-4 overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">Per-metric results</caption>
            <thead className="bg-sunken text-xs text-muted"><tr><th className="p-2.5 font-medium">Metric</th><th className="p-2.5 font-medium">Cases</th><th className="p-2.5 font-medium">Old</th><th className="p-2.5 font-medium">New</th><th className="p-2.5 font-medium">Difference (95% interval)</th><th className="p-2.5 text-right font-medium">Test p</th></tr></thead>
            <tbody className="divide-y divide-line">{r.per_metric.map((m) => (
              <tr key={m.metric_id}>
                <td className="p-2.5">{m.name}<span className="block text-xs text-muted">{m.kind}</span></td>
                <td className="tnum p-2.5">{m.n} of {m.applicable}</td><td className="tnum p-2.5">{m.old_mean}</td><td className="tnum p-2.5">{m.new_mean}</td>
                <td className="tnum p-2.5">{m.mean_diff} ({m.ci95[0]} to {m.ci95[1]})</td>
                <td className="tnum p-2.5 text-right">{m.test ? (m.test.p < 0.001 ? '<0.001' : m.test.p.toFixed(3)) : ''}</td>
              </tr>))}</tbody>
          </table>
        </div>
        <div className="mt-6 overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">Results per category</caption>
            <thead className="bg-sunken text-xs text-muted"><tr><th className="p-2.5 font-medium">Category</th><th className="p-2.5 font-medium">Fixed</th><th className="p-2.5 font-medium">Broken</th><th className="p-2.5 font-medium">Unchanged</th><th className="p-2.5 font-medium">Inconclusive</th></tr></thead>
            <tbody className="divide-y divide-line">{r.per_category.map((g) => <tr key={g.name}><td className="p-2.5">{g.name.replaceAll('_', ' ')}</td><td className="tnum p-2.5">{g.fixed}</td><td className="tnum p-2.5">{g.broken}</td><td className="tnum p-2.5">{g.unchanged}</td><td className="tnum p-2.5">{g.inconclusive}</td></tr>)}</tbody>
          </table>
        </div>
      </Section>

      <div className="mt-9 grid gap-x-12 gap-y-8 border-t border-line pt-6 md:grid-cols-2">
        <section>
          <h3 className="mb-2 text-base font-semibold">Judge reliability</h3>
          <DefList>
            <DefRow term="Your labels">{r.judge_reliability.calibration_agreement == null ? 'No labelled traces were imported' : `${pct(r.judge_reliability.calibration_agreement)} agreement`}</DefRow>
            <DefRow term="Order swap">{pct(r.judge_reliability.pairwise_disagreement_rate)} disagreement over {r.judge_reliability.pairwise_judgments} judgments</DefRow>
            {!!r.judge_reliability.ensemble_judgments && <DefRow term="Second judge">{pct(r.judge_reliability.pointwise_disagreement_rate)} disagreement over {r.judge_reliability.ensemble_judgments} judgments</DefRow>}
          </DefList>
        </section>
        <section>
          <h3 className="mb-2 text-base font-semibold">Cost</h3>
          <DefList>
            <DefRow term="Spent">${r.cost.spent.toFixed(4)} of a ${r.cost.cap.toFixed(4)} cap</DefRow>
            <DefRow term="Estimated">${r.cost.estimated?.toFixed(4)} expected, ${r.cost.estimated_high?.toFixed(4)} high</DefRow>
            <DefRow term="Probe">${r.cost.probe_spent?.toFixed(4)}</DefRow>
          </DefList>
        </section>
      </div>
    </div>
  )
}
