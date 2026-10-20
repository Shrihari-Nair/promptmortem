# Frontend design notes

The UI was redesigned with the codebase's own skills (`.claude/skills/redesign-existing-projects`, `.claude/skills/design-taste-frontend`).
`design-taste-frontend` names dashboards and dense product UI as out of its scope, so it was applied only where it is universal (typography,
colour calibration, shape consistency, interactive states, contrast, copy, dark mode, accessibility); `redesign-existing-projects` drove the
process: audit, then targeted upgrades on the existing stack (React, Tailwind v4, Radix, TanStack Query), no framework change.

**Design read:** a calm, trust-first, data-dense developer tool. Dials: variance 3, motion 2, density 5.

**What changed, from the audit**
- Typography: Geist and Geist Mono (self-hosted via fontsource), tabular figures for every number, tighter headings, sentence case, 65 to 75 character measures.
- Colour: one neutral family and one cobalt accent in CSS tokens (`src/index.css`), light and dark from the same tokens (system by default, a Theme dropdown to override). Green, amber and red appear only for real state (fixed, out of date, broken). No pure black or white, no gradients, no glow.
- Shape: controls use `rounded-md`, panels and dialogs `rounded-lg`; nothing is a pill except a status dot. Boxes only where grouping needs to read as one object; elsewhere hairlines and space.
- Layout: a single-line top bar replaces the left sidebar so wide tables get the page; on a phone the step bar takes its own scrollable row. Pages share one rhythm (header, hairline-separated sections). Budget tiers are a comparison table with radios, not a trio of equal cards.
- States: skeleton loaders, composed empty states, inline errors, pressed-state feedback on every button, visible focus rings, and in-app dialogs instead of `window.prompt` and `confirm` (including the API token request).
- Forms: a visible label above every field (placeholders are examples only), help text, and errors linked with `aria-describedby`.
- Accessibility: skip link as the first tab stop (focus is not stolen on load), landmarks, `aria-current` on the step, out-of-date markers with real screen-reader text, a labelled progress bar, a labelled chart, reduced-motion support, `min-h-[100dvh]`.
- Content: no emoji, no em-dashes, no middle-dot separators, no status dots except real state.
- Icons: Phosphor only.

**Verified by the browser test (`npm run e2e`):** the full flow with accessible selectors, no uncaught errors, no horizontal scroll at 390px on any step, the skip link and focus order. `E2E_SHOTS=<dir> E2E_THEME=dark npm run e2e` writes screenshots of every screen for review.
