// Build-time-only entry point (run under Node via `vite build --ssr`, never
// shipped to the browser). Renders App's initial state — `report` is always
// null before any effect/fetch runs, so this deterministically produces the
// marketing/empty-state view, which is what scripts/prerender.mjs injects into
// dist/index.html for crawlers that don't execute JavaScript.
//
// Invariant: everything transitively imported from App (including all of
// ./report/*) must not touch window/document/navigator/localStorage at module
// scope. Component bodies are fine — they never execute here, since `report`
// stays null and ReportCard's `{report && ...}` gate keeps it unmounted — but
// top-level statements run at import time regardless of whether the
// component they belong to ever renders.
import { renderToString } from 'react-dom/server'
import App from './App'

export function render(): string {
  return renderToString(<App />)
}
