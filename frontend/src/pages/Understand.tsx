import { FileMagnifyingGlass, Plus, X } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Input, Select, Textarea } from '../components/ui/field'
import { EmptyState, PageHeader, Section, Skeleton } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import type { Analysis, Change, Context, Project } from '../types'

const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)

function ListField({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  return <Textarea label={label} help="One per line." rows={Math.max(2, Math.min(value.length + 1, 6))} defaultValue={value.join('\n')} onBlur={(e) => onChange(lines(e.target.value))} />
}

export default function Understand({ project }: { project: Project }) {
  const qc = useQueryClient()
  const key = ['analysis', project.id]
  const { data, isLoading } = useQuery({ queryKey: key, queryFn: () => api<Analysis>(`/projects/${project.id}/analysis`) })
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: key }))
  const running = !!jobId && (!job || job.status === 'pending' || job.status === 'running')
  const analyze = async () => setJobId((await api<{ job_id: string }>(`/projects/${project.id}/analyze`, { method: 'POST' })).job_id)

  const save = async (kind: 'context' | 'diff', body: unknown) => {
    await api(`/projects/${project.id}/analysis/${kind}`, { method: 'PUT', body: JSON.stringify(body) })
    qc.invalidateQueries({ queryKey: key })
  }
  const ctx = data?.context?.data
  const changes = data?.diff?.data.changes ?? []
  const setChange = (i: number, patch: Partial<Change>) => save('diff', { changes: changes.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

  return (
    <div className="max-w-[1000px]">
      <PageHeader
        title="What the tool understood"
        description="Check what the agents think your app does and which behaviours your change could affect. Fix anything that is wrong; your edits are used by every later step."
        actions={<>
          <JobStatus job={job} />
          {ctx && <Button variant="secondary" disabled={running} onClick={analyze}>Analyze again (replaces your edits)</Button>}
        </>}
      />

      {isLoading && <div className="space-y-3" aria-busy="true"><Skeleton className="h-5 w-1/3" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
      {!isLoading && !ctx && !running && (
        <EmptyState icon={<FileMagnifyingGlass size={28} />} title="Nothing analysed yet" action={<Button onClick={analyze}>Analyze prompts</Button>}>
          One model call reads your description and both prompts, then lists every change, including implicit ones like reordered instructions, with the behaviours each could affect.
        </EmptyState>
      )}
      {running && !ctx && <div className="space-y-3" aria-busy="true"><Skeleton className="h-5 w-1/3" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}

      {ctx && (
        <Section title="Your app, as understood" actions={data?.context?.edited ? <Badge tone="accent">edited by you</Badge> : undefined}>
          <div className="grid gap-5">
            <Textarea label="Task" rows={2} defaultValue={ctx.task} onBlur={(e) => e.target.value !== ctx.task && save('context', { ...ctx, task: e.target.value })} />
            <Input label="Output format" defaultValue={ctx.output_format} onBlur={(e) => e.target.value !== ctx.output_format && save('context', { ...ctx, output_format: e.target.value })} />
            <div className="grid gap-5 md:grid-cols-2">
              {([['user_types', 'Who uses it'], ['input_types', 'What inputs look like'], ['risk_areas', 'Risks'], ['success_criteria', 'What good looks like'], ['assumptions', 'Assumptions to check']] as [keyof Context, string][]).map(([k, label]) => (
                <ListField key={k} label={label} value={ctx[k] as string[]} onChange={(v) => save('context', { ...ctx, [k]: v })} />
              ))}
            </div>
          </div>
        </Section>
      )}

      {data?.diff && (
        <Section title="What changed and what it could affect" description="Each change drives which scenarios get priority. Add what the agent missed, remove what it got wrong." actions={data.diff.edited ? <Badge tone="accent">edited by you</Badge> : undefined}>
          {changes.length === 0 && <p className="text-muted">No differences found between the prompts.</p>}
          <ol className="divide-y divide-line border-y border-line">
            {changes.map((c, i) => (
              <li key={c.id} className="space-y-4 py-5">
                <div className="flex flex-wrap items-center gap-3">
                  <Badge className="font-mono">{c.id}</Badge>
                  <span className="font-medium">{c.type.replaceAll('_', ' ')}</span>
                  <div className="ml-auto flex items-end gap-2">
                    <Select label="Risk" hideLabel value={c.risk} onChange={(e) => setChange(i, { risk: e.target.value as Change['risk'] })}>
                      {['low', 'medium', 'high'].map((r) => <option key={r}>{r}</option>)}
                    </Select>
                    <Button variant="ghost" size="sm" aria-label={`Remove ${c.id}`} onClick={() => save('diff', { changes: changes.filter((_, j) => j !== i) })}><X size={14} />Remove</Button>
                  </div>
                </div>
                <div className="grid gap-3 font-mono text-[12px] leading-5 md:grid-cols-2">
                  <div className="rounded-xl bg-bad-soft p-2.5 text-bad">{c.old_text ?? <em>nothing before</em>}</div>
                  <div className="rounded-xl bg-good-soft p-2.5 text-good">{c.new_text ?? <em>removed</em>}</div>
                </div>
                <Input label="Intent" defaultValue={c.intent} onBlur={(e) => e.target.value !== c.intent && setChange(i, { intent: e.target.value })} />
                <ListField label="Could affect" value={c.could_affect} onChange={(v) => setChange(i, { could_affect: v })} />
              </li>
            ))}
          </ol>
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => save('diff', { changes: [...changes, { id: `chg_${changes.length + 1}`, type: 'other', old_text: null, new_text: null, intent: '', could_affect: [], risk: 'low' }] })}>
            <Plus size={14} />Add a change the agent missed
          </Button>
        </Section>
      )}
    </div>
  )
}
