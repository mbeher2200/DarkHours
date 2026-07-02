import { scoreBand } from './format'

// ── Moon phase image (NASA SVS) ──────────────────────────────────────────────
// Uses NASA Scientific Visualization Studio 1024×1024 phase images
// (public domain, downloaded to /moon-phases/).

export function MoonPhaseSvg({ phaseName, size = 22 }: {
  phaseName: string
  illuminationPct?: number   // kept for API compat; image handles accuracy
  size?: number
}) {
  const p   = phaseName.toLowerCase()
  const src = p.includes('new')                               ? '/moon-phases/new.jpg'
            : p.includes('waxing') && p.includes('crescent') ? '/moon-phases/waxing-crescent.jpg'
            : p.includes('first')                             ? '/moon-phases/first-quarter.jpg'
            : p.includes('waxing') && p.includes('gibbous')  ? '/moon-phases/waxing-gibbous.jpg'
            : p.includes('full')                              ? '/moon-phases/full.jpg'
            : p.includes('waning') && p.includes('gibbous')  ? '/moon-phases/waning-gibbous.jpg'
            : p.includes('last') || p.includes('third')      ? '/moon-phases/last-quarter.jpg'
            : p.includes('waning') && p.includes('crescent') ? '/moon-phases/waning-crescent.jpg'
            : '/moon-phases/full.jpg'

  return (
    <img
      src={src}
      alt={phaseName}
      width={size}
      height={size}
      style={{ borderRadius: '50%', display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
    />
  )
}

// ── Score bar ────────────────────────────────────────────────────────────────

export function ScoreBar({ label, value }: { label: string; value: number }) {
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
