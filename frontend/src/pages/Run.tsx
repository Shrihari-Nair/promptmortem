import { Play, Stop } from '@phosphor-icons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, withToken } from '../api'
import { Badge, statusTone } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../components/ui/dialog'
import { Input } from '../components/ui/field'
import { EmptyState, Meter, Notice, PageHeader, Section } from '../components/ui/page'
import { JobStatus, useJob } from '../hooks'
import type { Metric, Project, RunEvent, RunState } from '../types'

const usd = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`
export const runKey = (pid: string) => `pv_run_${pid}`

export default function Run({ project, goReport }: { project: Project; goReport: () => void }) {
  const qc = useQueryClient()
  const [runId, setRunId] = useState<string | null>(() => localStorage.getItem(runKey(project.id)))
  const [jobId, setJobId] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [extra, setExtra] = useState('')
  const plan = useQuery({ queryKey: ['plan', project.id], queryFn: () => api<{ stage: string; chosen: { tier: string; cap: number; expected: number } | null }>(`/projects/${project.id}/plan`) })
  const metrics = useQuery({ queryKey: ['metrics', project.id], queryFn: () => api<Metric[]>(`/projects/${project.id}/metrics`) })
  const run = useQuery({
    queryKey: ['run', runId], enabled: !!runId, queryFn: () => api<RunState>(`/runs/${runId}`),
    refetchInterval: (q) => (['pending', 'running'].includes(q.state.data?.status ?? '') || jobId ? 1500 : false),
  })
  const job = useJob(jobId, () => qc.invalidateQueries({ queryKey: ['run', runId] }))
  const st = run.data
  const live = !!st && ['pending', 'running'].includes(st.status)

  // live events (SSE) while a run executes; the polled state above is the source of truth after a reload
  const es = useRef<EventSource | null>(null)
  useEffect(() => {
    if (!runId || !live) return
    es.current?.close()
    setEvents([])
    const src = new EventSource(withToken(`/api/runs/${runId}/events`))
    src.onmessage = (m) => {
      const ev: RunEvent = JSON.parse(m.data)
      setEvents((e) => [...e.slice(-300), ev])
      if (['done', 'paused', 'stopped', 'failed'].includes(ev.type)) { src.close(); qc.invalidateQueries({ queryKey: ['run', runId] }) }
    }
    src.onerror = () => src.close()
    es.current = src
    return () => src.close()
  }, [runId, live, st?.status === 'running'])

  const start = async () => {
    setErr('')
    try {
      const r = await api<{ run_id: string; job_id: string }>(`/projects/${project.id}/runs`, { method: 'POST' })
      localStorage.setItem(runKey(project.id), r.run_id); setRunId(r.run_id); setJobId(r.job_id)
    } catch (e) { setErr(String(e)) }
  }
  const act = async (path: string, body?: object) => {
    setErr('')
    try {
      const r = await api<{ job_id?: string }>(`/runs/${runId}/${path}`, { method: 'POST', body: body && JSON.stringify(body) })
      if (r.job_id) setJobId(r.job_id)
      qc.invalidateQueries({ queryKey: ['run', runId] })
    } catch (e) { setErr(String(e)) }
  }

  const last = (t: string) => [...events].reverse().find((e) => e.type === t)
  const prog = last('progress'), noise = last('noise')
  const regs = events.filter((e) => e.type === 'regression')
  const warns = [...new Set([...(st?.warnings ?? []), ...events.filter((e) => e.type === 'warning').map((e) => e.message as string)])]
  const done = prog?.done ?? 0, total = prog?.total ?? st?.total ?? 0
  const names = Object.fromEntries((metrics.data ?? []).map((m) => [m.id, m.name]))
  const band = (noise?.band ?? st?.noise?.band ?? {}) as Record<string, number>
  const finished = !st || ['done', 'stopped', 'failed'].includes(st.status)

  return (
    <div className="max-w-[1000px]">
      <PageHeader
        title="Run"
        description="A smoke test first, then your chosen tier, then extra cases near any regression. Spending is capped: the run pauses and asks when it reaches the cap."
        actions={<>
          <JobStatus job={job?.status === 'failed' ? job : undefined} />
          {live && <Button variant="secondary" onClick={() => act('stop')}><Stop size={15} weight="fill" aria-hidden />Stop and report partial</Button>}
          {finished && <Button disabled={!plan.data?.chosen} onClick={start}><Play size={15} weight="fill" aria-hidden />{st ? 'Start a new run' : 'Start run'}</Button>}
        </>}
      />
      {err && <Notice tone="bad" className="mb-6 whitespace-pre-wrap">{err}</Notice>}
      {!plan.isLoading && !plan.data?.chosen && <EmptyState title="Confirm a budget first">A run needs a tier and a hard cap. Choose them in the Budget step.</EmptyState>}
      {plan.data?.chosen && !st && <p className="text-[13px] text-muted">Plan: the <b className="text-ink">{plan.data.chosen.tier}</b> tier, expected <span className="tnum">{usd(plan.data.chosen.expected)}</span>, hard cap <span className="tnum">{usd(plan.data.chosen.cap)}</span>.</p>}

      {st && (
        <>
          <Section title="Progress">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px]">
              <Badge tone={statusTone(st.status)}>{st.status}</Badge>
              <span><span className="text-muted">Stage</span> <b>{st.stage}</b></span>
              <span className="tnum"><span className="text-muted">Cases</span> <b>{done}</b> of {total}</span>
            </div>
            <div className="mt-4 space-y-1.5">
              <div className="tnum flex justify-between text-[13px]"><span>Spent {usd(st.spent)}</span><span className="text-muted">Hard cap {usd(st.cap)}</span></div>
              <Meter value={st.spent} max={st.cap} label="Spend against the hard cap" />
            </div>
            {st.status === 'failed' && <Notice tone="bad" className="mt-4">The run failed: {st.error}</Notice>}
          </Section>

          {warns.map((w, i) => <Notice key={i} tone="warn" className="mt-4">{w}</Notice>)}

          {Object.keys(band).length > 0 && (
            <Section title="Old versus old noise" description="Differences smaller than a metric's band are not counted as fixed or broken.">
              <ul className="divide-y divide-line border-y border-line text-[13px]">
                {Object.entries(band).map(([m, b]) => <li key={m} className="flex justify-between gap-4 py-2"><span>{names[m] ?? m}</span><span className="tnum text-muted">plus or minus {(+b).toFixed(2)}</span></li>)}
              </ul>
            </Section>
          )}

          {regs.length > 0 && (
            <Section title={`Regressions found so far (${regs.length})`}>
              <ul className="divide-y divide-line border-y border-line text-[13px]">
                {regs.slice(-8).map((r, i) => <li key={i} className="tnum flex gap-3 py-2"><Badge tone="bad">severity {r.severity}</Badge><span>{names[r.metric_id] ?? 'metric'}: {(+r.old).toFixed(2)} to {(+r.new).toFixed(2)}</span></li>)}
              </ul>
            </Section>
          )}
          {['done', 'stopped'].includes(st.status) && <div className="mt-8"><Button onClick={goReport}>View the report</Button></div>}
        </>
      )}

      <Dialog open={st?.status === 'paused'}>
        <DialogContent onInteractOutside={(e) => e.preventDefault()} onEscapeKeyDown={(e) => e.preventDefault()}>
          {st && (<>
            <DialogTitle>{st.pause_reason === 'cap' ? 'Hard cap reached' : st.pause_reason === 'deep_dive_needs_budget' ? 'The deep dive would exceed the cap' : 'Run paused'}</DialogTitle>
            <DialogDescription asChild>
              <p className="text-muted">
                Spent {usd(st.spent)} of {usd(st.cap)}. {st.pause_reason === 'cap' && 'Nothing was dropped silently: the run stopped here and is waiting for you.'}
                {st.pause_reason === 'deep_dive_needs_budget' && 'The main run is complete. Extra scenarios near the regressions would cost more than the cap allows.'}
                {st.pause_reason.startsWith('error') && <span className="text-bad"> {st.pause_reason}</span>}
                {st.pause_reason.startsWith('interrupted') && ' The server restarted; nothing was lost.'}
              </p>
            </DialogDescription>
            {(st.pause_reason === 'cap' || st.pause_reason === 'deep_dive_needs_budget') && <Input label="Extend by (USD)" type="number" step="0.01" min="0" value={extra} onChange={(e) => setExtra(e.target.value)} />}
            <div className="flex flex-wrap justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => act('stop')}>Stop and report partial</Button>
              {(st.pause_reason === 'cap' || st.pause_reason === 'deep_dive_needs_budget')
                ? <Button disabled={!(+extra > 0)} onClick={() => act('extend', { amount: +extra })}>Extend and continue</Button>
                : <Button onClick={() => act('resume')}>Resume</Button>}
            </div>
          </>)}
        </DialogContent>
      </Dialog>
    </div>
  )
}
