import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import type { NightReport, MilkyWaySummary, VisibleTarget } from '../../types'
import { formatTime, cardinal } from '../../format'
import { bestWindow, wxAtTime } from '../Targets'
import { eqToAltAz } from './astro'
import { loadCatalog } from './catalog'
import { sqmFromBortle } from './model'
import { SkyRenderer, type TickResult } from './render'

// ── Realistic 360° sky dome ───────────────────────────────────────────────────
// Canvas underlay (star field, Milky Way, moon disc + phase, light domes, haze,
// clouds — see render.ts) with an SVG overlay on top for target markers, rings,
// cardinal labels, and tooltips. The SVG keeps the original gnomonic projection
// and heading/tilt drag behavior; the canvas mirrors it exactly.

const FOV_HALF_DEG = 60                          // 120° total horizontal FoV
const F = 100 / Math.tan(FOV_HALF_DEG * Math.PI / 180)
const toRad = (deg: number) => (deg * Math.PI) / 180

// Pure fallback for the scrubbed time when the report has no sunset/sunrise and
// no summary times (unreachable behind the card's mw_summary gating).
const LOAD_MS = Date.now()

const CARDINALS = [
  { deg: 0, label: 'N' }, { deg: 45, label: 'NE' }, { deg: 90, label: 'E' },
  { deg: 135, label: 'SE' }, { deg: 180, label: 'S' }, { deg: 225, label: 'SW' },
  { deg: 270, label: 'W' }, { deg: 315, label: 'NW' },
]
const ALT_RINGS = [20, 40] as const

type Hover = { lines: string[]; x: number; y: number } | null

function targetMarkerClass(v: VisibleTarget['viability']): string {
  return v === 'ok' ? 'sky-tgt-ok' : v === 'degraded' ? 'sky-tgt-degraded' : 'sky-tgt-blocked'
}

export function SkyDome({ summary, report }: {
  summary: MilkyWaySummary | null
  report: NightReport
}) {
  const [heading, setHeading] = useState<number>(
    summary?.core_peak_az_deg != null ? Math.round(summary.core_peak_az_deg) : 180,
  )
  const [tilt, setTilt] = useState<number>(0)
  const [hover, setHover] = useState<Hover>(null)
  const [catState, setCatState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [tickResult, setTickResult] = useState<TickResult | null>(null)

  const pointerRef = useRef<{ x: number; y: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stackRef = useRef<HTMLDivElement>(null)
  const rendererRef = useRef<SkyRenderer | null>(null)
  const rafRef = useRef(0)

  // ── Time scrubber domain: sunset → sunrise ──────────────────────────────────
  const tMin = useMemo(() => {
    const t = report.sunset ?? report.night_start
    return t ? new Date(t).getTime() : null
  }, [report.sunset, report.night_start])
  const tMax = useMemo(() => {
    const t = report.sunrise ?? report.night_end
    return t ? new Date(t).getTime() : null
  }, [report.sunrise, report.night_end])
  const defaultTime = useMemo(() => {
    const best = summary?.best_viewing_time ?? summary?.core_peak_time
    let t = best ? new Date(best).getTime() : NaN
    if (!Number.isFinite(t) && tMin != null && tMax != null) t = (tMin + tMax) / 2
    if (!Number.isFinite(t)) t = LOAD_MS
    if (tMin != null) t = Math.max(tMin, t)
    if (tMax != null) t = Math.min(tMax, t)
    return t
  }, [summary, tMin, tMax])
  const [timeMs, setTimeMs] = useState<number>(defaultTime)

  // A new report (different night/site) can arrive without remounting — snap the
  // scrubber back to that night's best viewing time (render-time state reset).
  const [prevDefault, setPrevDefault] = useState(defaultTime)
  if (prevDefault !== defaultTime) {
    setPrevDefault(defaultTime)
    setTimeMs(defaultTime)
  }

  const requestDraw = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0
      rendererRef.current?.render()
    })
  }, [])

  // ── Renderer lifecycle: create, size via ResizeObserver, load the catalog ───
  useEffect(() => {
    const canvas = canvasRef.current
    const stack = stackRef.current
    if (!canvas || !stack) return
    const renderer = new SkyRenderer(canvas)
    rendererRef.current = renderer

    const ro = new ResizeObserver(entries => {
      const rect = entries[0].contentRect
      if (rect.width < 4) return
      renderer.setSize(rect.width, rect.height, window.devicePixelRatio || 1)
      requestDraw()
    })
    ro.observe(stack)

    let alive = true
    loadCatalog()
      .then(cat => {
        if (!alive) return
        renderer.setCatalog(cat)
        setCatState('ready')
      })
      .catch(err => {
        console.warn('sky dome: star catalog failed to load', err)
        if (alive) setCatState('error')
      })

    return () => {
      alive = false
      ro.disconnect()
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      rendererRef.current = null
    }
  }, [requestDraw])

  // ── Conditions at the scrubbed time ─────────────────────────────────────────
  const wxPt = useMemo(() => {
    if (report.wx_no_data || report.wx_pending) return null
    return wxAtTime(report.weather_points || [], new Date(timeMs).toISOString())
  }, [report, timeMs])
  const cloudFrac = Math.min(1, Math.max(0, (wxPt?.cloud_cover_pct ?? 0) / 100))
  const aod = wxPt?.aerosol_optical_depth ?? null

  // ── Per-tick recompute: statics + time + conditions → positions/visibility ──
  useEffect(() => {
    const renderer = rendererRef.current
    if (!renderer) return
    const sqm = report.light_pollution?.sqm
      ?? sqmFromBortle(report.light_pollution?.bortle_class ?? 4)
    renderer.setStatics({
      lat: report.lat,
      lon: report.lon,
      sqm,
      lightDome: report.light_dome,
      illumPct: report.illumination_pct,
    })
    setTickResult(renderer.tick({ utcMs: timeMs, cloudFrac, aod }))
    requestDraw()
  }, [report, timeMs, cloudFrac, aod, catState, requestDraw])

  // ── View changes redraw only (no recompute) ─────────────────────────────────
  useEffect(() => {
    rendererRef.current?.setView(heading, tilt)
    requestDraw()
  }, [heading, tilt, requestDraw])

  // ── Gnomonic projection in SVG viewBox units (identical to the canvas) ──────
  const project = useCallback((alt: number, az: number) => {
    const altR = toRad(alt)
    const azR = toRad(az - heading)
    const tiltR = toRad(tilt)
    const cosAlt = Math.cos(altR), sinAlt = Math.sin(altR)
    const dx = cosAlt * Math.sin(azR)
    const dy = sinAlt * Math.cos(tiltR) - cosAlt * Math.cos(azR) * Math.sin(tiltR)
    const dz = cosAlt * Math.cos(azR) * Math.cos(tiltR) + sinAlt * Math.sin(tiltR)
    if (dz <= 0) return { x: 100, y: 100, isFront: false }
    return { x: 100 + F * (dx / dz), y: 100 - F * (dy / dz), isFront: true }
  }, [heading, tilt])

  const horizonY = Math.min(120, 100 + F * Math.tan(toRad(tilt)))

  // ── Drag interaction (pointer + native iOS touch) ───────────────────────────
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    let lastX = 0, lastY = 0
    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault()
      e.stopPropagation()
      lastX = e.touches[0].clientX
      lastY = e.touches[0].clientY
    }
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (!e.touches[0]) return
      const dx = e.touches[0].clientX - lastX
      const dy = e.touches[0].clientY - lastY
      lastX = e.touches[0].clientX
      lastY = e.touches[0].clientY
      const rect = svg.getBoundingClientRect()
      const sens = (180 / Math.PI) / F
      setHeading(h => ((h + dx * (180 / rect.width) * sens) % 360 + 360) % 360)
      setTilt(t => Math.max(0, Math.min(45, t - dy * (120 / rect.height) * sens)))
    }
    svg.addEventListener('touchstart', onTouchStart, { passive: false })
    svg.addEventListener('touchmove', onTouchMove, { passive: false })
    return () => {
      svg.removeEventListener('touchstart', onTouchStart)
      svg.removeEventListener('touchmove', onTouchMove)
    }
  }, [])

  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    pointerRef.current = { x: e.clientX, y: e.clientY }
    ;(e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId)
  }
  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pointerRef.current) return
    const dx = e.clientX - pointerRef.current.x
    const dy = e.clientY - pointerRef.current.y
    pointerRef.current = { x: e.clientX, y: e.clientY }
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const sens = (180 / Math.PI) / F
    setHeading(h => ((h + dx * (180 / rect.width) * sens) % 360 + 360) % 360)
    setTilt(t => Math.max(0, Math.min(45, t - dy * (120 / rect.height) * sens)))
  }
  const handlePointerUp = () => { pointerRef.current = null }

  // ── Target markers at the scrubbed time ─────────────────────────────────────
  const targetDots = useMemo(() =>
    (report.visible_targets || []).flatMap(t => {
      let alt: number, az: number
      if (t.ra_deg != null && t.dec_deg != null) {
        const pos = eqToAltAz(t.ra_deg, t.dec_deg, report.lat, report.lon, timeMs)
        alt = pos.alt; az = pos.az
      } else {
        // Older cached payloads: fall back to the static window-peak position.
        const w = bestWindow(t)
        if (w.peak_alt_deg == null) return []
        alt = w.peak_alt_deg; az = w.peak_az_deg
      }
      if (alt <= 0) return []
      const w = bestWindow(t)
      const meta = t.viability === 'ok'
        ? t.type.replace('_', ' ')
        : `${t.type.replace('_', ' ')} · ${t.viability}${w.blockers?.length ? `: ${w.blockers.join(', ').replace(/_/g, ' ')}` : ''}`
      return [{
        name: t.name,
        cls: targetMarkerClass(t.viability),
        persistent: t.type === 'planet' || t.name === 'Galactic Core',
        alt, az,
        lines: [
          t.name.toUpperCase(),
          meta.toUpperCase(),
          `ALT ${Math.round(alt)}° · AZ ${Math.round(az)}° ${cardinal(az)}`,
        ],
      }]
    }),
    [report.visible_targets, report.lat, report.lon, timeMs],
  )

  // Moon hover marker position (the disc itself is canvas-drawn).
  const moonPos = tickResult && tickResult.moon && tickResult.moon.alt > 0
    ? project(tickResult.moon.alt, tickResult.moon.az)
    : null

  // ── Ring + cardinal furniture (verbatim from the previous dome) ─────────────
  const ringPolylines = ALT_RINGS.map(alt => {
    const pts: string[] = []
    for (let dAz = -FOV_HALF_DEG; dAz <= FOV_HALF_DEG; dAz += 1) {
      const p = project(alt, heading + dAz)
      if (p.isFront) pts.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    }
    return { alt, points: pts.join(' ') }
  })
  const ringLabels = ALT_RINGS.map(alt => {
    const p = project(alt, heading - 54)
    if (!p.isFront || p.x < 11 || p.x > 100) return null
    return { alt, x: p.x, y: p.y }
  })

  // ── Scrubber track decoration: dark intervals + moon rise/set ticks ─────────
  const pct = useCallback((iso: string | null) => {
    if (iso == null || tMin == null || tMax == null || tMax <= tMin) return null
    const p = (new Date(iso).getTime() - tMin) / (tMax - tMin)
    return p >= 0 && p <= 1 ? p * 100 : null
  }, [tMin, tMax])
  const darkGradient = useMemo(() => {
    if (tMin == null || tMax == null) return undefined
    const stops: string[] = []
    for (const [a, b] of report.dark_intervals || []) {
      const pa = pct(a), pb = pct(b)
      if (pa == null && pb == null) continue
      const lo = Math.max(0, pa ?? 0), hi = Math.min(100, pb ?? 100)
      if (hi <= lo) continue
      stops.push(`transparent ${lo}%, currentColor ${lo}%, currentColor ${hi}%, transparent ${hi}%`)
    }
    return stops.length
      ? `linear-gradient(90deg, ${stops.join(', ')})`
      : undefined
  }, [report.dark_intervals, pct, tMin, tMax])
  const moonTicks = [
    { iso: report.moonrise, label: 'moonrise' },
    { iso: report.moonset, label: 'moonset' },
  ].flatMap(t => {
    const p = pct(t.iso)
    return p == null ? [] : [{ ...t, p }]
  })

  const timeIso = new Date(timeMs).toISOString()
  const hasScrub = tMin != null && tMax != null && tMax > tMin

  return (
    <div className="mw-dome-wrap">
      <div className="mw-dome-title">360° Sky Dome</div>
      <div className="sky-stack" ref={stackRef}>
        <canvas className="sky-canvas" ref={canvasRef} />
        <svg
          ref={svgRef}
          viewBox="10 0 180 120"
          xmlns="http://www.w3.org/2000/svg"
          className="mw-dome-svg sky-svg"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={handlePointerUp}
        >
          {horizonY < 120 && (
            <rect className="mw-dome-ground" x="0" y={horizonY} width="200" height={120 - horizonY} />
          )}

          <g clipPath="url(#sky-half-dome-clip)">
            <defs>
              <clipPath id="sky-half-dome-clip">
                <rect x="0" y="0" width="200" height={horizonY} />
              </clipPath>
            </defs>
            {ringPolylines.map(ring => ring.points && (
              <polyline key={ring.alt} className="mw-dome-ring" points={ring.points} fill="none" />
            ))}
            {ringLabels.map(lbl => lbl && (
              <text key={`rl-${lbl.alt}`} className="mw-dome-ring-label"
                x={lbl.x + 2} y={lbl.y - 2} textAnchor="start">{lbl.alt}°</text>
            ))}
          </g>

          {/* Target markers, styled by viability */}
          {targetDots.map(t => {
            const p = project(t.alt, t.az)
            if (!p.isFront || p.y > horizonY + 2) return null
            return (
              <g key={t.name}>
                <circle cx={p.x} cy={p.y} r={t.cls === 'sky-tgt-ok' ? 2.2 : 1.8}
                  className={`sky-tgt ${t.cls}`} pointerEvents="none" />
                {t.persistent && (
                  <text className="sky-tgt-label" x={p.x + 4} y={p.y + 2.5}>{t.name}</text>
                )}
                <circle cx={p.x} cy={p.y} r="8" fill="transparent" pointerEvents="all"
                  style={{ cursor: 'pointer' }}
                  onPointerDown={e => e.stopPropagation()}
                  onMouseEnter={() => setHover({ lines: t.lines, x: p.x, y: p.y })}
                  onMouseLeave={() => setHover(null)} />
              </g>
            )
          })}

          {/* Moon hover region (disc itself is canvas-drawn) */}
          {moonPos && moonPos.isFront && (
            <circle cx={moonPos.x} cy={moonPos.y} r="10" fill="transparent" pointerEvents="all"
              style={{ cursor: 'pointer' }}
              onPointerDown={e => e.stopPropagation()}
              onMouseEnter={() => setHover({
                lines: [
                  'MOON',
                  `${report.phase_name.toUpperCase()} · ${Math.round(report.illumination_pct)}% LIT`,
                  `ALT ${Math.round(tickResult!.moon.alt)}° · AZ ${Math.round(tickResult!.moon.az)}° ${cardinal(tickResult!.moon.az)}`,
                ],
                x: moonPos.x, y: moonPos.y,
              })}
              onMouseLeave={() => setHover(null)} />
          )}

          {horizonY < 120 && (
            <line className="mw-dome-horizon" x1="0" y1={horizonY} x2="200" y2={horizonY} />
          )}
          <rect className="mw-dome-frame" x="10.5" y="0.5" width="179" height="119" fill="none" />

          {horizonY < 120 && CARDINALS.map(c => {
            let relAz = c.deg - heading
            while (relAz <= -180) relAz += 360
            while (relAz > 180) relAz -= 360
            if (Math.abs(relAz) >= FOV_HALF_DEG) return null
            const x = 100 + F * Math.tan(toRad(relAz)) / Math.cos(toRad(tilt))
            const labelY = horizonY + 14
            if (x < 11 || x > 189 || labelY > 119) return null
            return (
              <g key={c.label}>
                <line className="mw-dome-tick" x1={x} y1={horizonY} x2={x} y2={horizonY + 3} />
                <text className="mw-dome-label" x={x} y={labelY} textAnchor="middle">{c.label}</text>
              </g>
            )
          })}

          {hover && (
            <foreignObject x={Math.min(115, Math.max(15, hover.x - 37.5))} y={Math.max(1, hover.y - 12 - hover.lines.length * 8)}
              width="105" height={6 + hover.lines.length * 9} pointerEvents="none">
              <div className="sky-tooltip">
                {hover.lines.map((l, i) => <div key={i}>{l}</div>)}
              </div>
            </foreignObject>
          )}
        </svg>
      </div>

      {/* Time scrubber */}
      {hasScrub && (
        <div className="sky-scrub">
          <div className="sky-scrub-track">
            <div className="sky-scrub-dark" style={{ backgroundImage: darkGradient }} />
            {moonTicks.map(t => (
              <div key={t.label} className="sky-scrub-moontick" style={{ left: `${t.p}%` }}
                title={t.label} />
            ))}
            <input
              type="range"
              min={tMin!} max={tMax!} step={300000}
              value={Math.min(tMax!, Math.max(tMin!, timeMs))}
              onChange={e => setTimeMs(Number(e.target.value))}
              aria-label="Time of night"
            />
          </div>
          <div className="sky-scrub-readout">{formatTime(timeIso, report.tz_name)}</div>
        </div>
      )}

      {/* Stats row */}
      <div className="sky-stats">
        {catState === 'loading' && <span>LOADING STAR FIELD…</span>}
        {catState === 'error' && <span>EST. VISIBLE STARS —</span>}
        {catState === 'ready' && tickResult && (
          <span>
            EST. VISIBLE STARS {tickResult.visibleCount.toLocaleString()}
            {' · '}LIM. MAG {tickResult.limitingMag.toFixed(1)}
          </span>
        )}
      </div>
      <div className="mw-dome-subtitle">Simulated sky · drag to pan · scrub time</div>
    </div>
  )
}
