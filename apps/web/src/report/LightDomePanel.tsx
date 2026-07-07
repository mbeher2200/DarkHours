import { useState, useRef, useEffect } from 'react'
import type { Direction, LightDomeSummary } from '../types'
import { fmtDist } from '../format'
import { LD_DIRS, LD_THETA_K, LD_THETA_FLOOR_DEG, LD_THETA_DEFAULT_DEG, LD_SIZE, ldColor, ldTent } from './glow'

// ── Light dome (all-sky fisheye) ─────────────────────────────────────────────
// An all-sky heatmap of horizon light pollution: centre = zenith (dark), rim = the
// 360° horizon, N at top. Each direction's horizon glow blooms upward by that
// dome's apparent height, so a distant low metro dome hugs the rim while a near one
// reaches higher. Mirrors the engine: glow(az,alt) = score(az)/(1+(alt/θ(az))²)
// (PyNightSkyPredictor/light_dome.py glow_toward).
// LD constants and utility functions (LD_DIRS, ldTent, glowToward, etc.) live in
// ./glow so this panel, MilkyWayDome, and the targets table share one model.


export function LightDomePanel({ summary, imperial }: { summary: LightDomeSummary; imperial: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  // Disk size; matched to the score card's content (the meta block) so the panel
  // doesn't make the card taller. Capped at LD_SIZE; falls back to it pre-measure.
  const [size, setSize] = useState(LD_SIZE)
  const { sky_state, scores, darkest_direction, domes } = summary
  // The darkest horizon is only a meaningful "point here" call when a darker side exists.
  const showBest = sky_state === 'dark' || sky_state === 'domed'

  useEffect(() => {
    const panel = panelRef.current
    if (!panel) return
    // ResizeObserver on the panel itself (flex: 0 0 100% — outer size is CSS-controlled,
    // not affected by canvas content changes, so no feedback loop).
    let rafId = 0
    const measure = () => {
      const body = panel.querySelector('.ld-body') as HTMLElement | null
      const isRow = !!body && getComputedStyle(body).flexDirection === 'row'
      const caption = panel.querySelector('.ld-caption') as HTMLElement | null
      const captionW = isRow ? (caption?.offsetWidth ?? 130) : 0
      const gap     = isRow ? 18 : 0
      const avail   = panel.clientWidth - captionW - gap
      setSize(Math.max(88, Math.min(LD_SIZE, Math.round(avail))))
    }
    // Debounce ResizeObserver: skip intermediate firings during layout reflow,
    // coalescing them into one rAF-deferred measurement per animation frame.
    const onResize = () => {
      cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(measure)
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(panel)
    return () => { ro.disconnect(); cancelAnimationFrame(rafId) }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = Math.round(size * dpr)
    canvas.width = W
    canvas.height = W
    canvas.style.width = `${size}px`
    canvas.style.height = `${size}px`

    // Per-direction glow and apparent dome height (real height for flagged domes).
    const domeH: Partial<Record<Direction, number>> = {}
    for (const d of domes) domeH[d.direction] = d.dome_height_deg
    const scoreArr = LD_DIRS.map(d => scores[d] ?? 0)
    const thetaArr = LD_DIRS.map(d =>
      domeH[d] != null ? Math.max(domeH[d]! * LD_THETA_K, LD_THETA_FLOOR_DEG) : LD_THETA_DEFAULT_DEG)

    // Pixel pass over the disk (device resolution; transform-independent putImageData).
    const margin = Math.max(11, size * 0.095)   // room for the N/E/S/W labels
    const cx = W / 2, cy = W / 2
    const R = (size / 2 - margin) * dpr
    const img = ctx.createImageData(W, W)
    const buf = img.data
    for (let y = 0; y < W; y++) {
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4
        const dx = x - cx, dy = y - cy
        const rr = Math.sqrt(dx * dx + dy * dy)
        if (rr > R) { buf[i + 3] = 0; continue }
        const alt = 90 * (1 - rr / R)                       // centre = zenith, rim = horizon
        const az = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360  // N up, clockwise
        const sc = ldTent(scoreArr, az)
        const th = ldTent(thetaArr, az)
        const g = sc / (1 + (alt / th) ** 2)
        const [r, gg, b] = ldColor(g)
        const edge = Math.max(0, Math.min(1, (R - rr) / (1.5 * dpr)))  // soft rim AA
        buf[i] = r; buf[i + 1] = gg; buf[i + 2] = b; buf[i + 3] = 255 * edge
      }
    }
    ctx.putImageData(img, 0, 0)

    // Decorations in CSS px.
    ctx.scale(dpr, dpr)
    const c = size / 2
    const cssR = size / 2 - margin
    ctx.lineWidth = 1
    for (const rad of [cssR * 0.5, cssR * 0.83]) {
      ctx.beginPath(); ctx.arc(c, c, rad, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(148,163,184,0.12)'; ctx.stroke()
    }
    ctx.beginPath(); ctx.arc(c, c, cssR, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(148,163,184,0.22)'; ctx.stroke()

    const lab = margin * 0.45
    ctx.fillStyle = '#94A3B8'
    ctx.font = '600 10px Poppins, system-ui, sans-serif'
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText('N', c, lab); ctx.fillText('S', c, size - lab)
    ctx.fillText('E', size - lab, c); ctx.fillText('W', lab, c)

    if (showBest) {
      const a = (LD_DIRS.indexOf(darkest_direction) * 45) * Math.PI / 180
      ctx.fillStyle = '#34D399'
      ctx.font = '700 12px Poppins, system-ui, sans-serif'
      ctx.fillText('★', c + Math.sin(a) * (cssR - 8), c - Math.cos(a) * (cssR - 8))
    }
  }, [summary, scores, darkest_direction, domes, sky_state, showBest, size])

  const fmtMi = (mi: number) => fmtDist(mi * 1.60934, imperial)
  const top = domes[0]
  const topDist = top?.mean_distance_mi != null ? `  ·  ${fmtMi(top.mean_distance_mi)}` : ''

  const aria =
    sky_state === 'urban' ? 'Urban sky: horizon washed out in all directions.'
    : sky_state === 'bright' ? `Bright sky: uniform glow, darkest horizon to the ${darkest_direction}.`
    : sky_state === 'domed' ? `${top?.label ?? 'Light dome'}. Darkest horizon to the ${darkest_direction}.`
    : `Dark sky. Darkest horizon to the ${darkest_direction}.`

  return (
    <div className="lightdome-panel" ref={panelRef}>
      <div className="ld-title">Horizon Glow</div>
      <div className="ld-body">
      <canvas ref={canvasRef} className="ld-canvas" role="img" aria-label={aria} />
      <div className="ld-caption">
        <span className={`ld-state ld-state-${sky_state}`}>
          {sky_state === 'dark' ? 'Dark sky'
            : sky_state === 'domed' ? 'Light dome'
            : sky_state === 'bright' ? 'Bright sky'
            : 'Urban sky'}
        </span>
        {sky_state === 'domed' && (
          <>
            <span className={`ld-line${top?.severity ? ` ld-dome-${top.severity}` : ''}`}>{top?.label}{topDist}</span>
            <span className="ld-sub">Best view <b>{darkest_direction}</b></span>
          </>
        )}
        {sky_state === 'dark' && (
          <span className="ld-line">Darkest horizon <b>{darkest_direction}</b></span>
        )}
        {sky_state === 'bright' && (
          <>
            <span className="ld-line">Uniform glow, no single dome</span>
            <span className="ld-sub">Darkest <b>{darkest_direction}</b>, still washed</span>
          </>
        )}
        {sky_state === 'urban' && (
          <span className="ld-line">Washed out in all directions</span>
        )}
        <span className="ld-legend" aria-hidden="true">
          <span>dark</span><span className="ld-legend-bar" /><span>dome</span>
        </span>
      </div>
      </div>
    </div>
  )
}
