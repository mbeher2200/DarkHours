import type { ApiError, NightReport, NearbyResult, NearbyJobRecord } from './types'

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

const NEARBY_POLL_MS    = 3_000
const NEARBY_TIMEOUT_MS = 120_000

/**
 * Submit a nearby dark-sky search and poll until complete.
 * Uses the same async SQS+job pattern as /calendar and /trip.
 */
export async function fetchNearby(lat: number, lon: number, radius = 60): Promise<NearbyResult> {
  const p = new URLSearchParams({ lat: String(lat), lon: String(lon), radius: String(radius) })
  let res: Response
  try {
    res = await fetch(`/nearby?${p}`)
  } catch {
    throw new ApiRequestError(0, 'Could not reach the API. Check your connection and try again.')
  }
  if (res.status !== 202) {
    let detail = `Nearby request failed (${res.status}).`
    try {
      const b = (await res.json()) as ApiError
      if (b?.detail) detail = b.detail
    } catch { /* non-JSON error body */ }
    throw new ApiRequestError(res.status, detail)
  }
  const { job_id } = (await res.json()) as { job_id: string }

  const deadline = Date.now() + NEARBY_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, NEARBY_POLL_MS))
    let poll: Response
    try {
      poll = await fetch(`/jobs/${job_id}`)
    } catch {
      throw new ApiRequestError(0, 'Lost connection while waiting for nearby results.')
    }
    if (!poll.ok) throw new ApiRequestError(poll.status, `Poll failed (${poll.status}).`)
    const rec = (await poll.json()) as NearbyJobRecord
    if (rec.status === 'done')  return rec.result
    if (rec.status === 'error') throw new ApiRequestError(500, rec.error)
  }
  throw new ApiRequestError(0, 'Nearby search timed out. Try a smaller radius.')
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
