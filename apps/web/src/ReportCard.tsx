import React, { useState, useRef, useEffect } from 'react'
import type { NightReport, SkyEvent, WeatherPoint, VisibleTarget, TargetWindow, MilkyWaySummary, NearbyResult, NearbyPlace, LightDomeSummary, Direction } from './types'
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

// Horizontal coordinates in the conventional order: azimuth (with compass point)
// first, then altitude — e.g. "Az 195° S · Alt 42°".
function fmtPos(altDeg: number, azDeg: number): string {
  return `Az ${Math.round(azDeg)}° ${cardinal(azDeg)} · Alt ${Math.round(altDeg)}°`
}

// ── Moon phase image (NASA SVS) ──────────────────────────────────────────────
// Uses NASA Scientific Visualization Studio 1024×1024 phase images
// (public domain, downloaded to /moon-phases/).

function MoonPhaseSvg({ phaseName, size = 22 }: {
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

function WeatherTable({ points, events = [], tz, imperial, darkIntervals }: {
  points: WeatherPoint[]
  events?: SkyEvent[]
  tz: string
  imperial: boolean
  darkIntervals?: [string, string][]
}) {
  // Clip the table to the sunset→sunrise window. Events/points outside this
  // range are daytime and not useful once the astro-night band conveys darkness.
  const sunsetTs  = (() => { const e = events.find(e => e.label.toLowerCase().includes('sunset'));  return e ? new Date(e.time).getTime() : -Infinity })()
  const sunriseTs = (() => { const e = events.find(e => e.label.toLowerCase().includes('sunrise')); return e ? new Date(e.time).getTime() :  Infinity })()
  const visiblePoints = points.filter(p => { const t = new Date(p.time).getTime(); return t >= sunsetTs && t <= sunriseTs })
  const visibleEvents = events.filter(e => {
    const t = new Date(e.time).getTime()
    return t >= sunsetTs && t <= sunriseTs
  })

  const hasTemp   = visiblePoints.some(p => p.temperature_c   != null)
  const hasDew    = visiblePoints.some(p => p.dew_point_c     != null)
  const hasSeeing = visiblePoints.some(p => p.seeing_arcsec   != null)
  const hasTransp = visiblePoints.some(p => p.transparency    != null)
  const hasWx     = visiblePoints.length > 0
  const totalCols = 5 + (hasSeeing ? 1 : 0) + (hasTransp ? 1 : 0) + (hasTemp ? 1 : 0) + (hasDew ? 1 : 0)

  const darkRanges = darkIntervals?.map(([s, e]) => [new Date(s).getTime(), new Date(e).getTime()] as [number, number])

  // Determine moon state at sunset so the Sunset row can always orient the user.
  // Uses the full events array (moon events aren't window-clipped) and falls back to
  // inferring state from the first post-sunset event when no pre-sunset event exists.
  const moonStateAtSunset: 'above' | 'below' | null = (() => {
    if (sunsetTs === -Infinity) return null
    const allMoonEvents = events
      .filter(e => { const l = e.label.toLowerCase(); return l.includes('moonrise') || l.includes('moonset') })
      .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
    if (allMoonEvents.length === 0) return null
    const lastBefore = [...allMoonEvents].reverse().find(e => new Date(e.time).getTime() <= sunsetTs)
    if (lastBefore) return lastBefore.label.toLowerCase().includes('moonrise') ? 'above' : 'below'
    const firstAfter = allMoonEvents.find(e => new Date(e.time).getTime() > sunsetTs)
    if (!firstAfter) return null
    return firstAfter.label.toLowerCase().includes('moonrise') ? 'below' : 'above'
  })()

  type Row = { kind: 'event'; ev: SkyEvent; ts: number } | { kind: 'wx'; pt: WeatherPoint; ts: number }
  const rows: Row[] = [
    ...visibleEvents.map(ev => ({ kind: 'event' as const, ev, ts: new Date(ev.time).getTime() })),
    ...visiblePoints.map(pt => ({ kind: 'wx'    as const, pt, ts: new Date(pt.time).getTime() })),
  ].sort((a, b) => a.ts - b.ts)

  const ip = { size: 12, strokeWidth: 1.5, style: { flexShrink: 0 } } as const
  function evIcon(label: string) {
    const l = label.toLowerCase()
    if (l.includes('sunrise'))                           return <Sunrise {...ip} />
    if (l.includes('sunset'))                            return <Sunset  {...ip} />
    if (l.includes('moonrise') || l.includes('moonset')) return <Moon    {...ip} />
    if (l.includes('astronomical night'))                return <Stars   {...ip} />
    if (l.includes('twilight'))                          return <Star    {...ip} />
    return null
  }
  function evClass(label: string): string {
    const l = label.toLowerCase()
    if (l.includes('sunrise') || l.includes('sunset')) return 'wx-ev-sun'
    if (l.includes('moonrise') || l.includes('moonset')) return 'wx-ev-moon'
    if (l.includes('astronomical night')) return 'wx-ev-astro'
    return ''
  }

  return (
    <details className="wx-details" open>
      <summary>Night Timeline{hasWx ? ` · Weather (${visiblePoints.length} hours)` : ''}</summary>
      <div className="wx-table-wrap">
        <table className="wx-table">
          {hasWx && (
            <thead>
              <tr>
                <th>Time</th>
                <th>Conditions</th>
                <th>Cloud</th>
                {hasTransp && <th>Transp</th>}
                {hasSeeing && <th>Seeing</th>}
                {hasTemp   && <th>Temp</th>}
                {hasDew    && <th>Dew Pt</th>}
                <th>Wind</th>
              </tr>
            </thead>
          )}
          <tbody>
            {rows.map((row, i) => {
              if (row.kind === 'event') {
                const icon    = evIcon(row.ev.label)
                const cls     = evClass(row.ev.label)
                const isSunset = row.ev.label.toLowerCase().includes('sunset')
                return (
                  <tr key={`ev-${i}`} className={`wx-ev-row${cls ? ` ${cls}` : ''}`}>
                    <td className="wx-time wx-ev-time">{formatTime(row.ev.time, tz)}</td>
                    <td colSpan={hasWx ? totalCols - 1 : 1} className="wx-ev-content">
                      <span className="wx-ev-inner">
                        {icon && <span className="wx-ev-icon">{icon}</span>}
                        <span className="wx-ev-label">{row.ev.label}</span>
                        {isSunset && moonStateAtSunset && (
                          <span className="wx-ev-moon-aside">
                            <Moon size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                            <span>{moonStateAtSunset === 'above' ? 'Moon above horizon' : 'Moon below horizon'}</span>
                          </span>
                        )}
                      </span>
                    </td>
                  </tr>
                )
              }
              const p = row.pt
              const isAstro = darkRanges?.some(([s, e]) => row.ts >= s && row.ts <= e) ?? false
              return (
                <tr key={`wx-${i}`} className={isAstro ? 'wx-row-astro' : undefined}>
                  <td className="wx-time">{formatTime(p.time, tz)}</td>
                  <td className={`wx-num wx-rating wx-rating-${scoreBand(rateConditions(p))}`}>
                    <WmoIcon code={p.weather_code} />
                  </td>
                  <td className="wx-num">{p.cloud_cover_pct != null ? `${p.cloud_cover_pct}%` : '—'}</td>
                  {hasTransp && (
                    <td className="wx-num">
                      {p.transparency != null
                        ? `${{ Excellent: 10, Good: 8, Fair: 4, Poor: 1 }[p.transparency] ?? 5}/10`
                        : '—'}
                    </td>
                  )}
                  {hasSeeing && (
                    <td className="wx-num">
                      {p.seeing_arcsec != null
                        ? `${Math.max(1, Math.min(10, Math.round((3.0 - p.seeing_arcsec) / 2.6 * 10)))}/10 (${p.seeing_arcsec.toFixed(2)}")`
                        : '—'}
                    </td>
                  )}
                  {hasTemp   && <td className="wx-num">{fmtTemp(p.temperature_c, imperial)}</td>}
                  {hasDew    && <td className="wx-num">{fmtTemp(p.dew_point_c, imperial)}</td>}
                  <td className="wx-num">{fmtWind(p.wind_speed_ms, p.wind_direction_deg, imperial)}</td>
                </tr>
              )
            })}
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

function SatellitePasses({ report }: { report: NightReport; }) {
  const tz = report.tz_name

  // Unavailability notices
  if (report.sat_network_error) {
    return <p className="sat-notice">Satellite data unavailable: could not reach Celestrak and no cached TLE exists.</p>
  }
  if (report.sat_stale) {
    return <p className="sat-notice">Satellite pass predictions require a current TLE; historical dates are not supported.</p>
  }
  if (report.sat_future_stale) {
    return <p className="sat-notice">Satellite pass predictions longer than 7 days are highly inaccurate and TLE data cannot accurately predict this date.</p>
  }

  const notes: string[] = []
  if (report.sat_tle_stale)   notes.push('Note: Using cached TLE data (Celestrak unreachable). Pass times may be slightly off.')
  if (report.sat_future_warn) notes.push('Note: Pass times are approximate. TLE accuracy degrades beyond 3 days.')

  const visible  = report.sat_passes.filter(p => p.in_sunlight && p.sky_dark)
  const twilight = report.sat_passes.filter(p => p.in_sunlight && !p.sky_dark)
  const shadow   = report.sat_passes.filter(p => !p.in_sunlight)
  const display  = [...visible, ...twilight].sort((a, b) => a.rise_time.localeCompare(b.rise_time))

  const trains = report.starlink_trains ?? []

  const hasAny = display.length > 0 || trains.length > 0

  if (!hasAny) {
    const shadowMsg = shadow.length > 0
      ? `${shadow.length} pass${shadow.length > 1 ? 'es' : ''} tonight but in Earth's shadow: not visible.`
      : 'No visible satellite passes this night.'
    return (
      <>
        {notes.map((n, i) => <p key={i} className="sat-notice sat-note">{n}</p>)}
        <p className="sat-notice">{shadowMsg}</p>
        </>
    )
  }

  const az = (deg: number) => `${deg.toFixed(0)}° ${cardinal(deg)}`

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
                {report.light_dome && <th>Glow</th>}
              </tr>
              <tr className="sat-subhdr">
                <th></th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th></th><th></th>
                {report.light_dome && <th></th>}
              </tr>
            </thead>
            <tbody>
              {display.map((p, i) => {
                const label    = p.satellite_name + (!p.sky_dark ? ' †' : '')
                const setAlt   = `${p.set_alt_deg.toFixed(0)}°${p.ends_in_shadow ? '*' : ''}`
                const moonSepLow = !p.moon_transit && p.moon_sep_deg != null && p.moon_sep_deg < 5
                const moonStr  = p.moon_transit
                  ? `TRANSIT ${p.moon_transit_sep_deg?.toFixed(3)}°`
                  : p.moon_sep_deg != null
                    ? `${p.moon_sep_deg.toFixed(1)}°`
                    : '—'
                const satGlow = report.light_dome
                  ? glowToward(report.light_dome, p.peak_az_deg, p.peak_alt_deg)
                  : null
                const wxAtPeak = wxAtTime(report.weather_points, p.peak_time)
                const satCloudy = wxAtPeak != null && wxAtPeak.cloud_cover_pct != null && wxAtPeak.cloud_cover_pct > 70
                if (satCloudy) return (
                  <tr key={i} className="tg-row-blocked">
                    <td>{label}</td>
                    <td className="wx-num" colSpan={11 + (satGlow != null ? 1 : 0)} style={{textAlign: 'center'}}>
                      <span className="mw-moon-badge badge-poor">[ Clouded out ]</span>
                    </td>
                  </tr>
                )
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
                    <td className="wx-num" style={moonSepLow ? {color: 'var(--excellent)', fontWeight: 700, fontSize: '1rem'} : undefined}>{moonStr}</td>
                    {satGlow != null && (
                      <td className="wx-num cond-glow" style={satGlow >= 0.03 ? glowStyle(satGlow) : undefined}>
                        {satGlow >= 0.03 ? glowLabel(satGlow) : '—'}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {(display.some(p => p.ends_in_shadow) || twilight.length > 0 || shadow.length > 0) && (
        <div className="sat-footnotes">
          {display.some(p => p.ends_in_shadow) && <div>* Set alt &gt; 10°: satellite entered Earth's shadow before geometric set</div>}
          {twilight.length > 0 && <div>† Pass during civil twilight: sky too bright to observe</div>}
          {shadow.length > 0 && <div>+{shadow.length} pass{shadow.length > 1 ? 'es' : ''} in Earth's shadow (not visible)</div>}
        </div>
      )}
    </>
  )
}

// ── Targets helpers ──────────────────────────────────────────────────────────

// nebula / galaxy / cluster are collapsed into a single display group ("dso")
// so the targets table shows a clean unlabeled DSO block followed by Planets.
const DSO_TYPES = new Set(['nebula', 'galaxy', 'cluster'])

const TYPE_ORDER: Record<string, number> = {
  meteor_shower: 0,
  dso:           1,  // nebula + galaxy + cluster
  planet:        2,
}
const TYPE_LABELS: Record<string, string> = {
  meteor_shower: 'Meteor Showers',
  // 'dso' has no label — after prominence filtering the list is short enough
  planet: 'Planets',
}

const MOON_ARCMIN = 30

function moonScaleLabel(arcmin: number | null | undefined): string | null {
  if (arcmin == null) return null
  const ratio = arcmin / MOON_ARCMIN
  if (ratio >= 1.5) return `${Math.round(ratio)}x Moon`
  if (ratio >= 1.0) return '1x Moon'
  if (ratio >= 0.5) return '½ Moon'
  if (ratio >= 0.3) return '⅓ Moon'
  return null
}

// ── Milky Way card ───────────────────────────────────────────────────────────

// Waypoints disclosure — closed by default with Phase 3 density reductions applied inside.
function WaypointsAccordion({ waypoints, summary, report }: {
  waypoints: VisibleTarget[]
  summary: MilkyWaySummary
  report: NightReport
}) {
  const tz = report.tz_name
  const detailsRef = useRef<HTMLDetailsElement>(null)

  useEffect(() => {
    const el = detailsRef.current
    if (!el) return
    const handler = () => {
      if (el.open) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    el.addEventListener('toggle', handler)
    return () => el.removeEventListener('toggle', handler)
  }, [])

  return (
    <details ref={detailsRef} className="mw-waypoints-detail">
      <summary className="mw-waypoints-summary">
        Arch Waypoints ({summary.n_visible})
      </summary>
      <div className="tg-table-wrap mw-waypoints-table-wrap">
        <table className="tg-table">
          <thead>
            <tr>
              <th>Waypoint</th>
              <th>Best</th>
              <th>Window</th>
            </tr>
          </thead>
          <tbody>
            {waypoints.map(t => {
              const w = bestWindow(t)
              if (!w.peak_time || w.peak_alt_deg == null) {
                return (
                  <tr key={t.name}>
                    <td>{t.name}</td>
                    <td className="wx-num">—</td>
                    <td className="wx-num">—</td>
                  </tr>
                )
              }
              const archAngle = w.arch_angle_deg
              const archBadge = archAngle != null && (archAngle < 35 || archAngle >= 60)
                ? <span className="tg-note"> · {archAngle.toFixed(0)}° {archAngle >= 60 ? 'steep' : 'flat'}</span>
                : null
              const glow = report.light_dome
                ? glowToward(report.light_dome, w.peak_az_deg, w.peak_alt_deg)
                : null
              const showGlow = glow != null && glow >= 0.03
              const bestT = w.best_time ?? w.peak_time
              return (
                <tr key={t.name}>
                  <td>
                    {t.name}
                    {showGlow && (
                      <span className="tg-glow-inline cond-glow" style={glowStyle(glow!)}>
                        {` · glow ${glowLabel(glow!)}`}
                      </span>
                    )}
                  </td>
                  <td className="wx-num">
                    <span className="tg-t">{formatTime(bestT, tz)}</span>
                    <span className="tg-p"> (Alt </span>
                    <span className="tg-alt">{Math.round(w.peak_alt_deg)}°</span>
                    <span className="tg-p"> · Az </span>
                    <span className="tg-az">{Math.round(w.peak_az_deg)}°</span>
                    <span className="tg-p"> </span>
                    <span className="tg-dir">{cardinal(w.peak_az_deg)}</span>
                    <span className="tg-p">)</span>
                    {archBadge}
                  </td>
                  <td className="wx-num">
                    <span className="tg-t">{formatTime(w.start, tz)}</span>
                    <span className="tg-p"> – </span>
                    <span className="tg-t">{formatTime(w.end, tz)}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function MoonBadge({ type, severity }: { type: 'penalty' | 'limited'; severity?: string | null }) {
  const base = type === 'penalty' ? 'Moon interference' : 'Moon limited'
  const text = severity ? `${base}: ${severity}` : base
  return <span className="mw-moon-badge">[ {text} ]</span>
}


// IAU (1958) galactic → ICRS rotation matrix (mirrors milky_way.py exactly).
const _GAL_TO_ICRS = [
  [-0.0548755604, +0.4941094279, -0.8676661490],
  [-0.8734370902, -0.4448296300, -0.1980763734],
  [-0.4838350155, +0.7469822445, +0.4559837762],
]




// Full galactic → horizontal (Alt/Az) transformation at a given UTC instant.
// l_deg, b_deg   — galactic coordinates
// lat_deg, lon_deg — observer position
// utcMs          — UTC milliseconds since Unix epoch
function galToAltAz(l_deg: number, b_deg: number, lat_deg: number, lon_deg: number, utcMs: number) {
  const toRad = (d: number) => d * Math.PI / 180
  const l = toRad(l_deg), b = toRad(b_deg)
  const xg = Math.cos(b) * Math.cos(l)
  const yg = Math.cos(b) * Math.sin(l)
  const zg = Math.sin(b)
  const R  = _GAL_TO_ICRS
  const xi = R[0][0]*xg + R[0][1]*yg + R[0][2]*zg
  const yi = R[1][0]*xg + R[1][1]*yg + R[1][2]*zg
  const zi = R[2][0]*xg + R[2][1]*yg + R[2][2]*zg
  const ra_rad  = Math.atan2(yi, xi)
  const dec_rad = Math.asin(Math.max(-1, Math.min(1, zi)))
  // GMST (degrees) via Meeus Ch.12 — valid at any time of day, not just 0h UT
  const jd      = utcMs / 86_400_000 + 2_440_587.5
  const D       = jd - 2_451_545.0                    // days from J2000.0
  const T       = D / 36_525.0
  const gmst_deg = ((280.46061837 + 360.98564736629 * D + 0.000387933 * T * T - T * T * T / 38_710_000) % 360 + 360) % 360
  const lst_rad  = toRad(gmst_deg + lon_deg)
  const ha_rad  = lst_rad - ra_rad
  const lat_rad = toRad(lat_deg)
  const alt = Math.asin(
    Math.sin(dec_rad) * Math.sin(lat_rad) +
    Math.cos(dec_rad) * Math.cos(lat_rad) * Math.cos(ha_rad)
  )
  const az = Math.atan2(
    -Math.cos(dec_rad) * Math.sin(ha_rad),
    Math.sin(dec_rad) * Math.cos(lat_rad) - Math.cos(dec_rad) * Math.sin(lat_rad) * Math.cos(ha_rad)
  )
  return {
    alt: alt * 180 / Math.PI,
    az:  ((az  * 180 / Math.PI) + 360) % 360,
  }
}

// Simplified moon position (Meeus Ch.47, largest perturbation terms).
// Accurate to ~0.3° — sufficient for the dome visualization glow blob.
function moonAltAz(lat: number, lon: number, utcMs: number): { alt: number; az: number } {
  const r   = (d: number) => d * Math.PI / 180
  const mod = (x: number) => ((x % 360) + 360) % 360
  const JD  = utcMs / 86_400_000 + 2_440_587.5
  const D   = JD - 2_451_545.0
  const T   = D / 36_525.0

  // Fundamental arguments (degrees)
  const Lp = mod(218.3164477 + 481267.88123421 * T)
  const Mp = mod(134.9633964 + 477198.8675055  * T)
  const M  = mod(357.5291092 + 35999.0502909   * T)
  const Dg = mod(297.8501921 + 445267.1114034  * T)
  const F  = mod(93.2720950  + 483202.0175233  * T)

  // Ecliptic longitude (10 largest terms, coefficients ×1e-6 degrees)
  const ΣL = (
    + 6288774 * Math.sin(r(Mp))
    + 1274027 * Math.sin(r(2*Dg - Mp))
    +  658314 * Math.sin(r(2*Dg))
    +  213618 * Math.sin(r(2*Mp))
    -  185116 * Math.sin(r(M))
    -  114332 * Math.sin(r(2*F))
    +   58793 * Math.sin(r(2*Dg - 2*Mp))
    +   57066 * Math.sin(r(2*Dg - M - Mp))
    +   53322 * Math.sin(r(2*Dg + Mp))
    +   45758 * Math.sin(r(2*Dg - M))
  ) / 1e6

  // Ecliptic latitude (6 largest terms)
  const ΣB = (
    + 5128122 * Math.sin(r(F))
    +  280602 * Math.sin(r(Mp + F))
    +  277693 * Math.sin(r(Mp - F))
    +  173237 * Math.sin(r(2*Dg - F))
    +   55413 * Math.sin(r(2*Dg - Mp + F))
    +   46271 * Math.sin(r(2*Dg - Mp - F))
  ) / 1e6

  const λ = r(mod(Lp + ΣL))
  const β = r(ΣB)
  const ε = r(23.4393 - 0.013004 * T)  // obliquity of ecliptic

  // Ecliptic → equatorial
  const ra  = Math.atan2(Math.sin(λ) * Math.cos(ε) - Math.tan(β) * Math.sin(ε), Math.cos(λ))
  const dec = Math.asin(Math.sin(β) * Math.cos(ε) + Math.cos(β) * Math.sin(ε) * Math.sin(λ))

  // GMST (Meeus Ch.12) → HA — same formula as galToAltAz
  const gmst = r(mod(280.46061837 + 360.98564736629 * D + 0.000387933 * T * T - T * T * T / 38_710_000))
  const ha   = gmst + r(lon) - ra
  const latR = r(lat)

  const sinAlt = Math.sin(dec) * Math.sin(latR) + Math.cos(dec) * Math.cos(latR) * Math.cos(ha)
  const alt    = Math.asin(Math.max(-1, Math.min(1, sinAlt))) * 180 / Math.PI
  const az     = (Math.atan2(
    -Math.cos(dec) * Math.sin(ha),
    Math.sin(dec) * Math.cos(latR) - Math.cos(dec) * Math.sin(latR) * Math.cos(ha),
  ) * 180 / Math.PI + 360) % 360
  return { alt, az }
}

// ── Light dome direction/glow utilities ──────────────────────────────────────
// Defined before MilkyWayDome so archSegmentBrightness (below) can call glowToward.

const LD_DIRS: Direction[] = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const LD_DIR_AZ: Record<Direction, number> = { N:0, NE:45, E:90, SE:135, S:180, SW:225, W:270, NW:315 }
// Thresholds mirror light_dome.py (minor_threshold / major_threshold) so the colour
// scale is comparable across sites: green→amber at MINOR, →red by MAJOR.
const LD_MINOR = 0.25
const LD_MAJOR = 3.0
// Log-interpolated colour stops (RGB), keyed on the thresholds, using the app's own
// quality ramp (excellent → good → fair → poor). The darkest end fades to BLACK — the
// "excellent of excellent" = true darkness. Glow rises: black → green → blue → lilac → rose.
const LD_STOPS: [number, [number, number, number]][] = [
  [0,        [0, 0, 0]],        // darkness — the best (excellent-of-excellent)
  [0.03,     [52, 211, 153]],   // --excellent green
  [0.12,     [96, 165, 250]],   // --good blue
  [LD_MINOR, [167, 139, 250]],  // --fair lilac — a minor dome
  [0.9,      [251, 113, 133]],  // --poor rose
  [LD_MAJOR, [225, 80, 100]],   // deep --poor — a major dome
]
// Bloom-legibility transform: real dome heights are ~1° (a sub-pixel rim sliver), so
// for *display* we scale them up and floor them. This shapes the on-screen bloom, not
// the physics. Directions with no flagged dome get a small default height.
const LD_THETA_K = 5
const LD_THETA_FLOOR_DEG = 6
const LD_THETA_DEFAULT_DEG = 4
const LD_SIZE = 168            // CSS px; disk + room for N/E/S/W labels

function ldColor(v: number): [number, number, number] {
  if (v <= LD_STOPS[0][0]) return LD_STOPS[0][1]
  for (let i = 1; i < LD_STOPS.length; i++) {
    const [hv, hc] = LD_STOPS[i]
    if (v <= hv) {
      const [lv, lc] = LD_STOPS[i - 1]
      const t = (Math.log(Math.max(v, 1e-4)) - Math.log(Math.max(lv, 1e-4))) /
                (Math.log(hv) - Math.log(Math.max(lv, 1e-4)))
      const k = Math.max(0, Math.min(1, t))
      return [lc[0] + (hc[0] - lc[0]) * k, lc[1] + (hc[1] - lc[1]) * k, lc[2] + (hc[2] - lc[2]) * k]
    }
  }
  return LD_STOPS[LD_STOPS.length - 1][1]
}

// Tent-interpolate a per-direction array at an arbitrary azimuth (partition of unity
// across the two nearest cardinals — same scheme as light_dome.glow_toward).
function ldTent(arr: number[], azDeg: number): number {
  const p = (((azDeg % 360) + 360) % 360) / 45
  const lo = Math.floor(p) % 8
  const hi = (lo + 1) % 8
  const f = p - Math.floor(p)
  return arr[lo] * (1 - f) + arr[hi] * f
}

// Mirrors light_dome.glow_toward(): score(az) / (1 + (alt/θ(az))²)
function glowToward(summary: LightDomeSummary, azDeg: number, altDeg: number): number {
  const scores8  = LD_DIRS.map(d => summary.scores[d] ?? 0)
  const heights8 = LD_DIRS.map(d => summary.dome_heights[d] ?? 0)
  const score    = ldTent(scores8, azDeg)
  const theta    = ldTent(heights8, azDeg)
  const alt      = Math.max(0, altDeg)
  if (theta <= 0) return alt === 0 ? score : 0
  return score / (1 + (alt / theta) ** 2)
}

function glowLabel(g: number): string {
  if (g < 0.03)     return 'negligible'
  if (g < LD_MINOR) return 'minor'
  if (g < LD_MAJOR) return 'moderate'
  return 'major'
}

// CSS colour for a glow value, reusing the LD stop palette.
// Negligible glow returns {} so the element inherits var(--text-dim) from CSS;
// the LD ramp starts at black (the zero-glow "excellent" end) which is invisible
// on dark backgrounds, so we only apply inline colour once the glow is meaningful.
function glowStyle(g: number): React.CSSProperties {
  if (g < 0.03) return {}
  const [r, gr, b] = ldColor(g)
  return { color: `rgb(${Math.round(r)},${Math.round(gr)},${Math.round(b)})` }
}

// Sky background brightness for MW arch visibility — distinct from glowToward().
// dome_heights are geometric angles (1-3° for a city 30+ mi away) but city glow
// scatters through the atmosphere and degrades sky brightness well above that.
// A 40° characteristic altitude matches observer perception: score at the horizon
// falls to ~50% at 40°, ~20% at 80° (zenith). Uses direction scores only, no heights.
function archGlowAt(summary: LightDomeSummary, azDeg: number, altDeg: number): number {
  const scores8 = LD_DIRS.map(d => summary.scores[d] ?? 0)
  const score   = ldTent(scores8, azDeg)
  const alt     = Math.max(0, altDeg)
  return score / (1 + (alt / 40) ** 2)
}

// ── Milky Way brightness model ────────────────────────────────────────────────
// Two-component empirical model: compact Gaussian bulge + linear disk + floor.
// sigma=0.28 chosen to match known bright regions (core→scutum→cygnus→anticenter).
function intrinsicBrightness(l_deg: number): number {
  const norm  = ((l_deg % 360) + 360) % 360
  const delta = Math.min(norm, 360 - norm)    // 0° at core, 180° at anticenter
  const x     = delta / 180                    // normalised [0, 1]
  const bulge = 0.70 * Math.exp(-x * x / (2 * 0.28 * 0.28))
  const disk  = 0.18 * (1 - x * 0.7)
  return Math.max(0, Math.min(1, 0.12 + bulge + disk))
}

// Exponential attenuation from a light-dome glow index → brightness multiplier [0,1].
// glow=0→1.0, glow=0.25(LD_MINOR)→0.82, glow=1.0→0.45, glow=3.0(LD_MAJOR)→0.09
function washoutFactor(glow: number): number {
  return Math.exp(-0.8 * glow)
}

// Per-segment arch brightness: intrinsic galactic profile × light-dome washout.
// Returns {glowOpacity, coreOpacity} for the two rendering layers.
function archSegmentBrightness(
  l: number, alt: number, az: number,
  lightDome: LightDomeSummary | null
): { glowOpacity: number; coreOpacity: number } {
  const I    = intrinsicBrightness(l)
  const glow = lightDome ? archGlowAt(lightDome, az, alt) : 0
  const W    = washoutFactor(glow)
  const B    = I * W
  return { glowOpacity: B * 0.35, coreOpacity: B * 0.85 }
}

// Orthographic projection of the sky dome, camera centered on the galactic core azimuth.
// The arch traces the galactic equator (b=0), sampled every 5° of galactic longitude.
function MilkyWayDome({ summary, waypoints, report }: { summary: MilkyWaySummary; waypoints: VisibleTarget[]; report: NightReport }) {
  const [heading, setHeading] = useState<number>(
    summary.core_peak_az_deg != null ? Math.round(summary.core_peak_az_deg) : 180
  );
  const [tilt, setTilt] = useState<number>(0);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);

  // 1. ADDED HERE: State to track which dot is currently being hovered
  const [hoveredDot, setHoveredDot] = useState<{name: string, x: number, y: number} | null>(null);

  if (summary.core_peak_alt_deg == null || summary.core_peak_alt_deg <= 0) {
    return (
      <div className="mw-dome-absent">
        Arch below horizon tonight
        <div className="mw-absent-reason">The Galactic Core is currently out of view.</div>
      </div>
    );
  }

  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const peakTimeMs = new Date(summary.core_peak_time).getTime();

  type EqSample = { l: number; alt: number; az: number; }
  type ArchSegment = { x1: number; y1: number; x2: number; y2: number; glowOpacity: number; coreOpacity: number }

  const allEquatorSamples: EqSample[] = Array.from({ length: 72 }, (_, i) => {
    const l = i * 5;
    const { alt, az } = galToAltAz(l, 0, report.lat, report.lon, peakTimeMs);
    return { l, alt, az };
  });

  // Gnomonic (rectilinear/perspective) projection — models a wide-angle camera
  // pointed at the horizon along `heading`. Great circles project to straight lines.
  const FOV_HALF_DEG = 60;                        // 120° total horizontal FoV
  const f = 100 / Math.tan(toRad(FOV_HALF_DEG)); // focal length ~57.7 SVG units

  const project = (alt: number, az: number) => {
    const altR = toRad(alt);
    const azR  = toRad(az - heading);
    const tiltR = toRad(tilt);
    const cosAlt = Math.cos(altR), sinAlt = Math.sin(altR);
    const cosAzR = Math.cos(azR), sinAzR = Math.sin(azR);
    const cosT = Math.cos(tiltR), sinT = Math.sin(tiltR);
    const dx = cosAlt * sinAzR;
    const dy = sinAlt * cosT - cosAlt * cosAzR * sinT;
    const dz = cosAlt * cosAzR * cosT + sinAlt * sinT;
    if (dz <= 0) return { x: 100, y: 100, isFront: false };
    return {
      x: 100 + f * (dx / dz),
      y: 100 - f * (dy / dz),
      isFront: true,
    };
  };

  // Horizon y-position in SVG coords — moves down as camera tilts up; clips to frame bottom.
  const horizonY = Math.min(120, 100 + f * Math.tan(toRad(tilt)));

  // Map the known Milky Way waypoint names to their exact Galactic Longitude (l)
  const WAYPOINT_L: Record<string, number> = {
    'Galactic Anticenter': 180,
    'Cassiopeia/Perseus': 135,
    'Cepheus Cloud': 105,
    'Cygnus Star Cloud': 80,
    'Aquila Rift': 45,
    'Scutum Star Cloud': 27,
    'Galactic Core': 0,
    'Scorpius Star Cloud': 347,
    'Norma Star Cloud': 330,
    'Crux & Coalsack': 302,
    'Carina Nebula & Cloud': 287,
    'Vela Supernova Region': 265,
    'Puppis Star Cloud': 245,
    'Monoceros': 210,
  };

  const wpDots = waypoints
    .map(wp => {
      const l = WAYPOINT_L[wp.name];

      // If we know the galactic longitude, calculate its exact position at the time of the arch
      if (l != null) {
        const { alt, az } = galToAltAz(l, 0, report.lat, report.lon, peakTimeMs);
        return { name: wp.name, alt, az };
      }

      // Fallback just in case a new target is added to the backend later
      const w = bestWindow(wp);
      return { name: wp.name, alt: w.peak_alt_deg ?? -1, az: w.peak_az_deg ?? 0 };
    })
    .filter(p => p.alt > 0)
    .map(p => ({ ...p, proj: project(p.alt, p.az) }));

  const doubled = [...allEquatorSamples, ...allEquatorSamples];
  let longestStreak: EqSample[] = [];
  let currentStreak: EqSample[] = [];
  for (const s of doubled) {
    if (s.alt > -2) currentStreak.push(s);
    else {
      if (currentStreak.length > longestStreak.length) longestStreak = currentStreak;
      currentStreak = [];
    }
  }
  const visibleArc = longestStreak.slice(0, 73).map(s => ({ ...s, proj: project(s.alt, s.az) }))

  // Build per-segment brightness for the variable-opacity arch rendering.
  const visibleSegments: ArchSegment[] = []
  for (let i = 0; i < visibleArc.length - 1; i++) {
    const a = visibleArc[i], b = visibleArc[i + 1]
    if (!a.proj.isFront || !b.proj.isFront) continue
    const bA = archSegmentBrightness(a.l, a.alt, a.az, report.light_dome ?? null)
    const bB = archSegmentBrightness(b.l, b.alt, b.az, report.light_dome ?? null)
    visibleSegments.push({
      x1: a.proj.x, y1: a.proj.y,
      x2: b.proj.x, y2: b.proj.y,
      glowOpacity: (bA.glowOpacity + bB.glowOpacity) / 2,
      coreOpacity: (bA.coreOpacity + bB.coreOpacity) / 2,
    })
  }
  const corePos = project(summary.core_peak_alt_deg, summary.core_peak_az_deg ?? 0);

  // Altitude reference rings for photographer framing (20° and 40°).
  // In gnomonic, constant-altitude loci curve upward at the edges — rendered as polylines.
  const ALT_RINGS = [20, 40] as const;
  const ringPolylines = ALT_RINGS.map(alt => {
    const pts: string[] = [];
    for (let dAz = -FOV_HALF_DEG; dAz <= FOV_HALF_DEG; dAz += 1) {
      const p = project(alt, heading + dAz);
      if (p.isFront) pts.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`);
    }
    return { alt, points: pts.join(' ') };
  });

  // Left-edge label anchor for each ring — sampled ~54° left of center so x≈20 near frame edge.
  const ringLabels = ALT_RINGS.map(alt => {
    const p = project(alt, heading - 54);
    if (!p.isFront || p.x < 11 || p.x > 100) return null;
    return { alt, x: p.x, y: p.y };
  });

  // Sky glow blobs: one per dome direction, anchored 5° above horizon so the gradient
  // peak sits just inside the dome rather than exactly on the arc edge.
  type DomeGlow = { dir: Direction; x: number; y: number; r: number; op: number }
  const domeGlows: DomeGlow[] = report.light_dome
    ? LD_DIRS.flatMap(d => {
        const score = report.light_dome!.scores[d] ?? 0
        if (score < LD_MINOR) return []
        const pos = project(5, LD_DIR_AZ[d])
        if (!pos.isFront) return []
        const r  = Math.min(85, 30 + 18 * Math.log1p(score))
        const op = Math.min(0.50, 0.12 + 0.15 * Math.log1p(score))
        return [{ dir: d, x: pos.x, y: pos.y, r, op }]
      })
    : []

  // Moon glow blob: position at arch peak time, brightness scales with illumination.
  // Only shown when moon is above the horizon and meaningfully illuminated (≥5%).
  const moonGlowPos = (() => {
    if (report.illumination_pct < 5) return null
    const { alt, az } = moonAltAz(report.lat, report.lon, peakTimeMs)
    if (alt <= 0) return null
    const pos = project(alt, az)
    if (!pos.isFront) return null
    const illumFrac = report.illumination_pct / 100
    return {
      x: pos.x, y: pos.y,
      r:  Math.min(70, 15 + 55 * illumFrac),
      op: Math.min(0.45, 0.05 + 0.40 * illumFrac),
    }
  })()

  const cardinals = [
    { deg: 0, label: 'N' }, { deg: 45, label: 'NE' }, { deg: 90, label: 'E' },
    { deg: 135, label: 'SE' }, { deg: 180, label: 'S' }, { deg: 225, label: 'SW' },
    { deg: 270, label: 'W' }, { deg: 315, label: 'NW' }
  ];

  // 1px drag → (180/displayWidth) SVG units → (180/π)/f degrees of heading/tilt change.
  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    pointerRef.current = { x: e.clientX, y: e.clientY };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  };
  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pointerRef.current) return;
    const dx = e.clientX - pointerRef.current.x;
    const dy = e.clientY - pointerRef.current.y;
    pointerRef.current = { x: e.clientX, y: e.clientY };
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const sens = (180 / Math.PI) / f;                    // ~1°/SVG unit
    const dxSvg = dx * (180 / rect.width) * sens;        // heading degrees
    const dySvg = dy * (120 / rect.height) * sens;        // tilt degrees
    setHeading(h => ((h + dxSvg) % 360 + 360) % 360);
    setTilt(t => Math.max(0, Math.min(45, t - dySvg)));
  };
  const handlePointerUp = () => { pointerRef.current = null; };

  return (
    <div className="mw-dome-wrap">
      <svg
        viewBox="10 0 180 120"
        xmlns="http://www.w3.org/2000/svg"
        style={{ touchAction: 'none', cursor: 'grab', userSelect: 'none' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
      >
        <defs>
          <clipPath id="mw-half-dome-clip">
            <rect x="0" y="0" width="200" height={horizonY} />
          </clipPath>
          <filter id="mw-f-band" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="3" />
          </filter>
          {domeGlows.map(g => (
            <radialGradient key={g.dir} id={`mw-ldg-${g.dir}`}
              cx={g.x} cy={g.y} r={g.r} gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stopColor="currentColor" stopOpacity={g.op} />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
          ))}
          {moonGlowPos && (
            <radialGradient id="mw-moon-g"
              cx={moonGlowPos.x} cy={moonGlowPos.y} r={moonGlowPos.r} gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stopColor="currentColor" stopOpacity={moonGlowPos.op} />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
          )}
        </defs>

        <rect x="0" y="0" width="200" height="120" fill="rgba(10, 15, 30, 0.4)" />
        {horizonY < 120 && (
          <rect className="mw-dome-ground" x="0" y={horizonY} width="200" height={120 - horizonY} />
        )}

        {/* Sky glow from light domes — color controlled via .mw-dome-glow for red-mode compliance */}
        {domeGlows.length > 0 && (
          <g className="mw-dome-glow" clipPath="url(#mw-half-dome-clip)">
            {domeGlows.map(g => (
              <circle key={g.dir} cx={g.x} cy={g.y} r={g.r}
                fill={`url(#mw-ldg-${g.dir})`} />
            ))}
            {domeGlows.map(g => (
              <circle key={`${g.dir}-hit`} cx={g.x} cy={g.y} r="20"
                fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseEnter={() => setHoveredDot({ name: `${g.dir} sky glow`, x: g.x, y: g.y })}
                onMouseLeave={() => setHoveredDot(null)} />
            ))}
          </g>
        )}

        {/* Moon glow — color controlled via .mw-moon-glow for red-mode compliance */}
        {moonGlowPos && (
          <g className="mw-moon-glow" clipPath="url(#mw-half-dome-clip)">
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r={moonGlowPos.r} fill="url(#mw-moon-g)" />
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r="2" fill="currentColor" opacity="0.75" />
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r="18"
              fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
              onPointerDown={(e) => e.stopPropagation()}
              onMouseEnter={() => setHoveredDot({ name: 'Moon', x: moonGlowPos.x, y: moonGlowPos.y })}
              onMouseLeave={() => setHoveredDot(null)} />
          </g>
        )}

        <g clipPath="url(#mw-half-dome-clip)">
          {/* GLOW LAYER: blurred wide band; filter on the group blends joints between segments */}
          <g filter="url(#mw-f-band)" className="mw-arch-glow">
            {visibleSegments.map((seg, i) => (
              <line
                key={i}
                x1={seg.x1} y1={seg.y1} x2={seg.x2} y2={seg.y2}
                stroke="currentColor"
                strokeWidth="14"
                strokeOpacity={seg.glowOpacity}
              />
            ))}
          </g>
          {/* CORE LAYER: thin bright stripe, variable opacity per segment */}
          <g className="mw-arch-core">
            {visibleSegments.map((seg, i) => (
              <line
                key={i}
                x1={seg.x1} y1={seg.y1} x2={seg.x2} y2={seg.y2}
                stroke="currentColor"
                strokeWidth="1.5"
                strokeOpacity={seg.coreOpacity}
              />
            ))}
          </g>
          {/* Altitude reference rings — perspective curves at 20° and 40° */}
          {ringPolylines.map(ring => ring.points && (
            <polyline key={ring.alt} className="mw-dome-ring" points={ring.points} fill="none" />
          ))}
          {ringLabels.map(lbl => lbl && (
            <text key={`rl-${lbl.alt}`} className="mw-dome-ring-label"
              x={lbl.x + 2} y={lbl.y - 2} textAnchor="start">{lbl.alt}°</text>
          ))}
        </g>

        {/* 2. REPLACED HERE: The Galactic Core */}
        {corePos.isFront && (
          <g>
            <circle className="mw-dome-core" cx={corePos.x} cy={corePos.y} r="3.5" pointerEvents="none" />
            <circle
              cx={corePos.x} cy={corePos.y} r="12" fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
              onPointerDown={(e) => e.stopPropagation()}
              onMouseEnter={() => setHoveredDot({ name: 'Galactic Core', x: corePos.x, y: corePos.y })}
              onMouseLeave={() => setHoveredDot(null)}
            />
          </g>
        )}

        {/* 3. REPLACED HERE: Waypoints Array */}
        {wpDots.map((wp, i) => (
          wp.proj.isFront && (
            <g key={i}>
              <circle cx={wp.proj.x} cy={wp.proj.y} r="2" fill="rgba(255, 255, 255, 0.6)" pointerEvents="none" />
              <circle
                cx={wp.proj.x} cy={wp.proj.y} r="12" fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseEnter={() => setHoveredDot({ name: wp.name, x: wp.proj.x, y: wp.proj.y })}
                onMouseLeave={() => setHoveredDot(null)}
              />
            </g>
          )
        ))}

        {horizonY < 120 && (
          <line className="mw-dome-horizon" x1="0" y1={horizonY} x2="200" y2={horizonY} />
        )}
        <rect className="mw-dome-frame" x="10.5" y="0.5" width="179" height="119" fill="none" />

        {horizonY < 120 && cardinals.map(c => {
          let relAz = c.deg - heading;
          while (relAz <= -180) relAz += 360;
          while (relAz > 180) relAz -= 360;

          if (Math.abs(relAz) < FOV_HALF_DEG) {
            // Gnomonic + tilt: horizon objects at azimuth relAz project to:
            // x = 100 + f * tan(relAz) / cos(tilt)
            const x = 100 + f * Math.tan(toRad(relAz)) / Math.cos(toRad(tilt));
            const labelY = horizonY + 14;
            if (x < 11 || x > 189 || labelY > 119) return null;
            return (
              <g key={c.label}>
                <line className="mw-dome-tick" x1={x} y1={horizonY} x2={x} y2={horizonY + 3} />
                <text className="mw-dome-label" x={x} y={labelY} textAnchor="middle">{c.label}</text>
              </g>
            );
          }
          return null;
        })}

        {/* 4. ADDED HERE: The Custom Tooltip directly inside the SVG */}
        {hoveredDot && (
          <foreignObject x={hoveredDot.x - 75} y={hoveredDot.y - 35} width="150" height="30" pointerEvents="none">
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'flex-end', height: '100%' }}>
              <div style={{
                background: 'var(--pop-bg, #1e2235)',
                color: 'var(--text-h, #fff)',
                border: '1px solid var(--card-border)',
                borderRadius: '6px',
                padding: '4px 8px',
                fontSize: '10px',
                whiteSpace: 'nowrap',
                boxShadow: '0 4px 12px rgba(0,0,0,0.6)'
              }}>
                {hoveredDot.name}
              </div>
            </div>
          </foreignObject>
        )}
      </svg>
    </div>
  );
}

function MilkyWayAbsent({ report: r }: { report: NightReport }) {
  const coreMaxAlt = Math.max(0, 90 - Math.abs(r.lat - (-28.9)))
  const bortle     = r.light_pollution?.bortle_class ?? 0
  const bortleDesc = r.light_pollution?.bortle_desc  ?? 'bright sky'

  let reason: string
  if (coreMaxAlt < 10) {
    reason = `Galactic core never rises above 10° from this latitude (max ${coreMaxAlt.toFixed(0)}° altitude)`
  } else if (bortle >= 6) {
    reason = `${bortleDesc} (Bortle ${bortle}) — light pollution prevents Milky Way visibility here`
  } else if (bortle >= 4) {
    reason = `Suburban skies (Bortle ${bortle}) are too bright for Milky Way visibility here`
  } else if (r.dark_intervals.length === 0) {
    reason = `Bright moon (${r.illumination_pct.toFixed(0)}%) is up all night — no dark sky window`
  } else {
    reason = 'Galactic core is below the horizon during tonight\'s dark window'
  }

  return <p className="mw-absent-reason">{reason}</p>
}

export function MilkyWayCard({ summary, waypoints, report }: {
  summary: MilkyWaySummary
  waypoints: VisibleTarget[]
  report: NightReport
}) {
  const tz = report.tz_name
  const s  = summary

  const archQuality = s.arch_angle_deg != null
    ? (s.arch_angle_deg >= 60 ? 'steep' : s.arch_angle_deg >= 35 ? 'moderate' : 'flat')
    : null

  // Directions where dome glow visibly dims the arch at peak time.
  // Uses archGlowAt (40° characteristic alt) at each dome direction within ±90° of the
  // core az; flags it when the resulting glow ≥ LD_MINOR (≥18% brightness reduction).
  const domeSections: { dir: Direction; glow: number }[] = (() => {
    if (!report.light_dome || s.core_peak_az_deg == null) return []
    const coreAz   = s.core_peak_az_deg
    const proxyAlt = Math.max(5, s.core_peak_alt_deg ?? 25)
    return LD_DIRS.flatMap(d => {
      const score = report.light_dome!.scores[d] ?? 0
      if (score < LD_MINOR) return []
      const dirAz = LD_DIR_AZ[d]
      let delta = ((dirAz - coreAz) + 360) % 360
      if (delta > 180) delta = 360 - delta
      if (delta > 90) return []
      const glow = archGlowAt(report.light_dome!, dirAz, proxyAlt)
      if (glow < LD_MINOR) return []
      return [{ dir: d, glow }]
    })
  })()

  const bestLabel = 'Best time'
  const bestTime  = s.best_viewing_time ?? (s.core_peak_in_window ? s.core_peak_time : s.arch_end)

  const moonSeverity = moonWashSeverity(
    report.illumination_pct,
    s.core_moon_sep_deg ?? null,
    s.core_moon_alt_deg ?? null,
  )

  return (
    <div className="mw-card">

      {/* Structural layout fix mapping correctly to your CSS */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center', columnGap: '1.5rem' }}>

        {/* Left Column Container reproducing the natural 7px vertical gap of mw-card */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>

          <div className="mw-score-row">
            <div className="mw-score-left">
              <div style={{display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap'}}>
                <span className={`mw-score mw-score-band-${scoreBand(s.local_score)}`}>{s.local_score.toFixed(1)}<span className="mw-score-denom">/10</span></span>
                {s.weather_blocked && <span className="mw-moon-badge badge-poor">[ Clouded out ]</span>}
                {!s.weather_blocked && s.weather_limited && <span className="mw-moon-badge">[ Partly cloudy ]</span>}
              </div>
              <div className="mw-sub-scores">
                <span className={`mw-score-band-${scoreBand(s.alt_score)}`}>Altitude {s.alt_score.toFixed(1)}/10</span>
                <span className={`mw-score-band-${scoreBand(s.cov_score)}`}>Coverage {s.cov_score.toFixed(1)}/10</span>
                <span className={`mw-score-band-${scoreBand(s.win_score)}`}>Window {s.win_score.toFixed(1)}/10</span>
                {s.moon_penalised && <MoonBadge type="penalty" severity={moonSeverity} />}
                {s.arch_moon_washout && <span className="mw-moon-badge">[ Moon washout ]</span>}
                {domeSections.length > 0 && (() => {
                  const maxGlow  = Math.max(...domeSections.map(ds => ds.glow))
                  const severity = glowLabel(maxGlow)
                  const dirs     = domeSections.map(ds => ds.dir).join(' + ')
                  const label    = `${dirs} ${domeSections.length > 1 ? 'sections' : 'section'}`
                  return (
                    <span className="mw-moon-badge">
                      {`[ Dome glow: ${label} · `}
                      <span className="cond-glow" style={glowStyle(maxGlow)}>{severity}</span>
                      {' ]'}
                    </span>
                  )
                })()}
              </div>
            </div>
          </div>

          <div className="mw-row">
            <span className="mw-label">Arch window</span>
            <span>
              {formatTime(s.arch_start, tz)} – {formatTime(s.arch_end, tz)}
              {'  ·  '}{Math.floor(s.arch_hours)}h {Math.round((s.arch_hours % 1) * 60).toString().padStart(2,'0')}m
              {s.moon_limited    && <MoonBadge type="limited" severity={moonSeverity} />}
              {s.weather_limited && !s.weather_blocked && (
                <span className="mw-moon-badge">
                  {`[ ${s.clear_arch_hours.toFixed(1)}h clear ]`}
                </span>
              )}
            </span>
          </div>

          <div className="mw-row">
            <span className="mw-label">Galactic core</span>
            <span>
              {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)} (max {s.core_max_alt_deg}° alt)
              {archQuality && s.arch_angle_deg != null && `  ·  arch ${s.arch_angle_deg.toFixed(0)}° (${archQuality})`}
            </span>
          </div>

          <div className="mw-row">
            <span className="mw-label">{bestLabel}</span>
            <span>
              {formatTime(bestTime, tz)} — core @ {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)}
              {s.farthest_name && s.farthest_peak_alt_deg != null && (
                <>,&nbsp;arch to {s.farthest_name} @ {fmtPos(s.farthest_peak_alt_deg, s.farthest_peak_az_deg ?? 0)}</>
              )}
            </span>
          </div>

        </div>

        {/* Right Column Container */}
        <div>
          <MilkyWayDome summary={s} waypoints={waypoints} report={report} />
        </div>

      </div>

      {waypoints.length > 0 && (
        <WaypointsAccordion waypoints={waypoints} summary={s} report={report} />
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

function skyClass(sky: string): string {
  const moonWashMatch = sky.match(/^Moon wash \((minor|moderate|severe)\)$/)
  if (moonWashMatch) return `tg-sky-moon-wash-${moonWashMatch[1]}`
  return `tg-sky-${sky.replace(/ /g, '-').toLowerCase()}`
}

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
  const skyCls = sky ? skyClass(sky) : ''

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

// ── Blocker badge (Phase 2) ───────────────────────────────────────────────────

function BlockerBadge({ blockers }: { blockers: string[] }) {
  let label = 'Unavailable Tonight'
  if (blockers.includes('cloud') || blockers.includes('transparency'))
    label = 'Clouded out'
  else if (blockers.includes('moon_washout'))
    label = 'Moon washout'
  else if (blockers.includes('light_dome'))
    label = 'Lost in light dome'
  return <span className="tg-blocker-badge">[ {label} ]</span>
}

function clipTooltip(w: TargetWindow, tz: string): string {
  const b = w.blockers ?? []
  const end = w.effective_end
  if (b.includes('cloud') || b.includes('transparency'))
    return `Partly cloudy${end ? ` after ${formatTime(end, tz)}` : ''}`
  if (b.includes('moon_washout')) return 'Moon washout'
  if (b.includes('light_dome'))   return 'Viewing constrained by horizon glow'
  return 'Window clipped by conditions'
}

function clipReasonShort(w: TargetWindow): string {
  const b = w.blockers ?? []
  if (b.includes('cloud') || b.includes('transparency')) return 'cloud'
  if (b.includes('moon_washout')) return 'moon'
  if (b.includes('light_dome'))   return 'dome'
  return 'conditions'
}

// ── TargetsTable ──────────────────────────────────────────────────────────────

function TargetsTable({ targets, report }: { targets: VisibleTarget[]; report: NightReport }) {
  const tz = report.tz_name

  // Milky Way + meteor showers rendered separately as cards; rest filtered to prime prominent
  const allPrime = targets
    .filter(t => t.type !== 'milky_way' && t.type !== 'meteor_shower')
    .filter(t => isPrime(t, report.dark_intervals))
    .filter(t => (t.landscape_suitability ?? 'prominent') === 'prominent')

  const viable   = allPrime.filter(t => t.viability !== 'blocked')
  const unviable = allPrime.filter(t => t.viability === 'blocked')

  if (allPrime.length === 0) return null

  // Shared sort + group logic.
  // nebula / galaxy / cluster are normalized to 'dso' so they render as one unlabeled block.
  type RowItem =
    | { kind: 'header'; type: string; key: string }
    | { kind: 'target'; target: VisibleTarget; key: string; blocked?: boolean }

  const displayType = (t: VisibleTarget) => DSO_TYPES.has(t.type) ? 'dso' : t.type

  function sortAndGroup(list: VisibleTarget[], blocked = false): RowItem[] {
    const sorted = [...list].sort((a, b) => {
      const ao = TYPE_ORDER[displayType(a)] ?? 99
      const bo = TYPE_ORDER[displayType(b)] ?? 99
      if (ao !== bo) return ao - bo
      const at = bestWindow(a).peak_time ?? ''
      const bt = bestWindow(b).peak_time ?? ''
      return at.localeCompare(bt)
    })
    const groups: { type: string; targets: VisibleTarget[] }[] = []
    for (const t of sorted) {
      const dt   = displayType(t)
      const last = groups[groups.length - 1]
      if (last && last.type === dt) last.targets.push(t)
      else groups.push({ type: dt, targets: [t] })
    }
    const rows: RowItem[] = []
    for (const g of groups) {
      if (TYPE_LABELS[g.type]) {
        rows.push({ kind: 'header', type: g.type, key: `hdr-${blocked ? 'blocked-' : ''}${g.type}` })
      }
      for (const t of g.targets) {
        rows.push({ kind: 'target', target: t, key: `${g.type}-${t.name}`, blocked })
      }
    }
    return rows
  }

  const viableRows   = sortAndGroup(viable, false)
  const unviableRows = sortAndGroup(unviable, true)

  function renderTargetRow(t: VisibleTarget, key: string, blocked: boolean) {
    const w         = bestWindow(t)
    const name      = t.type === 'meteor_shower' ? `${t.name} Meteor Shower` : t.name
    const sizeLabel = moonScaleLabel(t.angular_size_arcmin)

    // photo_cutoff clips both Peak and Window (mirrors CLI)
    const hasClip = !!(w.photo_cutoff
      && new Date(w.photo_cutoff) > new Date(w.start)
      && new Date(w.photo_cutoff) < new Date(w.end))

    // Fixed-width helpers — each piece gets a min-width span so columns
    // stay consistent across rows regardless of digit count or direction length.
    const Tt  = ({ t }: { t: string })    => <span className="tg-t">{formatTime(t, tz)}</span>
    const Alt = ({ deg }: { deg: number }) => <span className="tg-alt">{Math.round(deg)}°</span>
    const Az  = ({ az }: { az: number })   => <span className="tg-az">{Math.round(az)}°</span>
    const Dir = ({ az }: { az: number })   => <span className="tg-dir">{cardinal(az)}</span>
    const Sep = () => <span className="tg-p"> – </span>

    const peakForSky = blocked
      ? null
      : (w.best_time ?? (hasClip ? w.photo_cutoff! : w.peak_time))

    // Conditions: weather icon and glow inline in Target cell
    const wxPt = peakForSky && !report.wx_no_data && !report.wx_pending
      ? wxAtTime(report.weather_points, peakForSky)
      : null
    const glow = report.light_dome && w.peak_alt_deg != null
      ? glowToward(report.light_dome, w.peak_az_deg, w.peak_alt_deg)
      : null

    const targetCell = (
      <td>
        {name}
        {t.note    && <span className="tg-note"> · {t.note}</span>}
        {sizeLabel && <span className="tg-note"> · Size: {sizeLabel}</span>}
        {wxPt && (
          <span className="tg-wx-inline">
            <WmoIcon code={wxPt.weather_code} size={12} />
          </span>
        )}
        {glow != null && glow >= 0.03 && (
          <span className="tg-glow-inline cond-glow" style={glowStyle(glow)}>
            {` · glow ${glowLabel(glow)}`}
          </span>
        )}
      </td>
    )

    if (blocked) {
      return (
        <tr key={key} className="tg-row-blocked">
          {targetCell}
          <td><BlockerBadge blockers={t.windows[0]?.blockers ?? []} /></td>
          <td className="wx-num">—</td>
        </tr>
      )
    }

    // Peak cell: time · Alt · Az · Dir + sky badge when non-dark
    let peakJsx: React.ReactNode = '—'
    if (w.peak_time && w.peak_alt_deg != null) {
      const effectiveBestTime = w.best_time ?? (hasClip ? w.photo_cutoff! : w.peak_time)
      const effectiveBestAlt  = altAt(effectiveBestTime, w)

      const sky = peakForSky
        ? skyCondition(
            peakForSky, report.dark_intervals, report.night_start, report.night_end,
            report.illumination_pct, report.moonrise, report.moonset,
            w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg,
          )
        : '—'
      const skyCls = skyClass(sky)
      const moonNote = w.moon_interference && !sky.startsWith('Moon')
      const moonIsUpAtPeak = peakForSky
        ? moonUpAt(peakForSky, report.moonrise, report.moonset)
        : false
      const moonNoteText = moonNote
        ? (moonIsUpAtPeak ? ' · moon wash minimal' : ' · pre-moonrise')
        : null
      const showSkyBadge = sky !== 'Dark sky' && sky !== '—'

      peakJsx = (
        <>
          <Tt t={effectiveBestTime} />
          <span className="tg-p"> · Alt </span><Alt deg={effectiveBestAlt} />
          <span className="tg-p"> Az </span><Az az={w.peak_az_deg} /><span className="tg-p"> </span><Dir az={w.peak_az_deg} />
          {showSkyBadge && (
            <span className={`tg-sky-inline ${skyCls}`}>
              {' '}{sky}{moonNoteText ? <span className="tg-moon-note">{moonNoteText}</span> : null}
            </span>
          )}
          {!showSkyBadge && moonNoteText && (
            <span className="tg-moon-note">{' '}{moonNoteText}</span>
          )}
        </>
      )
    }

    // Window: use effective_start/effective_end from condition vectors when available
    let winJsx: React.ReactNode = '—'
    if (w.peak_time) {
      const rawEnd    = hasClip ? w.photo_cutoff! : w.end
      const effStart  = w.effective_start ?? w.start
      const effEnd    = w.effective_end   ?? rawEnd
      const effStartAlt = altAt(effStart, w)
      const effEndAlt   = altAt(effEnd, w)
      const isClipped = effStart > w.start || effEnd < rawEnd

      // Visual-observation extension beyond photo_cutoff (preserved from original logic)
      const visualExtJsx = hasClip && w.visual_cutoff ? (() => {
        const extraMin = Math.round(
          (new Date(w.visual_cutoff).getTime() - new Date(w.photo_cutoff!).getTime()) / 60000
        )
        return extraMin >= 10 ? <span className="tg-p"> +{extraMin}m visual</span> : null
      })() : null

      winJsx = (
        <>
          <Tt t={effStart} /><span className="tg-p"> @ </span><Alt deg={effStartAlt} />
          <Sep />
          <Tt t={effEnd} /><span className="tg-p"> @ </span><Alt deg={effEndAlt} />
          {isClipped && (
            <span
              className="tg-clip-indicator"
              title={clipTooltip(w, tz)}
              data-tip={clipReasonShort(w)}
            >*</span>
          )}
          {visualExtJsx}
        </>
      )
    }

    return (
      <tr key={key}>
        {targetCell}
        <td className="wx-num">{peakJsx}</td>
        <td className="wx-num">{winJsx}</td>
      </tr>
    )
  }

  return (
    <div className="tg-table-wrap">
      <table className="tg-table">
        <thead>
          <tr>
            <th>Target</th>
            <th>Peak</th>
            <th>Window</th>
          </tr>
        </thead>
        <tbody>
          {viableRows.map(row => {
            if (row.kind === 'header') {
              return (
                <tr key={row.key} className="tg-group-hdr">
                  <td colSpan={3}>{TYPE_LABELS[row.type] ?? row.type}</td>
                </tr>
              )
            }
            return renderTargetRow(row.target, row.key, false)
          })}

          {unviable.length > 0 && (
            <>
              <tr className="tg-unviable-hdr">
                <td colSpan={3}>Unavailable Tonight</td>
              </tr>
              {unviableRows.map(row => {
                if (row.kind === 'header') {
                  return (
                    <tr key={row.key} className="tg-group-hdr tg-row-blocked">
                      <td colSpan={3}>{TYPE_LABELS[row.type] ?? row.type}</td>
                    </tr>
                  )
                }
                return renderTargetRow(row.target, row.key, true)
              })}
            </>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ── Nearby dark-sky results ──────────────────────────────────────────────────

function nearbyBortleClass(bortleClass: number | null): string {
  if (bortleClass == null) return 'nearby-bortle'
  let colorClass = 'nearby-bortle-excellent'
  if (bortleClass <= 2) colorClass = 'nearby-bortle-excellent'
  else if (bortleClass <= 4) colorClass = 'nearby-bortle-good'
  else if (bortleClass <= 6) colorClass = 'nearby-bortle-fair'
  else colorClass = 'nearby-bortle-poor'
  return `nearby-bortle ${colorClass}`
}

function NearbyResults(
  { data, imperial, originLat, originLon }:
  { data: NearbyResult; imperial: boolean; originLat: number; originLon: number },
) {
  const { origin_bortle, origin_sqm, radius_miles, results, light_domes, best_available } = data
  const sqmStr = origin_sqm != null ? ` (SQM ${origin_sqm.toFixed(1)})` : ''

  // Convert stored miles to the active unit system
  const fmtMi = (mi: number) => fmtDist(mi * 1.60934, imperial)
  // Prefer actual road distance from the routing API; fall back to straight-line when a
  // candidate wasn't routed (raw "Remote" fallbacks).
  const distOf = (p: NearbyPlace) => fmtMi(p.drive_miles ?? p.distance_miles)
  // Google Maps driving directions, origin → location (falls back to a place pin).
  const dirLink = (p: NearbyPlace) =>
    `https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLon}` +
    `&destination=${p.lat},${p.lon}&travelmode=driving`

  const placeStr = (p: NearbyPlace) =>
    p.name ?? `${p.lat.toFixed(2)}°, ${p.lon.toFixed(2)}°`
  const formatDriveTime = (minutes: number | null): string | null => {
    if (minutes == null) return null
    const hrs = Math.floor(minutes / 60)
    const mins = minutes % 60
    return hrs > 0 ? `${hrs} hr ${mins} min` : `${mins} min`
  }
  const POI_TYPE_LABEL: Record<string, string> = {
    parking: 'Parking', viewpoint: 'Viewpoint', camp_site: 'Campsite', rest_area: 'Rest area',
    caravan_site: 'RV park', picnic_site: 'Picnic area', ranger_station: 'Ranger station',
    observatory: 'Observatory', attraction: 'Attraction', information: 'Info point',
    tourism: 'Tourism', pier: 'Pier', lighthouse: 'Lighthouse', tower: 'Observation tower',
    summer_camp: 'Summer camp', firepit: 'Fire pit', beach_resort: 'Beach resort',
    historic: 'Historic site',
  }
  // Render a place name with a category badge (routable POIs) or a "Remote" tag (off-road
  // fallbacks), plus a Google Maps driving-directions link on every result.
  const placeNode = (p: NearbyPlace) => {
    const appLink = `?lat=${p.lat.toFixed(5)}&lon=${p.lon.toFixed(5)}`
    return (
      <>
        <a className="poi-namelink" href={appLink}>{placeStr(p)}</a>
        {p.area_name && <span className="poi-area">{p.area_name}</span>}
        {p.is_poi
          ? (p.poi_type && <span className="poi-badge">{POI_TYPE_LABEL[p.poi_type] ?? p.poi_type}</span>)
          : <span className="poi-remote">Remote</span>}
        <a className="poi-maplink" href={dirLink(p)} target="_blank" rel="noopener noreferrer">Directions ↗</a>
      </>
    )
  }

  return (
    <>
      <p className="nearby-origin">
        Origin: <span className={nearbyBortleClass(origin_bortle)}>Bortle {origin_bortle}</span>{sqmStr}  ·  {fmtMi(radius_miles)} radius
      </p>

      {/* 1. Note when already at Bortle 1 — results still shown below */}
      {origin_bortle <= 1 && results.length > 0 && (
        <p className="sat-notice">
          Already at Bortle {origin_bortle}{sqmStr} — showing other Bortle 1 sites within {fmtMi(radius_miles)}.
        </p>
      )}

      {/* 2. Empty state */}
      {results.length === 0 && (
        <p className="sat-notice">
          {origin_bortle <= 1
            ? `No other Bortle 1 sites found within ${fmtMi(radius_miles)}.`
            : `No significantly darker sky found within ${fmtMi(radius_miles)}.`
          }
          {best_available && origin_bortle > 1 && (
            <> Closest darker spot: <span className={nearbyBortleClass(best_available.bortle_class)}>Bortle {best_available.bortle_class}</span>, {distOf(best_available)} {best_available.direction}{formatDriveTime(best_available.drive_minutes) ? ` · ${formatDriveTime(best_available.drive_minutes)} drive` : ''}  ({placeNode(best_available)})</>
          )}
        </p>
      )}

      {/* 3. Results table */}
      {results.length > 0 && (() => {
        const hasDrive = results.some(p => p.drive_minutes != null)

        // New Tiered Drive-Time Sort
        const sortedByDriveTime = [...results].sort((a, b) => {
          const bothHaveDrive = a.drive_minutes != null && b.drive_minutes != null;

          // Group pristine skies (Bortle 1 & 2) at the top
          const aIsTopTier = a.bortle_class <= 2;
          const bIsTopTier = b.bortle_class <= 2;

          if (aIsTopTier !== bIsTopTier) {
            return aIsTopTier ? -1 : 1;
          }

          // Sort by drive time (or distance fallback) within tiers
          if (bothHaveDrive) {
            if (a.drive_minutes !== b.drive_minutes) {
              return a.drive_minutes! - b.drive_minutes!;
            }
          } else {
            if (a.distance_miles !== b.distance_miles) {
              return a.distance_miles - b.distance_miles;
            }
          }

          // Tie-breaker: Darkest sky
          return a.bortle_class - b.bortle_class;
        });

        const nearest = sortedByDriveTime[0];

        // Keep darkest calculation as-is (strict Bortle-first sort)
        const darkest = [...results].sort((a, b) =>
          a.bortle_class !== b.bortle_class ? a.bortle_class - b.bortle_class : a.distance_miles - b.distance_miles
        )[0]

        const showDarkest = darkest !== nearest && darkest.bortle_class < nearest.bortle_class

        return (
          <>
            <div className="nearby-highlights">
              <div className="nearby-highlight-row">
                <span className="nearby-highlight-label">Nearest</span>
                <span><span className={nearbyBortleClass(nearest.bortle_class)}>Bortle {nearest.bortle_class}</span>  ·  {distOf(nearest)} {nearest.direction}{formatDriveTime(nearest.drive_minutes) ? `  ·  ${formatDriveTime(nearest.drive_minutes)} drive` : ''}  ({placeNode(nearest)})</span>
              </div>
              {showDarkest && (
                <div className="nearby-highlight-row">
                  <span className="nearby-highlight-label">Darkest</span>
                  <span><span className={nearbyBortleClass(darkest.bortle_class)}>Bortle {darkest.bortle_class}</span>  ·  {distOf(darkest)} {darkest.direction}{formatDriveTime(darkest.drive_minutes) ? `  ·  ${formatDriveTime(darkest.drive_minutes)} drive` : ''}  ({placeNode(darkest)})</span>
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
                    // Order by drive time, lowest first; unrouted (no ETA) last, then by distance.
                    .sort((a, b) => {
                      const ad = a.drive_minutes, bd = b.drive_minutes
                      if (ad == null && bd == null) return a.distance_miles - b.distance_miles
                      if (ad == null) return 1
                      if (bd == null) return -1
                      return ad - bd || a.bortle_class - b.bortle_class
                    })
                    .map((p, i) => (
                      <tr key={i}>
                        <td className={nearbyBortleClass(p.bortle_class)}>{placeNode(p)}</td>
                        <td className={`wx-num ${nearbyBortleClass(p.bortle_class)}`}>{p.bortle_class}</td>
                        <td className="wx-num">{p.sqm != null ? p.sqm.toFixed(1) : '—'}</td>
                        <td className="wx-num">{distOf(p)}</td>
                        {hasDrive && <td className="wx-num">{formatDriveTime(p.drive_minutes) ?? '—'}</td>}
                        <td className="wx-num">{p.direction}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </>
        )
      })()}

      {/* 4. ALWAYS show domes if they exist, regardless of origin Bortle */}
      {light_domes.length > 0 && (
        <div className="nearby-domes">
          <div className="nearby-domes-label">Light domes</div>
          {light_domes.map((d, i) => (
            <div key={i} className="nearby-dome-row">
              {placeStr(d)}  ·  <span className={nearbyBortleClass(d.bortle_class)}>Bortle {d.bortle_class}</span>  ·  {fmtMi(d.distance_miles)} {d.direction}
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// ── Light dome (all-sky fisheye) ─────────────────────────────────────────────
// An all-sky heatmap of horizon light pollution: centre = zenith (dark), rim = the
// 360° horizon, N at top. Each direction's horizon glow blooms upward by that
// dome's apparent height, so a distant low metro dome hugs the rim while a near one
// reaches higher. Mirrors the engine: glow(az,alt) = score(az)/(1+(alt/θ(az))²)
// (PyNightSkyPredictor/light_dome.py glow_toward).
// LD constants and utility functions (LD_DIRS, ldTent, glowToward, etc.) are defined
// earlier in this file, before MilkyWayDome, so archSegmentBrightness can use them.

// Nearest hourly weather point to a given ISO time.
function wxAtTime(points: WeatherPoint[], isoTime: string): WeatherPoint | null {
  if (!points.length) return null
  const t = new Date(isoTime).getTime()
  return points.reduce((a, b) =>
    Math.abs(new Date(a.time).getTime() - t) <= Math.abs(new Date(b.time).getTime() - t) ? a : b)
}

// Commenting out.
/* Compact conditions badge: WMO weather icon + rating score + horizon glow label.
// Used in both the prime targets table and the Milky Way waypoints table.
function CondBadges({ wxPt, glow }: { wxPt: WeatherPoint | null; glow: number | null }) {
  const rating = wxPt != null ? rateConditions(wxPt) : null
  return (
    <span className="cond-badges">
      {wxPt != null && rating != null && (
        <span className={`cond-badge wx-rating-${scoreBand(rating)}`}>
          <WmoIcon code={wxPt.weather_code} size={13} />
        </span>
      )}
      {glow != null && glow > 0 && (
        <span className="cond-badge cond-glow" style={glowStyle(glow)}>
          {glow >= 0.03 ? `Light dome ${glowLabel(glow)}` : null}
        </span>
      )}
    </span>
  )
}*/

function LightDomePanel({ summary, imperial }: { summary: LightDomeSummary; imperial: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  // Disk size; matched to the score card's content (the meta block) so the panel
  // doesn't make the card taller. Capped at LD_SIZE; falls back to it pre-measure.
  const [size, setSize] = useState(LD_SIZE)
  const { sky_state, scores, darkest_direction, domes } = summary
  // The darkest horizon is only a meaningful "point here" call when a darker side exists.
  const showBest = sky_state === 'dark' || sky_state === 'domed'

  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return
    // One-shot measure after DOM settles. A ResizeObserver on .meta caused a
    // feedback loop: canvas resize → meta flex-width change → meta height change
    // → observer fires → canvas resize → ... (visible as blinking at ~75% window width).
    const raf = requestAnimationFrame(() => {
      const meta = panel.closest('.overall')?.querySelector('.meta') as HTMLElement | null
      if (!meta) return
      const titleH = (panel.querySelector('.ld-title') as HTMLElement | null)?.offsetHeight ?? 18
      const avail = meta.offsetHeight - titleH - 8
      setSize(Math.max(88, Math.min(LD_SIZE, Math.round(avail))))
    })
    return () => cancelAnimationFrame(raf)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = Math.round(size * dpr)
    canvas.width = W
    canvas.height = W
    canvas.style.width = `${size}px`
    canvas.style.height = `${size}px`

    // Per-direction glow and apparent dome height (real height for flagged domes).
    const domeH: Partial<Record<Direction, number>> = {}
    for (const d of domes) domeH[d.direction] = d.dome_height_deg
    const scoreArr = LD_DIRS.map(d => scores[d] ?? 0)
    const thetaArr = LD_DIRS.map(d =>
      domeH[d] != null ? Math.max(domeH[d]! * LD_THETA_K, LD_THETA_FLOOR_DEG) : LD_THETA_DEFAULT_DEG)

    // Pixel pass over the disk (device resolution; transform-independent putImageData).
    const margin = Math.max(11, size * 0.095)   // room for the N/E/S/W labels
    const cx = W / 2, cy = W / 2
    const R = (size / 2 - margin) * dpr
    const img = ctx.createImageData(W, W)
    const buf = img.data
    for (let y = 0; y < W; y++) {
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4
        const dx = x - cx, dy = y - cy
        const rr = Math.sqrt(dx * dx + dy * dy)
        if (rr > R) { buf[i + 3] = 0; continue }
        const alt = 90 * (1 - rr / R)                       // centre = zenith, rim = horizon
        const az = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360  // N up, clockwise
        const sc = ldTent(scoreArr, az)
        const th = ldTent(thetaArr, az)
        const g = sc / (1 + (alt / th) ** 2)
        const [r, gg, b] = ldColor(g)
        const edge = Math.max(0, Math.min(1, (R - rr) / (1.5 * dpr)))  // soft rim AA
        buf[i] = r; buf[i + 1] = gg; buf[i + 2] = b; buf[i + 3] = 255 * edge
      }
    }
    ctx.putImageData(img, 0, 0)

    // Decorations in CSS px.
    ctx.scale(dpr, dpr)
    const c = size / 2
    const cssR = size / 2 - margin
    ctx.lineWidth = 1
    for (const rad of [cssR * 0.5, cssR * 0.83]) {
      ctx.beginPath(); ctx.arc(c, c, rad, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(148,163,184,0.12)'; ctx.stroke()
    }
    ctx.beginPath(); ctx.arc(c, c, cssR, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(148,163,184,0.22)'; ctx.stroke()

    const lab = margin * 0.45
    ctx.fillStyle = '#94A3B8'
    ctx.font = '600 10px Poppins, system-ui, sans-serif'
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText('N', c, lab); ctx.fillText('S', c, size - lab)
    ctx.fillText('E', size - lab, c); ctx.fillText('W', lab, c)

    if (showBest) {
      const a = (LD_DIRS.indexOf(darkest_direction) * 45) * Math.PI / 180
      ctx.fillStyle = '#34D399'
      ctx.font = '700 12px Poppins, system-ui, sans-serif'
      ctx.fillText('★', c + Math.sin(a) * (cssR - 8), c - Math.cos(a) * (cssR - 8))
    }
  }, [summary, scores, darkest_direction, domes, sky_state, showBest, size])

  const fmtMi = (mi: number) => fmtDist(mi * 1.60934, imperial)
  const top = domes[0]
  const topDist = top?.mean_distance_mi != null ? `  ·  ${fmtMi(top.mean_distance_mi)}` : ''

  const aria =
    sky_state === 'urban' ? 'Urban sky: horizon washed out in all directions.'
    : sky_state === 'bright' ? `Bright sky: uniform glow, darkest horizon to the ${darkest_direction}.`
    : sky_state === 'domed' ? `${top?.label ?? 'Light dome'}. Darkest horizon to the ${darkest_direction}.`
    : `Dark sky. Darkest horizon to the ${darkest_direction}.`

  return (
    <div className="lightdome-panel" ref={panelRef}>
      <div className="ld-title">Horizon Glow</div>
      <div className="ld-body">
      <canvas ref={canvasRef} className="ld-canvas" role="img" aria-label={aria} />
      <div className="ld-caption">
        <span className={`ld-state ld-state-${sky_state}`}>
          {sky_state === 'dark' ? 'Dark sky'
            : sky_state === 'domed' ? 'Light dome'
            : sky_state === 'bright' ? 'Bright sky'
            : 'Urban sky'}
        </span>
        {sky_state === 'domed' && (
          <>
            <span className="ld-line">{top?.label}{topDist}</span>
            <span className="ld-sub">Best view <b>{darkest_direction}</b></span>
          </>
        )}
        {sky_state === 'dark' && (
          <span className="ld-line">Darkest horizon <b>{darkest_direction}</b></span>
        )}
        {sky_state === 'bright' && (
          <>
            <span className="ld-line">Uniform glow, no single dome</span>
            <span className="ld-sub">Darkest <b>{darkest_direction}</b>, still washed</span>
          </>
        )}
        {sky_state === 'urban' && (
          <span className="ld-line">Washed out in all directions</span>
        )}
        <span className="ld-legend" aria-hidden="true">
          <span>dark</span><span className="ld-legend-bar" /><span>dome</span>
        </span>
      </div>
      </div>
    </div>
  )
}

// ── Main report card ─────────────────────────────────────────────────────────

export default function ReportCard({
  report,
  showWeather = false,
  showTargets = false,
  showSatellites = false,
  imperial = false,
  onToggleUnits,
}: {
  report: NightReport
  showWeather?: boolean
  showTargets?: boolean
  showSatellites?: boolean
  imperial?: boolean
  onToggleUnits?: (imp: boolean) => void
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
  // Short form for the dense score card — removes zone, source citation, keeps class + description
  const shortLps = lp?.bortle_class != null
    ? [`Bortle ${lp.bortle_class}`, lp.bortle_desc ?? null].filter(Boolean).join('  ·  ')
    : lps
  const tzZ = r.sunset ? tzAbbr(tz) : tz

  // Moon line
  const specialTags = []
  if (r.moon_special) specialTags.push(`*** ${r.moon_special.charAt(0).toUpperCase() + r.moon_special.slice(1)} ***`)
  for (const e of r.moon_eclipses ?? []) {
    const kind = e.kind.charAt(0).toUpperCase() + e.kind.slice(1)
    const mag  = (e.kind === 'partial' || e.kind === 'total')
      ? `umbral ${e.umbral_magnitude?.toFixed(3)}`
      : `penumbral ${e.penumbral_magnitude?.toFixed(3)}`
    specialTags.push(`${kind} lunar eclipse at ${formatTime(e.time, tz)}  (mag ${mag})`)
  }
  // Compact version for the score card: phase + illumination only
  const moonStrCard = `${r.phase_name}  ·  ${r.illumination_pct.toFixed(1)}% illuminated`
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
  // Intersect dark intervals with clear weather windows (cloud cover ≤ 50%).
  // Each weather point covers the 1-hour window starting at its timestamp.
  // Falls back to purely astronomical intervals when no weather data is present.
  const CLOUD_CLEAR_THRESHOLD = 50
  const clearDarkIntervals: [string, string][] | null = (() => {
    const pts = r.weather_points.filter(p => p.cloud_cover_pct != null && p.cloud_cover_pct <= CLOUD_CLEAR_THRESHOLD)
    if (!r.weather_points.length || !r.weather_points.some(p => p.cloud_cover_pct != null)) return null
    if (!r.dark_intervals.length) return []
    const HOUR_MS = 3600 * 1000
    const raw: [number, number][] = []
    for (const [ds, de] of r.dark_intervals) {
      const dStart = new Date(ds).getTime()
      const dEnd   = new Date(de).getTime()
      for (const p of pts) {
        const ps = new Date(p.time).getTime()
        const pe = ps + HOUR_MS
        const is = Math.max(dStart, ps)
        const ie = Math.min(dEnd,   pe)
        if (is < ie) raw.push([is, ie])
      }
    }
    if (!raw.length) return []
    raw.sort((a, b) => a[0] - b[0])
    const merged: [number, number][] = [raw[0]]
    for (let i = 1; i < raw.length; i++) {
      const last = merged[merged.length - 1]
      if (raw[i][0] <= last[1]) { last[1] = Math.max(last[1], raw[i][1]) }
      else merged.push(raw[i])
    }
    return merged.map(([s, e]) => [new Date(s).toISOString(), new Date(e).toISOString()])
  })()
  const clearDarkHours = clearDarkIntervals
    ? clearDarkIntervals.reduce((sum, [s, e]) => sum + (new Date(e).getTime() - new Date(s).getTime()) / 3_600_000, 0)
    : null

  // Compact version for the score card — tonight's window only, no cycle average
  const darkStrCard = (() => {
    if (clearDarkIntervals === null) {
      // No weather data — show purely astronomical dark window
      return r.dark_intervals.length > 0
        ? `${formatHm(r.dark_hours)}  (${r.dark_intervals.map(([s, e]) => `${formatTime(s, tz)} – ${formatTime(e, tz)}`).join(',  ')} ${tzZ})`
        : darkStr
    }
    if (clearDarkIntervals.length === 0) {
      return r.dark_intervals.length > 0 ? 'None (clouded out during dark window)' : darkStr
    }
    const spans = clearDarkIntervals.map(([s, e]) => `${formatTime(s, tz)} – ${formatTime(e, tz)}`).join(',  ')
    return `${formatHm(clearDarkHours!)}  (${spans} ${tzZ})`
  })()

  // Human-readable date
  const formattedDate = new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  }).format(new Date(r.date + 'T00:00:00'))

  const placePrimary = r.display_name.split(',')[0].trim()
  const placeSecondary = r.display_name.includes(',')
    ? r.display_name.split(',').slice(1).join(',').trim()
    : null

  return (
    <section className="card report">
      <header className="report-head">
        <div className="report-head-meta">
          <h2 className="place">
            {placePrimary}
            <a
              className="place-mappin"
              href={`https://www.google.com/maps?q=${r.lat},${r.lon}`}
              target="_blank"
              rel="noopener noreferrer"
              title="View on Google Maps"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5S10.62 6.5 12 6.5s2.5 1.12 2.5 2.5S13.38 11.5 12 11.5z"/>
              </svg>
            </a>
          </h2>
          {placeSecondary && <p className="place-sub">{placeSecondary}</p>}
          <p className="when">
            {formattedDate}  ·  {tzTitle(tz)}  ·  ({r.lat.toFixed(4)}°, {r.lon.toFixed(4)}°)
          </p>
        </div>
        {onToggleUnits && (
          <div className="units-toggle" role="group" aria-label="Unit system">
            <button type="button" className={!imperial ? 'active' : ''} onClick={() => onToggleUnits(false)}>°C / m/s</button>
            <button type="button" className={imperial ? 'active' : ''} onClick={() => onToggleUnits(true)}>°F / mph</button>
          </div>
        )}
      </header>

      <div className={`overall band-${scoreBand(r.score)}`}>
        <div className="overall-num">{r.score.toFixed(1)}</div>
        <div className="overall-meta">
          <div className="overall-label">{scoreLabel(r.score)}</div>
          <div className="overall-sub">0–10 composite score</div>
        </div>
          <div className="meta">
        {shortLps && <MetaRow k="Light Pollution" v={shortLps} />}

        {(r.active_showers?.length ?? 0) > 0 && (
          <MetaRow
            k="Meteor Showers"
            v={r.active_showers.map(s => `${s.name}  ·  ${s.note}  ·  ZHR ${s.zhr}`).join(',  ')}
          />
        )}
        <MetaRow k="Clear Dark Sky" v={darkStrCard} />
        {showWeather && r.weather_score != null && (
          <MetaRow
            k="Weather"
            v={`${r.weather_score.toFixed(1)}/10${r.wx_source ? `  ·  ${r.wx_source}` : ''}`}
          />
        )}
        {showWeather && r.wx_pending && <MetaRow k="Weather" v="Pending  (beyond the ~7-day forecast horizon)" />}
        {showWeather && r.wx_no_data && <MetaRow k="Weather" v="No data  (not covered for this location/date)" />}
        {showWeather && r.wx_error && !r.weather_points.length && <MetaRow k="Weather" v="Temporarily unavailable: weather providers are down" />}
        <MetaRow k="Lunar Conditions" v={moonStrCard}
          icon={<MoonPhaseSvg phaseName={r.phase_name} illuminationPct={r.illumination_pct} size={30} />}
        />
        </div>
        {r.light_dome && <LightDomePanel summary={r.light_dome} imperial={imperial} />}
      </div>

      <div className="bars">
        {r.score_components.bortle  != null && <ScoreBar label="Dark Sky Quality"      value={r.score_components.bortle} />}
        {r.score_components.moon    != null && <ScoreBar label="Lunar Conditions"         value={r.score_components.moon} />}
        {r.score_components.dark    != null && <ScoreBar label="Dark Sky Hours"    value={r.score_components.dark} />}
        {showWeather && r.score_components.weather != null && <ScoreBar label="Weather" value={r.score_components.weather} />}
      </div>


        <details className="nearby-section" open>
        <summary>Find Sky Nearby</summary>
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
            <NearbyResults data={nearbyState.data} imperial={imperial} originLat={report.lat} originLon={report.lon} />
          )}
        </div>
      </details>

      {(r.events.length > 0 || (showWeather && r.weather_points.length > 0)) && (
        <WeatherTable
          points={showWeather ? r.weather_points : []}
          events={r.events}
          tz={tz}
          imperial={imperial}
          darkIntervals={r.dark_intervals}
        />
      )}

      {showTargets && (() => {
        const showerTargets  = r.visible_targets.filter(t => t.type === 'meteor_shower')
        const primeDSOs      = r.visible_targets
          .filter(t => t.type !== 'milky_way' && t.type !== 'meteor_shower')
          .filter(t => isPrime(t, r.dark_intervals))
          .filter(t => (t.landscape_suitability ?? 'prominent') === 'prominent')
        const viableCount    = primeDSOs.filter(t => t.viability !== 'blocked').length
        const blockedCount   = primeDSOs.filter(t => t.viability === 'blocked').length
        const hasAnything    = r.visible_targets.length > 0

        return (
        <details className="targets" open>
          <summary>
            Iconic Sky Features
            {viableCount > 0 ? ` (${viableCount})` : ''}
            {blockedCount > 0 ? ` · ${blockedCount} blocked` : ''}
          </summary>
          {!hasAnything
            ? <p className="sat-notice" style={{ paddingTop: 10 }}>No prime targets for this night.</p>
            : <>
                <div className="mw-section">
                  <div className="mw-section-label">Milky Way</div>
                  {r.mw_summary && r.mw_summary.n_visible > 1
                    ? <MilkyWayCard
                        summary={r.mw_summary}
                        waypoints={r.visible_targets.filter(t => t.type === 'milky_way')}
                        report={r}
                      />
                    : <MilkyWayAbsent report={r} />
                  }
                </div>
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
                {primeDSOs.length > 0 && (
                  <>
                    <div className="iconic-section-divider" />
                    <div className="mw-section-label iconic-targets-label">
                      Deep Sky Targets{primeDSOs.some(t => t.type === 'planet') ? ' & Planets' : ''}
                    </div>
                  </>
                )}
                <TargetsTable targets={r.visible_targets} report={r} />
                {primeDSOs.length === 0 && showerTargets.length === 0 && (
                  <p className="sat-notice" style={{ paddingTop: 10 }}>
                    No deep-sky targets meet prime criteria (≥40° altitude, ≥1h window) this night.
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


    </section>
  )
}
