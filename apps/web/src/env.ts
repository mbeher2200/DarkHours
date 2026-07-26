// SSR-safe browser-environment detection. Node (used for build-time prerendering
// via entry-server.tsx) has no `window` — unlike `navigator`, which Node 21+
// partially stubs with a bare `.userAgent`, `window` is never defined outside a
// real browser/DOM environment, so it's the reliable sentinel.
export const isBrowser = typeof window !== 'undefined'

export function readLocalStorage(key: string): string | null {
  return isBrowser ? localStorage.getItem(key) : null
}
