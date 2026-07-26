// Runs after build:client + build:ssr, as part of `npm run build`. Renders the
// app's deterministic initial (no-report) state via the SSR bundle and bakes
// it into dist/index.html, so crawlers that don't execute JavaScript see real
// content instead of an empty <div id="root">.
//
// Every check below fails the build loudly (non-zero exit) rather than
// shipping a silently-degraded snapshot — this runs before `cdk deploy` in CI,
// so a failure here blocks the deploy entirely.
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const webDir = new URL('..', import.meta.url).pathname
const { render } = await import(join(webDir, 'dist-ssr', 'entry-server.js'))

const appHtml = render()

// PR2 replaces this literal with FEATURES.length once content/features.ts
// exists, so this count can't silently drift from the real feature list.
const EXPECTED_FEATURE_COUNT = 12

function fail(message) {
  console.error(`[prerender] ${message}`)
  process.exit(1)
}

if (appHtml.length < 1500) {
  fail(`rendered HTML is suspiciously small (${appHtml.length} chars) — expected the full marketing/empty-state view.`)
}

const capCount = (appHtml.match(/class="es-cap"/g) ?? []).length
if (capCount !== EXPECTED_FEATURE_COUNT) {
  fail(`expected ${EXPECTED_FEATURE_COUNT} feature tiles (class="es-cap"), found ${capCount}.`)
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

writeFileSync(indexPath, html)
console.log(`[prerender] injected ${appHtml.length} chars into dist/index.html (${capCount} feature tiles).`)
