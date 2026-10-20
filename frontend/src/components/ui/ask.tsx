import * as React from 'react'
import { Button } from './button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './dialog'
import { Input, Textarea } from './field'

type AskOpts = { title: string; description?: string; label: string; placeholder?: string; confirmLabel?: string; multiline?: boolean; required?: boolean; secret?: boolean; initial?: string }
type ConfirmOpts = { title: string; description?: string; confirmLabel?: string; danger?: boolean }
type State = ({ kind: 'ask' } & AskOpts | { kind: 'confirm' } & ConfirmOpts) & { resolve: (v: string | boolean | null) => void }

const Ctx = React.createContext<{ ask: (o: AskOpts) => Promise<string | null>; confirm: (o: ConfirmOpts) => Promise<boolean> } | null>(null)
let tokenRequester: (() => Promise<string | null>) | null = null
/** For non-React code (the API client): ask the user for the API token in the app's own dialog. */
export const requestToken = () => (tokenRequester ? tokenRequester() : Promise.resolve(null))

export function AskProvider({ children }: { children: React.ReactNode }) {
  const [s, setS] = React.useState<State | null>(null)
  const [value, setValue] = React.useState('')
  const api = React.useMemo(() => ({
    ask: (o: AskOpts) => new Promise<string | null>((resolve) => { setValue(o.initial ?? ''); setS({ kind: 'ask', ...o, resolve: resolve as State['resolve'] }) }),
    confirm: (o: ConfirmOpts) => new Promise<boolean>((resolve) => setS({ kind: 'confirm', ...o, resolve: resolve as State['resolve'] })),
  }), [])
  React.useEffect(() => {
    tokenRequester = () => api.ask({ title: 'API token required', description: 'This server was started with PV_API_TOKEN. Enter it once; it is remembered in this browser.', label: 'API token', secret: true, required: true, confirmLabel: 'Continue' })
    return () => { tokenRequester = null }
  }, [api])
  const close = (v: string | boolean | null) => { s?.resolve(v); setS(null) }
  const ready = s?.kind === 'ask' ? !s.required || value.trim().length > 0 : true
  return (
    <Ctx.Provider value={api}>
      {children}
      <Dialog open={!!s} onOpenChange={(o) => !o && close(s?.kind === 'ask' ? null : false)}>
        <DialogContent>
          {s && (
            <form onSubmit={(e) => { e.preventDefault(); if (ready) close(s.kind === 'ask' ? value : true) }} className="space-y-4">
              <DialogTitle>{s.title}</DialogTitle>
              <DialogDescription className="text-[13px] text-muted">{s.description ?? ''}</DialogDescription>
              {s.kind === 'ask' && (s.multiline
                ? <Textarea label={s.label} placeholder={s.placeholder} rows={4} value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
                : <Input label={s.label} placeholder={s.placeholder} type={s.secret ? 'password' : 'text'} value={value} onChange={(e) => setValue(e.target.value)} autoFocus />)}
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => close(s.kind === 'ask' ? null : false)}>Cancel</Button>
                <Button type="submit" variant={s.kind === 'confirm' && s.danger ? 'danger' : 'primary'} disabled={!ready}>{s.confirmLabel ?? 'Confirm'}</Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </Ctx.Provider>
  )
}
export function useAsk() {
  const c = React.useContext(Ctx)
  if (!c) throw new Error('useAsk needs <AskProvider>')
  return c
}
