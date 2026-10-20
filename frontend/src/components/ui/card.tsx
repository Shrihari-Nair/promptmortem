import * as React from 'react'
import { cn } from '../../lib/utils'

/** A bordered panel. Use it only where grouping needs to read as one object; otherwise separate with spacing and dividers. */
export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLElement>) => <section className={cn('rounded-lg border border-line bg-surface p-5', className)} {...p} />
export const CardTitle = ({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => <h3 className={cn('mb-3 text-[15px] font-semibold', className)} {...p} />
