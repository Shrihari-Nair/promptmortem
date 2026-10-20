import { ListChecks } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge, statusTone } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { useAsk } from '../components/ui/ask'
import { Input, Select, Textarea } from '../components/ui/field'
import { EmptyState, PageHeader, Section, Skeleton } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import type { Calibration, Metric, Project } from '../types'

const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)
const isPair = (m: Metric) => m.kind === 'llm_judge' && m.definition.mode === 'pairwise'
const kindLabel = (m: Metric) => (m.kind === 'programmatic' ? 'Programmatic' : isPair(m) ? 'Regression judge (pairwise)' : 'LLM judge')

type Sample = { input: Record<string, unknown>; output: string; old_output?: string; result: Record<string, unknown> }

function MetricRow({ m, cal, onChange }: { m: Metric; cal?: Calibration; onChange: () => void }) {
  const { ask } = useAsk()
  const d = m.definition
  const [def, setDef] = useState(m.kind === 'programmatic' ? JSON.stringify(d, null, 1) : d.rubric)
  const [pass, setPass] = useState<string>((d.pass_examples ?? []).join('\n'))
  const [fail, setFail] = useState<string>((d.fail_examples ?? []).join('\n'))
  const [applies, setApplies] = useState(JSON.stringify(m.applies_to))
  const [weight, setWeight] = useState(m.weight)
  const [ens, setEns] = useState<boolean>(!!d.ensemble)
  const [ensModel, setEnsModel] = useState<string>(d.ensemble_model ?? '')
  const [err, setErr] = useState('')
  const [preview, setPreview] = useState<Sample[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [calJob, setCalJob] = useState<string | null>(null)
  const job = useJob(calJob, onChange)

  const patch = async (body: object) => {
    setErr('')
    try { await api(`/metrics/${m.id}`, { method: 'PATCH', body: JSON.stringify(body) }); onChange() } catch (e) { setErr(String(e)) }
  }
  const save = () => {
    try {
      const definition = m.kind === 'programmatic' ? JSON.parse(def) : { ...d, rubric: def, ...(isPair(m) ? {} : { pass_examples: lines(pass), fail_examples: lines(fail), ensemble: ens, ensemble_model: ensModel || null }) }
      patch({ definition, applies_to: JSON.parse(applies), weight })
    } catch { setErr('The definition and the applies-to filter must be valid JSON.') }
  }
  const run = async () => {
    setBusy(true); setErr('')
    try { setPreview(await api<Sample[]>(`/metrics/${m.id}/preview`, { method: 'POST', body: JSON.stringify({ n: 4 }) })) } catch (e) { setErr(String(e)) }
    setBusy(false)
  }
  const reject = async () => {
    const comment = await ask({ title: `Reject "${m.name}"?`, description: 'Your reason is fed to the metric designer so it does not propose this again.', label: 'Why reject it?', multiline: true, confirmLabel: 'Reject metric' })
    if (comment !== null) patch({ status: 'rejected', comment })
  }
  const byLabel = cal?.details?.by_label
  return (
    <li className={`py-6 ${m.status === 'rejected' ? 'opacity-55' : ''}`}>
      <div className="flex flex-wrap items-center gap-2.5">
        <h4 className="text-[15px] font-semibold">{m.name}</h4>
        <Badge tone={m.kind === 'programmatic' ? 'neutral' : 'accent'}>{kindLabel(m)}</Badge>
        <Badge tone={statusTone(m.status)}>{m.status}</Badge>
      </div>
      {m.notes && <p className="mt-1 max-w-[70ch] text-[13px] text-muted">{m.notes}</p>}
      <div className="mt-4 grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <Textarea mono={m.kind === 'programmatic'} label={m.kind === 'programmatic' ? 'Checks (declarative JSON, no code is executed)' : 'Rubric'} rows={m.kind === 'programmatic' ? 7 : 4} value={def} onChange={(e) => setDef(e.target.value)} />
        <div className="grid content-start gap-4">
          {m.kind === 'llm_judge' && !isPair(m) && (<>
            <Textarea label="Pass examples" help="One per line." rows={2} value={pass} onChange={(e) => setPass(e.target.value)} />
            <Textarea label="Fail examples" help="One per line." rows={2} value={fail} onChange={(e) => setFail(e.target.value)} />
          </>)}
          <div className="grid grid-cols-[1fr_6rem] gap-3">
            <Input label="Applies to" help="A JSON filter on categories or tags." value={applies} className="font-mono" onChange={(e) => setApplies(e.target.value)} />
            <Input label="Weight" type="number" step="0.5" min="0" value={weight} onChange={(e) => setWeight(+e.target.value)} />
          </div>
          {m.kind === 'llm_judge' && !isPair(m) && (
            <div className="space-y-2">
              <label className="flex items-start gap-2 text-[13px]"><input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={ens} onChange={(e) => setEns(e.target.checked)} />
                <span>Also ask a second judge<span className="block text-xs text-muted">A disagreement is logged; the first judge's verdict stands.</span></span></label>
              {ens && <Input label="Second judge model" help="Leave empty for the server default." value={ensModel} onChange={(e) => setEnsModel(e.target.value)} />}
            </div>
          )}
        </div>
      </div>
      {err && <p role="alert" className="mt-3 whitespace-pre-wrap text-[13px] text-bad">{err}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={() => patch({ status: 'approved' })}>Approve</Button>
        <Button size="sm" variant="secondary" onClick={save}>Save edits</Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={run}>{busy ? 'Running preview' : 'Preview on samples'}</Button>
        {m.kind === 'llm_judge' && !isPair(m) && <Button size="sm" variant="secondary" onClick={async () => setCalJob((await api<{ job_id: string }>(`/metrics/${m.id}/calibrate`, { method: 'POST' })).job_id)}>Calibrate on my labels</Button>}
        <Button size="sm" variant="ghost" onClick={reject}>Reject</Button>
        <JobStatus job={job} />
      </div>
      {cal && (
        <p className="mt-3 text-[13px] text-muted">
          Agreement with your labels: <b className="tnum text-ink">{Math.round((cal.agreement ?? 0) * 100)}%</b> on {cal.n_labeled} labelled examples
          {byLabel && <>. Passes it accepted: <span className="tnum">{byLabel.pass?.agree ?? 0}/{byLabel.pass?.n ?? 0}</span>; fails it caught: <span className="tnum">{byLabel.fail?.agree ?? 0}/{byLabel.fail?.n ?? 0}</span></>}.
        </p>
      )}
      {preview && (
        <div className="mt-4 overflow-x-auto rounded-md border border-line">
          <table className="w-full table-fixed text-left text-[13px]">
            <caption className="sr-only">Preview of this metric on sample outputs</caption>
            <thead className="bg-sunken text-xs text-muted"><tr><th className="w-1/4 p-2 font-medium">Input</th><th className="w-2/5 p-2 font-medium">Output</th><th className="p-2 font-medium">Result</th></tr></thead>
            <tbody className="divide-y divide-line align-top">{preview.map((p, i) => (
              <tr key={i}>
                <td className="p-2">{Object.values(p.input).join(' | ')}</td>
                <td className="p-2">{p.output}{p.old_output && <div className="mt-1.5 text-muted">Old output: {p.old_output}</div>}</td>
                <td className="p-2 font-mono text-xs">{JSON.stringify(p.result)}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </li>
  )
}

const TEMPLATES = {
  programmatic: '{\n "checks": [{"type": "max_words", "value": 120}],\n "aggregate": "all_pass"\n}',
  llm_judge: '{\n "mode": "pointwise",\n "rubric": "Pass only if the reply ... (explicit and checkable)",\n "pass_examples": ["a reply that passes"],\n "fail_examples": ["a reply that fails"],\n "output": "binary"\n}',
}

function AddMetric({ pid, onAdded }: { pid: string; onAdded: () => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'programmatic' | 'llm_judge'>('programmatic')
  const [def, setDef] = useState(TEMPLATES.programmatic)
  const [applies, setApplies] = useState('{"categories": ["*"]}')
  const [weight, setWeight] = useState(1)
  const [err, setErr] = useState('')
  const submit = async () => {
    setErr('')
    try {
      await api(`/projects/${pid}/metrics`, { method: 'POST', body: JSON.stringify({ name, kind, definition: JSON.parse(def), applies_to: JSON.parse(applies), weight }) })
      setName(''); onAdded()
    } catch (e) { setErr(e instanceof SyntaxError ? 'The definition and the applies-to filter must be valid JSON.' : String(e)) }
  }
  return (
    <Section title="Add my own metric" description="Check types: max_words, min_words, max_chars, regex_required, forbidden_regex, contains_all, contains_none, json_valid, json_schema, equals, starts_with, language_is. No code is ever executed.">
      <div className="grid max-w-[760px] gap-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_16rem_6rem]">
          <Input label="Metric name" placeholder="No internal policy IDs" value={name} onChange={(e) => setName(e.target.value)} />
          <Select label="Kind" value={kind} onChange={(e) => { const k = e.target.value as 'programmatic' | 'llm_judge'; setKind(k); setDef(TEMPLATES[k]) }}>
            <option value="programmatic">Programmatic (free)</option><option value="llm_judge">LLM judge (rubric)</option>
          </Select>
          <Input label="Weight" type="number" step="0.5" min="0" value={weight} onChange={(e) => setWeight(+e.target.value)} />
        </div>
        <Textarea mono label="Metric definition (JSON)" rows={7} value={def} onChange={(e) => setDef(e.target.value)} />
        <Input label="Applies to" help="A JSON filter on categories or tags." className="font-mono" value={applies} onChange={(e) => setApplies(e.target.value)} />
        {err && <p role="alert" className="whitespace-pre-wrap text-[13px] text-bad">{err}</p>}
        <div><Button disabled={!name} onClick={submit}>Add metric</Button></div>
      </div>
    </Section>
  )
}

export default function Metrics({ project }: { project: Project }) {
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const refresh = () => { qc.invalidateQueries({ queryKey: ['metrics', project.id] }); qc.invalidateQueries({ queryKey: ['cal', project.id] }) }
  const job = useJob(jobId, refresh)
  const running = !!jobId && (!job || job.status === 'pending' || job.status === 'running')
  const metrics = useQuery({ queryKey: ['metrics', project.id], queryFn: () => api<Metric[]>(`/projects/${project.id}/metrics`) })
  const cals = useQuery({ queryKey: ['cal', project.id], queryFn: () => api<Calibration[]>(`/projects/${project.id}/calibration`) })
  const list = metrics.data ?? []
  const design = async () => setJobId((await api<{ job_id: string }>(`/projects/${project.id}/metrics/generate`, { method: 'POST' })).job_id)
  return (
    <div className="max-w-[1000px]">
      <PageHeader
        title="Metrics"
        description="Programmatic checks run first and cost nothing. An LLM judge is used only where a rule cannot express the quality. Preview each metric on real outputs, then approve it. Regenerating replaces untouched proposals; approved, edited and rejected metrics are kept."
        actions={<><JobStatus job={job} />{list.length > 0 && <Button variant="secondary" disabled={running} onClick={design}>Regenerate proposed metrics</Button>}</>}
      />
      {metrics.isLoading && <div className="space-y-3" aria-busy="true"><Skeleton className="h-5 w-1/3" /><Skeleton className="h-24" /></div>}
      {!metrics.isLoading && list.length === 0 && (
        <EmptyState icon={<ListChecks size={28} />} title="No metrics yet" action={<Button onClick={design} disabled={running}>Design metrics</Button>}>
          The designer proposes 5 to 12 metrics from your scenarios, prefers free programmatic checks, and always adds a regression judge that compares old and new outputs.
        </EmptyState>
      )}
      {list.length > 0 && <ul className="divide-y divide-line border-y border-line">{list.map((m) => <MetricRow key={m.id + m.status + m.weight + JSON.stringify(m.definition)} m={m} cal={cals.data?.find((c) => c.metric_id === m.id)} onChange={refresh} />)}</ul>}
      <AddMetric pid={project.id} onAdded={refresh} />
    </div>
  )
}
