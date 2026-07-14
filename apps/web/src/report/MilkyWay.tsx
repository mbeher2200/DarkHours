import { useRef, useEffect } from 'react'
import type { NightReport, VisibleTarget, MilkyWaySummary, Direction } from '../types'
import { formatTime, cardinal, rateConditions, scoreBand, scoreLabel, resolveMoonSeverity, showAodAmplifyTip, AOD_AMPLIFY_TIP_COPY } from '../format'
import { ScoreBar, InfoTip } from '../shared'
import { WmoIcon } from './icons'
import { fmtPos } from './common'
import { LD_DIRS, LD_DIR_AZ, LD_MINOR, glowToward, glowLabel, glowStyle, archGlowAt } from './glow'
import { bestWindow, skyCondition, skyClass, wxAtTime } from './Targets'
import { SkyDome } from './skydome/SkyDome'

// ── Milky Way card ───────────────────────────────────────────────────────────

// Waypoints disclosure — closed by default with Phase 3 density reductions applied inside.
export function WaypointsAccordion({ waypoints, summary, report }: {
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
        Galactic Plane Waypoints ({summary.n_visible})
      </summary>
      <div className="tg-table-wrap mw-waypoints-table-wrap">
        <table className="tg-table">
          <thead>
            <tr>
              <th>Waypoint</th>
              <th>Best</th>
              <th></th>
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
              const wxPt = !report.wx_no_data && !report.wx_pending
                ? wxAtTime(report.weather_points || [], bestT)
                : null
              const waypointCloudy = wxPt != null && wxPt.cloud_cover_pct != null && wxPt.cloud_cover_pct > 70
              if (waypointCloudy) return (
                <tr key={t.name} className="tg-row-blocked">
                  <td>{t.name}</td>
                  <td className="wx-num" colSpan={3} style={{textAlign: 'center'}}>
                    <span className="mw-moon-badge badge-poor">Clouded out</span>
                  </td>
                </tr>
              )
              const sky = skyCondition(
                bestT, report.dark_intervals, report.night_start, report.night_end,
                report.illumination_pct, report.moonrise, report.moonset,
                w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg, w.moon_wash_severity,
              )
              const wpAodTip = showAodAmplifyTip(
                resolveMoonSeverity(w.moon_wash_severity, report.illumination_pct,
                                    w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg),
                report.night_aod,
              )
              const moonBadgeSpan = <span className={`tg-sky-inline ${skyClass(sky)}`}>{' '}{sky}</span>
              const moonBadge = sky.startsWith('Moon')
                ? (wpAodTip ? <InfoTip tip={<>{AOD_AMPLIFY_TIP_COPY}</>}>{moonBadgeSpan}</InfoTip> : moonBadgeSpan)
                : null
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
                    <span className="tg-p"> · Alt </span>
                    <span className="tg-alt">{Math.round(w.peak_alt_deg)}°</span>
                    <span className="tg-p"> · Az </span>
                    <span className="tg-az">{Math.round(w.peak_az_deg)}°</span>
                    <span className="tg-p"> </span>
                    <span className="tg-dir">{cardinal(w.peak_az_deg)}</span>
                    {archBadge}
                    {moonBadge}
                  </td>
                  <td className="wx-num tg-cond-col">
                    {wxPt && (
                      <span className={`tg-wx-inline wx-rating-${scoreBand(rateConditions(wxPt))}`}>
                        <WmoIcon code={wxPt.weather_code} size={12} />
                      </span>
                    )}
                  </td>
                  <td className="wx-num wp-window-td">
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

export function MoonBadge({ type, severity, aodTip }: { type: 'penalty' | 'limited'; severity?: string | null; aodTip?: boolean }) {
  const base = type === 'penalty' ? 'Moon interference' : 'Moon limited'
  const text = severity ? `${base}: ${severity}` : base
  return (
    <InfoTip tip={<>Moon wash — scattered moonlight brightening the sky along this line of sight. Severity comes from phase, moon altitude, angular separation, and aerosols, not illumination % alone.{aodTip ? <> {AOD_AMPLIFY_TIP_COPY}</> : null}</>}>
      <span className="mw-moon-badge">{text}</span>
    </InfoTip>
  )
}


export function MilkyWayAbsent({ report: r }: { report: NightReport }) {
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

  const moonSeverity = resolveMoonSeverity(
    s.core_moon_severity,
    report.illumination_pct,
    s.core_moon_sep_deg ?? null,
    s.core_moon_alt_deg ?? null,
  )
  const moonAodTip = showAodAmplifyTip(moonSeverity, report.night_aod)

      return (
    <div className="mw-card">
      <div className="mw-meta-block">
        {/* Unified Score Row */}
        <div className="meta-row">
          <span className="meta-k">Score</span>
            {/* Using the standard meta-v class for uniformity */}
          <span className={`meta-v mw-score mw-score-band-${scoreBand(s.local_score)}`}>
            {s.local_score.toFixed(1)}
          <span className="mw-score-denom">{scoreLabel(s.local_score)}</span>
            {s.weather_blocked && <span className="mw-moon-badge badge-poor" style={{marginLeft: 8}}>Clouded out</span>}
            {!s.weather_blocked && s.weather_limited && <span className="mw-moon-badge" style={{marginLeft: 8}}>Partly cloudy</span>}
  </span>
</div>
        {/* Unified Metadata List */}
        <div className="meta-row">
          <span className="meta-k">Arch window</span>
          <span className="meta-v">
            {formatTime(s.arch_start, tz)} – {formatTime(s.arch_end, tz)}
            {'  ·  '}{Math.floor(s.arch_hours)}h {Math.round((s.arch_hours % 1) * 60).toString().padStart(2,'0')}m
            {s.moon_limited && !s.arch_moon_washout && <MoonBadge type="limited" severity={moonSeverity} aodTip={moonAodTip} />}
            {s.weather_limited && !s.weather_blocked && <span className="mw-moon-badge">{`${s.clear_arch_hours.toFixed(1)}h clear`}</span>}
          </span>
        </div>
        <div className="meta-row">
          <span className="meta-k">Galactic core</span>
          <span className="meta-v">
            {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)} (max {s.core_max_alt_deg}° alt)
            {archQuality && s.arch_angle_deg != null && `  ·  arch ${s.arch_angle_deg.toFixed(0)}° (${archQuality})`}
          </span>
        </div>
        <div className="meta-row">
          <span className="meta-k">{bestLabel}</span>
          <span className="meta-v">
            {formatTime(bestTime, tz)} — core @ {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)}
          </span>
        </div>
      </div>

      {/* New Title Centered Above Both */}
      <div className="mw-group-title">360° Sky View</div>

      {/* 3 & 4: Skydome (Left) and Notes (Right) */}
      <div className="mw-mid-section">

        <div className="mw-dome-container">
          <SkyDome summary={s} report={report} />
        </div>

        <div className="mw-notes-container">
          {s.moon_penalised && !s.arch_moon_washout && <MoonBadge type="penalty" severity={moonSeverity} aodTip={moonAodTip} />}
          {s.arch_moon_washout && <span className="mw-moon-badge">Moon washout</span>}
          {domeSections.length > 0 && (() => {
            const maxGlow  = Math.max(...domeSections.map(ds => ds.glow))
            return (
              <span className="mw-moon-badge cond-glow" style={glowStyle(maxGlow)}>
                {`Dome glow: ${glowLabel(maxGlow)}`}
              </span>
            )
          })()}
        </div>
      </div>

      <div className="mw-bars-section telemetry-mini-bars">
        <ScoreBar label="Altitude" value={s.alt_score} />
        <ScoreBar label="Coverage" value={s.cov_score} />
        <ScoreBar label="Window" value={s.win_score} />
      </div>

      {waypoints.length > 0 && (
        <WaypointsAccordion waypoints={waypoints} summary={s} report={report} />
      )}
    </div>
  )
}
