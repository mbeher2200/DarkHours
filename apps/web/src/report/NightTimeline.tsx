import { useState, useEffect, useMemo } from 'react'
import type { SkyEvent, WeatherPoint } from '../types'
import { formatTime, formatDayTime, fmtTempValue, tempUnitLabel, fmtWindSpeed, windUnitLabel, moonUpAt, formatAge, formatIssuedUtc } from '../format'
import { InfoTip } from '../shared'
import { Star } from 'lucide-react'
import { WiIcon, TRANSP_ABBR, seeingTier, AtmosEq, SkyCover, WmoIcon, showCloudTotal } from './icons'
import { cell } from './common'

// ── Weather table ────────────────────────────────────────────────────────────

export function WeatherTable({ points, events = [], tz, imperial, moonrise, moonset, isFetching = false, cathodeSnap = false, wxSource = null, wxFetchedAt = null }: {
  points: WeatherPoint[]
  events?: SkyEvent[]
  tz: string
  imperial: boolean
  moonrise?: string | null
  moonset?: string | null
  isFetching?: boolean
  cathodeSnap?: boolean
  wxSource?: string | null
  wxFetchedAt?: string | null
}) {
  // Clip the table to the sunset→sunrise window. Events/points outside this
  // range are daytime and not useful once the astro-night band conveys darkness.
  // sunriseTs must be the sunrise AFTER sunset — for western-US summer dates the
  // events array can start with an early-morning sunrise (end of the previous night,
  // UTC date = target date) before the actual sunset that starts the target night.
  const sunsetTs  = (() => { const e = events.find(e => e.label.toLowerCase().includes('sunset'));  return e ? new Date(e.time).getTime() : -Infinity })()
  const sunriseTs = (() => {
    const afterSunset = events.find(e => e.label.toLowerCase().includes('sunrise') && new Date(e.time).getTime() > sunsetTs)
    if (afterSunset) return new Date(afterSunset.time).getTime()
    const first = events.find(e => e.label.toLowerCase().includes('sunrise'))
    return first ? new Date(first.time).getTime() : Infinity
  })()
  const visiblePoints = points.filter(p => { const t = new Date(p.time).getTime(); return t >= sunsetTs && t <= sunriseTs })
  const visibleEvents = events.filter(e => {
    const t = new Date(e.time).getTime()
    return t >= sunsetTs && t <= sunriseTs
  })

  const hasTemp   = visiblePoints.some(p => p.temperature_c   != null)
  const hasDew    = visiblePoints.some(p => p.dew_point_c     != null)
  const hasSeeing = visiblePoints.some(p => p.seeing_arcsec   != null)
  const hasTransp = visiblePoints.some(p => p.transparency    != null)
  const hasAtmos  = hasSeeing || hasTransp
  const hasWx     = visiblePoints.length > 0
  const totalCols = 5 + (hasAtmos ? 1 : 0) + ((hasTemp || hasDew) ? 1 : 0)

  // Astronomical night window (sun-based, matches the "ASTRO DARK BEGINS/ENDS"
  // divider rows below) — NOT darkIntervals/dark_intervals, which is a different
  // concept (astro night intersected with moon-below-horizon) that goes empty
  // whenever the moon is up past the crescent exemption, even on a clear night.
  const astroStartTs = (() => { const e = events.find(e => e.label.toLowerCase().includes('astronomical night') && e.label.toLowerCase().includes('begins')); return e ? new Date(e.time).getTime() : null })()
  const astroEndTs   = (() => { const e = events.find(e => e.label.toLowerCase().includes('astronomical night') && e.label.toLowerCase().includes('ends'));   return e ? new Date(e.time).getTime() : null })()

  // Moon up/down at an arbitrary timestamp, from the full sky-events list — used to
  // pick between the full-dark and moonlit-dark shades within the astro window.
  // Generalizes to a full moonrise→moonset→moonrise cycle in one night (long winter
  // nights), unlike moonUpAt() in format.ts, which only handles one rise/set pair.
  // A transition event's own timestamp takes the state it introduces (inclusive).
  const sortedMoonEvents = events
    .filter(e => { const l = e.label.toLowerCase(); return l.includes('moonrise') || l.includes('moonset') })
    .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
  function moonUpAtTs(ts: number): boolean {
    const lastAtOrBefore = [...sortedMoonEvents].reverse().find(e => new Date(e.time).getTime() <= ts)
    if (lastAtOrBefore) return lastAtOrBefore.label.toLowerCase().includes('moonrise')
    const firstAfter = sortedMoonEvents.find(e => new Date(e.time).getTime() > ts)
    if (!firstAfter) return false
    return !firstAfter.label.toLowerCase().includes('moonrise')
  }

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

  // Live "now" marker — updates every 30s so it stays accurate if the page is
  // left open. Only injected when the current moment is inside the night window.
  const [nowTs, setNowTs] = useState(Date.now)
  useEffect(() => {
    const id = setInterval(() => {
      // Skip update when the tab is hidden — avoids re-sorting hidden content.
      if (document.visibilityState === 'visible') setNowTs(Date.now())
    }, 30_000)
    return () => clearInterval(id)
  }, [])
  const isLive = sunsetTs !== -Infinity && sunriseTs !== Infinity
               && nowTs > sunsetTs && nowTs < sunriseTs

  type Row = { kind: 'event'; ev: SkyEvent; ts: number } | { kind: 'wx'; pt: WeatherPoint; ts: number } | { kind: 'now'; ts: number }

  // Memoize the static sorted rows (events + weather points). These only change when
  // the underlying data changes, not every 30s when the "now" marker ticks.
  const baseRows = useMemo<Row[]>(() => [
    ...visibleEvents.map(ev => ({ kind: 'event' as const, ev, ts: new Date(ev.time).getTime() })),
    ...visiblePoints.map(pt => ({ kind: 'wx'    as const, pt, ts: new Date(pt.time).getTime() })),
  ].sort((a, b) => a.ts - b.ts), [visibleEvents, visiblePoints])

  // Insert the live "now" row at the correct sorted position on each tick.
  // Suppressed while recalculating — showing "now" against stale, about-to-be-
  // replaced rows is misleading mid-fetch.
  const rows = useMemo<Row[]>(() => {
    if (!isLive || isFetching) return baseRows
    const nowRow: Row = { kind: 'now', ts: nowTs }
    const insertAt = baseRows.findIndex(r => r.ts > nowTs)
    if (insertAt === -1) return [...baseRows, nowRow]
    return [...baseRows.slice(0, insertAt), nowRow, ...baseRows.slice(insertAt)]
  }, [baseRows, isLive, isFetching, nowTs])

  const ip = { size: 12, strokeWidth: 1.5, style: { flexShrink: 0 } } as const
  // Sunrise/sunset/moonrise/moonset/astronomical-night all render as text dividers
  // (see isDivider below) — twilight is the only event type that still shows an icon.
  function evIcon(label: string) {
    const l = label.toLowerCase()
    if (l.includes('twilight')) return <Star {...ip} />
    return null
  }
  function evClass(label: string): string {
    const l = label.toLowerCase()
    if (l.includes('sunrise') || l.includes('sunset')) return 'wx-ev-sun'
    if (l.includes('moonrise') || l.includes('moonset')) return 'wx-ev-moon'
    if (l.includes('astronomical night')) return 'wx-ev-astro'
    return ''
  }

  // Collapsed-summary takeaway: cloud range + worst wind across the night.
  const wxBrief = (() => {
    if (!visiblePoints.length) return null
    const clouds = visiblePoints.map(p => p.cloud_cover_pct).filter((v): v is number => v != null)
    const parts: string[] = []
    if (clouds.length) {
      const minC = Math.round(Math.min(...clouds))
      const maxC = Math.round(Math.max(...clouds))
      parts.push(maxC <= 15 ? 'Clear' : `Clouds ${minC}–${maxC}%`)
    }
    const winds = visiblePoints.map(p => Math.max(p.wind_speed_ms ?? 0, p.wind_gust_ms ?? 0))
    const maxWind = winds.length ? Math.max(...winds) : 0
    parts.push(maxWind < 3 ? 'wind calm' : `wind to ${fmtWindSpeed(maxWind, imperial)} ${windUnitLabel(imperial)}`)
    return parts.join(' · ')
  })()

  return (
    <details id="report-timeline" className="wx-details" open>
      <summary>
        Night Timeline
        {sunsetTs !== -Infinity && sunriseTs !== Infinity &&
          `: ${formatDayTime(new Date(sunsetTs).toISOString(), tz)} → ${formatDayTime(new Date(sunriseTs).toISOString(), tz)}`}
        {wxBrief && <span className="sum-brief"> · {wxBrief}</span>}
      </summary>
      <div className={`wx-table-wrap${isFetching ? ' is-recalculating' : ''}${cathodeSnap ? ' cathode-snap' : ''}`}>
        {isFetching && (
          <div className="recalc-overlay" role="status" aria-live="polite">
            RECALCULATING...<span className="recalc-cursor" aria-hidden="true">█</span>
          </div>
        )}
        <table className="wx-table">
          {hasWx && (
            <thead>
              <tr>
                <th>Time</th>
                <th className="wx-cond-col"></th>
                <th className="wx-cloud-col wx-cloud-hdr">
                  <InfoTip tip={<>The big number is total cloud cover. The telemetry stack breaks it out by altitude — high (&gt;20kft / &gt;6km), mid (&gt;6kft / &gt;2km), low (&lt;6kft / &lt;2km) — with more filled blocks meaning more cloud at that layer.</>}>
                    Sky Cover
                  </InfoTip>
                </th>
                {hasAtmos  && (
                  <th className="wx-atmos-col wx-atmos-hdr">
                    <InfoTip tip={<>A segmented readout — more bars is better (Full = Optimal). C — Clarity (transparency): how cleanly light passes the air column (Optimal → Poor). S — Seeing: turbulence blur, steadier is better (under 1.5″ is pin-sharp, over 2.5″ smears stars, matters most at long focal lengths). Both from 7Timer's astro forecast.</>}>
                      Clarity /<br />Seeing
                    </InfoTip>
                  </th>
                )}
                {(hasTemp || hasDew) && <th className="wx-temp-col wx-temp-hdr">TEMP/<br />DEW PT</th>}
                <th className="wx-wind-col">Wind</th>
              </tr>
            </thead>
          )}
          <tbody>
            {rows.map((row, i) => {
              if (row.kind === 'now') {
                return (
                  <tr key="now-marker" className="wx-now-row">
                    <td className="wx-time wx-now-time">{formatTime(new Date(row.ts).toISOString(), tz)}</td>
                    <td colSpan={hasWx ? totalCols - 1 : 1} className="wx-now-content">
                      <span className="wx-now-inner">▶ Now</span>
                    </td>
                  </tr>
                )
              }
              if (row.kind === 'event') {
                const icon    = evIcon(row.ev.label)
                const cls     = evClass(row.ev.label)
                const l       = row.ev.label.toLowerCase()
                const isSunset   = l.includes('sunset')
                const isSunrise  = l.includes('sunrise')
                const isMoonrise = l.includes('moonrise')
                const isMoonset  = l.includes('moonset')
                const isAstro    = l.includes('astronomical night')
                // Sunrise/sunset/moonrise/moonset/astro-night-begin/end all render as
                // the same plain, centered text divider — no icon, no italic label.
                const isDivider  = isSunrise || isSunset || isMoonrise || isMoonset || isAstro
                // Shade any event row landing inside the dark window (e.g. a moonrise
                // or moonset mid-window), not just the begin/end markers themselves —
                // lighter tone while the moon is up, full tone while it's down.
                const inDarkWindow  = astroStartTs != null && astroEndTs != null && row.ts >= astroStartTs && row.ts <= astroEndTs
                const astroShadeCls = inDarkWindow ? (moonUpAtTs(row.ts) ? 'wx-row-astro-lit' : 'wx-row-astro') : ''

                const dividerText = isAstro
                  ? (l.includes('begins') ? 'ASTRO DARK BEGINS' : 'ASTRO DARK ENDS')
                  : isSunset
                  ? (moonStateAtSunset === 'above' ? 'SUNSET - MOON UP' : moonStateAtSunset === 'below' ? 'SUNSET - MOON DOWN' : 'SUNSET')
                  : isSunrise
                  ? 'SUNRISE'
                  : isMoonrise
                  ? 'MOONRISE'
                  : 'MOONSET'

                return (
                  <tr key={`ev-${i}`} className={`wx-ev-row${cls ? ` ${cls}` : ''}${astroShadeCls ? ` ${astroShadeCls}` : ''}`}>
                  <td className="wx-time wx-ev-time">{formatTime(row.ev.time, tz)}</td>
                  <td colSpan={hasWx ? totalCols - 1 : 1} className="wx-ev-content">
                  <span className="wx-ev-inner">
                  {icon && !isDivider && <span className="wx-ev-icon">{icon}</span>}

                  {isDivider ? (
                    <span className="wx-ev-divider">{dividerText}</span>
                  ) : (
                    <span className="wx-ev-label">{row.ev.label}</span>
                  )}
            </span>
          </td>
        </tr>
  )
}
              const p = row.pt
              const inAstroWindow = astroStartTs != null && astroEndTs != null && row.ts >= astroStartTs && row.ts <= astroEndTs
              const astroShadeCls = inAstroWindow ? (moonUpAtTs(row.ts) ? 'wx-row-astro-lit' : 'wx-row-astro') : undefined
              const windSevere = p.wind_speed_ms != null && p.wind_speed_ms >= 6.3
              const gustSevere = p.wind_gust_ms  != null && p.wind_gust_ms  >= 6.3
              const dewSpread = p.temperature_c != null && p.dew_point_c != null ? p.temperature_c - p.dew_point_c : null
              const dewGate  = dewSpread != null && dewSpread <= 5
              return (
                <tr key={`wx-${i}`} className={astroShadeCls}>
                  <td className="wx-time">{formatTime(p.time, tz)}</td>
                  <td className="wx-num wx-rating">
                    {cell(isFetching, <div className="wx-cond-cell">
                      <WmoIcon code={p.weather_code} cloudCover={p.cloud_cover_pct}
                        moonUp={moonUpAt(p.time, moonrise ?? null, moonset ?? null)}
                        aod={p.aerosol_optical_depth} pm25={p.pm2_5} visibilityM={p.visibility_m} precipType={p.precip_type}
                        windSpeedMs={p.wind_speed_ms} windGustMs={p.wind_gust_ms} transparency={p.transparency} />
                      {showCloudTotal({ code: p.weather_code, cloudCover: p.cloud_cover_pct, precipType: p.precip_type,
                        windSpeedMs: p.wind_speed_ms, windGustMs: p.wind_gust_ms }) && (
                        <span className="wx-cond-total">{p.cloud_cover_pct}%</span>
                      )}
                    </div>)}
                  </td>
                  <td className="wx-num wx-cloud-col">
                    {cell(isFetching, <SkyCover
                      low={p.cloud_cover_low_pct}
                      mid={p.cloud_cover_mid_pct}
                      high={p.cloud_cover_high_pct}
                      imperial={imperial}
                    />)}
                  </td>
                  {hasAtmos && (
                    <td className="wx-num wx-atmos-col">
                      {cell(isFetching, <AtmosEq
                        clarity={p.transparency != null ? (TRANSP_ABBR[p.transparency] ?? p.transparency) : null}
                        seeing={p.seeing_arcsec != null ? seeingTier(p.seeing_arcsec) : null}
                      />)}
                    </td>
                  )}
                  {(hasTemp || hasDew) && (
                    <td className="wx-num wx-temp-col">
                      {cell(isFetching, <>
                        {p.temperature_c != null && <span className="wx-temp-val">{fmtTempValue(p.temperature_c, imperial)}</span>}
                        {p.temperature_c != null && p.dew_point_c != null && <span className="wx-temp-sep">/</span>}
                        {p.dew_point_c != null && (
                          <span className={`wx-temp-val wx-dew-val${dewGate ? ' wx-dew-warn' : ''}`}>
                            {fmtTempValue(p.dew_point_c, imperial)}
                          </span>
                        )}
                        {(p.temperature_c != null || p.dew_point_c != null) && (
                          <span className="wx-temp-unit">{tempUnitLabel(imperial)}</span>
                        )}
                      </>)}
                    </td>
                  )}
                  <td className={`wx-num wx-wind-col${windSevere ? ' wx-gate-warn' : ''}`}>
                {cell(isFetching, <>
                {p.wind_speed_ms != null ? (
                  <>
                    <span className="wx-wind-val">{fmtWindSpeed(p.wind_speed_ms, imperial)}</span>
                    {p.wind_gust_ms != null && (
                      <>
                        <span className="wx-wind-sep">/</span>
                        <span className={`wx-wind-val wx-wind-gust${gustSevere ? ' wx-wind-gust-severe' : ''}`}>
                          {fmtWindSpeed(p.wind_gust_ms, imperial)}
                        </span>
                      </>
                    )}
                    <span className="wx-wind-unit">{windUnitLabel(imperial)}</span>
                  </>
                ) : '—'}

                {p.wind_direction_deg != null && (
                  // wi-wind-deg's default orientation + weather-icons' own "towards-*"
                  // rotation classes assume the "towards" convention, but our data is
                  // "from" (standard meteorological convention — see wind.py). Rotate
                  // by +180° so the arrow points where the wind is heading, matching
                  // typical consumer weather-app wind arrows.
                  <WiIcon name="wind-deg" size={24}
                  style={{ marginLeft: 4, verticalAlign: 'middle', transform: `rotate(${(p.wind_direction_deg + 180) % 360}deg)` }} />
                )}
            </>)}
                </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {hasWx && <WxProvenanceBadge source={wxSource} fetchedAt={wxFetchedAt} />}
      </div>
    </details>
  )
}

// Dense, monospace "instrument readout" footer for the Night Timeline table — shows
// which weather source served this forecast and how fresh the data is.
export function WxProvenanceBadge({ source, fetchedAt }: { source: string | null; fetchedAt: string | null }) {
  if (!source) return null
  return (
    <div className="wx-provenance">
      <span className="wx-prov-item">SOURCE: {source.toUpperCase()}</span>
      <span className="wx-prov-sep">·</span>
      <span className="wx-prov-item">ISSUED: {formatIssuedUtc(fetchedAt)}</span>
      <span className="wx-prov-sep">·</span>
      <span className="wx-prov-item">UPDATED: {formatAge(fetchedAt)}</span>
    </div>
  )
}
