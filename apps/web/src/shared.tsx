import { useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
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

// ── Info popover ─────────────────────────────────────────────────────────────
// Hover/focus explainer for domain terms (Bortle, seeing, ZHR, score weights…).
// The bubble renders through a portal with fixed positioning because several
// hosts (Night Timeline, targets table) sit inside overflow-x:auto wrappers
// that would clip an absolutely-positioned child. Focusable so it works on
// keyboard and tap; pointer-events:none on the bubble keeps hover stable.

const TIP_HALF_W = 140 // half of max bubble width + margin, for viewport clamping

export function InfoTip({ tip, children }: { tip: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ x: number; y: number; below: boolean } | null>(null)

  function show() {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    const below = r.top < 130 // no headroom above → open downward
    setPos({
      x: Math.max(TIP_HALF_W, Math.min(window.innerWidth - TIP_HALF_W, r.left + r.width / 2)),
      y: below ? r.bottom : r.top,
      below,
    })
  }
  const hide = () => setPos(null)

  return (
    <span
      ref={ref}
      className="info-tip"
      tabIndex={0}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {pos && createPortal(
        <span
          className={`info-tip-bubble${pos.below ? ' below' : ''}`}
          role="tooltip"
          style={{ left: pos.x, top: pos.y }}
        >
          {tip}
        </span>,
        document.body,
      )}
    </span>
  )
}

// ── Score bar ────────────────────────────────────────────────────────────────

export function ScoreBar({ label, value, tip }: { label: string; value: number; tip?: ReactNode }) {
  const pct = Math.max(0, Math.min(100, value * 10))
  return (
    <div className="bar-row">
      <span className="bar-label">{tip ? <InfoTip tip={tip}>{label}</InfoTip> : label}</span>
      <span className="bar-track">
        <span className={`bar-fill band-${scoreBand(value)}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="bar-value">{value.toFixed(1)}</span>
    </div>
  )
}
