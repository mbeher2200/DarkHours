import React from 'react'
import { cardinal } from '../format'
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

export function MetaRow({ k, v, icon, tip }: { k: string; v: string; icon?: React.ReactNode; tip?: React.ReactNode }) {
  return (
    <div className="meta-row">
      <span className="meta-k">{tip ? <InfoTip tip={tip}>{k}</InfoTip> : k}:</span>
      <span className="meta-v" style={icon ? { display: 'inline-flex', alignItems: 'center', gap: 6 } : undefined}>
        {icon}{v}
      </span>
    </div>
  )
}
