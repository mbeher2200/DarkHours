// Presentation helpers. Times are formatted in the *report's* timezone (tz_name),
// not the viewer's, so "Sunset 8:32 PM" reads correctly for the queried location.

import type { WeatherPoint, LightPollution } from './types'

// Mirror CLI detect_units(): imperial for en-US locale, SI otherwise.
export const imperial: boolean =
  typeof navigator !== 'undefined' &&
  (navigator.language === 'en-US' || navigator.language.startsWith('en-US'))

// " 6:47 PM"  — space-padded 12-hour, no date (mirrors FormatCtx.fmt_time)
export function formatTime(iso: string | null, tz: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const parts = new Intl.DateTimeFormat('en-US', {
      hour: 'numeric', minute: '2-digit', hour12: true, timeZone: tz,
    }).formatToParts(d)
    const hour = parts.find(p => p.type === 'hour')?.value ?? '0'
    const min  = parts.find(p => p.type === 'minute')?.value ?? '00'
    const ampm = parts.find(p => p.type === 'dayPeriod')?.value ?? 'AM'
    return `${hour.padStart(2, ' ')}:${min} ${ampm}`
  } catch {
    return '—'
  }
}

// "Aug 12,  7:08 AM"  — month/day + space-padded 12-hour (mirrors FormatCtx.fmt)
export function formatDayTime(iso: string | null, tz: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const parts = new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true, timeZone: tz,
    }).formatToParts(d)
    const month = parts.find(p => p.type === 'month')?.value ?? ''
    const day   = parts.find(p => p.type === 'day')?.value ?? ''
    const hour  = parts.find(p => p.type === 'hour')?.value ?? '0'
    const min   = parts.find(p => p.type === 'minute')?.value ?? '00'
    const ampm  = parts.find(p => p.type === 'dayPeriod')?.value ?? 'AM'
    return `${month} ${day}, ${hour.padStart(2, ' ')}:${min} ${ampm}`
  } catch {
    return '—'
  }
}

// "6h 12m"
export function formatHm(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}h ${m}m`
}

// Short timezone abbreviation, e.g. "MST", "PDT"
export function tzAbbr(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, timeZoneName: 'short',
    }).formatToParts(new Date())
    return parts.find(p => p.type === 'timeZoneName')?.value ?? tz
  } catch {
    return tz
  }
}

// 8-point cardinal from azimuth degrees
export function cardinal(az: number): string {
  const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  return dirs[Math.round(az / 45) % 8]
}

// Port of weather.rate_conditions() — returns 1–10
export function rateConditions(p: WeatherPoint): number {
  if (p.precip_type && p.precip_type !== 'none') return 1

  const scores: Record<string, number>  = {}
  const weights: Record<string, number> = {}

  if (p.cloud_cover_pct != null) {
    scores.cloud  = Math.max(0, 1 - Math.pow(p.cloud_cover_pct / 100, 0.7))
    weights.cloud = 0.50
  }
  if (p.seeing_arcsec != null) {
    scores.seeing  = Math.max(0, (3.0 - p.seeing_arcsec) / 2.6)
    weights.seeing = 0.20
  }
  if (p.transparency != null) {
    const tmap: Record<string, number> = { Excellent: 1.0, Good: 0.75, Fair: 0.4, Poor: 0.1 }
    scores.transp  = tmap[p.transparency] ?? 0.5
    weights.transp = 0.15
  }
  if (p.wind_speed_ms != null) {
    scores.wind  = Math.max(0, 1 - p.wind_speed_ms / 12)
    weights.wind = 0.10
  }
  if (p.humidity_pct != null) {
    scores.humid  = Math.max(0, 1 - Math.max(0, p.humidity_pct - 50) / 40)
    weights.humid = 0.05
  }

  const keys = Object.keys(scores)
  if (!keys.length) return 5

  const totalW   = keys.reduce((s, k) => s + weights[k], 0)
  const weighted = keys.reduce((s, k) => s + scores[k] * weights[k], 0) / totalW
  return Math.max(1, Math.min(10, Math.round(weighted * 10)))
}

// Temperature formatting (mirrors FormatCtx.temp)
export function fmtTemp(c: number | null): string {
  if (c == null) return '—'
  if (imperial) return `${Math.round(c * 9 / 5 + 32)}°F`
  return `${c.toFixed(1)}°C`
}

// Wind formatting with optional direction (mirrors FormatCtx.wind)
export function fmtWind(ms: number | null, dir: number | null): string {
  if (ms == null) return '—'
  const speed = imperial ? `${Math.round(ms * 2.237)}mph` : `${ms.toFixed(1)}m/s`
  return dir != null ? `${speed} ${cardinal(dir)}` : speed
}

// Combined light-pollution display string (mirrors format_ctx.lp_str)
export function lpString(lp: LightPollution): string | null {
  if (lp.below_detection) return 'Light pollution data unavailable'
  if (lp.sqm == null) return null
  return `SQM ${lp.sqm}  ·  Zone ${lp.lp_zone}  ·  Bortle ${lp.bortle_class}  (${lp.bortle_desc})  [${lp.source}]`
}

/** 1–10 → a band used for color + label. */
export function scoreBand(score: number): 'excellent' | 'good' | 'fair' | 'poor' {
  if (score >= 8) return 'excellent'
  if (score >= 6) return 'good'
  if (score >= 4) return 'fair'
  return 'poor'
}

export function scoreLabel(score: number): string {
  return { excellent: 'Excellent', good: 'Good', fair: 'Fair', poor: 'Poor' }[scoreBand(score)]
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}
