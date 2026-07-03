import type { ApiError, NightReport, DateOnlyNightReport, NearbyResult, NearbyJobRecord, CalendarResult, CalendarJobRecord } from './types'

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

// Adaptive poll backoff: a warm job finishes in ~1.3s, so probe early and often
// at first, then ease off. Each entry is the wait BEFORE that poll; the last value
// repeats until the timeout. Catches a warm result near ~1.5s instead of the old
// fixed 3s tick (no compute change — purely perceived latency).
const NEARBY_POLL_BACKOFF_MS = [500, 1_000, 2_000, 3_000]
const NEARBY_TIMEOUT_MS = 120_000

/**
 * Submit a nearby dark-sky search and poll until complete.
 * Uses the same async SQS+job pattern as /calendar.
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
  for (let attempt = 0; Date.now() < deadline; attempt++) {
    const wait = NEARBY_POLL_BACKOFF_MS[Math.min(attempt, NEARBY_POLL_BACKOFF_MS.length - 1)]
    await new Promise(r => setTimeout(r, wait))
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

// A calendar job can cover up to 30 location-nights (vs /nearby's fixed single-point
// scan), so give it more headroom before giving up; same backoff shape otherwise.
const CALENDAR_POLL_BACKOFF_MS = [500, 1_000, 2_000, 3_000]
const CALENDAR_TIMEOUT_MS = 180_000

/**
 * Submit a multi-night outlook search for one location and poll until complete.
 * Uses the same async SQS+job pattern as /nearby.
 */
export async function fetchCalendar(lat: number, lon: number, start: string, days: number): Promise<CalendarResult> {
  const p = new URLSearchParams({ lat: String(lat), lon: String(lon), start, days: String(days) })
  let res: Response
  try {
    res = await fetch(`/calendar?${p}`)
  } catch {
    throw new ApiRequestError(0, 'Could not reach the API. Check your connection and try again.')
  }
  if (res.status !== 202) {
    let detail = `Calendar request failed (${res.status}).`
    try {
      const b = (await res.json()) as ApiError
      if (b?.detail) detail = b.detail
    } catch { /* non-JSON error body */ }
    throw new ApiRequestError(res.status, detail)
  }
  const { job_id } = (await res.json()) as { job_id: string }

  const deadline = Date.now() + CALENDAR_TIMEOUT_MS
  for (let attempt = 0; Date.now() < deadline; attempt++) {
    const wait = CALENDAR_POLL_BACKOFF_MS[Math.min(attempt, CALENDAR_POLL_BACKOFF_MS.length - 1)]
    await new Promise(r => setTimeout(r, wait))
    let poll: Response
    try {
      poll = await fetch(`/jobs/${job_id}`)
    } catch {
      throw new ApiRequestError(0, 'Lost connection while waiting for calendar results.')
    }
    if (!poll.ok) throw new ApiRequestError(poll.status, `Poll failed (${poll.status}).`)
    const rec = (await poll.json()) as CalendarJobRecord
    if (rec.status === 'done')  return rec.result
    if (rec.status === 'error') throw new ApiRequestError(500, rec.error)
  }
  throw new ApiRequestError(0, 'Calendar search timed out. Try a shorter range.')
}

/**
 * Typeahead place suggestions for the search box (autocomplete).
 *
 * Best-effort: any failure (network, non-2xx) resolves to an empty list so a
 * flaky geocoder never disrupts typing. An aborted request (superseded by a
 * newer keystroke) re-throws AbortError for the caller to ignore.
 */
export async function fetchSuggestions(q: string, signal?: AbortSignal): Promise<string[]> {
  const p = new URLSearchParams({ q })
  let res: Response
  try {
    res = await fetch(`/suggest?${p}`, { signal })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    return []
  }
  if (!res.ok) return []
  try {
    const body = (await res.json()) as { suggestions?: string[] }
    return body.suggestions ?? []
  } catch {
    return []
  }
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

export interface DateOnlyNightQuery {
  lat: number
  lon: number
  date: string
  weather: boolean
  targets: boolean
  satellites: boolean
}

/**
 * Fetch only the date-dependent fields of a report for a location whose
 * location-keyed fields (light_pollution/bortle_score/light_dome) are already
 * known client-side from a prior full /night response. Always uses lat/lon —
 * never `location=` — since the location is already resolved and re-geocoding
 * a place name here could silently drift to different coordinates.
 */
export async function fetchNightDateOnly(q: DateOnlyNightQuery): Promise<DateOnlyNightReport> {
  const p = new URLSearchParams({
    lat: String(q.lat), lon: String(q.lon), date: q.date,
    weather: String(q.weather), targets: String(q.targets), satellites: String(q.satellites),
    date_only: 'true',
  })
  let res: Response
  try {
    res = await fetch(`/night?${p}`)
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
  return (await res.json()) as DateOnlyNightReport
}
