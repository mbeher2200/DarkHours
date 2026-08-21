import { StrictMode } from 'react'
import { hydrateRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// hydrateRoot, not createRoot: reuses prerender.mjs's server-rendered snapshot
// instead of discarding and rebuilding it (was ~3.3s of LCP). Deep-linked report
// URLs still fall back to a client re-render on mismatch, as before.
hydrateRoot(
  document.getElementById('root')!,
  <StrictMode>
    <App />
  </StrictMode>,
)

// Defer RUM initialization until after first render so it doesn't block TTI.
// The ~80 KB aws-rum-web bundle is parsed off the critical path.
setTimeout(() => import('./rum.ts'), 0)
