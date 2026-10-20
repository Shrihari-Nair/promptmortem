import { WarningCircle } from '@phosphor-icons/react'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from './api'
import { AskProvider } from './components/ui/ask'
import { Button } from './components/ui/button'
import { Notice } from './components/ui/page'
import { useTheme, type Theme } from './lib/theme'
import Budget from './pages/Budget'
import Home from './pages/Home'
import Metrics from './pages/Metrics'
import Refine from './pages/Refine'
import ReportPage from './pages/Report'
import Run from './pages/Run'
import Scenarios from './pages/Scenarios'
import Setup from './pages/Setup'
import Understand from './pages/Understand'
import { STEPS, type Step } from './steps'
import type { Project, Stale } from './types'

const qc = new QueryClient()
const KEY = 'pv_project'

function Shell() {
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>('Setup')
  const [home, setHome] = useState(true)  // landing page until the user starts or resumes a project
  const [project, setProject] = useState<Project | null>(null)
  const [theme, setTheme] = useTheme()
  const stale = useQuery({ queryKey: ['stale', project?.id], enabled: !!project, queryFn: () => api<Stale>(`/projects/${project!.id}/stale`), refetchInterval: 4000 }).data ?? {}

  useEffect(() => {  // resume the last project after a reload
    const id = localStorage.getItem(KEY)
    if (id) api<Project>(`/projects/${id}`).then((p) => { setProject(p); setStep('Understand'); setHome(false) }).catch(() => localStorage.removeItem(KEY))
  }, [])
  /** User navigation moves focus to the new step's content. Resuming a project on load does not (the skip link must stay the first Tab stop). */
  const go = (s: Step) => { setHome(false); setStep(s); window.setTimeout(() => document.getElementById('main')?.focus({ preventScroll: true }), 0) }

  const page = () => {
    if (step === 'Setup') return <Setup onCreated={(p) => { localStorage.setItem(KEY, p.id); setProject(p); go('Understand') }} />
    if (!project) return null
    if (step === 'Understand') return <Understand project={project} />
    if (step === 'Scenarios') return <Scenarios project={project} />
    if (step === 'Metrics') return <Metrics project={project} />
    if (step === 'Budget') return <Budget project={project} />
    if (step === 'Run') return <Run project={project} goReport={() => go('Report')} />
    if (step === 'Report') return <ReportPage project={project} />
    return <Refine project={project} />
  }

  const logo = (
    <button type="button" onClick={() => setHome(true)} aria-label="Home" className="flex shrink-0 items-center gap-2 rounded-full pr-2 transition-opacity duration-500 hover:opacity-80">
      <img src="/favicon.svg" alt="" width={24} height={24} className="rounded-[7px]" />
      <span className="hidden text-sm font-semibold tracking-tight lg:inline">Prompt Mortem</span>
    </button>
  )
  const themeSelect = (
    <select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} className="h-8 rounded-full border-0 bg-transparent px-3 text-[13px] text-muted ring-1 ring-line hover:ring-line-strong">
      <option value="system">Theme: system</option><option value="light">Theme: light</option><option value="dark">Theme: dark</option>
    </select>
  )

  return (
    <div className="min-h-[100dvh]">
      <div aria-hidden className="ambient" /><div aria-hidden className="grain" />
      <a href="#main" className="sr-only z-50 rounded-full bg-ink px-4 py-2 text-canvas focus:not-sr-only focus:fixed focus:left-4 focus:top-3">Skip to content</a>
      <header className="sticky top-3 z-30 px-3 pt-3">
        <div className="glass mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-4 gap-y-1 rounded-[1.75rem] px-4 py-2 md:h-14 md:flex-nowrap md:rounded-full md:py-0">
          {logo}
          {home ? (
            <nav aria-label="Sections" className="mx-auto hidden items-center gap-1 text-[13px] text-muted md:flex">
              <a href="#pipeline" className="rounded-full px-3 py-1.5 transition-colors duration-500 hover:bg-sunken hover:text-ink">How it works</a>
              <a href="#principles" className="rounded-full px-3 py-1.5 transition-colors duration-500 hover:bg-sunken hover:text-ink">Why trust it</a>
            </nav>
          ) : (
          <nav aria-label="Steps" className="relative order-last w-full min-w-0 overflow-x-auto md:order-none md:w-auto md:flex-1">
            <ol className="flex w-max items-center gap-0.5">
              {STEPS.map((s, i) => {
                const active = s === step
                const off = s !== 'Setup' && !project
                return (
                  <li key={s}>
                    <button
                      disabled={off}
                      aria-current={active ? 'step' : undefined}
                      onClick={() => go(s)}
                      className={`flex h-8 items-center gap-1.5 rounded-full px-3 text-[13px] transition-colors duration-500 disabled:opacity-35 ${active ? 'bg-ink font-medium text-canvas' : 'text-muted hover:bg-sunken hover:text-ink'}`}
                    >
                      <span className="tnum text-[11px] opacity-60">{i + 1}</span>{' '}{s}
                      {stale[s] && (<><span aria-hidden="true" title={stale[s].reason} className="size-1.5 rounded-full bg-warn" /><span className="sr-only"> (out of date)</span></>)}
                    </button>
                  </li>
                )
              })}
            </ol>
          </nav>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-2">{
            !home && project && <span className="hidden max-w-[14rem] truncate text-[13px] text-muted xl:inline" title={project.target_model}>{project.name}</span>}
            {themeSelect}
            {home ? <Button size="sm" onClick={() => go(project ? 'Understand' : 'Setup')}>{project ? 'Resume project' : 'Open app'}</Button>
              : project && <Button variant="ghost" size="sm" onClick={() => { localStorage.removeItem(KEY); setProject(null); go('Setup') }}>New project</Button>}
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className={home ? 'outline-none' : 'mx-auto w-full max-w-[1280px] px-6 pb-24 pt-10 outline-none'}>
        {home ? <Home start={() => go(project ? 'Understand' : 'Setup')} /> : (<>
        {project && stale[step] && (
          <Notice tone="warn" className="mb-6 flex items-center gap-3">
            <WarningCircle size={18} weight="regular" aria-hidden className="shrink-0" />
            <span>This step may be out of date: {stale[step].reason}. Redo it, or confirm it is still right.</span>
            <Button variant="secondary" size="sm" className="ml-auto" onClick={async () => { await api(`/projects/${project.id}/stale/${step}/ack`, { method: 'POST' }); queryClient.invalidateQueries({ queryKey: ['stale', project.id] }) }}>Mark as reviewed</Button>
          </Notice>
        )}
        <div key={step} className="rise">{page()}</div>
        </>)}
      </main>
    </div>
  )
}

export default function App() {
  return <QueryClientProvider client={qc}><AskProvider><Shell /></AskProvider></QueryClientProvider>
}
