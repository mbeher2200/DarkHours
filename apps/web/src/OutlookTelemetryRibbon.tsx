import { useMemo, useState, type CSSProperties } from 'react'
import type { CalendarNight, CalendarResult } from './types'
import { scoreBand, scoreLabel, tonightIso, formatHm } from './format'
import { MoonPhaseSvg, ScoreBar } from './shared'

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const DOW_SHORT = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

function dateParts(iso: string): { dow: string; mmdd: string; long: string } {
  const d = new Date(iso + 'T00:00:00')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const long = `${DOW[d.getDay()]}, ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
  return { dow: DOW[d.getDay()], mmdd: `${mm}/${dd}`, long }
}

// Monochromatic luminance map: the raw score alone drives the cell wash's
// opacity (continuous, not banded) — hue never changes, and higher scores
// are darker/more saturated. A score of 0 is fully clear (no tint at all —
// just the plain card background); opacity scales linearly up to ~15% for
// the best possible night, maintaining contrast while preventing the pure
// black wash from reading too heavily.
function cellAlpha(score: number): number {
  const clamped = Math.max(0, Math.min(9, score))
  return (clamped / 9) * 0.15
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
  data, days, onViewDetails, isFetchingDetails, viewDetailsError,
}: {
  data: CalendarResult
  days: number
  onViewDetails: (date: string) => void
  isFetchingDetails?: boolean
  viewDetailsError?: string | null
}) {
  const nights = useMemo(() => [...data.nights].sort((a, b) => a.date.localeCompare(b.date)), [data.nights])
  const best = data.ranked[0] as CalendarNight | undefined
  const [selectedDate, setSelectedDate] = useState<string | null>(best?.date ?? nights[0]?.date ?? null)
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
      {best?.score != null && (
        <div className="telemetry-hero">
          <span className="telemetry-hero-label">
            Best night, {days}-day outlook · {dateParts(best.date).long} -{' '}
          </span>
          <span className={bestBand ? `telemetry-score-${bestBand}` : undefined}>
            {best.score.toFixed(1)}/10
          </span>
        </div>
      )}

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
                style={n.score != null ? ({ '--cell-alpha': cellAlpha(n.score) } as CSSProperties) : undefined}
                onClick={() => setSelectedDate(n.date)}
                aria-pressed={isSelected}
                title={`${dow} ${mmdd} — ${n.score != null ? n.score.toFixed(1) : 'N/A'}${isBest ? ' (best night)' : ''}`}
                aria-label={`${dow} ${mmdd}, score ${n.score != null ? n.score.toFixed(1) : 'unavailable'}${isBest ? ', best night' : ''}${isSelected ? ', currently selected' : ''}`}
              >
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
              <MoonPhaseSvg phaseName={selected.phase_name} illuminationPct={selected.illumination_pct} size={30} />
              <div className="telemetry-selected-date">{dateParts(selected.date).long}</div>
            </div>
            <div className="meta-row">
              <span className="meta-k">Score</span>
              <span className={`meta-v${selectedBand ? ` telemetry-score-${selectedBand}` : ''}`}>
                {selected.score != null ? `${selected.score.toFixed(1)} · ${scoreLabel(selected.score)}` : '—'}
              </span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Dark Hours</span>
              <span className="meta-v">{formatHm(selected.dark_hours)}</span>
            </div>
            <div className="meta-row">
              <span className="meta-k">Lunar Conditions</span>
              <span className="meta-v">{selected.phase_name} · {selected.illumination_pct.toFixed(0)}% illuminated</span>
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
                {sc.bortle != null && <ScoreBar label="Dark Sky" value={sc.bortle} />}
                {sc.moon   != null && <ScoreBar label="Lunar"    value={sc.moon} />}
                {sc.dark   != null && <ScoreBar label="Dark Hours" value={sc.dark} />}
                {selected.weather_informed && sc.weather != null && <ScoreBar label="Weather" value={sc.weather} />}
              </div>
            )}
            <button
              type="button"
              className="telemetry-view-details submit"
              disabled={isFetchingDetails}
              onClick={() => onViewDetails(selected.date)}
            >
              {isFetchingDetails ? 'Loading…' : 'View Details'}
            </button>
            {viewDetailsError && <p className="sat-notice">{viewDetailsError}</p>}
          </>
        ) : (
          <p className="sat-notice">No data</p>
        )}
      </div>
    </div>
  )
}
