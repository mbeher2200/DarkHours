import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import type { NightReport, MilkyWaySummary, VisibleTarget } from '../../types'
import { formatTime, cardinal } from '../../format'
import { bestWindow, wxAtTime } from '../Targets'
import { eqToAltAz } from './astro'
import { loadCatalog } from './catalog'
import { sqmFromBortle } from './model'
import { buildGrainPoints, loadMwTexture } from './mwtex'
import { SkyRenderer, FOV_HALF_DEG, F_SVG, type TickResult } from './render'

// ── Realistic 360° sky dome ───────────────────────────────────────────────────
// Canvas underlay (star field, Milky Way, moon disc + phase, light domes, haze,
// clouds — see render.ts) with an SVG overlay on top for target markers, rings,
// cardinal labels, and tooltips. The SVG keeps the original gnomonic projection
// and heading/tilt drag behavior; the canvas mirrors it exactly.

const F = F_SVG   // stereographic focal length, shared with the canvas renderer
const toRad = (deg: number) => (deg * Math.PI) / 180

// Static compass ribbon pinned to the bottom of the frame (viewBox y 110.5–119.5).
const RIBBON_TOP = 110.5

// Pure fallback for the scrubbed time when the report has no sunset/sunrise and
// no summary times (unreachable behind the card's mw_summary gating).
const LOAD_MS = Date.now()

const CARDINALS = [
  { deg: 0, label: 'N' }, { deg: 45, label: 'NE' }, { deg: 90, label: 'E' },
  { deg: 135, label: 'SE' }, { deg: 180, label: 'S' }, { deg: 225, label: 'SW' },
  { deg: 270, label: 'W' }, { deg: 315, label: 'NW' },
]
// Altitude reference rings every 15°; 90° is the zenith — a point, not a ring —
// rendered as a small cross marker when it's in view (tilt ≳ 25°).
const ALT_RINGS = [15, 30, 45, 60, 75] as const

type Hover = { key: string; lines: string[]; x: number; y: number } | null

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
  const [mwReady, setMwReady] = useState(false)
  const [stackW, setStackW] = useState(0)
  const [tickResult, setTickResult] = useState<TickResult | null>(null)

  const pointerRef = useRef<{ x: number; y: number; sx: number; sy: number; moved: boolean } | null>(null)
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

    // Canvas resolution tracks the rendered size (ResizeObserver: window/card
    // width changes) AND the device pixel ratio (matchMedia: moving the window
    // to a different-density display, browser zoom).
    const resize = () => {
      const rect = stack.getBoundingClientRect()
      if (rect.width < 4) return
      renderer.setSize(rect.width, rect.height, window.devicePixelRatio || 1)
      setStackW(rect.width)
      requestDraw()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(stack)
    let mq: MediaQueryList | null = null
    const onDprChange = () => { resize(); watchDpr() }
    const watchDpr = () => {
      mq?.removeEventListener('change', onDprChange)
      mq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`)
      mq.addEventListener('change', onDprChange)
    }
    watchDpr()

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
    loadMwTexture()
      .then(tex => {
        if (!alive) return
        renderer.setMwTexture(tex, buildGrainPoints(tex))
        setMwReady(true)   // re-tick so band/grain get this tick's dimming
      })
      .catch(err => console.warn('sky dome: milky way texture failed to load', err))

    return () => {
      alive = false
      ro.disconnect()
      mq?.removeEventListener('change', onDprChange)
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
  // The 12k-star recompute is coalesced to one run per animation frame: a fast
  // scrub emits many input events per frame, and running the tick synchronously
  // in each commit saturates a phone's main thread and makes the slider stick.
  const tickParamsRef = useRef<{ utcMs: number; cloudFrac: number; aod: number | null } | null>(null)
  const tickRafRef = useRef(0)
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
    tickParamsRef.current = { utcMs: timeMs, cloudFrac, aod }
    if (tickRafRef.current) return   // a tick is already scheduled this frame
    tickRafRef.current = requestAnimationFrame(() => {
      tickRafRef.current = 0
      const params = tickParamsRef.current
      const ren = rendererRef.current
      if (!params || !ren) return
      setTickResult(ren.tick(params))
      ren.render()
    })
  }, [report, timeMs, cloudFrac, aod, catState, mwReady])
  useEffect(() => () => {
    if (tickRafRef.current) cancelAnimationFrame(tickRafRef.current)
    tickRafRef.current = 0
  }, [])

  // ── View changes redraw only (no recompute) ─────────────────────────────────
  useEffect(() => {
    rendererRef.current?.setView(heading, tilt)
    requestDraw()
  }, [heading, tilt, requestDraw])

  // ── Stereographic projection in SVG viewBox units (identical to the canvas) ─
  const project = useCallback((alt: number, az: number) => {
    const altR = toRad(alt)
    const azR = toRad(az - heading)
    const tiltR = toRad(tilt)
    const cosAlt = Math.cos(altR), sinAlt = Math.sin(altR)
    const dx = cosAlt * Math.sin(azR)
    const dy = sinAlt * Math.cos(tiltR) - cosAlt * Math.cos(azR) * Math.sin(tiltR)
    const dz = cosAlt * Math.cos(azR) * Math.cos(tiltR) + sinAlt * Math.sin(tiltR)
    if (dz <= 0) return { x: 100, y: 100, isFront: false }
    const k = (2 * F) / (1 + dz)
    return { x: 100 + k * dx, y: 100 - k * dy, isFront: true }
  }, [heading, tilt])

  // Horizon at the frame center. Stereographic maps the horizon great circle
  // to an ARC that bows up toward the frame edges when tilted, so the ground,
  // clip region, and horizon line are drawn from sampled points instead of a
  // straight rect (the center value still anchors soft checks).
  const horizonY = Math.min(120, 100 + 2 * F * Math.tan(toRad(tilt) / 2))
  const horizonArc = (() => {
    const pts: { x: number; y: number }[] = []
    for (let dAz = -80; dAz <= 80; dAz += 4) {
      const p = project(0, heading + dAz)
      if (p.isFront) pts.push({ x: p.x, y: p.y })
    }
    const str = pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    return {
      line: str.join(' '),
      ground: `${str.join(' ')} 250,140 -50,140`,
      sky: `-50,-50 250,-50 ${[...str].reverse().join(' ')}`,
      visible: pts.some(p => p.y < 121),
    }
  })()

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
    // Block native text selection from starting here — without this, dragging
    // the view highlights the direction/alt/target labels and nearby card text.
    e.preventDefault()
    pointerRef.current = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, moved: false }
    ;(e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId)
  }
  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const p = pointerRef.current
    if (!p) return
    const dx = e.clientX - p.x
    const dy = e.clientY - p.y
    p.x = e.clientX
    p.y = e.clientY
    if (!p.moved && Math.hypot(e.clientX - p.sx, e.clientY - p.sy) > 8) {
      p.moved = true
      setHover(null)   // don't leave a tooltip floating once the view rotates
    }
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const sens = (180 / Math.PI) / F
    setHeading(h => ((h + dx * (180 / rect.width) * sens) % 360 + 360) % 360)
    setTilt(t => Math.max(0, Math.min(45, t - dy * (120 / rect.height) * sens)))
  }
  const handlePointerUp = () => {
    // A tap (no meaningful movement) on empty sky dismisses an open tooltip.
    if (pointerRef.current && !pointerRef.current.moved) setHover(null)
    pointerRef.current = null
  }

  // Touch has no hover: tapping a marker toggles its tooltip. Mouse pointers
  // keep the pure hover behavior (a click must not clear the hovered tooltip).
  const tapToggle = (e: React.PointerEvent, h: NonNullable<Hover>) => {
    if (e.pointerType === 'mouse') return
    e.stopPropagation()
    setHover(prev => (prev?.key === h.key ? null : h))
  }

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
    const p = project(alt, heading - 60)
    if (!p.isFront || p.x < 11 || p.x > 100 || p.y < 4) return null
    return { alt, x: p.x, y: p.y }
  })
  const zenith = (() => {
    const p = project(90, heading)
    return p.isFront && p.y > 2 && p.y < horizonY ? p : null
  })()

  // Compass ribbon marks: cardinal letters (every 45°) + azimuth degrees every
  // 20° (numbers skipped where a N/E/S/W letter already sits). Positions use the
  // pure azimuth projection — the ribbon is a fixed indicator, not sky-anchored.
  const ribbonMarks: { x: number; kind: 'cardinal' | 'deg'; label: string }[] = []
  const pushRibbonMark = (az: number, kind: 'cardinal' | 'deg', label: string) => {
    let rel = az - heading
    while (rel <= -180) rel += 360
    while (rel > 180) rel -= 360
    if (Math.abs(rel) >= 80) return
    const x = 100 + 2 * F * Math.tan(toRad(rel) / 2)
    if (x < 14 || x > 186) return
    ribbonMarks.push({ x, kind, label })
  }
  CARDINALS.forEach(c => pushRibbonMark(c.deg, 'cardinal', c.label))
  for (let d = 0; d < 360; d += 20) {
    if (d % 90 !== 0) pushRibbonMark(d, 'deg', String(d))
  }

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

  // Hour labels along the track: whole local hours, thinned to ~5-7 labels.
  const hourMarks = useMemo(() => {
    if (tMin == null || tMax == null || tMax <= tMin) return []
    const spanH = (tMax - tMin) / 3_600_000
    const stepH = spanH > 11 ? 3 : spanH > 5.5 ? 2 : 1
    const labelFmt = new Intl.DateTimeFormat('en-US', { hour: 'numeric', timeZone: report.tz_name })
    const hourFmt = new Intl.DateTimeFormat('en-US', { hour: 'numeric', hourCycle: 'h23', timeZone: report.tz_name })
    const marks: { p: number; label: string }[] = []
    for (let t = Math.ceil(tMin / 3_600_000) * 3_600_000; t <= tMax; t += 3_600_000) {
      if (Number(hourFmt.format(t)) % stepH !== 0) continue
      const p = ((t - tMin) / (tMax - tMin)) * 100
      if (p < 3 || p > 97) continue
      marks.push({ p, label: labelFmt.format(t).replace(/[\s\u202f]/g, '') })
    }
    return marks
  }, [tMin, tMax, report.tz_name])

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
          {horizonArc.visible && (
            <polygon className="mw-dome-ground" points={horizonArc.ground} />
          )}

          <g clipPath="url(#sky-half-dome-clip)">
            <defs>
              <clipPath id="sky-half-dome-clip">
                <polygon points={horizonArc.sky} />
              </clipPath>
            </defs>
            {ringPolylines.map(ring => ring.points && (
              <polyline key={ring.alt} className="mw-dome-ring" points={ring.points} fill="none" />
            ))}
            {ringLabels.map(lbl => lbl && (
              <text key={`rl-${lbl.alt}`} className="mw-dome-ring-label"
                x={lbl.x + 2} y={lbl.y - 2} textAnchor="start">{lbl.alt}°</text>
            ))}
            {zenith && (
              <g>
                <line className="mw-dome-ring" x1={zenith.x - 2} y1={zenith.y} x2={zenith.x + 2} y2={zenith.y} />
                <line className="mw-dome-ring" x1={zenith.x} y1={zenith.y - 2} x2={zenith.x} y2={zenith.y + 2} />
                <text className="mw-dome-ring-label" x={zenith.x + 3} y={zenith.y - 2} textAnchor="start">90°</text>
              </g>
            )}
          </g>

          {/* Target markers, styled by viability */}
          {targetDots.map(t => {
            const p = project(t.alt, t.az)
            if (!p.isFront || p.y > horizonY + 2) return null
            return (
              <g key={t.name}>
                <circle cx={p.x} cy={p.y} r={t.cls === 'sky-tgt-ok' ? 1.1 : 0.9}
                  className={`sky-tgt ${t.cls}`} pointerEvents="none" />
                {t.persistent && (
                  <text className="sky-tgt-label" x={p.x + 4} y={p.y + 2.5}>{t.name}</text>
                )}
                <circle cx={p.x} cy={p.y} r="9" fill="transparent" pointerEvents="all"
                  style={{ cursor: 'pointer' }}
                  onPointerDown={e => e.stopPropagation()}
                  onPointerUp={e => tapToggle(e, { key: t.name, lines: t.lines, x: p.x, y: p.y })}
                  onMouseEnter={() => setHover({ key: t.name, lines: t.lines, x: p.x, y: p.y })}
                  onMouseLeave={() => setHover(null)} />
              </g>
            )
          })}

          {/* Moon hover region (disc itself is canvas-drawn) */}
          {moonPos && moonPos.isFront && (() => {
            const moonHover: NonNullable<Hover> = {
              key: 'moon',
              lines: [
                'MOON',
                `${report.phase_name.toUpperCase()} · ${Math.round(report.illumination_pct)}% LIT`,
                `ALT ${Math.round(tickResult!.moon.alt)}° · AZ ${Math.round(tickResult!.moon.az)}° ${cardinal(tickResult!.moon.az)}`,
              ],
              x: moonPos.x, y: moonPos.y,
            }
            return (
              <circle cx={moonPos.x} cy={moonPos.y} r="10" fill="transparent" pointerEvents="all"
                style={{ cursor: 'pointer' }}
                onPointerDown={e => e.stopPropagation()}
                onPointerUp={e => tapToggle(e, moonHover)}
                onMouseEnter={() => setHover(moonHover)}
                onMouseLeave={() => setHover(null)} />
            )
          })()}

          {horizonArc.visible && (
            <polyline className="mw-dome-horizon" points={horizonArc.line} fill="none" />
          )}
          <rect className="mw-dome-frame" x="10.5" y="0.5" width="179" height="119" fill="none" />

          {/* Static compass ribbon — pinned to the frame bottom, unaffected by tilt */}
          <g className="sky-ribbon">
            <rect className="sky-ribbon-band" x="10.5" y={RIBBON_TOP} width="179" height={119.5 - RIBBON_TOP} />
            {ribbonMarks.map(m => m.kind === 'cardinal' ? (
              <g key={`rc-${m.label}`}>
                <line className="sky-ribbon-tick sky-ribbon-tick-major"
                  x1={m.x} y1={RIBBON_TOP} x2={m.x} y2={RIBBON_TOP + 2.5} />
                <text className="sky-ribbon-cardinal" x={m.x} y={118.2} textAnchor="middle">{m.label}</text>
              </g>
            ) : (
              <g key={`rd-${m.label}`}>
                <line className="sky-ribbon-tick"
                  x1={m.x} y1={RIBBON_TOP} x2={m.x} y2={RIBBON_TOP + 1.8} />
                <text className="sky-ribbon-deg" x={m.x} y={117.4} textAnchor="middle">{m.label}</text>
              </g>
            ))}
          </g>

          {hover && (() => {
            // The tooltip lives in SVG user units, so its CSS font scales with
            // the rendered card width (units × width/180). Counter-scale above
            // the mobile cap (420px, where 5.5px units ≈ 13px on screen) so
            // the on-screen size stays constant on desktop-width cards.
            const tipScale = stackW > 420 ? 420 / stackW : 1
            return (
              <foreignObject x={Math.min(115, Math.max(15, hover.x - 37.5))}
                y={Math.max(1, hover.y - (12 + hover.lines.length * 8) * tipScale)}
                width="105" height={6 + hover.lines.length * 9} pointerEvents="none">
                <div className="sky-tooltip"
                  style={tipScale < 1 ? { transform: `scale(${tipScale})`, transformOrigin: '50% 0' } : undefined}>
                  {hover.lines.map((l, i) => <div key={i}>{l}</div>)}
                </div>
              </foreignObject>
            )
          })()}
        </svg>
      </div>

      {/* Time scrubber */}
      {hasScrub && (
        <div className="sky-scrub">
          <div className="sky-scrub-rail">
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
            <div className="sky-scrub-hours">
              {hourMarks.map(m => (
                <span key={m.p} className="sky-scrub-hour" style={{ left: `${m.p}%` }}>{m.label}</span>
              ))}
            </div>
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
