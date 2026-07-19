import { useMemo, useState, type CSSProperties } from 'react'
import type { CalendarNight, CalendarResult } from './types'
import { scoreBand, scoreLabel, tonightIso } from './format'
import { ScoreBar } from './shared'
import { MoonPhaseIcon, WiIcon, WI_METEOR_VIEWBOX, WI_AURORA_VIEWBOX } from './report/icons'

const AURORA_TIER_LABELS: Record<string, string> = {
  overhead:     'overhead display',
  naked_eye:    'naked-eye on the horizon',
  photographic: 'camera-only glow',
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const DOW_SHORT = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

function dateParts(iso: string): { dow: string; mmdd: string; long: string; longWithYear: string } {
  const d = new Date(iso + 'T00:00:00')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const long = `${DOW[d.getDay()]}, ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
  const longWithYear = `${DOW[d.getDay()]}, ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
  return { dow: DOW[d.getDay()], mmdd: `${mm}/${dd}`, long, longWithYear }
}

// The raw score alone drives the cell wash's opacity (continuous, not banded).
// A score of 0 is fully clear (no tint at all — just the plain card background);
// opacity scales linearly up to ~15% for the best possible night, maintaining
// contrast while preventing the wash from reading too heavily.
function cellAlpha(score: number): number {
  const clamped = Math.max(0, Math.min(10, score))
  return (clamped / 10) * 0.15
}

// Astronomy-themed hue gradient for the wash color itself: deep red (poor, score 0)
// through amber (fair) to deep star-field green (excellent, score 10), interpolated
// continuously rather than banded into four flat colors. Stops reuse the app's
// existing semantic score tokens (--poor/--fair/--excellent) so the hue matches what
// those words mean elsewhere in the UI. Red mode never sees this — it keeps the
// neutral --dh-wash-rgb wash, which 02-red-mode.css already force-remaps to pure red;
// this function's output is only wired up for the non-red-mode case (see below).
const WASH_STOPS: [number, [number, number, number]][] = [
  [0, [200, 86, 86]],    // --poor #C85656
  [5, [217, 155, 65]],   // --fair #D99B41
  [10, [58, 135, 114]],  // --excellent #3A8772
]

function cellWashColor(score: number): string {
  const clamped = Math.max(0, Math.min(10, score))
  let [loScore, loRgb] = WASH_STOPS[0]
  let [hiScore, hiRgb] = WASH_STOPS[WASH_STOPS.length - 1]
  for (let i = 0; i < WASH_STOPS.length - 1; i++) {
    if (clamped >= WASH_STOPS[i][0] && clamped <= WASH_STOPS[i + 1][0]) {
      ;[loScore, loRgb] = WASH_STOPS[i]
      ;[hiScore, hiRgb] = WASH_STOPS[i + 1]
      break
    }
  }
  const t = hiScore === loScore ? 0 : (clamped - loScore) / (hiScore - loScore)
  const rgb = loRgb.map((c, i) => Math.round(c + (hiRgb[i] - c) * t))
  return rgb.join(', ')
}

/**
 * Compact calendar-style heat map: one small square per night, colored by
 * score band, laid out in day-of-week-aligned rows (like a contribution
 * calendar). Best night is called out as a hero metric; a detail panel below
 * shows the selected/hovered night. Composite score is whatever the API
 * returned — moon + dark hours + weather + bortle only, satellites never
 * factor in.
 */
export default function OutlookTelemetryRibbon({
  data, startExpanded, onViewDetails, isFetchingDetails, viewDetailsError, redMode,
}: {
  data: CalendarResult
  startExpanded?: boolean
  onViewDetails: (date: string) => void
  isFetchingDetails?: boolean
  viewDetailsError?: string | null
  redMode?: boolean
}) {
  const nights = useMemo(() => [...data.nights].sort((a, b) => a.date.localeCompare(b.date)), [data.nights])
  const best = data.ranked[0] as CalendarNight | undefined
  const [selectedDate, setSelectedDate] = useState<string | null>(best?.date ?? nights[0]?.date ?? null)
  const [expanded, setExpanded] = useState(startExpanded ?? false)
  const selected = nights.find(n => n.date === selectedDate) ?? nights[0] ?? null
  const today = tonightIso()

  // Pad leading cells so the grid's columns line up with day-of-week, like a real calendar.
  // Trailing cells are padded too — a 7- or 14-day range otherwise leaves the
  // last grid row incomplete, and those missing slots have no cell element at
  // all, so the grid container's own dark hairline background shows through
  // solid instead of the same empty-cell treatment leading blanks get.
  const leadingBlanks = nights.length ? new Date(nights[0].date + 'T00:00:00').getDay() : 0
  const trailingBlanks = (7 - ((leadingBlanks + nights.length) % 7)) % 7
  const cells: (CalendarNight | null)[] = [
    ...Array<null>(leadingBlanks).fill(null),
    ...nights,
    ...Array<null>(trailingBlanks).fill(null),
  ]

  const bestBand = best?.score != null ? scoreBand(best.score) : null
  const selectedBand = selected?.score != null ? scoreBand(selected.score) : null
  const sc = selected?.score_components

  return (
    <div className="telemetry-ribbon">
      <details
        className="telemetry-collapse"
        open={expanded}
        onToggle={e => setExpanded(e.currentTarget.open)}
      >
        <summary>
          <strong>Optimal Window:</strong>{' '}
          {best?.score != null ? (
            <>
              {dateParts(best.date).long} -{' '}
              <span className={bestBand ? `telemetry-score-${bestBand}` : undefined}>
                {best.score.toFixed(1)}/10
              </span>
            </>
          ) : '—'}
        </summary>

        <div className="telemetry-columns">
          <div className="heatmap-body">
            <div className="heatmap-dow-row" aria-hidden="true">
              {DOW_SHORT.map((d, i) => <span key={i}>{d}</span>)}
            </div>
            <div className="heatmap-grid">
              {cells.map((n, i) => {
                if (!n) return <span key={`pad-${i}`} className="heatmap-cell heatmap-cell-empty" />
                const band = n.score != null ? scoreBand(n.score) : null
                const isPast = n.date < today
                const isMuted = isPast || n.score == null
                const isSelected = n.date === selectedDate
                const isBest = n.date === best?.date
                const { dow, mmdd } = dateParts(n.date)
                const dayNum = Number(n.date.slice(8, 10))
                return (
                  <button
                    key={n.date}
                    type="button"
                    className={[
                      'heatmap-cell',
                      band ? `hm-${band}` : '',
                      isMuted ? 'muted' : '',
                      isSelected ? 'selected' : '',
                      isBest ? 'best' : '',
                    ].filter(Boolean).join(' ')}
                    style={n.score != null ? ({
                      '--cell-alpha': cellAlpha(n.score),
                      ...(redMode ? {} : { '--cell-wash-rgb': cellWashColor(n.score) }),
                    } as CSSProperties) : undefined}
                    onClick={() => setSelectedDate(n.date)}
                    aria-pressed={isSelected}
                    title={`${dow} ${mmdd} — ${n.score != null ? n.score.toFixed(1) : 'N/A'}${isBest ? ' (best night)' : ''} · ${n.phase_name}${n.meteor_shower ? ` · ${n.meteor_shower.name} meteor shower (${n.meteor_shower.note})` : ''}${n.aurora ? ` · Aurora Kp ${n.aurora.kp_max}${n.aurora.noaa_scale ? ` (${n.aurora.noaa_scale})` : ''} — ${AURORA_TIER_LABELS[n.aurora.tier]}` : ''}`}
                    aria-label={`${dow} ${mmdd}, score ${n.score != null ? n.score.toFixed(1) : 'unavailable'}${isBest ? ', best night' : ''}${isSelected ? ', currently selected' : ''}, ${n.phase_name} moon${n.meteor_shower ? `, ${n.meteor_shower.name} meteor shower active, ${n.meteor_shower.note}` : ''}${n.aurora ? `, aurora forecast Kp ${n.aurora.kp_max}, ${AURORA_TIER_LABELS[n.aurora.tier]}` : ''}`}
                  >
                    <span className="hm-moon">
                      <MoonPhaseIcon phaseName={n.phase_name} illuminationPct={n.illumination_pct} size={11} />
                    </span>
                    {n.meteor_shower && (
                      <span className="hm-meteor">
                        <WiIcon name="wi-meteor" size={11} viewBox={WI_METEOR_VIEWBOX} />
                      </span>
                    )}
                    {n.aurora && (
                      <span className="hm-aurora">
                        <WiIcon name="wi-aurora" size={11} viewBox={WI_AURORA_VIEWBOX} />
                      </span>
                    )}
                    <span className="hm-day">{dayNum}</span>
                    <span className="hm-score">{n.score != null ? n.score.toFixed(1) : '—'}</span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="telemetry-readout">
            {selected ? (
              <>
                <div className="telemetry-selected-head">
                  <span className="telemetry-preview-badge">Date Preview</span>
                  <div className="telemetry-selected-date">{dateParts(selected.date).longWithYear}</div>
                </div>
                <div className="meta-row">
                  <span className="meta-k">Score</span>
                  <span className={`meta-v${selectedBand ? ` telemetry-score-${selectedBand}` : ''}`}>
                    {selected.score != null ? `${selected.score.toFixed(1)} · ${scoreLabel(selected.score)}` : '—'}
                  </span>
                </div>
                {!selected.weather_informed && (
                  <p className="sat-notice">
                    Astronomy-only estimate —{' '}
                    {selected.wx_pending
                      ? 'forecast not yet available for this date'
                      : selected.wx_no_data
                      ? 'weather provider returned no data for this date'
                      : 'beyond the 16-day forecast horizon'}
                  </p>
                )}
                {sc && (
                  <div className="telemetry-mini-bars">
                    {sc.bortle != null && <ScoreBar label="Light Pollution" value={sc.bortle} />}
                    {sc.dark   != null && <ScoreBar label="Clear Dark Sky"  value={sc.dark} />}
                    {selected.weather_informed && sc.weather != null && <ScoreBar label="Weather" value={sc.weather} />}
                    {sc.moon   != null && <ScoreBar label="Lunar Conditions" value={sc.moon} />}
                  </div>
                )}
                <button
                  type="button"
                  className="telemetry-view-details submit"
                  disabled={isFetchingDetails}
                  onClick={() => onViewDetails(selected.date)}
                >
                  {isFetchingDetails ? 'Loading…' : 'Load Timeline'}
                </button>
                {viewDetailsError && <p className="sat-notice">{viewDetailsError}</p>}
              </>
            ) : (
              <p className="sat-notice">No data</p>
            )}
          </div>
        </div>
      </details>
    </div>
  )
}
