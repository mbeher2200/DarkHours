// Presentation helpers. Times are formatted in the *report's* timezone (tz_name),
// not the viewer's, so "Sunset 8:32 PM" reads correctly for the queried location.

import type { WeatherPoint, LightPollution } from './types'

// Mirror CLI detect_units(): imperial for en-US locale, SI otherwise.
// Used only to seed the initial default; components receive `imperial` as a prop.
export function defaultImperial(): boolean {
  if (typeof navigator === 'undefined') return false
  const saved = localStorage.getItem('units')
  if (saved) return saved === 'imperial'
  return navigator.language === 'en-US' || navigator.language.startsWith('en-US')
}

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
    return `${hour}:${min} ${ampm}`
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
    return `${month} ${day}, ${hour}:${min} ${ampm}`
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

// "3m ago" / "2h ago" — age of a past ISO timestamp relative to now
export function formatAge(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000))
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

// "14:32Z" — UTC HH:MM with Z suffix, for the provenance badge's ISSUED field
export function formatIssuedUtc(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${hh}:${mm}Z`
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

// Full timezone title, e.g. "Eastern Daylight Time"
export function tzTitle(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, timeZoneName: 'long',
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

// WMO weather interpretation code → short label
const WMO_LABELS: Record<number, string> = {
  0: 'Clear', 1: 'Mainly Clear', 2: 'Partly Cloudy', 3: 'Overcast',
  45: 'Fog', 48: 'Rime Fog',
  51: 'Lt. Drizzle', 53: 'Drizzle', 55: 'Dense Drizzle',
  56: 'Lt. Frz. Drizzle', 57: 'Frz. Drizzle',
  61: 'Light Rain', 63: 'Moderate Rain', 65: 'Heavy Rain',
  66: 'Lt. Frz. Rain', 67: 'Frz. Rain',
  71: 'Light Snow', 73: 'Moderate Snow', 75: 'Heavy Snow', 77: 'Snow Grains',
  80: 'Lt. Showers', 81: 'Showers', 82: 'Heavy Showers',
  85: 'Snow Showers', 86: 'Heavy Snow Showers',
  95: 'Thunderstorm', 96: 'T-storm + Hail', 99: 'T-storm + Hail',
}

export function fmtWeatherCode(code: number | null, prob: number | null): string {
  if (code == null) return '—'
  const label = WMO_LABELS[code] ?? `Code ${code}`
  // Show probability alongside precip-class codes (≥51)
  if (prob != null && code >= 51) return `${label} (${prob}%)`
  return label
}

// Port of weather.rate_conditions() — multiplicative limiter model — returns 1–10
export function rateConditions(p: WeatherPoint): number {
  // Hard gate 1: any non-"none" precip_type (covers rain/snow/frzr/icep/fog/tstorm
  // uniformly, since weather.py now derives precip_type from weather_code server-side).
  if (p.precip_type && p.precip_type !== 'none') return 1

  // Hard gate 2: visibility < 1000 m
  if (p.visibility_m != null && p.visibility_m < 1000) return 1

  const limiters: number[] = []

  if (p.cloud_cover_low_pct != null || p.cloud_cover_mid_pct != null || p.cloud_cover_high_pct != null) {
    const low  = (p.cloud_cover_low_pct  ?? 0) / 100
    const mid  = (p.cloud_cover_mid_pct  ?? 0) / 100
    const high = (p.cloud_cover_high_pct ?? 0) / 100
    const effective = Math.min(1, Math.max(low, mid) + 0.6 * high)
    limiters.push(Math.max(0, 1 - Math.pow(effective, 1.5)))
  } else if (p.cloud_cover_pct != null) {
    limiters.push(Math.max(0, 1 - Math.pow(p.cloud_cover_pct / 100, 1.5)))
  }

  if (p.wind_speed_ms != null)
    limiters.push(Math.max(0, 1 - Math.pow(p.wind_speed_ms / 17, 2)))
  if (p.transparency != null) {
    const tmap: Record<string, number> = { Excellent: 1.0, Good: 0.8, Fair: 0.4, Poor: 0.1 }
    limiters.push(tmap[p.transparency] ?? 0.5)
  }

  if (p.aerosol_optical_depth != null) {
    const aod = p.aerosol_optical_depth
    let s: number
    if (aod <= 0.1) s = 1.0
    else if (aod <= 0.3) s = 1.0 - 0.4 * (aod - 0.1) / 0.2
    else if (aod <= 0.8) s = 0.6 * Math.max(0, 1 - Math.pow((aod - 0.3) / 0.5, 1.5))
    else s = 0.0
    limiters.push(s)
  } else if (p.pm2_5 != null) {
    const pm = p.pm2_5
    let s: number
    if (pm <= 12) s = 1.0
    else if (pm <= 35) s = 1.0 - 0.4 * (pm - 12) / 23
    else if (pm <= 150) s = 0.6 * Math.max(0, 1 - Math.pow((pm - 35) / 115, 1.5))
    else s = 0.0
    limiters.push(s)
  }

  if (p.visibility_m != null) {
    const v = p.visibility_m
    let s: number
    if (v >= 20000) s = 1.0
    else if (v >= 10000) s = 0.7 + 0.3 * (v - 10000) / 10000
    else s = 0.7 * (Math.log10(v / 1000) / Math.log10(10))
    limiters.push(Math.max(0, Math.min(1, s)))
  }

  // Quality base: average of seeing and humidity (additive)
  const base: number[] = []
  if (p.seeing_arcsec != null)
    base.push(Math.max(0, Math.min(1, (4.0 - p.seeing_arcsec) / 3.0)))
  if (p.humidity_pct != null)
    base.push(Math.max(0, 1 - Math.max(0, p.humidity_pct - 50) / 50))

  if (!limiters.length && !base.length) return 10

  const baseScore = base.length ? base.reduce((s, v) => s + v, 0) / base.length : 1.0
  const final = limiters.reduce((s, v) => s * v, baseScore)
  return Math.max(1, Math.min(10, Math.round(final * 10)))
}

// Temperature formatting (mirrors FormatCtx.temp)
export function fmtTemp(c: number | null, imp: boolean): string {
  if (c == null) return '—'
  if (imp) return `${Math.round(c * 9 / 5 + 32)}°F`
  return `${c.toFixed(1)}°C`
}

// Wind formatting with optional direction (mirrors FormatCtx.wind)
export function fmtWind(ms: number | null, dir: number | null, imp: boolean): string {
  if (ms == null) return '—'
  const speed = imp ? `${Math.round(ms * 2.237)}mph` : `${ms.toFixed(1)}m/s`
  return dir != null ? `${speed} ${cardinal(dir)}` : speed
}

// Distance formatting: km ↔ mi (mirrors FormatCtx.dist)
export function fmtDist(km: number, imp: boolean): string {
  if (imp) return `${Math.round(km * 0.621371).toLocaleString()} mi`
  return `${Math.round(km).toLocaleString()} km`
}

// ── Moon wash (Krisciunas & Schaefer 1991) — mirrors moonlight.py ────────────

// Sky surface brightness increase from scattered moonlight (Δ mag/arcsec²)
function ksDeltaMag(illuminationPct: number, sepDeg: number, moonAltDeg: number): number {
  if (illuminationPct <= 0 || moonAltDeg <= 0) return 0
  const illum   = illuminationPct / 100
  const alpha   = Math.acos(Math.max(-1, Math.min(1, 2 * illum - 1))) * 180 / Math.PI
  const V_moon  = -12.73 + 0.026 * alpha + 4e-9 * Math.pow(alpha, 4)
  const I_moon  = Math.pow(10, -0.4 * (V_moon + 16.57))
  const alt     = Math.max(1, moonAltDeg)
  const X_moon  = 1 / Math.cos((90 - alt) * Math.PI / 180)
  const ext     = Math.pow(10, -0.4 * 0.172 * X_moon)
  const rho     = Math.max(0.1, sepDeg)
  const f_rho   = rho > 10
    ? Math.pow(10, 5.36) * (1.06 + Math.pow(Math.cos(rho * Math.PI / 180), 2))
    : 6.2e7 / (rho * rho)
  const I_sky   = Math.pow(10, (27.78 - 21.6) / 2.5)  // Bortle-2 baseline
  return 2.5 * Math.log10(1 + f_rho * ext * I_moon / I_sky)
}

// None = negligible, 'minor', 'moderate', 'severe' (mirrors moon_wash_severity)
export function moonWashSeverity(
  illuminationPct: number,
  sepDeg: number | null,
  moonAltDeg: number | null,
): string | null {
  const delta = ksDeltaMag(illuminationPct, sepDeg ?? 45, moonAltDeg ?? 45)
  if (delta < 0.10) return null
  if (delta < 0.50) return 'minor'
  if (delta < 1.50) return 'moderate'
  return 'severe'
}

// Is the moon above the horizon at the given ISO time?
export function moonUpAt(iso: string, moonrise: string | null, moonset: string | null): boolean {
  const t    = new Date(iso).getTime()
  const rise = moonrise ? new Date(moonrise).getTime() : null
  const set  = moonset  ? new Date(moonset).getTime()  : null
  if (rise && set) return rise < set ? (t >= rise && t <= set) : (t >= rise || t <= set)
  if (rise) return t >= rise
  if (set)  return t <= set
  return false
}

// ── Combined light-pollution display string (mirrors format_ctx.lp_str) ──────
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

// YYYY-MM-DD in the viewer's *local* timezone (not UTC). Using toISOString()
// here would yield the UTC date, which is a day ahead once it's past evening in
// the Americas — so the picker/initial load would jump to "tomorrow".
export function toIsoDate(d: Date): string {
  const year  = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day   = String(d.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function todayIso(): string {
  return toIsoDate(new Date())
}

// The date the *current night* belongs to — which is yesterday when it's past
// midnight but before noon (sunrise hasn't happened yet for any US location).
// Use this for default queries so a photographer opening the app at 2 AM sees
// the night they're standing in, not tomorrow's upcoming observations.
export function tonightIso(): string {
  const now = new Date()
  if (now.getHours() < 6) {
    const d = new Date(now)
    d.setDate(d.getDate() - 1)
    return toIsoDate(d)
  }
  return toIsoDate(now)
}

export function addDaysIso(iso: string, days: number): string {
  const d = new Date(iso + 'T00:00:00')
  d.setDate(d.getDate() + days)
  return toIsoDate(d)
}

// Inclusive night count spanning [startIso, endIso].
export function daySpan(startIso: string, endIso: string): number {
  const s = new Date(startIso + 'T00:00:00')
  const e = new Date(endIso + 'T00:00:00')
  return Math.round((e.getTime() - s.getTime()) / 86_400_000) + 1
}

// Weather forecast (7-day) and satellite TLE accuracy ([0,10]-day) horizons —
// mirrors apps/api's fetch limits. Shared by App.tsx's form-level gating and
// ReportCard's per-date "View Details" gating so the two never drift.
export function availabilityFor(dateIso: string): { wxUnavail: boolean; satUnavail: boolean } {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const d = new Date(dateIso + 'T00:00:00')
  const days = Math.round((d.getTime() - today.getTime()) / 86_400_000)
  return { wxUnavail: days > 7, satUnavail: days < 0 || days > 10 }
}
