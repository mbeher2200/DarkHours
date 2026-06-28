import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Defer RUM initialization until after first render so it doesn't block TTI.
// The ~80 KB aws-rum-web bundle is parsed off the critical path.
setTimeout(() => import('./rum.ts'), 0)
