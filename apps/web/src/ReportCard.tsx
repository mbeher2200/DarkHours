import { useState, useRef, useEffect, useMemo } from 'react'
import type { NightReport, NearbyResult, CalendarResult } from './types'
import { formatTime, formatHm, tzAbbr, tzTitle, fmtDist, lpString, scoreBand, scoreLabel, tonightIso, availabilityFor, nightVerdict } from './format'
import { MoonPhaseSvg, InfoTip } from './shared'
import { fetchNearby, fetchCalendar, fetchNightDateOnly, ApiRequestError } from './api'
import OutlookTelemetryRibbon from './OutlookTelemetryRibbon'
import CalendarRangePicker, { type CalendarPickerState } from './CalendarRangePicker'
import { fmtNightDate, MetaRow } from './report/common'
import { WeatherTable } from './report/NightTimeline'
import { SatellitePasses } from './report/Satellites'
import { MilkyWayAbsent, MilkyWayCard } from './report/MilkyWay'
import { isPrime, MeteorShowerCard, TargetsTable, MeteorAlertBanner, meteorShowerAlert } from './report/Targets'
import { auroraAlert, AuroraAlertBanner, AuroraCard } from './report/Aurora'
import { NearbyResults } from './report/Nearby'
import { LightDomePanel } from './report/LightDomePanel'

const AUTO_CALENDAR_DAYS = 30

// ReportCard fully unmounts and remounts on every /night fetch, including
// same-location day navigation (App.tsx only renders it once `report` is set
// and `loading` is false — see the `report && !loading` gate). Without this,
// the effect below would re-hit /calendar on every single day-nav click. Cache
// the in-flight promise (not just the settled result) at module scope, keyed
// by location, so remounts for a location already loading/loaded reuse it —
// this also collapses React StrictMode's dev-mode double-invoke into one call.
// TTL matches the server's own weather cache freshness window (_WX_CACHE_TTL,
// 30 min in predictor.py) — this is an in-memory, tab-lifetime cache with no
// other invalidation, so without a TTL a long-lived tab would keep serving a
// result computed hours/days ago even after the underlying data changes.
const _AUTO_CALENDAR_TTL_MS = 30 * 60 * 1000
const _autoCalendarCache = new Map<string, { promise: Promise<CalendarResult>; at: number }>()

function autoFetchCalendar(lat: number, lon: number, start: string): Promise<CalendarResult> {
  const key = `${lat},${lon},${start}`
  const cached = _autoCalendarCache.get(key)
  if (cached && Date.now() - cached.at < _AUTO_CALENDAR_TTL_MS) return cached.promise
  const p = fetchCalendar(lat, lon, start, AUTO_CALENDAR_DAYS)
  p.catch(() => _autoCalendarCache.delete(key)) // don't cache failures — allow retry on next mount
  _autoCalendarCache.set(key, { promise: p, at: Date.now() })
  return p
}

// Same remount problem as the calendar cache above, applied to Find Sky Nearby.
// Nearby search stays manual/opt-in (no auto-fire) — this only restores a
// search the user already ran for this location, instead of dropping back to
// the two radius buttons on every day-nav click. Keyed by lat,lon,radius for
// the fetch cache; lat,lon alone for "which radius did they last pick here".
const _nearbyFetchCache = new Map<string, Promise<NearbyResult>>()
const _nearbyLastRadius = new Map<string, number>()

function nearbyLocKey(lat: number, lon: number) {
  return `${lat},${lon}`
}

function fetchNearbyCached(lat: number, lon: number, radius: number): Promise<NearbyResult> {
  const key = `${lat},${lon},${radius}`
  let p = _nearbyFetchCache.get(key)
  if (!p) {
    p = fetchNearby(lat, lon, radius)
    p.catch(() => _nearbyFetchCache.delete(key))
    _nearbyFetchCache.set(key, p)
  }
  _nearbyLastRadius.set(nearbyLocKey(lat, lon), radius)
  return p
}

// ── Main report card ─────────────────────────────────────────────────────────

export default function ReportCard({
  report,
  showWeather = false,
  showTargets = false,
  showSatellites = false,
  imperial = false,
  onToggleUnits,
  onDateDetail,
}: {
  report: NightReport
  showWeather?: boolean
  showTargets?: boolean
  showSatellites?: boolean
  imperial?: boolean
  onToggleUnits?: (imp: boolean) => void
  onDateDetail?: (next: NightReport, date: string) => void
}) {
  const [nearbyState, setNearbyState] = useState<
    | { phase: 'idle' }
    | { phase: 'loading'; radius: number }
    | { phase: 'done'; data: NearbyResult }
    | { phase: 'error'; message: string }
  >(() => {
    const radius = _nearbyLastRadius.get(nearbyLocKey(report.lat, report.lon))
    return radius != null ? { phase: 'loading', radius } : { phase: 'idle' }
  })

  const [draftRadius, setDraftRadius] = useState<60 | 120>(60)

  async function handleFindNearby(radius: number) {
    setNearbyState({ phase: 'loading', radius })
    try {
      const data = await fetchNearbyCached(report.lat, report.lon, radius)
      setNearbyState({ phase: 'done', data })
    } catch (err) {
      setNearbyState({
        phase: 'error',
        message: err instanceof ApiRequestError ? err.message : 'Nearby search failed.',
      })
    }
  }

  useEffect(() => {
    const radius = _nearbyLastRadius.get(nearbyLocKey(report.lat, report.lon))
    if (radius == null) return
    let cancelled = false
    fetchNearbyCached(report.lat, report.lon, radius).then(data => {
      if (!cancelled) setNearbyState({ phase: 'done', data })
    }).catch(err => {
      if (!cancelled) {
        setNearbyState({
          phase: 'error',
          message: err instanceof ApiRequestError ? err.message : 'Nearby search failed.',
        })
      }
    })
    return () => { cancelled = true }
  }, [report.lat, report.lon])

  const [calendarState, setCalendarState] = useState<CalendarPickerState>({ phase: 'idle' })
  const manualCalendarRef = useRef(false)

  async function handleFindCalendar(start: string, days: number) {
    manualCalendarRef.current = true
    setCalendarState({ phase: 'loading', start, days })
    try {
      const data = await fetchCalendar(report.lat, report.lon, start, days)
      setCalendarState({ phase: 'done', data, days })
    } catch (err) {
      setCalendarState({
        phase: 'error',
        message: err instanceof ApiRequestError ? err.message : 'Calendar search failed.',
      })
    }
  }

  // Anchor the default outlook on the date picker's selection, not always
  // tonight — someone planning 3 months out wants the 30-day window to start
  // around that date, not reset to today. Clamped to today because the
  // outlook is forecast-only and can't look backward.
  const calendarAnchor = report.date > tonightIso() ? report.date : tonightIso()

  useEffect(() => {
    manualCalendarRef.current = false
    let cancelled = false
    setCalendarState({ phase: 'loading', start: calendarAnchor, days: AUTO_CALENDAR_DAYS })
    autoFetchCalendar(report.lat, report.lon, calendarAnchor).then(data => {
      if (!cancelled && !manualCalendarRef.current) {
        setCalendarState({ phase: 'done', data, days: AUTO_CALENDAR_DAYS })
      }
    }).catch(err => {
      if (!cancelled && !manualCalendarRef.current) {
        setCalendarState({
          phase: 'error',
          message: err instanceof ApiRequestError ? err.message : 'Calendar search failed.',
        })
      }
    })
    return () => { cancelled = true }
  }, [report.lat, report.lon, calendarAnchor])

  const outlookDays = calendarState.phase === 'loading' || calendarState.phase === 'done'
    ? calendarState.days
    : AUTO_CALENDAR_DAYS

  // Verdict-layer chip: the first upcoming night in the outlook that scores
  // "good" (≥6); when none does, fall back to the best upcoming night that
  // still beats tonight. Rendered only when tonight itself is below good.
  const nextGoodNight = useMemo(() => {
    if (calendarState.phase !== 'done') return null
    const upcoming = [...calendarState.data.nights]
      .filter(n => n.date > report.date && n.score != null)
      .sort((a, b) => a.date.localeCompare(b.date))
    const firstGood = upcoming.find(n => n.score! >= 6)
    if (firstGood) return { night: firstGood, kind: 'next' as const }
    const best = [...upcoming].sort((a, b) => b.score! - a.score!)[0]
    return best && best.score! > report.score ? { night: best, kind: 'best' as const } : null
  }, [calendarState, report.date, report.score])

  const [dateFetch, setDateFetch] = useState<
    | { phase: 'idle' }
    | { phase: 'fetching'; date: string }
    | { phase: 'error'; date: string; message: string }
  >({ phase: 'idle' })

  const detailTokenRef = useRef(0)
  useEffect(() => {
    return () => { detailTokenRef.current++ }
  }, [report.lat, report.lon])

  async function handleViewDetails(date: string) {
    const token = ++detailTokenRef.current
    setDateFetch({ phase: 'fetching', date })
    const { wxUnavail, satUnavail } = availabilityFor(date)
    try {
      const partial = await fetchNightDateOnly({
        lat: report.lat, lon: report.lon, date,
        weather: !wxUnavail, targets: true, satellites: !satUnavail,
      })
      if (detailTokenRef.current !== token) return
      const merged: NightReport = {
        ...report,
        ...partial,
        // Safely fallback to empty/undefined if the new date lacks this data
        weather_points: partial.weather_points ?? [],
        mw_summary: partial.mw_summary ?? null, // Swapped to null to satisfy the type
        visible_targets: partial.visible_targets ?? []
      }
      onDateDetail?.(merged, date)
      setDateFetch({ phase: 'idle' })
    } catch (err) {
      if (detailTokenRef.current !== token) return
      setDateFetch({
        phase: 'error',
        date,
        message: err instanceof ApiRequestError ? err.message : 'Could not load that date.',
      })
    }
  }

  const isFetchingDetails = dateFetch.phase === 'fetching'
  const prevReportRef = useRef(report)
  const [cathodeSnap, setCathodeSnap] = useState(false)
  const snapTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (prevReportRef.current !== report) {
      prevReportRef.current = report
      if (snapTimeoutRef.current) clearTimeout(snapTimeoutRef.current)
      setCathodeSnap(true)
      snapTimeoutRef.current = setTimeout(() => {
        setCathodeSnap(false)
        snapTimeoutRef.current = null
      }, 50)
    }
    return () => { if (snapTimeoutRef.current) clearTimeout(snapTimeoutRef.current) }
  }, [report])

  const r   = report
  const tz  = r.tz_name
  const lp  = r.light_pollution
  const lps = lpString(lp)
  const shortLps = lp?.bortle_class != null
    ? [`Bortle ${lp.bortle_class}`, lp.bortle_desc ?? null].filter(Boolean).join('  ·  ')
    : lps
  const tzZ = r.sunset ? tzAbbr(tz) : tz

  const specialTags = []
  if (r.moon_special) specialTags.push(`*** ${r.moon_special.charAt(0).toUpperCase() + r.moon_special.slice(1)} ***`)
  for (const e of r.moon_eclipses ?? []) {
    const kind = e.kind.charAt(0).toUpperCase() + e.kind.slice(1)
    const mag  = (e.kind === 'partial' || e.kind === 'total')
      ? `umbral ${e.umbral_magnitude?.toFixed(3)}`
      : `penumbral ${e.penumbral_magnitude?.toFixed(3)}`
    specialTags.push(`${kind} lunar eclipse at ${formatTime(e.time, tz)}  (mag ${mag})`)
  }
  const moonStrCard = `${r.phase_name}  ·  ${r.illumination_pct.toFixed(1)}% illuminated`
    + (specialTags.length ? `  ·  ${specialTags.join('  ·  ')}` : '')

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

  const CLOUD_CLEAR_THRESHOLD = 50
  const clearDarkIntervals: [string, string][] | null = (() => {
    const pts = (r.weather_points || []).filter(p => p.cloud_cover_pct != null && p.cloud_cover_pct <= CLOUD_CLEAR_THRESHOLD)
    if (!(r.weather_points || []).length || !(r.weather_points || []).some(p => p.cloud_cover_pct != null)) return null
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

  const darkStrCard = (() => {
    if (clearDarkIntervals === null) {
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

  const formattedDate = new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  }).format(new Date(r.date + 'T00:00:00'))

  const verdict = nightVerdict(r)
  const meteorAlert = meteorShowerAlert(r)
  const aurAlert = auroraAlert(r)

  // Stacked "PREDICTED SKY EVENT" banners, strongest event first. Aurora
  // outranks a meteor shower when it's at least naked-eye at this location
  // (overhead 3 > naked_eye 2 > meteor 1.5 > photographic-only aurora 1).
  // The group border takes the TOP banner's state — it visually joins that
  // banner, and a secondary event's blocked-red around an ok headline misreads.
  const banners: { key: string; state: 'ok' | 'degraded' | 'blocked'; strength: number; node: React.ReactNode }[] = []
  if (aurAlert) {
    banners.push({
      key: 'aurora', state: aurAlert.state,
      strength: { overhead: 3, naked_eye: 2, photographic: 1 }[aurAlert.aurora.tier],
      node: <AuroraAlertBanner key="aurora" alert={aurAlert} />,
    })
  }
  if (meteorAlert) {
    banners.push({
      key: 'meteor', state: meteorAlert.state, strength: 1.5,
      node: <MeteorAlertBanner key="meteor" alert={meteorAlert} />,
    })
  }
  banners.sort((a, b) => b.strength - a.strength)
  const groupState = banners[0]?.state

  // Collapsed-summary one-liners (hidden while a section is open — see .sum-brief)
  const planningBrief = calendarState.phase === 'done' && calendarState.data.ranked[0]?.score != null
    ? `Best night ${fmtNightDate(calendarState.data.ranked[0].date)} · ${calendarState.data.ranked[0].score!.toFixed(1)}`
    : null

  const satBrief = (() => {
    const passes = r.sat_passes ?? []
    const trains = r.starlink_trains?.length ?? 0
    if (!passes.length && !trains) return null
    const parts: string[] = []
    if (passes.length) {
      parts.push(`${passes.length} pass${passes.length === 1 ? '' : 'es'}`)
      const first = [...passes].sort((a, b) => a.rise_time.localeCompare(b.rise_time))[0]
      parts.push(`${first.satellite_name} ${formatTime(first.rise_time, tz)}`)
    }
    if (trains) parts.push(`${trains} Starlink train${trains === 1 ? '' : 's'}`)
    return parts.join(' · ')
  })()

  // Shareable permalink: the URL already tracks location + date (runQuery and
  // handleDateDetail both replaceState), so copying it is the whole feature.
  const [copied, setCopied] = useState(false)
  async function copyLink() {
    const url = window.location.href
    let ok = false
    try {
      await navigator.clipboard.writeText(url)
      ok = true
    } catch {
      // Async Clipboard needs a secure context + user activation — fall back to
      // the legacy selection path (old Safari, embedded webviews).
      const ta = document.createElement('textarea')
      ta.value = url
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      try { ok = document.execCommand('copy') } catch { ok = false }
      ta.remove()
    }
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    }
  }

  // Sticky section nav — only sections actually rendered this query get links.
  const hasTimeline = r.events.length > 0 || (showWeather && (r.weather_points?.length ?? 0) > 0)
  const navSections: [string, string][] = [
    ['report-planning', 'Planning'],
    ...(hasTimeline ? [['report-timeline', 'Timeline'] as [string, string]] : []),
    ...(showTargets ? [['report-targets', 'Sky Features'] as [string, string]] : []),
    ...(showSatellites ? [['report-satellites', 'Satellites'] as [string, string]] : []),
  ]
  function jumpTo(id: string) {
    const el = document.getElementById(id)
    if (!el) return
    if (el instanceof HTMLDetailsElement) el.open = true // jumping to a collapsed section expands it
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

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
          <p className="place-sub">
            {placeSecondary && <>{placeSecondary}  ·  </>}
            <span className="report-head-coords">({r.lat.toFixed(4)}°, {r.lon.toFixed(4)}°)</span>
          </p>
          <p className="when">
            {formattedDate}  ·  {tzTitle(tz)}
          </p>
        </div>
        <div className="report-head-actions">
          <button
            type="button"
            className={`copy-link-btn${copied ? ' copied' : ''}`}
            onClick={copyLink}
            title="Copy a shareable link to this report"
          >
            {copied ? 'Copied ✓' : 'Copy Link'}
          </button>
          {onToggleUnits && (
            <div className="units-toggle" role="group" aria-label="Unit system">
              <button type="button" className={!imperial ? 'active' : ''} onClick={() => onToggleUnits(false)}>°C / m/s</button>
              <button type="button" className={imperial ? 'active' : ''} onClick={() => onToggleUnits(true)}>°F / mph</button>
            </div>
          )}
        </div>
      </header>

      {navSections.length > 1 && (
        <nav className="report-nav" aria-label="Report sections">
          {navSections.map(([id, label]) => (
            <button key={id} type="button" className="report-nav-link" onClick={() => jumpTo(id)}>
              {label}
            </button>
          ))}
        </nav>
      )}

      <div className={`overall-group${groupState ? ` state-${groupState}` : ''}`}>
      {banners.map(b => b.node)}
      <div className={`overall band-${scoreBand(r.score)} ${banners.length ? 'overall--has-alert' : ''}`}>
        <div className="overall-left">
        <div className="overall-score-block">
          <div className="overall-score-header">
            <div className="overall-num">{r.score.toFixed(1)}</div>
            <div className="overall-label">{scoreLabel(r.score)}</div>
          </div>
          <div className="overall-sub">
            <InfoTip tip={<>Weighted geometric mean of Weather 40% · Lunar 25% · Dark Hours 25% · Dark Sky 10% (weights redistribute when a factor is unavailable). Geometric means one hard zero — full overcast, full moon — zeroes the whole night, just like it does in the field.</>}>
              0–10 composite score
            </InfoTip>
          </div>
          {verdict && <div className="overall-verdict">{verdict}</div>}
          {r.score < 6 && nextGoodNight && (
            <button
              type="button"
              className="next-good-chip"
              disabled={isFetchingDetails}
              onClick={() => handleViewDetails(nextGoodNight.night.date)}
              title="Load the full report for this night"
            >
              <span className="ngc-k">
                {nextGoodNight.kind === 'next' ? 'Next good night' : 'Best night ahead'}
              </span>
              <span className="ngc-date">{fmtNightDate(nextGoodNight.night.date)}</span>
              <span className={`telemetry-score-${scoreBand(nextGoodNight.night.score!)}`}>
                {nextGoodNight.night.score!.toFixed(1)}
              </span>
            </button>
          )}
        </div>
          <div className="meta">
        {shortLps && (
          <MetaRow
            k="Light Pollution"
            v={shortLps}
            tip={<>Bortle class 1 (pristine) to 9 (inner city), derived from satellite radiance{lp?.source ? ` (${lp.source})` : ''}. SQM is zenith sky brightness in mag/arcsec² — each +1 is ~2.5× darker; 21.7+ is a genuinely dark site.</>}
            score={r.score_components.bortle}
            scoreTip={<>10% of the composite — the Bortle class at this location. Fixed for the spot; the only lever is going somewhere darker (see Find Sky Nearby).</>}
          />
        )}

        <MetaRow
          k="Clear Dark Sky"
          v={darkStrCard}
          tip={<>Hours you can actually shoot: astronomical darkness (sun ≥18° below the horizon), minus moon interference, minus hours clouded over (&gt;50% cover).</>}
          score={r.score_components.dark}
          scoreTip={<>25% of the composite — tonight's moon-free dark hours measured against this lunar cycle's average, so the score reflects what this month can actually offer.</>}
        />
        {showWeather && r.weather_score != null && (
          <MetaRow
            k="Weather"
            v={`${r.weather_score.toFixed(1)}/10${r.wx_source ? `  ·  ${r.wx_source}` : ''}`}
            score={r.score_components.weather}
            scoreTip={<>40% of the composite — hourly condition ratings averaged across the night, with dark-window hours weighted 3× over twilight. Clouds dominate; then seeing, transparency, wind, and humidity.</>}
          />
        )}
        {showWeather && r.wx_pending && <MetaRow k="Weather" v="Pending  (beyond the ~16-day forecast horizon)" />}
        {showWeather && r.wx_no_data && <MetaRow k="Weather" v="No data  (not covered for this location/date)" />}
        {showWeather && r.wx_error && !(r.weather_points?.length) && <MetaRow k="Weather" v="Temporarily unavailable: weather providers are down" />}
        <MetaRow k="Lunar Conditions" v={moonStrCard}
          icon={<MoonPhaseSvg phaseName={r.phase_name} illuminationPct={r.illumination_pct} size={20} />}
          score={r.score_components.moon}
          scoreTip={<>25% of the composite — scattered-moonlight model: phase, moon altitude, and hours above the horizon, distance-corrected. A bright moon that sets early can still score well.</>}
        />
        </div>
        </div>
        {r.light_dome && <LightDomePanel summary={r.light_dome} imperial={imperial} />}
      </div>
      </div>


        <details id="report-planning" className="planning-tools-section" open>
        <summary>Planning Tools{planningBrief && <span className="sum-brief"> · {planningBrief}</span>}</summary>
        <div className="planning-tools-body">
          <div className="planning-tool">
            <h4 className="planning-tool-title">Find Sky Nearby</h4>
            <p className="planning-tool-subtitle">Search for better sky in other locations</p>
            <div className="nearby-body">
              {nearbyState.phase === 'idle' && (
                <div className="nearby-radius-control">
                  <span className="nearby-radius-label">Radius</span>
                  <div className="units-toggle" role="group" aria-label="Search radius">
                    <button type="button" className={draftRadius === 60 ? 'active' : ''} onClick={() => setDraftRadius(60)}>
                      {fmtDist(60 * 1.60934, imperial)}
                    </button>
                    <button type="button" className={draftRadius === 120 ? 'active' : ''} onClick={() => setDraftRadius(120)}>
                      {fmtDist(120 * 1.60934, imperial)}
                    </button>
                  </div>
                  <button className="submit nearby-search-btn" onClick={() => handleFindNearby(draftRadius)}>Search Nearby</button>
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
          </div>

          <div className="iconic-section-divider" />

          <div className="planning-tool">
            <h4 className="planning-tool-title">{outlookDays} Day Outlook View</h4>
            <div className="nearby-body">
              <CalendarRangePicker state={calendarState} anchor={calendarAnchor} onApply={handleFindCalendar} />
              {calendarState.phase === 'error' && (
                <p className="sat-notice">{calendarState.message}</p>
              )}
              {calendarState.phase === 'done' && (
                <OutlookTelemetryRibbon
                  data={calendarState.data}
                  startExpanded={manualCalendarRef.current}
                  onViewDetails={handleViewDetails}
                  isFetchingDetails={isFetchingDetails}
                  viewDetailsError={dateFetch.phase === 'error' ? dateFetch.message : null}
                />
              )}
            </div>
          </div>
        </div>
      </details>

      {(r.events.length > 0 || (showWeather && (r.weather_points?.length ?? 0) > 0)) && (
        <WeatherTable
          points={showWeather ? (r.weather_points ?? []) : []}
          events={r.events}
          tz={tz}
          imperial={imperial}
          moonrise={r.moonrise}
          moonset={r.moonset}
          moonPhaseName={r.phase_name}
          moonIlluminationPct={r.illumination_pct}
          isFetching={isFetchingDetails}
          cathodeSnap={cathodeSnap}
          wxSource={showWeather ? r.wx_source : null}
          wxFetchedAt={showWeather ? r.wx_fetched_at : null}
        />
      )}

      {showTargets && (() => {
        const showerTargets  = r.visible_targets.filter(t => t.type === 'meteor_shower')
        const primeDSOs      = r.visible_targets
          .filter(t => t.type !== 'milky_way' && t.type !== 'meteor_shower')
          .filter(t => isPrime(t, r.dark_intervals))
          .filter(t => (t.landscape_suitability ?? 'prominent') === 'prominent')
        const hasAnything    = r.visible_targets.length > 0 || r.aurora != null

        const targetsBrief = (() => {
          const parts: string[] = []
          if (r.mw_summary && r.mw_summary.n_visible > 1) parts.push(`Milky Way ${r.mw_summary.local_score.toFixed(1)}/10`)
          if (r.aurora) parts.push(`Aurora Kp ${r.aurora.kp_max.toFixed(0)}`)
          const viableN  = primeDSOs.filter(t => t.viability !== 'blocked').length
          const blockedN = primeDSOs.length - viableN
          if (viableN)  parts.push(`${viableN} target${viableN === 1 ? '' : 's'} viable`)
          if (blockedN) parts.push(`${blockedN} blocked`)
          return parts.length ? parts.join(' · ') : null
        })()

        return (
        <details id="report-targets" className="targets" open>
          <summary>
            Prominent Sky Features{targetsBrief && <span className="sum-brief"> · {targetsBrief}</span>}
          </summary>
          {!hasAnything
            ? <p className="sat-notice" style={{ paddingTop: 10 }}>No prime targets for this night.</p>
            : <>
                <div className="mw-section">
                  <div className="mw-section-label">Milky Way</div>
                  {r.mw_summary && r.mw_summary.n_visible > 1
                    ? <MilkyWayCard
                    key={r.date} // Forces refresh when r.date changes
                    summary={r.mw_summary}
                    waypoints={r.visible_targets.filter(t => t.type === 'milky_way')}
                  report={r}
              />
            : <MilkyWayAbsent report={r} />
          }
                </div>
                {r.aurora && (
                  <div className="ms-section">
                    <div className="mw-section-label">Aurora</div>
                    <div className="ms-cards">
                      <AuroraCard aurora={r.aurora} report={r} />
                    </div>
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
                {primeDSOs.length > 0 && (
                  <>
                    <div className="iconic-section-divider" />
                    <div className="mw-section-label iconic-targets-label">
                      Deep Sky Objects{primeDSOs.some(t => t.type === 'planet') ? ' & Planets' : ''}
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
        // Deliberately collapsed by default — reference material, not verdict.
        // The .sum-brief keeps the takeaway visible without expanding.
        <details id="report-satellites" className="sat-section">
          <summary>Satellite Ephemeris{satBrief && <span className="sum-brief"> · {satBrief}</span>}</summary>
          <div className="sat-body">
            <SatellitePasses report={r} isFetching={isFetchingDetails} cathodeSnap={cathodeSnap} />
          </div>
        </details>
      )}


    </section>
  )
}
