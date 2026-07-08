import { useState } from 'react'
import type { NightReport } from '../types'
import { formatTime, cardinal, rateConditions, scoreBand } from '../format'
import { WmoIcon } from './icons'
import { cell } from './common'
import { glowToward, glowLabel, glowStyle } from './glow'
import { wxAtTime } from './Targets'

// ── Satellite passes ─────────────────────────────────────────────────────────

export function SatellitePasses({ report, isFetching = false, cathodeSnap = false }: { report: NightReport; isFetching?: boolean; cathodeSnap?: boolean }) {
  const tz = report.tz_name
  // Clouded-out passes collapse to one summary row by default — on a bad
  // night a wall of identical "Clouded out" rows is noise, not signal.
  const [showBlocked, setShowBlocked] = useState(false)

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
      ? `${shadow.length} transit${shadow.length > 1 ? 's' : ''} tonight but in Earth's shadow: not visible.`
      : 'No notable orbital transits this night.'
    return (
      <>
        {notes.map((n, i) => <p key={i} className="sat-notice sat-note">{n}</p>)}
        <p className="sat-notice">{shadowMsg}</p>
        </>
    )
  }

  const az = (deg: number) => `${deg.toFixed(0)}° ${cardinal(deg)}`

  // Split passes into clear vs. clouded-out so a bad night doesn't render as
  // a wall of blank "Clouded out" rows — mirrors the Targets unviable rollup.
  const passInfo = display.map(p => {
    const wxAtPeak  = wxAtTime(report.weather_points || [], p.peak_time)
    const satCloudy = wxAtPeak != null && wxAtPeak.cloud_cover_pct != null && wxAtPeak.cloud_cover_pct > 70
    return { p, wxAtPeak, satCloudy }
  })
  const clearPasses  = passInfo.filter(x => !x.satCloudy)
  const cloudyPasses = passInfo.filter(x => x.satCloudy)
  const colCount = 12 + (report.light_dome ? 1 : 0)

  // Pass geometry (rise/peak/set) is astronomical fact, unaffected by weather —
  // clouded rows still show it, just flagged, rather than blanked out.
  function renderPassRow(p: typeof display[number], wxAtPeak: ReturnType<typeof wxAtTime>, i: number, cloudy: boolean) {
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
    return (
      <tr key={i} className={cloudy ? 'tg-row-blocked' : undefined}>
        <td>
          {cell(isFetching, <>
            {label}
            {cloudy && <span className="mw-moon-badge badge-poor sat-cloudy-badge"> Clouded out</span>}
          </>)}
        </td>
        <td className="wx-num">{cell(isFetching, formatTime(p.rise_time, tz))}</td>
        <td className="wx-num">{cell(isFetching, `${p.rise_alt_deg.toFixed(0)}°`)}</td>
        <td className="wx-num">{cell(isFetching, az(p.rise_az_deg))}</td>
        <td className="wx-num">
          {cell(isFetching, <>
            {formatTime(p.peak_time, tz)}
            {wxAtPeak && (
              <span className={`tg-wx-inline wx-rating-${scoreBand(rateConditions(wxAtPeak))}`}>
                <WmoIcon code={wxAtPeak.weather_code} size={12} />
              </span>
            )}
          </>)}
        </td>
        <td className="wx-num">{cell(isFetching, `${p.peak_alt_deg.toFixed(0)}°`)}</td>
        <td className="wx-num sat-peak-az-col">{cell(isFetching, az(p.peak_az_deg))}</td>
        <td className="wx-num sat-set-col">{cell(isFetching, formatTime(p.set_time, tz))}</td>
        <td className="wx-num sat-set-col">{cell(isFetching, setAlt)}</td>
        <td className="wx-num sat-set-col">{cell(isFetching, az(p.set_az_deg))}</td>
        <td className="wx-num sat-dur-col">{cell(isFetching, `${p.duration_min.toFixed(0)}m`)}</td>
        <td className="wx-num" style={moonSepLow ? {color: 'var(--excellent)', fontWeight: 700, fontSize: '1rem'} : undefined}>{cell(isFetching, moonStr)}</td>
        {report.light_dome && (
          <td className="wx-num cond-glow" style={satGlow != null && satGlow >= 0.03 ? glowStyle(satGlow) : undefined}>
            {cell(isFetching, satGlow != null && satGlow >= 0.03 ? glowLabel(satGlow) : '—')}
          </td>
        )}
      </tr>
    )
  }

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
        <div className={`wx-table-wrap${isFetching ? ' is-recalculating' : ''}${cathodeSnap ? ' cathode-snap' : ''}`}>
          {isFetching && (
            <div className="recalc-overlay" role="status" aria-live="polite">
              RECALCULATING...<span className="recalc-cursor" aria-hidden="true">█</span>
            </div>
          )}
          <table className="wx-table sat-table">
            <thead>
              <tr>
                <th>Satellite</th>
                <th colSpan={3}>Rise</th>
                <th colSpan={3}>Peak</th>
                <th className="sat-set-col" colSpan={3}>Set</th>
                <th className="sat-dur-col">Dur</th>
                <th>Moon Sep</th>
                {report.light_dome && <th>Glow</th>}
              </tr>
              <tr className="sat-subhdr">
                <th></th>
                <th>Time</th><th>Alt</th><th>Az</th>
                <th>Time</th><th>Alt</th><th className="sat-peak-az-col">Az</th>
                <th className="sat-set-col">Time</th><th className="sat-set-col">Alt</th><th className="sat-set-col">Az</th>
                <th className="sat-dur-col"></th><th></th>
                {report.light_dome && <th></th>}
              </tr>
            </thead>
            <tbody>
              {clearPasses.map(({ p, wxAtPeak }, i) => renderPassRow(p, wxAtPeak, i, false))}

              {cloudyPasses.length > 0 && (
                <>
                  <tr className="tg-unviable-hdr">
                    <td colSpan={colCount}>
                      <button
                        type="button"
                        className="tg-blocked-toggle"
                        aria-expanded={showBlocked}
                        onClick={() => setShowBlocked(v => !v)}
                      >
                        <span className="tg-blocked-caret" aria-hidden="true">{showBlocked ? '▾' : '▸'}</span>
                        {`Unavailable Tonight (${cloudyPasses.length})`}
                        <span className="tg-blocked-counts">{' — '}Clouded out</span>
                      </button>
                    </td>
                  </tr>
                  {showBlocked && cloudyPasses.map(({ p, wxAtPeak }, i) => renderPassRow(p, wxAtPeak, i, true))}
                </>
              )}
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
