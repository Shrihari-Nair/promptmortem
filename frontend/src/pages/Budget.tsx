import { Coins, DownloadSimple, Lock, PushPin } from '@phosphor-icons/react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, withToken } from '../api'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/field'
import { EmptyState, Notice, PageHeader, Section } from '../components/ui/page'
import type { Estimate, Project, RankRow, Tier } from '../types'

const usd = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`
const cap1 = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function Lost({ t }: { t: Tier }) {
  const cats = Object.entries(t.coverage_lost.categories)
  if (cats.length === 0 && t.coverage_lost.clusters_dropped.length === 0) return <span className="text-muted">Nothing lost</span>
  return (
    <div className="space-y-0.5">
      {cats.length > 0 && <p title={cats.map(([c, p]) => `${c} ${p}`).join(', ')}>{cats.slice(0, 3).map(([c, p]) => `${c.replaceAll('_', ' ')} ${p}`).join(', ')}{cats.length > 3 && <span className="text-muted"> and {cats.length - 3} more</span>}</p>}
      {t.coverage_lost.clusters_dropped.length > 0 && <p className="text-bad">Clusters dropped entirely: {t.coverage_lost.clusters_dropped.length}</p>}
    </div>
  )
}

export default function Budget({ project }: { project: Project }) {
  const [est, setEst] = useState<Estimate | null>(null)
  const [levers, setLevers] = useState<Record<string, boolean> | undefined>()
  const [tier, setTier] = useState('balanced')
  const [cap, setCap] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<{ cap: number; warning: string | null } | null>(null)
  const [custom, setCustom] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [unlock, setUnlock] = useState(false)
  const locked = useQuery({ queryKey: ['plan', project.id], queryFn: () => api<{ chosen: { tier: string; cap: number; warning?: string | null } | null }>(`/projects/${project.id}/plan`) }).data?.chosen
  const rank = useQuery({ queryKey: ['rank', project.id, est?.tiers.length], enabled: custom, queryFn: () => api<RankRow[]>(`/projects/${project.id}/plan/ranking`) })

  const estimate = async (lv = levers, path = 'estimate', extra: object = {}) => {
    setBusy(true); setErr(''); setDone(null)
    try {
      const e = await api<Estimate>(`/projects/${project.id}/plan/${path}`, { method: 'POST', body: JSON.stringify({ levers: lv, ...extra }) })
      setEst(e); setLevers(Object.fromEntries(e.levers.map((l) => [l.id, l.enabled])))
    } catch (x) { setErr(String(x)) }
    setBusy(false)
  }
  useEffect(() => { if (!sel.size && rank.data) setSel(new Set(rank.data.filter((r, i) => r.protected || i < rank.data.length * 0.5).map((r) => r.id))) }, [rank.data, sel.size])
  const chosen = est?.tiers.find((t) => t.tier === tier)
  useEffect(() => { if (chosen) setCap(String(chosen.suggested_cap)) }, [tier, est?.tiers.length, chosen?.suggested_cap])

  const confirm = async () => {
    setErr('')
    try {
      const r = await api<{ cap: number; warning: string | null }>(`/projects/${project.id}/plan/confirm`, {
        method: 'POST', body: JSON.stringify({ tier, cap: +cap, levers, ...(tier === 'custom' ? { scenario_ids: [...sel], confirm_drop_protected: unlock } : {}) }),
      })
      setDone(r)
    } catch (x) { setErr(String(x)) }
  }

  return (
    <div className="max-w-[1000px]">
      <PageHeader
        title="Budget"
        description="Planning was never limited by cost. Estimating runs a small probe, a few cases on both prompts plus a few judge calls, to measure real per-call prices. Then you choose how much of the plan to run."
        actions={<>
          {project.promptfoo_enabled !== false && <Button variant="secondary" asChild><a href={withToken(`/api/projects/${project.id}/export/promptfoo`)} download="promptfooconfig.yaml"><DownloadSimple size={15} aria-hidden />Export Promptfoo config</a></Button>}
          {est && <Button variant="secondary" disabled={busy} onClick={() => estimate(levers, 'estimate', { reprobe: true })}>Measure again</Button>}
          {(est || busy) && <Button disabled={busy} onClick={() => estimate(levers)}>{busy ? 'Measuring' : 'Re-estimate'}</Button>}
        </>}
      />
      {err && <Notice tone="bad" className="mb-6 whitespace-pre-wrap">{err}</Notice>}
      {locked && !est && <Notice tone="good" className="mb-6">A plan is already locked: the {locked.tier} tier with a hard cap of {usd(locked.cap)}. Estimate again only if you want to change it.</Notice>}
      {!est && !busy && (
        <EmptyState icon={<Coins size={28} />} title="No estimate yet" action={<Button onClick={() => estimate(levers)}>Estimate cost</Button>}>
          The probe costs a few cents. You will see three tiers with a low, expected and high cost, and exactly which coverage each one gives up.
        </EmptyState>
      )}

      {est && (
        <>
          {est.warnings?.map((w, i) => <Notice key={i} tone="warn" role="alert" className="mb-6">{w}</Notice>)}
          <Section title="Choose a tier" description={`${est.protected_scenarios} pinned or high-severity cases are never trimmed automatically. Per-call prices were measured on a probe that cost ${usd(est.probe.paid)}; lever savings are estimates until a run measures them.`}>
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full text-left text-[13px]">
                <caption className="sr-only">Tiers with cost range and coverage lost</caption>
                <thead className="bg-sunken text-xs text-muted">
                  <tr><th className="w-44 p-3 font-medium">Tier</th><th className="p-3 font-medium">Cases and repeats</th><th className="p-3 text-right font-medium">Expected</th><th className="p-3 font-medium">Range</th><th className="p-3 font-medium">Coverage lost</th></tr>
                </thead>
                <tbody className="divide-y divide-line" role="radiogroup" aria-label="Tier">
                  {est.tiers.map((t) => (
                    <tr key={t.tier} onClick={() => setTier(t.tier)} className={`cursor-pointer align-top transition-colors duration-150 hover:bg-sunken ${t.tier === tier ? 'bg-accent-soft' : ''}`}>
                      <td className="p-3"><label className="flex cursor-pointer items-center gap-2.5 font-medium"><input type="radio" name="tier" aria-label={`${cap1(t.tier)} tier`} checked={t.tier === tier} onChange={() => setTier(t.tier)} className="size-4 accent-[var(--accent)]" />{cap1(t.tier)}</label></td>
                      <td className="tnum p-3">{t.n_scenarios} cases, {t.repeats} repeats</td>
                      <td className="tnum p-3 text-right text-[15px] font-semibold">{usd(t.cost.expected)}</td>
                      <td className="tnum p-3 text-muted">{usd(t.cost.low)} to {usd(t.cost.high)}</td>
                      <td className="p-3"><Lost t={t} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Free cost levers" description="Switch on what you are comfortable with. A lever that is unavailable for your provider says why.">
            <ul className="divide-y divide-line border-y border-line">
              {est.levers.map((l) => (
                <li key={l.id} className="py-3.5">
                  <label className={`flex items-start gap-3 ${l.available === false ? 'opacity-55' : 'cursor-pointer'}`}>
                    <input type="checkbox" className="mt-1 size-4 accent-[var(--accent)]" checked={levers?.[l.id] ?? false} disabled={busy || l.available === false} onChange={(e) => estimate({ ...levers, [l.id]: e.target.checked })} />
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{cap1(l.id.replaceAll('_', ' '))}</span>
                      <span className="tnum ml-2 text-[13px] text-muted">{l.saving_expected >= 0 ? `saves about ${usd(l.saving_expected)} on the full tier` : `costs about ${usd(-l.saving_expected)} more on the full tier`}</span>
                      <span className="mt-0.5 block max-w-[70ch] text-[13px] text-muted">{l.note}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Custom selection" actions={<label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-[var(--accent)]" checked={custom} onChange={(e) => setCustom(e.target.checked)} />Choose cases by hand</label>}>
            {!custom && <p className="text-[13px] text-muted">Sort by priority, untick the cases you can live without, and see the cost and the coverage you give up.</p>}
            {custom && rank.data && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-4 text-[13px]">
                  <span className="tnum text-muted">{sel.size} of {rank.data.length} selected</span>
                  <label className="flex items-center gap-2"><input type="checkbox" className="size-4 accent-[var(--accent)]" checked={unlock} onChange={(e) => setUnlock(e.target.checked)} />Allow dropping pinned and high-severity cases (needs this explicit confirmation)</label>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={async () => { await estimate(levers, 'trim', { scenario_ids: [...sel], confirm_drop_protected: unlock }); setTier('custom') }}>Estimate selection</Button>
                </div>
                <div className="max-h-96 overflow-auto rounded-lg border border-line">
                  <table className="w-full text-left text-[13px]">
                    <caption className="sr-only">Cases by priority</caption>
                    <thead className="sticky top-0 bg-sunken text-xs text-muted"><tr><th className="w-10 p-2"><span className="sr-only">Include</span></th><th className="p-2 font-medium">Priority</th><th className="p-2 font-medium">Severity</th><th className="p-2 font-medium">Category</th><th className="p-2 font-medium">Case</th></tr></thead>
                    <tbody className="divide-y divide-line">
                      {rank.data.map((r) => (
                        <tr key={r.id} className={r.protected ? 'bg-warn-soft/50' : ''}>
                          <td className="p-2"><input type="checkbox" aria-label={`Include case ${r.id}`} className="size-4 accent-[var(--accent)]" checked={sel.has(r.id)} disabled={r.protected && !unlock} onChange={(e) => setSel((s) => { const n = new Set(s); e.target.checked ? n.add(r.id) : n.delete(r.id); return n })} /></td>
                          <td className="tnum p-2">{r.priority.toFixed(2)}</td><td className="tnum p-2">{r.severity}</td><td className="p-2">{r.category.replaceAll('_', ' ')}</td>
                          <td className="max-w-0 truncate p-2"><span className="inline-flex items-center gap-1.5">{r.pinned ? <PushPin size={13} aria-label="Pinned" /> : r.protected ? <Lock size={13} aria-label="Protected: high severity" /> : null}{r.text}</span></td>
                        </tr>))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </Section>

          <Section title="Hard cap">
            <div className="flex flex-wrap items-end gap-5">
              <Input label="Hard cap (USD)" help="The run pauses and asks when it reaches this; it never silently truncates. Default: the high estimate of the chosen tier." type="number" step="0.01" min="0" className="w-72" value={cap} onChange={(e) => setCap(e.target.value)} />
              <Button disabled={!chosen || !(+cap > 0)} onClick={confirm}>Confirm {tier} plan</Button>
            </div>
            {done && <Notice tone={done.warning ? 'warn' : 'good'} className="mt-4">Plan locked: cap {usd(done.cap)}.{done.warning ? ` ${done.warning}` : ''}</Notice>}
          </Section>
        </>
      )}
    </div>
  )
}
