import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The SPA always calls the API with RELATIVE paths (e.g. fetch('/night?...')).
// In production it is served from the same CloudFront distribution as the API,
// so relative paths are same-origin — no CORS, no base URL baked into the bundle.
//
// In dev there is no CloudFront, so the Vite dev server proxies those same API
// paths to the live API origin. The origin is read from VITE_API_ORIGIN in a
// gitignored .env.local (see .env.example) so the deployed URL stays out of the repo.
const API_PATHS = ['/night', '/suggest', '/healthz', '/calendar', '/trip', '/jobs', '/nearby']

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_ORIGIN
  const proxy = target
    ? Object.fromEntries(
        API_PATHS.map((p) => [p, { target, changeOrigin: true, secure: true }]),
      )
    : undefined

  return {
    plugins: [react()],
    server: { proxy },
  }
})
