import React, { useState } from 'react'
import type { NightReport, TargetWindow, VisibleTarget, WeatherPoint } from '../types'
import { formatTime, cardinal, rateConditions, scoreBand, moonWashSeverity, moonUpAt } from '../format'
import { InfoTip } from '../shared'
import { WmoIcon } from './icons'
import { fmtPos } from './common'
import { glowToward, glowLabel, glowStyle } from './glow'

// ── Targets helpers ──────────────────────────────────────────────────────────

// nebula / galaxy / cluster are collapsed into a single display group ("dso")
// so the targets table shows a clean unlabeled DSO block followed by Planets.
export const DSO_TYPES = new Set(['nebula', 'galaxy', 'cluster'])

export const TYPE_ORDER: Record<string, number> = {
  meteor_shower: 0,
  dso:           1,  // nebula + galaxy + cluster
  planet:        2,
}
export const TYPE_LABELS: Record<string, string> = {
  meteor_shower: 'Meteor Showers',
  // 'dso' has no label — after prominence filtering the list is short enough
  planet: 'Planets',
}

export const MOON_ARCMIN = 30

export function moonScaleLabel(arcmin: number | null | undefined): string | null {
  if (arcmin == null) return null
  const ratio = arcmin / MOON_ARCMIN
  if (ratio >= 1.5) return `${Math.round(ratio)}x Moon`
  if (ratio >= 1.0) return '1x Moon'
  if (ratio >= 0.5) return '½ Moon'
  if (ratio >= 0.3) return '⅓ Moon'
  return null
}

export function bestWindow(t: VisibleTarget): TargetWindow {
  const clean = t.windows.filter(w => !w.moon_interference)
  const pool  = clean.length ? clean : t.windows
  return pool.reduce((best, w) => (w.peak_alt_deg ?? 0) > (best.peak_alt_deg ?? 0) ? w : best)
}

// Sky condition at a given ISO time, incorporating K&S moon wash (mirrors CLI)
export function skyCondition(
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
    if (sev) return `Moon wash: ${sev}`
  }
  return base
}

// Interpolate altitude at a clipped time (mirrors _alt_at in render_report.py)
export function altAt(cutoffIso: string, w: TargetWindow): number {
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
export function isPrime(t: VisibleTarget, darkIntervals: [string, string][]): boolean {
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

export function skyClass(sky: string): string {
  const moonWashMatch = sky.match(/^Moon wash: (minor|moderate|severe)$/)
  if (moonWashMatch) return `tg-sky-moon-wash-${moonWashMatch[1]}`
  return `tg-sky-${sky.replace(/ /g, '-').toLowerCase()}`
}

export function MeteorShowerCard({ target, zhr, report }: {
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
        <span className="ms-zhr">
          <InfoTip tip={<>ZHR — zenithal hourly rate: meteors per hour for a single observer under a perfectly dark sky with the radiant overhead. Field counts run well below it.</>}>
            Peak ZHR {zhr}
          </InfoTip>
        </span>
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

export function blockerLabel(blockers: string[]): string {
  if (blockers.includes('cloud') || blockers.includes('transparency')) return 'Clouded out'
  if (blockers.includes('moon_washout')) return 'Moon washout'
  if (blockers.includes('light_dome')) return 'Lost in light dome'
  return 'Unavailable Tonight'
}

export function BlockerBadge({ blockers }: { blockers: string[] }) {
  return <span className="tg-blocker-badge">{blockerLabel(blockers)}</span>
}

export function clipTooltip(w: TargetWindow, tz: string): string {
  const b = w.blockers ?? []
  const end = w.effective_end
  if (b.includes('cloud') || b.includes('transparency'))
    return `Partly cloudy${end ? ` after ${formatTime(end, tz)}` : ''}`
  if (b.includes('moon_washout')) return 'Moon washout'
  if (b.includes('light_dome'))   return 'Viewing constrained by horizon glow'
  return 'Window clipped by conditions'
}

/*function clipReasonShort(w: TargetWindow): string {
  const b = w.blockers ?? []
  if (b.includes('cloud') || b.includes('transparency')) return 'cloud'
  if (b.includes('moon_washout')) return 'moon'
  if (b.includes('light_dome'))   return 'dome'
  return 'conditions'
}*/

// ── TargetsTable ──────────────────────────────────────────────────────────────

export function TargetsTable({ targets, report }: { targets: VisibleTarget[]; report: NightReport }) {
  const tz = report.tz_name
  // Blocked targets collapse to one summary row by default — on a bad night a
  // wall of identical "Clouded out" rows is noise, not signal.
  const [showBlocked, setShowBlocked] = useState(false)

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
    // Pre-compute bestWindow once per target so the sort comparator doesn't
    // call it O(n log n) times — sort only reads from this map.
    const bwMap = new Map(list.map(t => [t, bestWindow(t)]))
    const sorted = [...list].sort((a, b) => {
      const ao = TYPE_ORDER[displayType(a)] ?? 99
      const bo = TYPE_ORDER[displayType(b)] ?? 99
      if (ao !== bo) return ao - bo
      const at = bwMap.get(a)!.peak_time ?? ''
      const bt = bwMap.get(b)!.peak_time ?? ''
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
      ? wxAtTime(report.weather_points || [], peakForSky)
      : null
    const glow = report.light_dome && w.peak_alt_deg != null
      ? glowToward(report.light_dome, w.peak_az_deg, w.peak_alt_deg)
      : null

    const targetCell = (
      <td>
        {name}
        {t.note    && <span className="tg-note"> · {t.note}</span>}
        {sizeLabel && <span className="tg-note"> · Size: {sizeLabel}</span>}
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
          <td></td>
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
        <td className="wx-num tg-cond-col">
          {wxPt && (
            <span className={`tg-wx-inline wx-rating-${scoreBand(rateConditions(wxPt))}`}>
              <WmoIcon code={wxPt.weather_code} size={12} />
            </span>
          )}
        </td>
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
            <th></th>
            <th>Window</th>
          </tr>
        </thead>
        <tbody>
          {viableRows.map(row => {
            if (row.kind === 'header') {
              return (
                <tr key={row.key} className="tg-group-hdr">
                  <td colSpan={4}>{TYPE_LABELS[row.type] ?? row.type}</td>
                </tr>
              )
            }
            return renderTargetRow(row.target, row.key, false)
          })}

          {unviable.length > 0 && (
            <>
              <tr className="tg-unviable-hdr">
                <td colSpan={4}>
                  <button
                    type="button"
                    className="tg-blocked-toggle"
                    aria-expanded={showBlocked}
                    onClick={() => setShowBlocked(v => !v)}
                  >
                    <span className="tg-blocked-caret" aria-hidden="true">{showBlocked ? '▾' : '▸'}</span>
                    {`Unavailable Tonight (${unviable.length})`}
                    <span className="tg-blocked-counts">
                      {' — '}
                      {(() => {
                        const counts = new Map<string, number>()
                        for (const t of unviable) {
                          const label = blockerLabel(t.windows[0]?.blockers ?? [])
                          counts.set(label, (counts.get(label) ?? 0) + 1)
                        }
                        return [...counts.entries()]
                          .sort((a, b) => b[1] - a[1])
                          .map(([label, n]) => (n > 1 ? `${label} ×${n}` : label))
                          .join(' · ')
                      })()}
                    </span>
                  </button>
                </td>
              </tr>
              {showBlocked && unviableRows.map(row => {
                if (row.kind === 'header') {
                  return (
                    <tr key={row.key} className="tg-group-hdr tg-row-blocked">
                      <td colSpan={4}>{TYPE_LABELS[row.type] ?? row.type}</td>
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


export function wxAtTime(points: WeatherPoint[], isoTime: string): WeatherPoint | null {
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
