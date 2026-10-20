import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api'
import { Button } from '../components/ui/button'
import { Input, Select, Textarea } from '../components/ui/field'
import { Notice, PageHeader, Section } from '../components/ui/page'
import { columnsOf, lineDiff, templateVars } from '../lib/setup'
import type { Health, Project } from '../types'

const TYPES = ['string', 'string?', 'number', 'json']

type Upload = { name: string; text: string; format: 'csv' | 'jsonl'; cols: string[]; map: Record<string, string>; expected: string }

function Uploader({ id, title, hint, vars, value, onChange, accept }: { id: string; title: string; hint: string; vars: string[]; value: Upload | null; onChange: (u: Upload | null) => void; accept: string }) {
  const pick = async (file?: File) => {
    if (!file) return onChange(null)
    const text = await file.text()
    const format = file.name.endsWith('.csv') ? 'csv' : 'jsonl'
    const cols = columnsOf(text, format)
    const map = Object.fromEntries(vars.map((v) => [v, cols.includes(v) ? v : cols.includes('input') && vars.indexOf(v) === 0 ? 'input' : '']))
    onChange({ name: file.name, text, format, cols, map, expected: cols.includes('expected') ? 'expected' : '' })
  }
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-[13px] font-medium">{title}</label>
      <input id={id} type="file" accept={accept} onChange={(e) => pick(e.target.files?.[0])} className="block w-full text-[13px] file:mr-3 file:h-8 file:cursor-pointer file:rounded-md file:border file:border-line-strong file:bg-surface file:px-3 file:text-[13px] file:text-ink hover:file:bg-sunken" />
      <p className="text-xs text-muted">{hint}</p>
      {value && (
        <div className="space-y-2 rounded-md bg-sunken p-3">
          <p className="text-xs text-muted">{value.name} has the columns: {value.cols.join(', ') || 'none found'}</p>
          <div className="flex flex-wrap gap-4">
            {vars.map((v) => (
              <Select key={v} label={`Use for {{${v}}}`} value={value.map[v] ?? ''} onChange={(e) => onChange({ ...value, map: { ...value.map, [v]: e.target.value } })}>
                <option value="">(none)</option>{value.cols.map((c) => <option key={c}>{c}</option>)}
              </Select>
            ))}
            <Select label="Use as expected" value={value.expected} onChange={(e) => onChange({ ...value, expected: e.target.value })}>
              <option value="">(none)</option>{value.cols.map((c) => <option key={c}>{c}</option>)}
            </Select>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Setup({ onCreated }: { onCreated: (p: Project) => void }) {
  const [f, setF] = useState({ name: '', app_description: '', constraints: '', known_failure_notes: '', target_model: '', judge_model_default: '' })
  const [oldP, setOldP] = useState('')
  const [newP, setNewP] = useState('')
  const [change, setChange] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [draftNotes, setDraftNotes] = useState<string[]>([])
  const [types, setTypes] = useState<Record<string, string>>({})
  const [dataset, setDataset] = useState<Upload | null>(null)
  const [traces, setTraces] = useState<Upload | null>(null)
  const [promptfoo, setPromptfoo] = useState(true)
  const [draftErr, setDraftErr] = useState('')
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health') })
  const vars = useMemo(() => templateVars(oldP, newP), [oldP, newP])
  const schema = Object.fromEntries(vars.map((v) => [v, types[v] ?? 'string']))

  const draft = async () => {
    setDrafting(true); setDraftErr('')
    try {
      const r = await api<{ prompt: string; notes: string[] }>('/prompts/draft', { method: 'POST', body: JSON.stringify({ old_prompt: oldP, change_request: change, app_description: f.app_description, constraints: f.constraints, model: f.judge_model_default || undefined }) })
      setNewP(r.prompt); setDraftNotes(r.notes)
    } catch (e) { setDraftErr(String(e)) }
    setDrafting(false)
  }

  const importFile = (pid: string, path: string, u: Upload) => {
    const input = Object.fromEntries(Object.entries(u.map).filter(([, c]) => c))
    // the default JSONL shape ({"input": ..., "expected": ...}) needs no mapping; anything else is mapped column by column
    const defaultShape = u.format === 'jsonl' && Object.keys(input).length === 1 && input[vars[0]] === 'input' && (u.expected === '' || u.expected === 'expected')
    const mapping = defaultShape ? undefined : { input, expected: u.expected || null }
    return api(`/projects/${pid}/${path}/import`, { method: 'POST', body: JSON.stringify({ format: u.format, content: u.text, mapping, input_var: vars[0] ?? 'user_message' }) })
  }

  const create = useMutation({
    mutationFn: async () => {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v))
      const p = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ ...body, promptfoo_enabled: promptfoo }) })
      for (const [label, text] of [['old', oldP], ['new', newP]])
        await api(`/projects/${p.id}/prompts`, { method: 'POST', body: JSON.stringify({ label, text, variables_schema: schema }) })
      if (dataset) await importFile(p.id, 'dataset', dataset)
      if (traces) await importFile(p.id, 'traces', traces)
      return p
    },
    onSuccess: onCreated,
  })

  const keyOk = health.data && Object.values(health.data.keys).some(Boolean)
  const diff = lineDiff(oldP.split('\n'), newP.split('\n'))
  return (
    <div className="max-w-[880px]">
      <PageHeader title="Set up the comparison" description="Describe your app and paste the prompt before and after your change. Nothing is sent to a model until you start analysing." />
      {health.data && (
        <Notice tone={keyOk ? 'good' : 'warn'} className="mb-8">
          Provider keys on the server: {Object.entries(health.data.keys).map(([k, v]) => `${k} ${v ? 'found' : 'missing'}`).join(', ')}. Default models: {health.data.models.target} (target) and {health.data.models.judge} (judge).
          {!keyOk && ' No key is configured. Put GEMINI_API_KEY in a .env file and restart the server, or use fake/* models to try the tool without spending.'}
        </Notice>
      )}

      <Section title="Your app">
        <div className="grid gap-5">
          <Input label="Project name" placeholder="Billing support bot" value={f.name} onChange={set('name')} />
          <Textarea label="What the app does" help="Who uses it and what a good answer looks like." rows={3} placeholder="Answers customer questions about invoices, refunds and plan changes. Replies stay under 120 words." value={f.app_description} onChange={set('app_description')} />
          <Textarea label="Non-negotiable constraints" help="Rules a reply must never break. Metrics and the regression judge are built around these." rows={2} placeholder="Never promise a refund outside policy. Never reveal internal policy IDs." value={f.constraints} onChange={set('constraints')} />
          <Textarea label="What is wrong with the current prompt" help="Optional. Tells the analysis what you were trying to fix." rows={2} placeholder="Too verbose, and it sometimes over-promises refunds." value={f.known_failure_notes} onChange={set('known_failure_notes')} />
        </div>
      </Section>

      <Section title="Models" description="LiteLLM model names. The target model is the one your app calls; the judge model also runs the planning agents.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Input label="Target model" help="Leave empty for the server default." placeholder="gemini/gemini-2.5-flash" value={f.target_model} onChange={set('target_model')} />
          <Input label="Judge and agent model" help="Leave empty for the server default." placeholder="gemini/gemini-2.5-flash" value={f.judge_model_default} onChange={set('judge_model_default')} />
        </div>
      </Section>

      <Section title="Prompts" description="Use {{placeholders}} for the inputs, for example {{user_message}}.">
        <div className="grid gap-5 md:grid-cols-2">
          <Textarea mono label="Old prompt" rows={12} value={oldP} onChange={(e) => setOldP(e.target.value)} />
          <Textarea mono label="New prompt" rows={12} value={newP} onChange={(e) => setNewP(e.target.value)} />
        </div>
        <div className="mt-5 space-y-3 rounded-lg bg-sunken p-4">
          <Textarea label="Or describe the change and let the tool draft the new prompt" help="The draft fills the new prompt above. Review and edit it; nothing is saved until you continue." rows={2} placeholder="Answer in under 120 words and never promise refunds." value={change} onChange={(e) => setChange(e.target.value)} />
          <Button variant="secondary" size="sm" disabled={!oldP.trim() || !change.trim() || drafting} onClick={draft}>{drafting ? 'Drafting' : 'Draft new prompt'}</Button>
          {draftNotes.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-[13px] text-muted">{draftNotes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          {draftErr && <p role="alert" className="text-[13px] text-bad">{draftErr}</p>}
        </div>
        {(oldP || newP) && (
          <figure className="mt-5">
            <figcaption className="mb-1.5 text-[13px] font-medium">Difference</figcaption>
            <pre className="max-h-52 overflow-auto rounded-md border border-line bg-surface p-2 font-mono text-[12px] leading-5">
              {diff.map((d, i) => <div key={i} className={d.t === '+' ? 'bg-good-soft text-good' : d.t === '-' ? 'bg-bad-soft text-bad' : 'text-muted'}>{d.t} {d.s}</div>)}
            </pre>
          </figure>
        )}
      </Section>

      <Section title="Input variables" description="Detected from the prompts. Mark a variable string? when some inputs do not have it: it then renders as empty text instead of failing the run.">
        {vars.length === 0 ? <Notice tone="warn">No {'{{placeholders}}'} found in the prompts, so scenario inputs would have nowhere to go.</Notice> : (
          <div className="flex flex-wrap gap-5">
            {vars.map((v) => (
              <Select key={v} label={`{{${v}}}`} value={types[v] ?? 'string'} onChange={(e) => setTypes({ ...types, [v]: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</Select>
            ))}
          </div>
        )}
      </Section>

      <Section title="Your own data" description="Optional, but 10 to 20 labelled examples make the result much more trustworthy.">
        <div className="grid gap-6">
          <Uploader id="dataset-file" title="Dataset (CSV or JSONL)" hint="Rows with an expected value describe the wanted behaviour. Traces labelled pass or fail calibrate the judges." vars={vars.length ? vars : ['user_message']} value={dataset} onChange={setDataset} accept=".csv,.jsonl,.json" />
          <Uploader id="traces-file" title="Production traces (JSONL)" hint="One object per line: input, optional output, optional expected. Real inputs seed scenario generation; traces without an expected value are never counted as verified." vars={vars.length ? vars : ['user_message']} value={traces} onChange={setTraces} accept=".jsonl,.json,.csv" />
          <label className="flex items-start gap-2.5 text-[13px]"><input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" checked={promptfoo} onChange={(e) => setPromptfoo(e.target.checked)} />
            <span>Show the Promptfoo export<span className="block text-xs text-muted">An optional backend: rerun these cases and checks in Promptfoo.</span></span></label>
        </div>
      </Section>

      <div className="mt-10 flex items-center gap-4 border-t border-line pt-6">
        <Button disabled={!f.name || !oldP || !newP || create.isPending} onClick={() => create.mutate()}>{create.isPending ? 'Saving' : 'Save and continue'}</Button>
        {create.error && <p role="alert" className="text-[13px] text-bad">{String(create.error)}</p>}
      </div>
    </div>
  )
}
