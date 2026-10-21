# 9. The frontend

Location: `frontend/`. React 19, TypeScript, Vite, Tailwind v4, TanStack Query, Recharts, Radix Dialog, Phosphor icons, Geist fonts (self-hosted through fontsource).

## Structure

```
frontend/src/
  main.tsx, App.tsx      the shell: header, step navigation, which page to show
  steps.ts               the list of step names
  api.ts                 the one fetch wrapper
  hooks.tsx              job polling hook and job status text
  types.ts               TypeScript types for API objects
  index.css              design tokens, ambient background, bezel and motion styles
  pages/                 Home, Setup, Understand, Scenarios, Metrics, Budget, Run, Report, Refine
  components/ui/         button, badge, field (input, textarea, select), page (headers, sections,
                         notices, empty states, meter), dialog, ask (the token prompt), card
  lib/                   setup helpers (diff, placeholders, CSV columns), theme, reveal (scroll
                         animation), step context, utils
frontend/e2e/            Playwright test of the whole wizard
frontend/demo/          the demo recorder
```

## How the app shell works

`App.tsx` keeps three pieces of state: the current **step**, the current **project**, and whether the **home page** is showing.

- On load it shows the home page. If a project id is saved in the browser (`localStorage`), it fetches it and resumes at step 2.
- A floating glass bar holds the logo (back to home), the eight step buttons, the project name, a theme switch and "New project".
- Steps other than Setup are disabled until a project exists.
- A step that is out of date (from `GET /projects/{id}/stale`, refreshed every 4 seconds) shows a dot on its button and a banner with **Mark as reviewed**.
- After a user navigates, keyboard focus moves to the main content. On first load it does not, so the "Skip to content" link stays the first Tab stop.
- Steps with no own "continue" action get a **Continue to next step** button at the bottom (Understand to Budget, and Report to Refine). Setup has Save and continue, Run has View the report, and Refine is last.

There is no router library. The step is plain state.

## Talking to the server

- **`api()`** (`api.ts`) calls `/api/...`, adds the saved token if there is one, and on a 401 asks the user for the token once (`components/ui/ask.tsx`) and retries. Errors are shown inline.
- **Jobs:** `useJob` polls `GET /jobs/{id}` about every 0.7 seconds until the status is `done`, `failed` or `paused`, then calls back once. `JobStatus` shows progress text in a polite live region for screen readers.
- **Live runs:** the Run page opens an `EventSource` on `/api/runs/{id}/events` and updates the progress bar, noise table and regression list as events arrive. The token goes in the URL because an event stream cannot set headers.
- **Server state:** TanStack Query caches reads and refetches after changes.

## The pages

| Page | Main things on it |
|------|-------------------|
| Home | Hero with a mock verdict card, the eight-step grid, the principles, a call to action. Scroll reveals use `IntersectionObserver`. |
| Setup | App fields, model fields, side-by-side prompts with a live diff, the drafter, detected input variables, uploaders with column mapping, a "what happens next" rail. |
| Understand | The context card and the change list with editable risk and "could affect" notes, add or remove a change. |
| Scenarios | The coverage heatmap (click a cell to filter), review queue with approve, edit, reject, pin and comment, add-your-own, generate-more, and a dialog for comments. |
| Metrics | Metric cards with the definition editor, preview table, calibration, add-your-own with JSON validation errors. |
| Budget | Estimate, the three-tier table with coverage lost, cost levers, custom selection, hard cap, confirm. |
| Run | Start, progress and spend meter, stage, noise table, regressions as found, cap and deep-dive dialogs, stop. |
| Report | Verdict card, scope stamp, per-metric chart (Recharts, with error bars) and table, per-category table, regression and improvement cases with overrides, exports, re-score. |
| Refine | Start a search with a cap, candidates with their held-out reports, accept, edit, reject. |

## Design system

The design came from the design skills kept in the repo (`.claude/skills`, `.agents/skills`). See also `docs/frontend-design.md`.

- **Tokens** (`index.css`): one neutral family, one accent (cobalt), and semantic colours only for real state (good, warn, bad). Light and dark themes, following the system by default, with an explicit switch (`lib/theme.tsx` sets `data-theme` and remembers the choice).
- **Shapes:** buttons, nav and tags are pills. Inputs are rounded rectangles. Panels use a **double bezel**: an outer tray with a hairline ring and an inner core with a top highlight. `Section` is built this way, so every page shares it.
- **Type:** Geist and Geist Mono; tabular numbers for data.
- **Motion:** custom easing (`cubic-bezier(0.32, 0.72, 0, 1)`), animating only transform and opacity, with fade-up reveals. `prefers-reduced-motion` turns motion off.
- **Ambient layer:** a fixed, non-interactive background glow and a faint grain, so scrolling never repaints them.
- **Accessibility:** a skip link, visible focus rings, labelled controls, live regions for job status and run events, announced "out of date" state on step buttons, and no colour-only meaning. The end-to-end test checks several of these.
- **Responsive:** tested at 390 px wide with no page-level horizontal scroll. Wide tables scroll inside their own container.

## Frontend tests

- **Unit** (`npm test`, Vitest): the diff, placeholder and CSV helpers, and the step list.
- **End to end** (`npm run e2e`, Playwright): starts a real backend and the Vite server, uses the fake model, and walks Setup to Refine. It fails on any uncaught JavaScript error, console error or HTTP 5xx. Set `E2E_BASE_URL` to run the same flow against an already running app, such as the Docker container.
