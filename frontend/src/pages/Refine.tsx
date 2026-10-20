import { Sparkle } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, withToken } from '../api'
import { Badge, levelTone, statusTone } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { useAsk } from '../components/ui/ask'
import { Input, Select, Textarea } from '../components/ui/field'
import { EmptyState, Notice, PageHeader, Section } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import type { Candidate, HeldOut, Optimization, Project } from '../types'

const usd = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`
const LEVEL = { block: 'Block', caution: 'Caution', ok: 'Looks fine', incomplete: 'Incomplete', inconclusive: 'Inconclusive' } as Record<string, string>

function Diff({ text, label }: { text: string; label: string }) {
  return (
    <div>
      <h5 className="mb-1.5 text-xs font-medium text-muted">{label}</h5>
      {!text ? <p className="text-[13px] text-muted">No difference.</p> : (
        <pre className="max-h-56 overflow-auto rounded-2xl border border-line bg-surface p-2 font-mono text-[12px] leading-5">
          {text.split('\n').map((l, i) => <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'bg-good-soft text-good' : l.startsWith('-') && !l.startsWith('---') ? 'bg-bad-soft text-bad' : 'text-muted'}>{l || ' '}</div>)}
        </pre>
      )}
    </div>
  )
}

function HeldOutPanel({ title, h }: { title: string; h?: HeldOut }) {
  if (!h) return <div className="rounded-xl border border-dashed border-line-strong p-3 text-[13px] text-muted">{title}: not validated yet</div>
  const report = async () => {
    const r = await api<{ id: string }>(`/runs/${h.run_id}/report`)
    window.open(withToken(`/api/reports/${r.id}/export?format=md`), '_blank')
  }
  return (
    <div className="space-y-1.5 rounded-xl bg-sunken p-3 text-[13px]">
      <div className="flex items-center gap-2"><span className="font-medium">{title}</span><Badge tone={levelTone(h.level)}>{LEVEL[h.level] ?? h.level}</Badge>{h.status !== 'done' && <Badge tone="warn">{h.status}</Badge>}</div>
      <p>{h.recommendation}</p>
      <p className="tnum text-muted">Fixed {h.counts.fixed}, broken {h.counts.broken}, unchanged {h.counts.unchanged}, inconclusive {h.counts.inconclusive}, on {h.scope.tested} held-out cases.</p>
      <Button variant="link" size="sm" onClick={report}>Full report (Markdown)</Button>
    </div>
  )
}

function CandidateItem({ oid, c, onChange }: { oid: string; c: Candidate; onChange: () => void }) {
  const { ask, confirm } = useAsk()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(c.text)
  const [err, setErr] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId, onChange)
  const call = async (path: string, method: string, body?: object) => {
    setErr('')
    try { return await api<any>(`/optimizations/${oid}/candidates/${c.id}${path}`, { method, body: body && JSON.stringify(body) }) } catch (e) { setErr(String(e)); return null }
  }
  const evaluate = async () => {
    const cap = await ask({ title: 'Validate on held-out cases', description: 'Each comparison (against your new prompt and against your old prompt) gets its own hard cap.', label: 'Budget per comparison (USD)', initial: '1', required: true, confirmLabel: 'Start validation' })
    if (!cap) return
    const r = await call('/evaluate', 'POST', { cap: +cap })
    if (r) setJobId(r.job_id)
  }
  const reject = async () => {
    const comment = await ask({ title: 'Reject this candidate', description: 'Your reason is fed back into the next optimization.', label: 'What is wrong with it?', multiline: true, confirmLabel: 'Reject candidate' })
    if (comment !== null && (await call('/reject', 'POST', { comment }))) onChange()
  }
  const promote = async () => {
    if (await confirm({ title: 'Make this your new prompt?', description: "The current new prompt is kept as new_prev_N. Earlier analysis, plan and runs refer to the previous one, so they will be marked out of date.", confirmLabel: 'Accept and promote' }) && (await call('/accept', 'POST', { promote: true }))) onChange()
  }
  const pending = !c.heldout.vs_new || !c.heldout.vs_old || [c.heldout.vs_new, c.heldout.vs_old].some((h) => h?.status !== 'done')
  return (
    <li className={`space-y-4 py-6 ${c.status === 'rejected' ? 'opacity-55' : ''}`}>
      <div className="flex flex-wrap items-center gap-3">
        <h4 className="text-[15px] font-semibold">{c.label}</h4>
        <Badge tone={statusTone(c.status)}>{c.status}{c.edited_from ? ' (edited)' : ''}</Badge>
        {c.val_score != null && <span className="tnum text-[13px] text-muted">Validation score {c.seed_val_score?.toFixed(2)} to <b className="text-ink">{c.val_score.toFixed(2)}</b></span>}
        <JobStatus job={job} />
      </div>
      {c.notes.length > 0 && <ul className="max-w-[75ch] list-disc space-y-0.5 pl-5 text-[13px]">{c.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      <div className="grid gap-4 md:grid-cols-2"><Diff label="Changes against your new prompt" text={c.diff_vs_new} /><Diff label="Changes against your old prompt" text={c.diff_vs_old} /></div>
      <div className="grid gap-3 md:grid-cols-2"><HeldOutPanel title="Against the new prompt" h={c.heldout.vs_new} /><HeldOutPanel title="Against the old prompt" h={c.heldout.vs_old} /></div>
      <p className="text-xs text-muted">Held-out cases were never shown to the optimizer. Results use the same paired, noise-adjusted analysis as the main report.</p>
      {editing && (
        <div className="space-y-3">
          <Textarea mono label="Edit the candidate prompt" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" onClick={async () => { if (await call('', 'PATCH', { text })) { setEditing(false); onChange() } }}>Save as a new candidate</Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {err && <Notice tone="bad" className="whitespace-pre-wrap">{err}</Notice>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={c.status === 'accepted'} onClick={async () => { if (await call('/accept', 'POST', { promote: false })) onChange() }}>Accept</Button>
        <Button size="sm" variant="secondary" disabled={c.status === 'accepted'} onClick={promote}>Accept and make it my new prompt</Button>
        <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Edit</Button>
        {pending && <Button size="sm" variant="secondary" onClick={evaluate}>{c.heldout.vs_new ? 'Continue validation' : 'Validate on held-out'}</Button>}
        <Button size="sm" variant="ghost" onClick={reject}>Reject</Button>
      </div>
    </li>
  )
}

export default function Refine({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [cap, setCap] = useState('1')
  const [calls, setCalls] = useState('')
  const [n, setN] = useState(1)
  const [jobId, setJobId] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const key = ['opts', project.id]
  const list = useQuery({ queryKey: key, queryFn: () => api<Optimization[]>(`/projects/${project.id}/optimizations`), refetchInterval: jobId ? 2500 : false })
  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const job = useJob(jobId, () => { refresh(); setJobId(null) })
  const running = !!jobId && (!job || ['pending', 'running'].includes(job.status))
  const start = async () => {
    setErr('')
    try {
      const r = await api<{ job_id: string }>(`/projects/${project.id}/optimize`, { method: 'POST', body: JSON.stringify({ cap: +cap, n_candidates: n, ...(calls ? { max_metric_calls: +calls } : {}) }) })
      setJobId(r.job_id)
    } catch (e) { setErr(String(e)) }
  }
  const opts = [...(list.data ?? [])].reverse()
  return (
    <div className="max-w-[1000px]">
      <PageHeader title="Refine" description="Optional. Improves your new prompt with GEPA using your approved scenarios and metrics. About 30% of the scenarios are held out and never shown to the optimizer; each candidate is then compared with both your new and your old prompt on them." />
      <Section title="Start an optimization">
        <div className="flex flex-wrap items-end gap-5">
          <Input label="Hard cap (USD), required" help="Covers the search and the validation." type="number" step="0.1" min="0" className="w-52" value={cap} onChange={(e) => setCap(e.target.value)} />
          <Input label="Search size (metric calls)" help="Leave empty for the default." type="number" min="6" className="w-52" placeholder="120" value={calls} onChange={(e) => setCalls(e.target.value)} />
          <Select label="Candidates" className="w-28" value={n} onChange={(e) => setN(+e.target.value)}>{[1, 2, 3].map((x) => <option key={x}>{x}</option>)}</Select>
          <Button disabled={running || !(+cap > 0)} onClick={start}>{running ? 'Optimizing' : 'Start optimization'}</Button>
        </div>
        {running && <p aria-live="polite" className="mt-3 text-[13px] text-muted">{job?.progress || 'Starting'}</p>}
        {job?.status === 'failed' && <Notice tone="bad" className="mt-3">{job.error}</Notice>}
        {err && <Notice tone="bad" className="mt-3 whitespace-pre-wrap">{err}</Notice>}
      </Section>
      {!list.isLoading && opts.length === 0 && !running && (
        <EmptyState icon={<Sparkle size={28} />} title="No optimization yet">A reflector reads where your new prompt fails and proposes minimal edits that keep your constraints. You review the diff and the held-out results, then accept, edit or reject.</EmptyState>
      )}
      {opts.map((o) => (
        <Section key={o.id} title={`Optimization ${o.id.slice(-6)}`} actions={<Badge tone={statusTone(o.status)}>{o.status}</Badge>}>
          <p className="tnum mb-3 text-[13px] text-muted">
            Spent {usd(o.spent)}{o.config ? ` of ${usd(o.config.cap)}` : ''}.
            {o.config && ` Split: ${o.config.train_ids.length} train, ${o.config.val_ids.length} validation, ${o.config.heldout_ids.length} held out.`}
            {o.result?.seed_val_score != null && ` Validation score ${o.result.seed_val_score.toFixed(2)} to ${o.result.best_val_score?.toFixed(2)}.`}
          </p>
          {o.error && <Notice tone="warn" className="mb-3">{o.error}</Notice>}
          {o.result?.warnings.map((w, i) => <Notice key={i} tone="warn" className="mb-3">{w}</Notice>)}
          {o.result?.note && <p className="mb-3 text-[13px] text-muted">{o.result.note}. Your prompt is already as good as your metrics can tell.</p>}
          <ul className="divide-y divide-line border-y border-line">{o.result?.candidates.map((c) => <CandidateItem key={c.id + c.status + JSON.stringify(c.heldout).length} oid={o.id} c={c} onChange={refresh} />)}</ul>
        </Section>
      ))}
    </div>
  )
}
