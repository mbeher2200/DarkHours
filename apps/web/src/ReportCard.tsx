import React, { useState } from 'react'
import type { NightReport, WeatherPoint, VisibleTarget, TargetWindow, MilkyWaySummary, NearbyResult, NearbyPlace } from './types'
import {
  formatTime, formatHm, tzAbbr, tzTitle,
  cardinal, rateConditions, fmtTemp, fmtWind, fmtDist, lpString,
  scoreBand, scoreLabel, moonWashSeverity, moonUpAt,
} from './format'
import { fetchNearby, ApiRequestError } from './api'
import {
  Sunrise, Sunset, Moon, Star, Stars,
  MoonStar, CloudMoon, Cloudy, CloudFog, CloudDrizzle, CloudHail,
  CloudRain, CloudRainWind, CloudSnow, Snowflake, CloudMoonRain, CloudLightning,
  type LucideIcon,
} from 'lucide-react'

// ── WMO weather code icons ───────────────────────────────────────────────────

const WMO_ICONS: Record<number, LucideIcon> = {
  0: Stars,        1: MoonStar,      2: CloudMoon,    3: Cloudy,
  45: CloudFog,   48: CloudFog,
  51: CloudDrizzle, 53: CloudDrizzle, 55: CloudDrizzle,
  56: CloudHail,  57: CloudHail,
  61: CloudRain,  63: CloudRain,     65: CloudRainWind,
  66: CloudHail,  67: CloudHail,
  71: CloudSnow,  73: CloudSnow,     75: CloudSnow,   77: Snowflake,
  80: CloudMoonRain, 81: CloudMoonRain, 82: CloudRainWind,
  85: CloudSnow,  86: CloudSnow,
  95: CloudLightning, 96: CloudLightning, 99: CloudLightning,
}

function WmoIcon({ code, size = 19 }: { code: number | null; size?: number }) {
  if (code == null) return null
  const Icon = WMO_ICONS[code]
  if (!Icon) return null
  return <Icon size={size} strokeWidth={1.5} style={{ flexShrink: 0 }} />
}

// Alt/Az in standard format: "42° alt · 195° (S)"
function fmtPos(altDeg: number, azDeg: number): string {
  return `${Math.round(altDeg)}° alt · ${Math.round(azDeg)}° (${cardinal(azDeg)})`
}

// ── Moon phase image (NASA SVS) ──────────────────────────────────────────────
// Uses NASA Scientific Visualization Studio 1024×1024 phase images
// (public domain, downloaded to /moon-phases/).

function MoonPhaseSvg({ phaseName, size = 29 }: {
  phaseName: string
  illuminationPct?: number   // kept for API compat; image handles accuracy
  size?: number
}) {
  const p   = phaseName.toLowerCase()
  const src = p.includes('new')                               ? '/moon-phases/new.jpg'
            : p.includes('waxing') && p.includes('crescent') ? '/moon-phases/waxing-crescent.jpg'
            : p.includes('first')                             ? '/moon-phases/first-quarter.jpg'
            : p.includes('waxing') && p.includes('gibbous')  ? '/moon-phases/waxing-gibbous.jpg'
            : p.includes('full')                              ? '/moon-phases/full.jpg'
            : p.includes('waning') && p.includes('gibbous')  ? '/moon-phases/waning-gibbous.jpg'
            : p.includes('last') || p.includes('third')      ? '/moon-phases/last-quarter.jpg'
            : p.includes('waning') && p.includes('crescent') ? '/moon-phases/waning-crescent.jpg'
            : '/moon-phases/full.jpg'

  return (
    <img
      src={src}
      alt={phaseName}
      width={size}
      height={size}
      style={{ borderRadius: '50%', display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
    />
  )
}

// ── Weather table ────────────────────────────────────────────────────────────

function WeatherTable({ points, tz, imperial }: { points: WeatherPoint[]; tz: string; imperial: boolean }) {
  const hasTemp   = points.some(p => p.temperature_c   != null)
  const hasDew    = points.some(p => p.dew_point_c     != null)
  const hasSeeing = points.some(p => p.seeing_arcsec   != null)
  const hasTransp = points.some(p => p.transparency    != null)

  return (
    <details className="wx-details" open>
      <summary>Weather ({points.length} hours)</summary>
      <div className="wx-table-wrap">
        <table className="wx-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Rating</th>
              <th>Cloud</th>
              {hasSeeing && <th>Seeing</th>}
              {hasTransp && <th>Transparency</th>}
              {hasTemp   && <th>Temp</th>}
              {hasDew    && <th>Dew Pt</th>}
              <th>Humidity</th>
              <th>Wind</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={i}>
                <td className="wx-time">{formatTime(p.time, tz)}</td>
                <td className="wx-num">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <WmoIcon code={p.weather_code} />
                    <span style={{ display: 'inline-block', minWidth: '2ch', textAlign: 'right' }}>{rateConditions(p)}</span>/10
                  </span>
                </td>
                <td className="wx-num">{p.cloud_cover_pct != null ? `${p.cloud_cover_pct}%` : '—'}</td>
                {hasSeeing && (
                  <td className="wx-num">
                    {p.seeing_arcsec != null
                      ? `${Math.max(1, Math.min(10, Math.round((3.0 - p.seeing_arcsec) / 2.6 * 10)))}/10 (${p.seeing_arcsec.toFixed(2)}")`
                      : '—'}
                  </td>
                )}
                {hasTransp && (
                  <td className="wx-num">
                    {p.transparency != null
                      ? `${{ Excellent: 10, Good: 8, Fair: 4, Poor: 1 }[p.transparency] ?? 5}/10`
                      : '—'}
                  </td>
                )}
                {hasTemp   && <td className="wx-num">{fmtTemp(p.temperature_c, imperial)}</td>}
                {hasDew    && <td className="wx-num">{fmtTemp(p.dew_point_c, imperial)}</td>}
                <td className="wx-num">{p.humidity_pct != null ? `${p.humidity_pct}%` : '—'}</td>
                <td className="wx-num">{fmtWind(p.wind_speed_ms, p.wind_direction_deg, imperial)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

// ── Score bar ────────────────────────────────────────────────────────────────

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(100, value * 10))
  return (
    <div className="bar-row">
      <span className="bar-label">{label}</span>
      <span className="bar-track">
        <span className={`bar-fill band-${scoreBand(value)}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="bar-value">{value.toFixed(1)}</span>
    </div>
  )
}

// ── Metadata row ─────────────────────────────────────────────────────────────

function MetaRow({ k, v, icon }: { k: string; v: string; icon?: React.ReactNode }) {
  return (
    <div className="meta-row">
      <span className="meta-k">{k}:</span>
      <span className="meta-v" style={icon ? { display: 'inline-flex', alignItems: 'center', gap: 6 } : undefined}>
        {icon}{v}
      </span>
    </div>
  )
}

// ── Satellite passes ─────────────────────────────────────────────────────────

function SatellitePasses({ report }: { report: NightReport }) {
  const tz = report.tz_name

  // Unavailability notices
  if (report.sat_network_error) {
    return <p className="sat-notice">ISS data unavailable — could not reach Celestrak and no cached TLE exists.</p>
  }
  if (report.sat_stale) {
    return <p className="sat-notice">Satellite pass predictions require a current TLE — historical dates are not supported.</p>
  }
  if (report.sat_future_stale) {
    return <p className="sat-notice">ISS pass predictions longer than 7 days are highly inaccurate and TLE data cannot accurately predict this date.</p>
  }

  const notes: string[] = []
  if (report.sat_tle_stale)   notes.push('Note: Using cached TLE data (Celestrak unreachable) — pass times may be slightly off.')
  if (report.sat_future_warn) notes.push('Note: Pass times are approximate — TLE accuracy is limited beyond ~3 days.')

  const visible  = report.sat_passes.filter(p => p.in_sunlight && p.sky_dark)
  const twilight = report.sat_passes.filter(p => p.in_sunlight && !p.sky_dark)
  const shadow   = report.sat_passes.filter(p => !p.in_sunlight)
  const display  = [...visible, ...twilight].sort((a, b) => a.rise_time.localeCompare(b.rise_time))

  const trains = report.starlink_trains ?? []

  const hasAny = display.length > 0 || trains.length > 0

  if (!hasAny) {
    const shadowMsg = shadow.length > 0
      ? `${shadow.length} pass${shadow.length > 1 ? 'es' : ''} tonight but in Earth's shadow — not visible.`
      : 'No visible satellite passes this night.'
    return (
      <>
        {notes.map((n, i) => <p key={i} className="sat-notice sat-note">{n}</p>)}
        <p className="sat-notice">{shadowMsg}</p>
        </>
    )
  }

  const az = (deg: number) => `${deg.toFixed(0)}°(${cardinal(deg)})`

  return (
    <>
      {notes.map((n, i) => <p key={i} className="sat-notice sat-note">{n}</p>)}

      {trains.length > 0 && (
        <div className="sat-trains">
          <div className="sat-trains-label">Starlink Train{trains.length > 1 ? 's' : ''}:</div>
          {trains.map((tr, i) => {
            const moonStr = tr.moon_sep_deg != null ? `  ·  Moon Sep: ${tr.moon_sep_deg.toFixed(1)}°` : ''
            const skyTag  = !tr.sky_dark ? '  [civil twilight]' : ''
            const daysAgo = tr.launch_date
              ? `  ·  launched ${Math.round((new Date(report.date).getTime() - new Date(tr.launch_date).getTime()) / 86400000)}d ago`
              : ''
            return (
              <div key={i} className="sat-train-row">
                {tr.satellite_count} satellites{daysAgo}{skyTag}  ·  {formatTime(tr.first_rise, tz)} – {formatTime(tr.last_rise, tz)}  ·  Peak {tr.peak_alt_deg.toFixed(0)}°  ·  from {az(tr.lead_az_deg)}{moonStr}
              </div>
            )
          })}
        </div>
      )}

      {display.length > 0 && (
        <div className="wx-table-wrap">
          <table className="wx-table sat-table">
            <thead>
              <tr>
                <th>Satellite</th>
                <th colSpan={3}>Rise</th>
                <th colSpan={3}>Peak</th>
                <th colSpan={3}>Set</th>
                <th>Dur</th>
                <th>Moon Sep</th>
              </tr>
              <tr className="sat-subhdr">
                <th></th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th></th><th></th>
              </tr>
            </thead>
            <tbody>
              {display.map((p, i) => {
                const label    = p.satellite_name + (!p.sky_dark ? ' †' : '')
                const setAlt   = `${p.set_alt_deg.toFixed(0)}°${p.ends_in_shadow ? '*' : ''}`
                const moonStr  = p.moon_transit
                  ? `TRANSIT ${p.moon_transit_sep_deg?.toFixed(3)}°`
                  : p.moon_sep_deg != null
                    ? `${p.moon_sep_deg.toFixed(1)}°`
                    : '—'
                return (
                  <tr key={i}>
                    <td>{label}</td>
                    <td className="wx-num">{formatTime(p.rise_time, tz)}</td>
                    <td className="wx-num">{p.rise_alt_deg.toFixed(0)}°</td>
                    <td className="wx-num">{az(p.rise_az_deg)}</td>
                    <td className="wx-num">{formatTime(p.peak_time, tz)}</td>
                    <td className="wx-num">{p.peak_alt_deg.toFixed(0)}°</td>
                    <td className="wx-num">{az(p.peak_az_deg)}</td>
                    <td className="wx-num">{formatTime(p.set_time, tz)}</td>
                    <td className="wx-num">{setAlt}</td>
                    <td className="wx-num">{az(p.set_az_deg)}</td>
                    <td className="wx-num">{p.duration_min.toFixed(0)}m</td>
                    <td className="wx-num">{moonStr}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {(display.some(p => p.ends_in_shadow) || twilight.length > 0 || shadow.length > 0) && (
        <div className="sat-footnotes">
          {display.some(p => p.ends_in_shadow) && <div>* Set alt &gt; 10° — satellite entered Earth's shadow before geometric set</div>}
          {twilight.length > 0 && <div>† Pass during civil twilight — sky too bright to observe</div>}
          {shadow.length > 0 && <div>+{shadow.length} pass{shadow.length > 1 ? 'es' : ''} in Earth's shadow (not visible)</div>}
        </div>
      )}
    </>
  )
}

// ── Targets helpers ──────────────────────────────────────────────────────────

const TYPE_ORDER: Record<string, number> = {
  meteor_shower: 0, cluster: 1, planet: 2, nebula: 3, galaxy: 4,
}
const TYPE_LABELS: Record<string, string> = {
  meteor_shower: 'Meteor Showers', cluster: 'Clusters',
  planet: 'Planets', nebula: 'Nebulae', galaxy: 'Galaxies',
}

// ── Milky Way card ───────────────────────────────────────────────────────────

function MilkyWayCard({ summary, waypoints, report }: {
  summary: MilkyWaySummary
  waypoints: VisibleTarget[]
  report: NightReport
}) {
  const tz = report.tz_name
  const s  = summary

  const archQuality = s.arch_angle_deg != null
    ? (s.arch_angle_deg >= 60 ? 'steep' : s.arch_angle_deg >= 35 ? 'moderate' : 'flat')
    : null

  const bestLabel = s.core_peak_in_window ? 'Best time' : 'Best before'
  const bestTime  = s.core_peak_in_window ? s.core_peak_time : s.arch_end

  return (
    <div className="mw-card">
      {/* Score row */}
      <div className="mw-score-row">
        <span className="mw-score">{s.local_score.toFixed(1)}<span className="mw-score-denom">/10</span></span>
        <div className="mw-sub-scores">
          <span>Altitude {s.alt_score.toFixed(1)}/10</span>
          <span>Coverage {s.cov_score.toFixed(1)}/10</span>
          <span>Window {s.win_score.toFixed(1)}/10</span>
          {s.moon_penalised && <span className="mw-moon-flag">· moon penalty</span>}
        </div>
      </div>

      {/* Arch window row */}
      <div className="mw-row">
        <span className="mw-label">Arch window</span>
        <span>
          {formatTime(s.arch_start, tz)} – {formatTime(s.arch_end, tz)}
          {'  ·  '}{Math.floor(s.arch_hours)}h {Math.round((s.arch_hours % 1) * 60).toString().padStart(2,'0')}m
          {s.moon_limited && <span className="mw-moon-flag">  · moon-limited</span>}
        </span>
      </div>

      {/* Core row */}
      <div className="mw-row">
        <span className="mw-label">Galactic core</span>
        <span>
          {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)} (max {s.core_max_alt_deg}° alt)
          {archQuality && s.arch_angle_deg != null && `  ·  arch ${s.arch_angle_deg.toFixed(0)}° (${archQuality})`}
        </span>
      </div>

      {/* Best time row */}
      <div className="mw-row">
        <span className="mw-label">{bestLabel}</span>
        <span>
          {formatTime(bestTime, tz)} — core @ {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)}
          {s.farthest_name && s.farthest_peak_alt_deg != null && (
            <>,&nbsp;arch to {s.farthest_name} @ {fmtPos(s.farthest_peak_alt_deg, s.farthest_peak_az_deg ?? 0)}</>
          )}
        </span>
      </div>

      {/* Waypoints table */}
      {waypoints.length > 0 && (
        <div className="mw-waypoints">
          <div className="mw-waypoints-label">
            Waypoints visible: {s.n_visible} of {s.n_max_possible} possible ({s.n_total} total)
          </div>
          <div className="tg-table-wrap">
            <table className="tg-table">
              <thead>
                <tr>
                  <th>Waypoint</th>
                  <th>Peak</th>
                  <th>Arch angle</th>
                  <th>Window</th>
                  <th>Sky</th>
                </tr>
              </thead>
              <tbody>
                {waypoints.map(t => {
                  const w = bestWindow(t)
                  const archNote = w.arch_angle_deg != null
                    ? `${w.arch_angle_deg.toFixed(0)}° (${w.arch_angle_deg >= 60 ? 'steep' : w.arch_angle_deg >= 35 ? 'moderate' : 'flat'})`
                    : '—'
                  const sky = w.peak_time
                    ? skyCondition(w.peak_time, report.dark_intervals, report.night_start, report.night_end,
                        report.illumination_pct, report.moonrise, report.moonset,
                        w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg)
                    : '—'
                  return (
                    <tr key={t.name}>
                      <td>{t.name}{t.note ? <span className="tg-note"> · {t.note}</span> : null}</td>
                      <td className="wx-num">
                        {w.peak_time ? `${formatTime(w.peak_time, tz)} @ ${fmtPos(w.peak_alt_deg!, w.peak_az_deg)}` : '—'}
                      </td>
                      <td className="wx-num">{archNote}</td>
                      <td className="wx-num">
                        {w.peak_time ? `${formatTime(w.start, tz)} – ${formatTime(w.end, tz)}` : '—'}
                      </td>
                      <td className={`tg-sky ${sky.startsWith('Moon') ? 'tg-sky-moon-wash' : `tg-sky-${sky.replace(' ', '-').toLowerCase()}`}`}>{sky}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function bestWindow(t: VisibleTarget): TargetWindow {
  const clean = t.windows.filter(w => !w.moon_interference)
  const pool  = clean.length ? clean : t.windows
  return pool.reduce((best, w) => (w.peak_alt_deg ?? 0) > (best.peak_alt_deg ?? 0) ? w : best)
}

// Sky condition at a given ISO time, incorporating K&S moon wash (mirrors CLI)
function skyCondition(
  peakIso: string,
  darkIntervals: [string, string][],
  nightStart: string | null,
  nightEnd: string | null,
  illuminationPct: number,
  moonrise: string | null,
  moonset:  string | null,
  moonSepAtPeak:  number | null,
  moonAltAtPeak:  number | null,
): string {
  const pt = new Date(peakIso).getTime()

  let base = 'Twilight'
  for (const [s, e] of darkIntervals) {
    if (pt >= new Date(s).getTime() && pt <= new Date(e).getTime()) { base = 'Dark sky'; break }
  }
  if (base === 'Twilight' && nightStart && nightEnd) {
    const ns = new Date(nightStart).getTime(), ne = new Date(nightEnd).getTime()
    if (pt >= ns && pt <= ne) base = 'Astro night'
  }

  if (moonUpAt(peakIso, moonrise, moonset)) {
    const sev = moonWashSeverity(illuminationPct, moonSepAtPeak, moonAltAtPeak)
    if (sev) return `Moon wash (${sev})`
  }
  return base
}

// Interpolate altitude at a clipped time (mirrors _alt_at in render_report.py)
function altAt(cutoffIso: string, w: TargetWindow): number {
  if (!w.peak_time || w.peak_alt_deg == null) return w.start_alt_deg
  const t     = new Date(cutoffIso).getTime()
  const tPeak = new Date(w.peak_time).getTime()
  const t0    = t <= tPeak ? new Date(w.start).getTime()    : tPeak
  const a0    = t <= tPeak ? w.start_alt_deg                : w.peak_alt_deg
  const t1    = t <= tPeak ? tPeak                          : new Date(w.end).getTime()
  const a1    = t <= tPeak ? w.peak_alt_deg                 : w.end_alt_deg
  const frac  = t1 > t0 ? (t - t0) / (t1 - t0) : 0.5
  return Math.round(a0 + Math.max(0, Math.min(1, frac)) * (a1 - a0))
}

// Mirrors targets.is_prime(): DSOs need ≥40° peak alt, planets ≥20°, all need ≥1h window.
// Falls back to dark-interval overlap when all windows are moon-interfered.
function isPrime(t: VisibleTarget, darkIntervals: [string, string][]): boolean {
  const MIN_ALT = 40, PLANET_MIN_ALT = 20, MIN_HRS = 1.0
  const effAlt = t.type === 'planet' ? PLANET_MIN_ALT : MIN_ALT
  const clean = t.windows.filter(w => !w.moon_interference)

  if (clean.length === 0) {
    if (darkIntervals.length === 0) return false
    for (const w of t.windows) {
      const ws = new Date(w.start).getTime(), we = new Date(w.end).getTime()
      for (const [ds, de] of darkIntervals) {
        const oS = Math.max(ws, new Date(ds).getTime())
        const oE = Math.min(we, new Date(de).getTime())
        if ((oE - oS) / 3_600_000 >= MIN_HRS) {
          return t.type === 'milky_way' || (w.peak_alt_deg ?? 0) >= effAlt
        }
      }
    }
    return false
  }

  const best = clean.reduce((a, b) => (b.peak_alt_deg ?? 0) > (a.peak_alt_deg ?? 0) ? b : a)
  const durH = (new Date(best.end).getTime() - new Date(best.start).getTime()) / 3_600_000
  return t.type === 'milky_way'
    ? durH >= MIN_HRS
    : (best.peak_alt_deg ?? 0) >= effAlt && durH >= MIN_HRS
}

// ── Meteor shower card ───────────────────────────────────────────────────────

function MeteorShowerCard({ target, zhr, report }: {
  target: VisibleTarget
  zhr: number
  report: NightReport
}) {
  const tz = report.tz_name
  const w  = bestWindow(target)

  const sky = w.peak_time
    ? skyCondition(
        w.peak_time, report.dark_intervals, report.night_start, report.night_end,
        report.illumination_pct, report.moonrise, report.moonset,
        w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg,
      )
    : null
  const skyCls = sky
    ? sky.startsWith('Moon') ? 'tg-sky-moon-wash' : `tg-sky-${sky.replace(' ', '-').toLowerCase()}`
    : ''

  return (
    <div className="ms-card">
      <div className="ms-header-row">
        <span className="ms-name">{target.name} Meteor Shower</span>
        <span className="ms-zhr">Peak ZHR {zhr}</span>
      </div>
      {target.note && <div className="ms-note">{target.note}</div>}
      {w.peak_time && w.peak_alt_deg != null && (
        <>
          <div className="mw-row">
            <span className="mw-label">Best viewing</span>
            <span>{formatTime(w.peak_time, tz)} @ {fmtPos(w.peak_alt_deg, w.peak_az_deg)}</span>
          </div>
          <div className="mw-row">
            <span className="mw-label">Window</span>
            <span>
              {formatTime(w.start, tz)} – {formatTime(w.end, tz)}
              {'  ·  '}{w.start_alt_deg.toFixed(0)}° → {w.end_alt_deg.toFixed(0)}°
            </span>
          </div>
          {sky && (
            <div className="mw-row">
              <span className="mw-label">Sky</span>
              <span className={`tg-sky ${skyCls}`}>{sky}</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function TargetsTable({ targets, report }: { targets: VisibleTarget[]; report: NightReport }) {
  const tz = report.tz_name

  // Milky Way + meteor showers rendered separately as cards; rest filtered to prime
  const nonMW = targets
    .filter(t => t.type !== 'milky_way' && t.type !== 'meteor_shower')
    .filter(t => isPrime(t, report.dark_intervals))

  const sorted = [...nonMW].sort((a, b) => {
    const ao = TYPE_ORDER[a.type] ?? 99
    const bo = TYPE_ORDER[b.type] ?? 99
    if (ao !== bo) return ao - bo
    const at = bestWindow(a).peak_time ?? ''
    const bt = bestWindow(b).peak_time ?? ''
    return at.localeCompare(bt)
  })

  // Group by type
  const groups: { type: string; targets: VisibleTarget[] }[] = []
  for (const t of sorted) {
    const last = groups[groups.length - 1]
    if (last && last.type === t.type) last.targets.push(t)
    else groups.push({ type: t.type, targets: [t] })
  }

  // Flatten groups into a single list of row descriptors for the table
  type RowItem =
    | { kind: 'header'; type: string; key: string }
    | { kind: 'target'; target: VisibleTarget; key: string }

  if (sorted.length === 0) return null

  const rows: RowItem[] = []
  for (const g of groups) {
    rows.push({ kind: 'header', type: g.type, key: `hdr-${g.type}` })
    for (const t of g.targets) {
      rows.push({ kind: 'target', target: t, key: `${g.type}-${t.name}` })
    }
  }

  return (
    <div className="tg-table-wrap">
      <table className="tg-table">
        <thead>
          <tr>
            <th>Target</th>
            <th>Best Viewing</th>
            <th>Sky</th>
            <th>Astro Window</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            if (row.kind === 'header') {
              return (
                <tr key={row.key} className="tg-group-hdr">
                  <td colSpan={4}>{TYPE_LABELS[row.type] ?? row.type}</td>
                </tr>
              )
            }

            const t    = row.target
            const w    = bestWindow(t)
            const name = t.type === 'meteor_shower' ? `${t.name} Meteor Shower` : t.name

            // photo_cutoff clips both Best Viewing and Astro Window (mirrors CLI)
            const hasClip = !!(w.photo_cutoff
              && new Date(w.photo_cutoff) > new Date(w.start)
              && new Date(w.photo_cutoff) < new Date(w.end))

            let bestView = '—'
            if (w.peak_time && w.peak_alt_deg != null) {
              const bestTime = hasClip ? w.photo_cutoff! : w.peak_time
              const bestAlt  = hasClip ? altAt(w.photo_cutoff!, w) : Math.round(w.peak_alt_deg)
              bestView = `${formatTime(bestTime, tz)} @ ${fmtPos(bestAlt, w.peak_az_deg)}`
            }

            let winStr = '—'
            if (w.peak_time) {
              const startStr = `${formatTime(w.start, tz)} @ ${w.start_alt_deg.toFixed(0)}°`
              if (hasClip) {
                const clipAlt  = altAt(w.photo_cutoff!, w)
                const visualEnd = w.visual_cutoff ?? w.end
                const extraMs  = new Date(visualEnd).getTime() - new Date(w.photo_cutoff!).getTime()
                const extraMin = Math.round(extraMs / 60000)
                const visNote  = extraMin >= 10 ? `  +${extraMin}m visual` : ''
                winStr = `${startStr} – ${formatTime(w.photo_cutoff!, tz)} @ ${clipAlt}°${visNote}`
              } else {
                winStr = `${startStr} – ${formatTime(w.end, tz)} @ ${w.end_alt_deg.toFixed(0)}°`
              }
            }

            const peakForSky = hasClip ? w.photo_cutoff! : w.peak_time
            const sky = peakForSky
              ? skyCondition(
                  peakForSky, report.dark_intervals, report.night_start, report.night_end,
                  report.illumination_pct, report.moonrise, report.moonset,
                  w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg,
                )
              : '—'

            const skyCls = sky.startsWith('Moon') ? 'tg-sky-moon-wash'
                         : `tg-sky-${sky.replace(' ', '-').toLowerCase()}`
            const moonNote = w.moon_interference && !sky.startsWith('Moon')

            return (
              <tr key={row.key}>
                <td>{name}{t.note ? <span className="tg-note"> · {t.note}</span> : null}</td>
                <td className="wx-num">{bestView}</td>
                <td className={`tg-sky ${skyCls}`}>
                  {sky}{moonNote ? <span className="tg-moon-note"> · moon up</span> : null}
                </td>
                <td className="wx-num">{winStr}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Nearby dark-sky results ──────────────────────────────────────────────────

function NearbyResults({ data, imperial }: { data: NearbyResult; imperial: boolean }) {
  const { origin_bortle, origin_sqm, radius_miles, results, light_domes, best_available } = data
  const sqmStr = origin_sqm != null ? ` (SQM ${origin_sqm.toFixed(1)})` : ''

  // Convert stored miles to the active unit system
  const fmtMi = (mi: number) => fmtDist(mi * 1.60934, imperial)

  if (origin_bortle <= 1) {
    return (
      <p className="sat-notice">
        Already at Bortle {origin_bortle}{sqmStr} — you are at an optimal dark sky.
      </p>
    )
  }

  const placeStr = (p: NearbyPlace) =>
    p.name ?? `${p.lat.toFixed(2)}°, ${p.lon.toFixed(2)}°`
  const driveStr = (p: NearbyPlace) =>
    p.drive_minutes != null ? `~${p.drive_minutes}m drive` : null

  return (
    <>
      <p className="nearby-origin">
        Origin: Bortle {origin_bortle}{sqmStr}  ·  {fmtMi(radius_miles)} radius
      </p>

      {results.length === 0 && (
        <p className="sat-notice">
          No significantly darker sky found within {fmtMi(radius_miles)}.
          {best_available && (
            <> Closest darker spot: Bortle {best_available.bortle_class
            }, {fmtMi(best_available.distance_miles)} {best_available.direction
            }{best_available.drive_minutes != null ? ` · ~${best_available.drive_minutes}m drive` : ''
            }{best_available.name ? ` (${best_available.name})` : ''}</>
          )}
        </p>
      )}

      {results.length > 0 && (() => {
        const nearest = [...results].sort((a, b) => a.distance_miles - b.distance_miles)[0]
        const darkest = [...results].sort((a, b) =>
          a.bortle_class !== b.bortle_class ? a.bortle_class - b.bortle_class : a.distance_miles - b.distance_miles
        )[0]
        const showDarkest = darkest !== nearest && darkest.bortle_class < nearest.bortle_class
        const hasDrive = results.some(p => p.drive_minutes != null)
        return (
          <>
            <div className="nearby-highlights">
              <div className="nearby-highlight-row">
                <span className="nearby-highlight-label">Nearest</span>
                <span>Bortle {nearest.bortle_class}  ·  {fmtMi(nearest.distance_miles)} {nearest.direction}{driveStr(nearest) ? `  ·  ${driveStr(nearest)}` : ''}  ({placeStr(nearest)})</span>
              </div>
              {showDarkest && (
                <div className="nearby-highlight-row">
                  <span className="nearby-highlight-label">Darkest</span>
                  <span>Bortle {darkest.bortle_class}  ·  {fmtMi(darkest.distance_miles)} {darkest.direction}{driveStr(darkest) ? `  ·  ${driveStr(darkest)}` : ''}  ({placeStr(darkest)})</span>
                </div>
              )}
            </div>
            <div className="wx-table-wrap">
              <table className="wx-table">
                <thead>
                  <tr>
                    <th>Area</th>
                    <th>Bortle</th>
                    <th>SQM</th>
                    <th>Distance</th>
                    {hasDrive && <th>Drive</th>}
                    <th>Direction</th>
                  </tr>
                </thead>
                <tbody>
                  {[...results]
                    .sort((a, b) => a.distance_miles - b.distance_miles)
                    .map((p, i) => (
                      <tr key={i}>
                        <td>{placeStr(p)}</td>
                        <td className="wx-num">{p.bortle_class}</td>
                        <td className="wx-num">{p.sqm != null ? p.sqm.toFixed(1) : '—'}</td>
                        <td className="wx-num">{fmtMi(p.distance_miles)}</td>
                        {hasDrive && <td className="wx-num">{driveStr(p) ?? '—'}</td>}
                        <td className="wx-num">{p.direction}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </>
        )
      })()}

      {light_domes.length > 0 && (
        <div className="nearby-domes">
          <div className="nearby-domes-label">Light domes</div>
          {light_domes.map((d, i) => (
            <div key={i} className="nearby-dome-row">
              {placeStr(d)}  ·  Bortle {d.bortle_class}  ·  {fmtMi(d.distance_miles)} {d.direction}
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// ── Main report card ─────────────────────────────────────────────────────────

export default function ReportCard({
  report,
  showWeather = false,
  showTargets = false,
  showSatellites = false,
  imperial = false,
}: {
  report: NightReport
  showWeather?: boolean
  showTargets?: boolean
  showSatellites?: boolean
  imperial?: boolean
}) {
  const [nearbyState, setNearbyState] = useState<
    | { phase: 'idle' }
    | { phase: 'loading'; radius: number }
    | { phase: 'done'; data: NearbyResult }
    | { phase: 'error'; message: string }
  >({ phase: 'idle' })

  async function handleFindNearby(radius: number) {
    setNearbyState({ phase: 'loading', radius })
    try {
      const data = await fetchNearby(report.lat, report.lon, radius)
      setNearbyState({ phase: 'done', data })
    } catch (err) {
      setNearbyState({
        phase: 'error',
        message: err instanceof ApiRequestError ? err.message : 'Nearby search failed.',
      })
    }
  }

  const r   = report
  const tz  = r.tz_name
  const lp  = r.light_pollution
  const lps = lpString(lp)
  const tzZ = r.sunset ? tzAbbr(tz) : tz

  // Moon line
  const distStr     = fmtDist(r.moon_distance_km, imperial)
  const specialTags = []
  if (r.moon_special) specialTags.push(`*** ${r.moon_special.charAt(0).toUpperCase() + r.moon_special.slice(1)} ***`)
  for (const e of r.moon_eclipses ?? []) {
    const kind = e.kind.charAt(0).toUpperCase() + e.kind.slice(1)
    const mag  = (e.kind === 'partial' || e.kind === 'total')
      ? `umbral ${e.umbral_magnitude?.toFixed(3)}`
      : `penumbral ${e.penumbral_magnitude?.toFixed(3)}`
    specialTags.push(`${kind} lunar eclipse at ${formatTime(e.time, tz)}  (mag ${mag})`)
  }
  const moonStr = `${r.phase_name}  |  ${r.illumination_pct.toFixed(1)}% illuminated  |  ${distStr}`
    + (specialTags.length ? `  ·  ${specialTags.join('  ·  ')}` : '')

  // Dark sky hours line
  let darkStr: string
  if (r.dark_intervals.length > 0) {
    const spans = r.dark_intervals
      .map(([s, e]) => `${formatTime(s, tz)} – ${formatTime(e, tz)}`)
      .join(',  ')
    darkStr = `${formatHm(r.dark_hours)}  (${spans} ${tzZ})`
  } else if (r.night_start && r.night_end) {
    darkStr = 'None (moon up all night)'
  } else {
    darkStr = 'None (no astronomical darkness at this latitude/date)'
  }
  if (r.dark_cycle) {
    darkStr += `  ·  avg ${r.dark_cycle.mean_hours}h  ±${r.dark_cycle.stdev_hours}h over lunar cycle`
  }

  // Score components line
  const compMap: Record<string, string> = { moon: 'Lunar', dark: 'Dark Hours', weather: 'Weather', bortle: 'Bortle' }
  const compOrder = ['moon', 'dark', 'weather', 'bortle']
  const compParts = compOrder
    .filter(k => r.score_components[k as keyof typeof r.score_components] != null)
    .map(k => `${compMap[k]} ${(r.score_components[k as keyof typeof r.score_components] as number).toFixed(1)}`)
  const scoreLineDetail = compParts.length ? `  (${compParts.join('  ·  ')})` : ''

  return (
    <section className="card report">
      <header className="report-head">
        <h2 className="place">{r.display_name}</h2>
        <p className="when">
          {r.date}  ·  {tzTitle(tz)}  ·  ({r.lat.toFixed(4)}°, {r.lon.toFixed(4)}°)
        </p>
      </header>

      <div className={`overall band-${scoreBand(r.score)}`}>
        <div className="overall-num">{r.score.toFixed(1)}</div>
        <div className="overall-meta">
          <div className="overall-label">{scoreLabel(r.score)}</div>
          <div className="overall-sub">
            Night Quality Score · out of 10{scoreLineDetail}
          </div>
        </div>
      </div>

      <div className="bars">
        {r.score_components.moon    != null && <ScoreBar label="Lunar"         value={r.score_components.moon} />}
        {r.score_components.dark    != null && <ScoreBar label="Dark Hours"    value={r.score_components.dark} />}
        {r.score_components.bortle  != null && <ScoreBar label="Bortle"        value={r.score_components.bortle} />}
        {showWeather && r.score_components.weather != null && <ScoreBar label="Weather" value={r.score_components.weather} />}
      </div>

      <div className="meta">
        {lps && <MetaRow k="Light Pollution" v={lps} />}
        <MetaRow k="Moon" v={moonStr}
          icon={<MoonPhaseSvg phaseName={r.phase_name} illuminationPct={r.illumination_pct} />}
        />
        {(r.active_showers?.length ?? 0) > 0 && (
          <MetaRow
            k="Meteor Showers"
            v={r.active_showers.map(s => `${s.name}  ·  ${s.note}  ·  ZHR ${s.zhr}`).join(',  ')}
          />
        )}
        <MetaRow k="Clear Dark Sky" v={darkStr} />
        {showWeather && r.weather_score != null && (
          <MetaRow
            k="Weather"
            v={`${r.weather_score.toFixed(1)}/10${r.wx_source ? `  [${r.wx_source}]` : ''}`}
          />
        )}
        {showWeather && r.wx_pending && <MetaRow k="Weather" v="Pending  (beyond the ~7-day forecast horizon)" />}
        {showWeather && r.wx_no_data && <MetaRow k="Weather" v="No data  (not covered for this location/date)" />}
        {showWeather && r.wx_error && !r.weather_points.length && <MetaRow k="Weather" v="Temporarily unavailable — weather providers are down" />}
      </div>

      {r.events.length > 0 && (
        <details className="events" open>
          <summary>Night Timeline</summary>
          <div className="ev-table">
            {r.events.map((e, i) => {
              const l = e.label.toLowerCase()
              const ip = { size: 19, strokeWidth: 1.5, style: { flexShrink: 0, opacity: 0.7, verticalAlign: 'middle' } } as const
              const icon = l.includes('sunrise')                    ? <Sunrise {...ip} />
                         : l.includes('sunset')                     ? <Sunset  {...ip} />
                         : l.includes('moonrise') || l.includes('moonset') ? <Moon {...ip} />
                         : l.includes('astronomical night')         ? <Stars   {...ip} />
                         : l.includes('twilight')                   ? <Star    {...ip} />
                         : null
              return (
                <div key={i} className="ev-row">
                  <span className="ev-time">{formatTime(e.time, tz)}</span>
                  {icon && <span className="ev-icon">{icon}</span>}
                  <span className="ev-label">{e.label}</span>
                </div>
              )
            })}
          </div>
        </details>
      )}

      {showWeather && r.weather_points.length > 0 && (
        <WeatherTable points={r.weather_points} tz={tz} imperial={imperial} />
      )}

      {showTargets && (() => {
        const showerTargets  = r.visible_targets.filter(t => t.type === 'meteor_shower')
        const primeCount     = r.visible_targets.filter(t => isPrime(t, r.dark_intervals)).length
        const hasAnything    = r.visible_targets.length > 0

        return (
        <details className="targets" open>
          <summary>
            Prime Targets
            {primeCount > 0 ? ` (${primeCount})` : ''}
          </summary>
          {!hasAnything
            ? <p className="sat-notice" style={{ paddingTop: 10 }}>No prime targets for this night.</p>
            : <>
                {r.mw_summary && (
                  <div className="mw-section">
                    <div className="mw-section-label">Milky Way</div>
                    <MilkyWayCard
                      summary={r.mw_summary}
                      waypoints={r.visible_targets.filter(t => t.type === 'milky_way')}
                      report={r}
                    />
                  </div>
                )}
                {showerTargets.length > 0 && (
                  <div className="ms-section">
                    <div className="mw-section-label">
                      Meteor Shower{showerTargets.length > 1 ? 's' : ''}
                    </div>
                    <div className="ms-cards">
                      {showerTargets.map(t => (
                        <MeteorShowerCard
                          key={t.name}
                          target={t}
                          zhr={r.active_showers.find(s => s.name === t.name)?.zhr ?? 0}
                          report={r}
                        />
                      ))}
                    </div>
                  </div>
                )}
                <TargetsTable targets={r.visible_targets} report={r} />
                {primeCount === 0 && !r.mw_summary && showerTargets.length === 0 && (
                  <p className="sat-notice" style={{ paddingTop: 10 }}>
                    {r.dark_intervals.length === 0
                      ? 'No astronomical darkness this night — moon prevents dark-sky observing.'
                      : 'No targets meet prime criteria (DSOs ≥40°, planets ≥20° peak altitude, ≥1h window) this night.'}
                  </p>
                )}
              </>
          }
        </details>
        )
      })()}

      {showSatellites && (
        <details className="sat-section" open>
          <summary>Satellite Passes</summary>
          <div className="sat-body">
            <SatellitePasses report={r} />
          </div>
        </details>
      )}

      <details className="nearby-section" open={nearbyState.phase !== 'idle'}>
        <summary>Find nearby dark sky</summary>
        <div className="nearby-body">
          {nearbyState.phase === 'idle' && (
            <div className="nearby-radius-toggle">
              <button className="nearby-trigger" onClick={() => handleFindNearby(60)}>{fmtDist(60 * 1.60934, imperial)}</button>
              <button className="nearby-trigger" onClick={() => handleFindNearby(120)}>{fmtDist(120 * 1.60934, imperial)}</button>
            </div>
          )}
          {nearbyState.phase === 'loading' && (
            <p className="sat-notice">Scanning within {fmtDist(nearbyState.radius * 1.60934, imperial)}…</p>
          )}
          {nearbyState.phase === 'error' && (
            <p className="sat-notice">{nearbyState.message}</p>
          )}
          {nearbyState.phase === 'done' && (
            <NearbyResults data={nearbyState.data} imperial={imperial} />
          )}
        </div>
      </details>
    </section>
  )
}
