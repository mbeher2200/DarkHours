import React from 'react'
import { cardinal, scoreBand } from '../format'
import { InfoTip } from '../shared'

// first, then altitude — e.g. "Az 195° S · Alt 42°".
// "Mon, Jul 13" — date-only strings are report-local, render without TZ shift
export function fmtNightDate(iso: string): string {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  })
}

export function fmtPos(altDeg: number, azDeg: number): string {
  return `Az ${Math.round(azDeg)}° ${cardinal(azDeg)} · Alt ${Math.round(altDeg)}°`
}

// Phase A of the "View Details" terminal-recalculation sequence: while a
// date-only fetch is in flight, value cells render this placeholder instead of
// their (now-stale) content — row/column structure never changes, so there's
// no layout shift to guard against.
export function cell(isFetching: boolean, value: React.ReactNode): React.ReactNode {
  return isFetching ? '—' : value
}

// ── Metadata row ─────────────────────────────────────────────────────────────
// `score` ties the row to its composite-score factor (Bortle → Light Pollution,
// dark hours → Clear Dark Sky, etc.) via an inline bar; `scoreTip` carries the
// weighting/methodology explainer that used to live on the standalone score
// bars, now surfaced through a small "?" badge so it isn't lost.

export function MetaRow({ k, v, icon, tip, score, scoreTip }: {
  k: string; v: string; icon?: React.ReactNode; tip?: React.ReactNode
  score?: number | null; scoreTip?: React.ReactNode
}) {
  const pct = score != null ? Math.max(0, Math.min(100, score * 10)) : 0
  return (
    <div className="meta-row-wrap">
      <div className="meta-row">
        <span className="meta-k">{tip ? <InfoTip tip={tip}>{k}</InfoTip> : k}:</span>
        <span className="meta-v" style={icon ? { display: 'inline-flex', alignItems: 'center', gap: 6 } : undefined}>
          {icon}{v}
        </span>
      </div>
      {score != null && (
        <div className="meta-bar">
          <span className="bar-track">
            <span className={`bar-fill band-${scoreBand(score)}`} style={{ width: `${pct}%` }} />
          </span>
          <span className="bar-value-cell">
            <span className="bar-value">{score.toFixed(1)}</span>
            {scoreTip && (
              <InfoTip tip={scoreTip}>
                <span className="score-help" aria-label="How this score is calculated">?</span>
              </InfoTip>
            )}
          </span>
        </div>
      )}
    </div>
  )
}
