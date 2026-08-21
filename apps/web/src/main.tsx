import { StrictMode } from 'react'
import { hydrateRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// hydrateRoot, not createRoot: scripts/prerender.mjs bakes a real server-
// rendered snapshot into dist/index.html specifically so first paint doesn't
// wait on JS. createRoot ignores that markup and rebuilds the DOM from
// scratch once React loads — the prerendered text was still painting fast,
// but Lighthouse (and real users) saw that get thrown away and redone a
// beat later, which is most of what LCP's "element render delay" was
// measuring here (~3.3s of it, confirmed via PageSpeed's LCP breakdown).
// For a deep-linked report URL the client's first render differs from the
// prerendered (always-empty-state) snapshot — hydrateRoot detects that
// mismatch and falls back to a client re-render for that case, same as
// createRoot does today, so this only helps and never regresses.
hydrateRoot(
  document.getElementById('root')!,
  <StrictMode>
    <App />
  </StrictMode>,
)

// Defer RUM initialization until after first render so it doesn't block TTI.
// The ~80 KB aws-rum-web bundle is parsed off the critical path.
setTimeout(() => import('./rum.ts'), 0)
