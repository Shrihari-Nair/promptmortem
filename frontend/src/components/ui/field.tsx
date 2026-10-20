import * as React from 'react'
import { cn } from '../../lib/utils'

export const controlClass =
  'w-full rounded-xl border border-line-strong bg-canvas/60 px-3.5 text-sm text-ink placeholder:text-muted/60 transition-[border-color,box-shadow] duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] hover:border-muted focus-visible:border-accent focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-accent/20 disabled:opacity-50 aria-[invalid=true]:border-bad'

type Common = { label: string; help?: string; error?: string; className?: string; hideLabel?: boolean }

function Wrap({ id, label, help, error, hideLabel, className, children }: Common & { id: string; children: React.ReactNode }) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={id} className={cn('block text-[13px] font-medium', hideLabel && 'sr-only')}>{label}</label>
      {children}
      {help && !error && <p id={`${id}-help`} className="text-xs text-muted">{help}</p>}
      {error && <p id={`${id}-err`} role="alert" className="text-xs text-bad">{error}</p>}
    </div>
  )
}
const describe = (id: string, help?: string, error?: string) => (error ? `${id}-err` : help ? `${id}-help` : undefined)

export const Input = React.forwardRef<HTMLInputElement, Common & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'className'>>(({ label, help, error, className, hideLabel, ...p }, ref) => {
  const id = React.useId()
  return <Wrap {...{ id, label, help, error, className, hideLabel }}><input ref={ref} id={id} aria-invalid={!!error} aria-describedby={describe(id, help, error)} className={cn(controlClass, 'h-10')} {...p} /></Wrap>
})
Input.displayName = 'Input'

export const Textarea = React.forwardRef<HTMLTextAreaElement, Common & Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'className'> & { mono?: boolean }>(({ label, help, error, className, hideLabel, mono, ...p }, ref) => {
  const id = React.useId()
  return <Wrap {...{ id, label, help, error, className, hideLabel }}><textarea ref={ref} id={id} aria-invalid={!!error} aria-describedby={describe(id, help, error)} className={cn(controlClass, 'py-2 leading-relaxed', mono && 'font-mono text-[13px]')} {...p} /></Wrap>
})
Textarea.displayName = 'Textarea'

export const Select = React.forwardRef<HTMLSelectElement, Common & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'className'>>(({ label, help, error, className, hideLabel, children, ...p }, ref) => {
  const id = React.useId()
  return <Wrap {...{ id, label, help, error, className, hideLabel }}><select ref={ref} id={id} aria-invalid={!!error} aria-describedby={describe(id, help, error)} className={cn(controlClass, 'h-10 pr-8')} {...p}>{children}</select></Wrap>
})
Select.displayName = 'Select'
