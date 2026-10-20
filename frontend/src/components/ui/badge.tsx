import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'
import { cn } from '../../lib/utils'

const badgeVariants = cva('inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium leading-5', {
  variants: {
    tone: {
      neutral: 'bg-sunken text-ink',
      accent: 'bg-accent-soft text-accent',
      good: 'bg-good-soft text-good',
      warn: 'bg-warn-soft text-warn',
      bad: 'bg-bad-soft text-bad',
      solidBad: 'bg-bad text-canvas',
      solidGood: 'bg-good text-canvas',
      solidWarn: 'bg-warn text-canvas',
      solidNeutral: 'bg-muted text-canvas',
    },
  },
  defaultVariants: { tone: 'neutral' },
})

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>['tone']>
export const Badge = ({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) => (
  <span className={cn(badgeVariants({ tone }), className)} {...props} />
)
/** Report recommendation level -> the one place its colour is decided */
export const levelTone = (level: string): BadgeTone => (({ block: 'solidBad', caution: 'solidWarn', ok: 'solidGood' } as Record<string, BadgeTone>)[level] ?? 'solidNeutral')
export const statusTone = (s: string): BadgeTone => (({ done: 'good', accepted: 'good', approved: 'good', edited: 'accent', running: 'accent', pending: 'accent', paused: 'warn', failed: 'bad', rejected: 'bad', stopped: 'neutral' } as Record<string, BadgeTone>)[s] ?? 'neutral')
