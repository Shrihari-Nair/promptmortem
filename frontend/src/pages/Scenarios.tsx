import { ChatCircleText, CheckCircle, PencilSimple, PushPin, PushPinSlash, TreeStructure, XCircle } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge, statusTone } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { useAsk } from '../components/ui/ask'
import { Dialog, DialogContent, DialogTitle } from '../components/ui/dialog'
import { Input, Select, Textarea } from '../components/ui/field'
import { EmptyState, PageHeader, Section, Skeleton } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import { CATEGORIES, type Coverage, type Project, type Scenario } from '../types'

const msg = (s: Scenario) => Object.values(s.input).join(' | ')

function Heatmap({ cov, active, onPick }: { cov: Coverage; active: string; onPick: (id: string) => void }) {
  const max = Math.max(1, ...Object.values(cov.categories).flatMap((r) => Object.values(r)))
  return (
    <div className="space-y-1.5">
      {Object.entries(cov.categories).map(([cat, cells]) => (
        <div key={cat} className="flex items-center gap-3">
          <span className="w-36 shrink-0 text-[13px] text-muted">{cat.replaceAll('_', ' ')}</span>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(cells).map(([cid, n]) => {
              const share = n / max
              return (
                <button key={cid} title={`${cov.clusters[cid]?.name}: ${n}`} aria-label={`Cluster ${cov.clusters[cid]?.name}, ${n} scenarios`} aria-pressed={active === cid}
                  onClick={() => onPick(active === cid ? '' : cid)}
                  className={`tnum h-8 min-w-10 rounded-md px-2 text-xs font-medium transition-shadow duration-150 hover:shadow-panel ${active === cid ? 'ring-2 ring-ink' : ''}`}
                  style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(12 + 78 * share)}%, var(--surface))`, color: share > 0.55 ? 'var(--canvas)' : 'var(--ink)' }}>
                  {n}
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

function CaseRow({ s, onChange, onRejectDialog }: { s: Scenario; onChange: () => void; onRejectDialog: (s: Scenario) => void }) {
  const { ask } = useAsk()
  const [editing, setEditing] = useState(false)
  const [inp, setInp] = useState(JSON.stringify(s.input, null, 1))
  const [exp, setExp] = useState(s.expected_behavior)
  const [err, setErr] = useState('')
  const patch = async (body: object) => { await api(`/scenarios/${s.id}`, { method: 'PATCH', body: JSON.stringify(body) }); onChange() }
  const comment = async () => {
    const c = await ask({ title: 'Comment for the agents', description: 'Stored and fed back into the next scenario generation.', label: 'Your comment', multiline: true, required: true, confirmLabel: 'Save comment' })
    if (c) await api('/feedback', { method: 'POST', body: JSON.stringify({ project_id: s.project_id, target_type: 'scenario', target_id: s.id, comment: c }) })
  }
  const saveEdit = async () => {
    try { await patch({ input: JSON.parse(inp), expected_behavior: exp }); setEditing(false) } catch { setErr('The input must be valid JSON.') }
  }
  return (
    <li className="py-5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge>{s.category.replaceAll('_', ' ')}</Badge>
        <span className="text-muted">{s.origin}, {s.tier}</span>
        <span className="tnum text-muted">severity {s.severity}</span>
        <span className="tnum text-muted">relevance to the change {s.diff_relevance.toFixed(2)}</span>
        {s.flagged && <Badge tone="warn">uncertain</Badge>}
        {s.pinned && <span className="inline-flex items-center gap-1 text-muted"><PushPin size={13} weight="fill" aria-hidden />Pinned</span>}
        <Badge tone={statusTone(s.status)} className="ml-auto">{s.status}</Badge>
      </div>
      {editing ? (
        <div className="mt-3 grid gap-3">
          <Textarea mono label="Input (JSON)" rows={4} value={inp} onChange={(e) => setInp(e.target.value)} />
          <Textarea label="Expected behaviour" rows={2} value={exp} onChange={(e) => setExp(e.target.value)} />
          {err && <p role="alert" className="text-[13px] text-bad">{err}</p>}
          <div className="flex gap-2"><Button size="sm" onClick={saveEdit}>Save</Button><Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      ) : (
        <>
          <p className="mt-2.5 max-w-[75ch] whitespace-pre-wrap text-[15px] leading-relaxed">{msg(s)}</p>
          <p className="mt-1.5 max-w-[75ch] text-[13px] text-muted"><span className="font-medium text-ink">Expected:</span> {s.expected_behavior || 'not described'}</p>
          {s.rationale && <p className="mt-1 max-w-[75ch] text-xs text-muted">{s.rationale}</p>}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Button variant="secondary" size="sm" onClick={() => patch({ action: 'approve' })}><CheckCircle size={15} aria-hidden />Approve</Button>
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)}><PencilSimple size={15} aria-hidden />Edit</Button>
            <Button variant="secondary" size="sm" onClick={() => onRejectDialog(s)}><XCircle size={15} aria-hidden />Reject</Button>
            <Button variant="ghost" size="sm" onClick={() => patch({ pinned: !s.pinned })}>{s.pinned ? <PushPinSlash size={15} aria-hidden /> : <PushPin size={15} aria-hidden />}{s.pinned ? 'Unpin' : 'Pin'}</Button>
            <Button variant="ghost" size="sm" onClick={comment}><ChatCircleText size={15} aria-hidden />Comment</Button>
          </div>
        </>
      )}
    </li>
  )
}

export default function Scenarios({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const [view, setView] = useState<'queue' | 'all'>('queue')
  const [cluster, setCluster] = useState('')
  const [status, setStatus] = useState('')
  const [reject, setReject] = useState<Scenario | null>(null)
  const [why, setWhy] = useState('')
  const [more, setMore] = useState({ category: 'happy_path', n: 10 })
  const [mine, setMine] = useState({ category: 'happy_path', text: '', expected: '' })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['scenarios', project.id] }); qc.invalidateQueries({ queryKey: ['coverage', project.id] }) }
  const job = useJob(jobId, refresh)
  const running = !!jobId && (!job || job.status === 'pending' || job.status === 'running')

  const cov = useQuery({ queryKey: ['coverage', project.id], queryFn: () => api<Coverage>(`/projects/${project.id}/coverage`) })
  const list = useQuery({
    queryKey: ['scenarios', project.id, view, cluster, status],
    queryFn: () => {
      const q = new URLSearchParams()
      if (view === 'queue') q.set('review_queue', 'true')
      if (cluster) q.set('cluster', cluster)
      if (status) q.set('status', status)
      return api<Scenario[]>(`/projects/${project.id}/scenarios?${q}`)
    },
  })
  const items = view === 'queue' && cluster ? (list.data ?? []).filter((s) => s.cluster_id === cluster) : list.data ?? []
  const total = cov.data ? cov.data.counts.approved + cov.data.counts.unreviewed + cov.data.counts.rejected : 0
  const start = async (path: string, body?: object) => setJobId((await api<{ job_id: string }>(path, { method: 'POST', body: body && JSON.stringify(body) })).job_id)
  const patchOne = async (s: Scenario, body: object) => { await api(`/scenarios/${s.id}`, { method: 'PATCH', body: JSON.stringify(body) }); refresh() }

  return (
    <div>
      <PageHeader
        title="Scenarios"
        description="The agents plan test cases across categories, with no cost limit. You review the representative, uncertain and high-impact ones; rejecting a case can reject its whole cluster."
        actions={<><JobStatus job={job} />{total > 0 && <Button variant="secondary" disabled={running} onClick={() => start(`/projects/${project.id}/scenarios/generate`)}>Regenerate the plan</Button>}</>}
      />
      {job?.status === 'done' && job.result && <p className="-mt-5 mb-6 text-xs text-muted">Last generation: {Object.entries(job.result).map(([k, v]) => `${k.replaceAll('_', ' ')} ${v}`).join(', ')}.</p>}

      {cov.isLoading && <div className="space-y-2" aria-busy="true"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-8 w-1/2" /><Skeleton className="h-8 w-3/4" /></div>}
      {!cov.isLoading && total === 0 && !running && (
        <EmptyState icon={<TreeStructure size={28} />} title="No scenarios yet" action={<Button onClick={() => start(`/projects/${project.id}/scenarios/generate`)}>Generate scenario plan</Button>}>
          The plan covers happy paths, edge cases, adversarial inputs, one batch per prompt change, and guards for behaviour the old prompt gets right.
        </EmptyState>
      )}

      {cov.data && total > 0 && (
        <>
          <Section title="Coverage" description="Each cell is a cluster of similar scenarios and the number is its size. Select a cell to filter the list below.">
            <div className="mb-5 flex flex-wrap items-baseline gap-x-8 gap-y-1">
              <span className="tnum"><span className="text-muted">Approved</span> <b className="text-lg font-semibold">{cov.data.counts.approved}</b></span>
              <span className="tnum"><span className="text-muted">Unreviewed</span> <b className="text-lg font-semibold">{cov.data.counts.unreviewed}</b></span>
              <span className="tnum"><span className="text-muted">Rejected</span> <b className="text-lg font-semibold">{cov.data.counts.rejected}</b></span>
              {cluster && <Button variant="link" size="sm" onClick={() => setCluster('')}>Clear cluster filter</Button>}
            </div>
            <Heatmap cov={cov.data} active={cluster} onPick={setCluster} />
          </Section>

          <Section title="Add or extend">
            <div className="grid gap-8 lg:grid-cols-2">
              <div className="flex flex-wrap items-end gap-3">
                <Select label="Generate more in" value={more.category} onChange={(e) => setMore({ ...more, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c} value={c}>{c.replaceAll('_', ' ')}</option>)}</Select>
                <Input label="How many" type="number" min={1} className="w-24" value={more.n} onChange={(e) => setMore({ ...more, n: +e.target.value })} />
                <Button variant="secondary" disabled={running} onClick={() => start(`/projects/${project.id}/scenarios/generate`, more)}>Generate more</Button>
              </div>
              <form className="grid gap-3" onSubmit={async (e) => {
                e.preventDefault()
                await api(`/projects/${project.id}/scenarios`, { method: 'POST', body: JSON.stringify({ category: mine.category, input: { user_message: mine.text }, expected_behavior: mine.expected }) })
                setMine({ ...mine, text: '', expected: '' }); refresh()
              }}>
                <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
                  <Select label="Category" value={mine.category} onChange={(e) => setMine({ ...mine, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c} value={c}>{c.replaceAll('_', ' ')}</option>)}</Select>
                  <Input label="Add my own scenario" placeholder="What a real user would type" value={mine.text} onChange={(e) => setMine({ ...mine, text: e.target.value })} />
                </div>
                <Input label="Expected behaviour" placeholder="What a good reply does" value={mine.expected} onChange={(e) => setMine({ ...mine, expected: e.target.value })} />
                <div><Button type="submit" variant="secondary" disabled={!mine.text}>Add scenario</Button></div>
              </form>
            </div>
          </Section>

          <Section
            title={view === 'queue' ? 'Review queue' : 'All scenarios'}
            description={view === 'queue' ? 'Cluster representatives, cases the critic is unsure about, and the most change-relevant case per cluster. Unreviewed cases still run and are reported as unreviewed.' : undefined}
            actions={<div className="flex items-end gap-2">
              <div role="group" aria-label="List view" className="flex rounded-md border border-line-strong p-0.5">
                {(['queue', 'all'] as const).map((v) => <button key={v} aria-pressed={view === v} onClick={() => setView(v)} className={`h-7 rounded px-2.5 text-[13px] transition-colors duration-150 ${view === v ? 'bg-ink text-canvas' : 'text-muted hover:text-ink'}`}>{v === 'queue' ? 'Review queue' : 'All'}</button>)}
              </div>
              {view === 'all' && <Select label="Status" hideLabel value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option>{['proposed', 'approved', 'edited', 'rejected'].map((x) => <option key={x}>{x}</option>)}</Select>}
            </div>}
          >
            {list.isLoading && <div className="space-y-3" aria-busy="true"><Skeleton className="h-5 w-1/4" /><Skeleton className="h-12" /></div>}
            {!list.isLoading && items.length === 0 && <EmptyState title={view === 'queue' ? 'The review queue is clear' : 'Nothing matches this filter'}>{view === 'queue' ? 'Every representative and uncertain case has been reviewed. The rest still run.' : 'Change the filter or generate more scenarios.'}</EmptyState>}
            <ul className="divide-y divide-line border-y border-line">
              {items.slice(0, 100).map((s) => <CaseRow key={s.id + s.status + s.pinned + s.expected_behavior} s={s} onChange={refresh} onRejectDialog={(x) => { setReject(x); setWhy('') }} />)}
            </ul>
            {items.length > 100 && <p className="mt-3 text-[13px] text-muted">Showing 100 of {items.length}. Filter by cluster to see the rest.</p>}
          </Section>
        </>
      )}

      <Dialog open={!!reject} onOpenChange={(o) => !o && setReject(null)}>
        <DialogContent>
          <DialogTitle>Reject this scenario</DialogTitle>
          {reject && <p className="line-clamp-3 text-muted">{msg(reject)}</p>}
          <Textarea label="What is wrong?" help="Stored, and used to guide regeneration." rows={3} value={why} onChange={(e) => setWhy(e.target.value)} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="secondary" onClick={() => setReject(null)}>Cancel</Button>
            <Button variant="secondary" onClick={async () => { if (reject) { await patchOne(reject, { action: 'reject', comment: why }); setReject(null) } }}>Reject this case only</Button>
            {reject?.cluster_id && <Button variant="danger" onClick={async () => { const cid = reject.cluster_id!; setReject(null); await start(`/clusters/${cid}/reject`, { comment: why }) }}>Reject whole cluster and regenerate</Button>}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
