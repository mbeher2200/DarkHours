import type { ApiError, NightReport } from './types'

export interface NightQuery {
  location?: string
  lat?: number
  lon?: number
  date?: string
  weather: boolean
  targets: boolean
  satellites: boolean
}

/** Raised for any non-2xx response; `message` is the API's detail when present. */
export class ApiRequestError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiRequestError'
  }
}

function buildQuery(q: NightQuery): string {
  const p = new URLSearchParams()
  if (q.location) p.set('location', q.location)
  if (q.lat !== undefined) p.set('lat', String(q.lat))
  if (q.lon !== undefined) p.set('lon', String(q.lon))
  if (q.date) p.set('date', q.date)
  p.set('weather', String(q.weather))
  p.set('targets', String(q.targets))
  p.set('satellites', String(q.satellites))
  return p.toString()
}

/**
 * Fetch a single-night report. Calls the API with a RELATIVE URL so it is
 * same-origin in production (CloudFront) and proxied in dev (see vite.config.ts).
 */
export async function fetchNight(q: NightQuery): Promise<NightReport> {
  let res: Response
  try {
    res = await fetch(`/night?${buildQuery(q)}`)
  } catch {
    throw new ApiRequestError(0, 'Could not reach the API. Check your connection and try again.')
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status}).`
    try {
      const body = (await res.json()) as ApiError
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body — keep the generic message */
    }
    throw new ApiRequestError(res.status, detail)
  }
  return (await res.json()) as NightReport
}
