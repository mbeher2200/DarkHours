import type { NightReport, WeatherPoint, VisibleTarget, TargetWindow, SatPass } from './types'
import {
  formatDayTime, formatTime, formatHm, tzAbbr,
  cardinal, rateConditions, fmtTemp, fmtWind, lpString,
  scoreBand, scoreLabel,
} from './format'

// ── Weather table ────────────────────────────────────────────────────────────

function WeatherTable({ points, tz }: { points: WeatherPoint[]; tz: string }) {
  const hasTemp   = points.some(p => p.temperature_c   != null)
  const hasDew    = points.some(p => p.dew_point_c     != null)
  const hasFeels  = points.some(p => p.feels_like_c    != null)
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
              <th>Wx</th>
              <th>Cloud</th>
              {hasTemp   && <th>Temp</th>}
              {hasDew    && <th>Dew Pt</th>}
              {hasFeels  && <th>Feels</th>}
              {hasSeeing && <th>Seeing</th>}
              {hasTransp && <th>Transp.</th>}
              <th>Humidity</th>
              <th>Wind</th>
              <th>Precip</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={i}>
                <td className="wx-time">{formatDayTime(p.time, tz)}</td>
                <td className="wx-num">{rateConditions(p)}/10</td>
                <td className="wx-num">{p.cloud_cover_pct != null ? `${p.cloud_cover_pct}%` : '—'}</td>
                {hasTemp   && <td className="wx-num">{fmtTemp(p.temperature_c)}</td>}
                {hasDew    && <td className="wx-num">{fmtTemp(p.dew_point_c)}</td>}
                {hasFeels  && <td className="wx-num">{fmtTemp(p.feels_like_c)}</td>}
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
                <td className="wx-num">{p.humidity_pct != null ? `${p.humidity_pct}%` : '—'}</td>
                <td className="wx-num">{fmtWind(p.wind_speed_ms, p.wind_direction_deg)}</td>
                <td>{p.precip_type && p.precip_type !== 'none' ? p.precip_type.charAt(0).toUpperCase() + p.precip_type.slice(1) : 'None'}</td>
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

function MetaRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="meta-row">
      <span className="meta-k">{k}:</span>
      <span className="meta-v">{v}</span>
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
        {report.sat_starlink_unavailable && <p className="sat-notice">(Starlink train data unavailable — network error)</p>}
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
          {report.sat_starlink_unavailable && !report.sat_network_error &&
            <p className="sat-notice">(Starlink train data unavailable — network error)</p>}
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
  milky_way: 0, meteor_shower: 1, cluster: 2, planet: 3, nebula: 4, galaxy: 5,
}
const TYPE_LABELS: Record<string, string> = {
  milky_way: 'Milky Way', meteor_shower: 'Meteor Showers', cluster: 'Clusters',
  planet: 'Planets', nebula: 'Nebulae', galaxy: 'Galaxies',
}

function bestWindow(t: VisibleTarget): TargetWindow {
  const clean = t.windows.filter(w => !w.moon_interference)
  const pool  = clean.length ? clean : t.windows
  return pool.reduce((best, w) => (w.peak_alt_deg ?? 0) > (best.peak_alt_deg ?? 0) ? w : best)
}

function skyCondition(
  peakIso: string,
  darkIntervals: [string, string][],
  nightStart: string | null,
  nightEnd: string | null,
): string {
  const pt = new Date(peakIso).getTime()
  for (const [s, e] of darkIntervals) {
    if (pt >= new Date(s).getTime() && pt <= new Date(e).getTime()) return 'Dark sky'
  }
  if (nightStart && nightEnd) {
    const ns = new Date(nightStart).getTime()
    const ne = new Date(nightEnd).getTime()
    if (pt >= ns && pt <= ne) return 'Astro night'
  }
  return 'Twilight'
}

function TargetsTable({ targets, report }: { targets: VisibleTarget[]; report: NightReport }) {
  const tz = report.tz_name

  const sorted = [...targets].sort((a, b) => {
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

            let bestView = '—'
            if (w.peak_time && w.peak_alt_deg != null) {
              const az     = `${w.peak_az_deg.toFixed(0)}°(${cardinal(w.peak_az_deg)})`
              let archNote = ''
              if (t.type === 'milky_way' && w.arch_angle_deg != null) {
                const a = w.arch_angle_deg
                const q = a >= 60 ? 'steep' : a >= 35 ? 'moderate' : 'flat'
                archNote = `  arch ${a.toFixed(0)}° (${q})`
              }
              bestView = `${formatTime(w.peak_time, tz)} @ ${w.peak_alt_deg.toFixed(0)}°  ${az}${archNote}`
            }

            const winStr = w.peak_time
              ? `${formatTime(w.start, tz)} @ ${w.start_alt_deg.toFixed(0)}° – ${formatTime(w.end, tz)} @ ${w.end_alt_deg.toFixed(0)}°`
              : '—'

            const sky = w.peak_time
              ? skyCondition(w.peak_time, report.dark_intervals, report.night_start, report.night_end)
              : '—'

            return (
              <tr key={row.key}>
                <td>{name}{t.note ? <span className="tg-note"> · {t.note}</span> : null}</td>
                <td className="wx-num">{bestView}</td>
                <td className={`tg-sky tg-sky-${sky.replace(' ', '-').toLowerCase()}`}>{sky}</td>
                <td className="wx-num">{winStr}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main report card ─────────────────────────────────────────────────────────

export default function ReportCard({
  report,
  showWeather = false,
  showTargets = false,
  showSatellites = false,
}: {
  report: NightReport
  showWeather?: boolean
  showTargets?: boolean
  showSatellites?: boolean
}) {
  const r   = report
  const tz  = r.tz_name
  const lp  = r.light_pollution
  const lps = lpString(lp)
  const tzZ = r.sunset ? tzAbbr(tz) : tz

  // Moon line
  const distStr     = r.moon_distance_km.toLocaleString()
  const specialTags = []
  if (r.moon_special) specialTags.push(`*** ${r.moon_special.charAt(0).toUpperCase() + r.moon_special.slice(1)} ***`)
  for (const e of r.moon_eclipses ?? []) {
    const kind = e.kind.charAt(0).toUpperCase() + e.kind.slice(1)
    const mag  = (e.kind === 'partial' || e.kind === 'total')
      ? `umbral ${e.umbral_magnitude?.toFixed(3)}`
      : `penumbral ${e.penumbral_magnitude?.toFixed(3)}`
    specialTags.push(`${kind} lunar eclipse at ${formatTime(e.time, tz)}  (mag ${mag})`)
  }
  const moonStr = `${r.phase_name}  |  ${r.illumination_pct.toFixed(1)}% illuminated  |  ${distStr} km`
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
          {r.date}  ·  {tz}  ·  ({r.lat.toFixed(4)}°, {r.lon.toFixed(4)}°)
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
        <MetaRow k="Moon" v={moonStr} />
        {(r.active_showers?.length ?? 0) > 0 && (
          <MetaRow
            k="Meteor Showers"
            v={r.active_showers.map(s => `${s.name}  ·  ${s.note}  ·  ZHR ${s.zhr}`).join(',  ')}
          />
        )}
        <MetaRow k="Clear Dark Sky Hours" v={darkStr} />
        {showWeather && r.weather_score != null && (
          <MetaRow
            k="Weather"
            v={`${r.weather_score.toFixed(1)}/10${r.wx_source ? `  [${r.wx_source}]` : ''}`}
          />
        )}
        {showWeather && r.wx_pending && <MetaRow k="Weather" v="Pending  (beyond the ~7-day forecast horizon)" />}
        {showWeather && r.wx_no_data && <MetaRow k="Weather" v="No data  (not covered for this location/date)" />}
      </div>

      {showWeather && r.weather_points.length > 0 && (
        <WeatherTable points={r.weather_points} tz={tz} />
      )}

      {showSatellites && (
        <details className="sat-section" open>
          <summary>Satellite Passes</summary>
          <div className="sat-body">
            <SatellitePasses report={r} />
          </div>
        </details>
      )}

      {r.events.length > 0 && (
        <details className="events" open>
          <summary>Night Timeline</summary>
          <div className="ev-table">
            {r.events.map((e, i) => (
              <div key={i} className="ev-row">
                <span className="ev-time">{formatDayTime(e.time, tz)}</span>
                <span className="ev-label">{e.label}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {showTargets && (
        <details className="targets" open>
          <summary>
            Prime Targets
            {r.visible_targets.length > 0 ? ` (${r.visible_targets.length})` : ''}
          </summary>
          {r.visible_targets.length > 0
            ? <TargetsTable targets={r.visible_targets} report={r} />
            : <p className="sat-notice" style={{ paddingTop: 10 }}>No prime targets for this night.</p>
          }
        </details>
      )}
    </section>
  )
}
