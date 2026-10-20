import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api } from './api'
import { cn } from './lib/utils'
import type { Job } from './types'

const FINAL = ['done', 'failed', 'paused']

/** Polls a job until it finishes; calls onFinish once. */
export function useJob(jobId: string | null, onFinish?: (j: Job) => void) {
  const q = useQuery({
    queryKey: ['job', jobId],
    enabled: !!jobId,
    queryFn: () => api<Job>(`/jobs/${jobId}`),
    refetchInterval: (query) => (FINAL.includes(query.state.data?.status ?? '') ? false : 700),
  })
  const fired = useRef<string | null>(null)
  useEffect(() => {
    if (q.data && FINAL.includes(q.data.status) && fired.current !== jobId) {
      fired.current = jobId
      onFinish?.(q.data)
    }
  })
  return q.data
}

/** Live status of a background job (announced politely to screen readers). */
export function JobStatus({ job, className }: { job?: Job; className?: string }) {
  if (!job) return null
  const tone = job.status === 'failed' ? 'text-bad' : job.status === 'done' ? 'text-good' : 'text-muted'
  return (
    <p aria-live="polite" className={cn('text-[13px]', tone, className)}>
      {job.status === 'running' || job.status === 'pending' ? (job.progress || 'Working') : job.status === 'done' ? 'Done' : job.status === 'paused' ? 'Paused' : 'Failed'}
      {job.error ? `: ${job.error}` : ''}
    </p>
  )
}
