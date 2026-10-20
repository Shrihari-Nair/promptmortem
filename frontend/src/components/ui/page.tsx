import * as React from 'react'
import { cn } from '../../lib/utils'

export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-1.5 max-w-[65ch] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

/** A titled block separated from the previous one by a hairline and space, not a box. */
export function Section({ title, description, actions, children, className }: { title?: string; description?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('mt-9 border-t border-line pt-6 first:mt-0 first:border-t-0 first:pt-0', className)}>
      {(title || actions) && (
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
          <div>
            {title && <h3 className="text-base font-semibold">{title}</h3>}
            {description && <p className="mt-1 max-w-[65ch] text-[13px] text-muted">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

export function Notice({ tone = 'warn', children, className, role }: { tone?: 'warn' | 'bad' | 'good' | 'accent'; children: React.ReactNode; className?: string; role?: string }) {
  const t = { warn: 'bg-warn-soft text-warn', bad: 'bg-bad-soft text-bad', good: 'bg-good-soft text-good', accent: 'bg-accent-soft text-accent' }[tone]
  return <div role={role ?? (tone === 'bad' ? 'alert' : 'status')} className={cn('rounded-md px-3 py-2 text-[13px] leading-relaxed', t, className)}>{children}</div>
}

export function EmptyState({ icon, title, children, action }: { icon?: React.ReactNode; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="rise flex flex-col items-start gap-3 rounded-lg border border-dashed border-line-strong px-6 py-8">
      {icon && <div className="text-muted">{icon}</div>}
      <div>
        <p className="font-medium">{title}</p>
        {children && <p className="mt-1 max-w-[60ch] text-[13px] text-muted">{children}</p>}
      </div>
      {action}
    </div>
  )
}

export const Skeleton = ({ className }: { className?: string }) => <div aria-hidden className={cn('skeleton h-4 w-full', className)} />

export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  const tone = pct >= 100 ? 'bg-bad' : pct >= 85 ? 'bg-warn' : 'bg-ink'
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(value, max)} className="h-2 overflow-hidden rounded-md bg-sunken">
      <div className={cn('h-full transition-[width] duration-300', tone)} style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Term / value rows separated by hairlines. */
export function DefList({ children, className }: { children: React.ReactNode; className?: string }) {
  return <dl className={cn('divide-y divide-line text-[13px]', className)}>{children}</dl>
}
export const DefRow = ({ term, children }: { term: string; children: React.ReactNode }) => (
  <div className="grid gap-x-6 gap-y-0.5 py-2.5 sm:grid-cols-[11rem_1fr]">
    <dt className="text-muted">{term}</dt>
    <dd className="tnum">{children}</dd>
  </div>
)
