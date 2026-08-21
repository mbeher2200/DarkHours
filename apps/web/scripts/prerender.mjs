// Runs after build:client + build:ssr, as part of `npm run build`. Renders the
// app's deterministic initial (no-report) state via the SSR bundle and bakes
// it into dist/index.html, so crawlers that don't execute JavaScript see real
// content instead of an empty <div id="root">.
//
// Every check below fails the build loudly (non-zero exit) rather than
// shipping a silently-degraded snapshot — this runs before `cdk deploy` in CI,
// so a failure here blocks the deploy entirely.
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const webDir = new URL('..', import.meta.url).pathname
const { render, FEATURES } = await import(join(webDir, 'dist-ssr', 'entry-server.js'))

const appHtml = render()

function fail(message) {
  console.error(`[prerender] ${message}`)
  process.exit(1)
}

if (appHtml.length < 1500) {
  fail(`rendered HTML is suspiciously small (${appHtml.length} chars) — expected the full marketing/empty-state view.`)
}

const capCount = (appHtml.match(/class="es-cap"/g) ?? []).length
if (capCount !== FEATURES.length) {
  fail(`expected ${FEATURES.length} feature tiles (class="es-cap"), found ${capCount}.`)
}

if (appHtml.includes('class="report-head"')) {
  fail('rendered HTML contains ReportCard markup ("report-head") — the initial-render/deterministic-null-report assumption no longer holds.')
}

const indexPath = join(webDir, 'dist', 'index.html')
const template = readFileSync(indexPath, 'utf-8')

const EMPTY_ROOT = '<div id="root"></div>'
if (!template.includes(EMPTY_ROOT)) {
  fail(`could not find the literal "${EMPTY_ROOT}" in dist/index.html — has the Vite template changed?`)
}

const html = template.replace(EMPTY_ROOT, `<div id="root">${appHtml}</div>`)

if (html.includes(EMPTY_ROOT)) {
  fail('replace produced no change — dist/index.html still contains an empty root div.')
}

// Keep the WebApplication JSON-LD's featureList mechanically in sync with the
// actual feature grid — a featureList that drifts from real page content is
// exactly the kind of sloppy structured data that undermines the trust this
// whole prerendering effort is meant to build.
const jsonLdMatch = html.match(/(<script type="application\/ld\+json">)([\s\S]*?)(<\/script>)/)
if (!jsonLdMatch) {
  fail('could not find the WebApplication JSON-LD <script> block in dist/index.html.')
}
let jsonLd
try {
  jsonLd = JSON.parse(jsonLdMatch[2])
} catch (e) {
  fail(`WebApplication JSON-LD did not parse before mutation: ${e.message}`)
}
jsonLd.featureList = FEATURES.map(([title]) => title)
if (jsonLd.featureList.length !== FEATURES.length) {
  fail(`featureList length mismatch after sync: expected ${FEATURES.length}, got ${jsonLd.featureList.length}.`)
}
const finalHtml = html.replace(jsonLdMatch[0], `${jsonLdMatch[1]}\n    ${JSON.stringify(jsonLd, null, 2)}\n    ${jsonLdMatch[3]}`)

// main.tsx's setTimeout(() => import('./rum.ts'), 0) delays the ~80KB rum-web
// fetch behind the main bundle load+exec (was the longest network chain on the
// page). This modulepreload fetches it in parallel instead, fetchpriority="low"
// so it still doesn't compete with first render; setTimeout still controls when it runs.
const rumChunk = readdirSync(join(webDir, 'dist', 'assets')).find((f) => /^rum-.*\.js$/.test(f))
if (!rumChunk) {
  fail('could not find the built rum-*.js chunk in dist/assets — has main.tsx\'s RUM import changed?')
}
const rumPreload = `<link rel="modulepreload" fetchpriority="low" href="/assets/${rumChunk}">`
if (!finalHtml.includes('</head>')) {
  fail('could not find "</head>" in dist/index.html to inject the rum modulepreload hint.')
}
const withRumPreload = finalHtml.replace('</head>', `    ${rumPreload}\n  </head>`)

writeFileSync(indexPath, withRumPreload)
console.log(`[prerender] injected ${appHtml.length} chars into dist/index.html (${capCount} feature tiles, featureList synced, rum preload -> ${rumChunk}).`)
