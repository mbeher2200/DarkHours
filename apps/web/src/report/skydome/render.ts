// Imperative canvas renderer for the realistic sky dome.
//
// Two-stage pipeline:
//   tick(time+conditions)  — per scrub step: horizontal ENU unit vectors for
//     every star/MW sample (typed arrays, 2 trig calls each), per-star dimming
//     (extinction, light dome, moon, twilight, clouds) and the visible count.
//   render()               — per view frame: camera basis from heading/tilt,
//     3 dot products + gnomonic projection per point, draws batched by color
//     bin. Diffuse light (sky gradient, dome glows, MW band, moon halo, haze,
//     cloud veil) renders on a 1/3-resolution offscreen canvas scaled up.
//
// The projection is IDENTICAL to the SVG overlay's project() in SkyDome.tsx:
// gnomonic, f = 100/tan(60°) in the 180×120 viewBox space, center (100, 100).

import type { LightDomeSummary } from '../../types'
import { ldTent, LD_DIR_AZ, LD_DIRS } from '../glow'
import { lstRad, sunState, moonState, type MoonState } from './astro'
import type { StarCatalog } from './catalog'
import { COLOR_BINS } from './catalog'
import {
  airmass, buildMwSamples, domeScores8, extinctionCoeff, moonPenaltyMag,
  nelmFromSqm, rgb, skyBackground, starAlpha, starRadius,
  twilightPenaltyMag, type MwSamples,
  STAR_COLORS, STAR_COLORS_FAINT, FAINT_COLOR_MARGIN,
} from './model'

const DEG = Math.PI / 180
/** Half of the nominal field of view — single source of truth shared with the
 *  SVG overlay (SkyDome.tsx) so canvas and markers project identically. */
export const FOV_HALF_DEG = 65
const F_SVG = 100 / Math.tan(FOV_HALF_DEG * DEG)   // focal length in SVG units (viewBox 180×120)

export interface SkyStatics {
  lat: number
  lon: number
  sqm: number
  lightDome: LightDomeSummary | null
  illumPct: number
}

export interface TickInput {
  utcMs: number
  cloudFrac: number     // 0..1 total cloud cover at this time
  aod: number | null    // aerosol optical depth, null → default haze
}

export interface TickResult {
  visibleCount: number
  limitingMag: number
  moon: MoonState
  sunAlt: number
}

export class SkyRenderer {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private diffuse: HTMLCanvasElement
  private dctx: CanvasRenderingContext2D
  private haloSprite: HTMLCanvasElement
  private mwSpriteCool: HTMLCanvasElement
  private mwSpriteWarm: HTMLCanvasElement

  private W = 0
  private H = 0
  private dpr = 1

  private statics: SkyStatics | null = null
  private catalog: StarCatalog | null = null
  private mw: MwSamples = buildMwSamples()

  // Per-tick state
  private sE!: Float32Array; private sN!: Float32Array; private sU!: Float32Array
  private sAlpha!: Float32Array
  private mwE = new Float32Array(this.mw.n)
  private mwN = new Float32Array(this.mw.n)
  private mwU = new Float32Array(this.mw.n)
  private mwAlpha = new Float32Array(this.mw.n)
  private moon: MoonState | null = null
  private sunAltDeg = -90
  private sunAzDeg = 0
  private cloudFrac = 0
  private aod: number | null = null
  private magStopBound = 7
  private globalLim = 6.5

  // View
  private headingDeg = 180
  private tiltDeg = 0

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')!
    this.diffuse = document.createElement('canvas')
    this.dctx = this.diffuse.getContext('2d')!
    this.haloSprite = makeRadialSprite(32, 'rgba(255,255,255,0.55)')
    // Two-tone band (see the Mellinger-style panorama reference): cool silver
    // along the outer disk, warm cream near the bulge.
    this.mwSpriteCool = makeRadialSprite(64, 'rgba(186,196,216,0.85)')
    this.mwSpriteWarm = makeRadialSprite(64, 'rgba(228,212,184,0.85)')
  }

  setSize(cssW: number, cssH: number, dpr: number) {
    this.dpr = Math.min(2, dpr)
    this.W = Math.max(1, Math.round(cssW * this.dpr))
    this.H = Math.max(1, Math.round(cssH * this.dpr))
    if (this.canvas.width !== this.W) this.canvas.width = this.W
    if (this.canvas.height !== this.H) this.canvas.height = this.H
    this.diffuse.width = Math.max(1, Math.round(this.W / 3))
    this.diffuse.height = Math.max(1, Math.round(this.H / 3))
  }

  setStatics(s: SkyStatics) { this.statics = s }

  private labelIdx = new Set<number>()

  setCatalog(cat: StarCatalog) {
    this.catalog = cat
    this.sE = new Float32Array(cat.n)
    this.sN = new Float32Array(cat.n)
    this.sU = new Float32Array(cat.n)
    this.sAlpha = new Float32Array(cat.n)
    // Label the 30 brightest named stars plus Polaris (the names table is
    // magnitude-ordered, with Polaris force-included by the build script).
    this.labelIdx.clear()
    let taken = 0
    for (const [i, name] of cat.names) {
      if (taken < 30) { this.labelIdx.add(i); taken++ }
      else if (name === 'Polaris') this.labelIdx.add(i)
    }
  }

  setView(headingDeg: number, tiltDeg: number) {
    this.headingDeg = headingDeg
    this.tiltDeg = tiltDeg
  }

  /** Recompute positions + visibility for a moment in time. */
  tick(input: TickInput): TickResult {
    const s = this.statics
    if (!s) return { visibleCount: 0, limitingMag: 0, moon: null as never, sunAlt: -90 }
    const { utcMs, cloudFrac, aod } = input
    this.cloudFrac = cloudFrac
    this.aod = aod

    const lst = lstRad(utcMs, s.lon)
    const sinLat = Math.sin(s.lat * DEG)
    const cosLat = Math.cos(s.lat * DEG)

    const sun = sunState(s.lat, s.lon, utcMs)
    this.sunAltDeg = sun.alt
    this.sunAzDeg = sun.az
    const moon = moonState(s.lat, s.lon, utcMs)
    this.moon = moon

    const nelm = nelmFromSqm(s.sqm)
    const moonPen = moonPenaltyMag(s.illumPct, moon.alt)
    const twPen = twilightPenaltyMag(sun.alt)
    const k = extinctionCoeff(aod)
    const globalLim = nelm - moonPen - twPen
    this.globalLim = globalLim
    this.magStopBound = globalLim   // Δm_ext, Δm_ld ≥ 0 ⇒ nothing fainter can show
    const scores8 = domeScores8(s.lightDome)
    // Uniform veil: star visibility scales with the clear-sky fraction and
    // reaches exactly 0 at full overcast — nothing shines through 100% cloud.
    const starCloudDim = Math.pow(Math.max(0, 1 - cloudFrac), 1.3)

    let visible = 0
    const cat = this.catalog
    if (cat) {
      const { raRad, sinDec, cosDec, mag, n } = cat
      for (let i = 0; i < n; i++) {
        const ha = lst - raRad[i]
        const cosHa = Math.cos(ha)
        const U = sinDec[i] * sinLat + cosDec[i] * cosLat * cosHa
        if (U <= 0 || mag[i] > globalLim) { this.sAlpha[i] = 0; continue }
        const E = -cosDec[i] * Math.sin(ha)
        const N = sinDec[i] * cosLat - cosDec[i] * sinLat * cosHa
        this.sE[i] = E; this.sN[i] = N; this.sU[i] = U
        const altDeg = Math.asin(Math.min(1, U)) / DEG
        const azDeg = (Math.atan2(E, N) / DEG + 360) % 360
        const glow = ldTent(scores8, azDeg) / (1 + (altDeg / 40) ** 2)
        const dmLd = 0.8686 * glow
        const dmExt = k * (airmass(U) - 1)
        const margin = globalLim - dmLd - dmExt - mag[i]
        if (margin <= 0) { this.sAlpha[i] = 0; continue }
        visible++
        this.sAlpha[i] = starAlpha(margin) * starCloudDim
      }
    }

    // MW band samples: brightness = weight × 10^(−0.4·(Δm_ld + moon + twilight)),
    // the same magnitude→brightness mapping as washoutFactor, killed fast by cloud.
    const mwCloud = Math.pow(Math.max(0, 1 - cloudFrac), 1.5)
    for (let i = 0; i < this.mw.n; i++) {
      const ha = lst - this.mw.raRad[i]
      const cosHa = Math.cos(ha)
      const U = this.mw.sinDec[i] * sinLat + this.mw.cosDec[i] * cosLat * cosHa
      if (U <= -0.05) { this.mwAlpha[i] = 0; continue }
      const E = -this.mw.cosDec[i] * Math.sin(ha)
      const N = this.mw.sinDec[i] * cosLat - this.mw.cosDec[i] * sinLat * cosHa
      this.mwE[i] = E; this.mwN[i] = N; this.mwU[i] = U
      const altDeg = Math.asin(Math.max(-1, Math.min(1, U))) / DEG
      const azDeg = (Math.atan2(E, N) / DEG + 360) % 360
      const glow = ldTent(scores8, azDeg) / (1 + (Math.max(0, altDeg) / 40) ** 2)
      const dm = 0.8686 * glow + moonPen + twPen
      this.mwAlpha[i] = this.mw.weight[i] * Math.pow(10, -0.4 * dm) * mwCloud
    }

    return {
      visibleCount: Math.round(visible * (1 - cloudFrac)),
      limitingMag: Math.max(0, globalLim),
      moon,
      sunAlt: sun.alt,
    }
  }

  /** Gnomonic projection in device pixels; matches the SVG overlay exactly. */
  private camera() {
    const h = this.headingDeg * DEG
    const t = this.tiltDeg * DEG
    const sinH = Math.sin(h), cosH = Math.cos(h)
    const sinT = Math.sin(t), cosT = Math.cos(t)
    return {
      // ENU basis vectors
      fx: sinH * cosT, fy: cosH * cosT, fz: sinT,          // forward
      rx: cosH, ry: -sinH, rz: 0,                          // right
      ux: -sinH * sinT, uy: -cosH * sinT, uz: cosT,        // up
      F: F_SVG * (this.W / 180),
      cx: this.W / 2,
      cy: this.H * (100 / 120),
    }
  }

  private projectAltAz(cam: ReturnType<SkyRenderer['camera']>, altDeg: number, azDeg: number, scale = 1) {
    const alt = altDeg * DEG, az = azDeg * DEG
    const E = Math.cos(alt) * Math.sin(az)
    const N = Math.cos(alt) * Math.cos(az)
    const U = Math.sin(alt)
    const dz = E * cam.fx + N * cam.fy + U * cam.fz
    if (dz <= 0.001) return null
    const dx = E * cam.rx + N * cam.ry + U * cam.rz
    const dy = E * cam.ux + N * cam.uy + U * cam.uz
    return {
      x: (cam.cx + cam.F * (dx / dz)) * scale,
      y: (cam.cy - cam.F * (dy / dz)) * scale,
    }
  }

  render() {
    if (!this.statics) return
    const cam = this.camera()
    this.drawDiffuse(cam)
    const ctx = this.ctx
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.clearRect(0, 0, this.W, this.H)
    ctx.drawImage(this.diffuse, 0, 0, this.W, this.H)
    this.drawStars(cam)
    this.drawMoon(cam)
    ctx.globalAlpha = 1
  }

  // ── Diffuse light: sky gradient, dome glows, MW band, moon halo, haze, clouds ──
  private drawDiffuse(cam: ReturnType<SkyRenderer['camera']>) {
    const d = this.dctx
    const s = this.statics!
    const w = this.diffuse.width, h = this.diffuse.height
    const scale = w / this.W
    const horizonY = (cam.cy + cam.F * Math.tan(this.tiltDeg * DEG)) * scale

    d.globalCompositeOperation = 'source-over'
    d.globalAlpha = 1

    // 1. Sky background gradient (zenith → horizon), horizon color below.
    const bg = skyBackground(s.sqm, this.sunAltDeg, this.cloudFrac)
    const grad = d.createLinearGradient(0, Math.min(0, horizonY - h), 0, Math.max(1, horizonY))
    grad.addColorStop(0, rgb(bg.zenith))
    grad.addColorStop(1, rgb(bg.horizon))
    d.fillStyle = grad
    d.fillRect(0, 0, w, h)

    // 2. Light-dome glows: warm radial gradients anchored just above the horizon.
    if (s.lightDome) {
      const cloudBoost = 1 + 0.6 * this.cloudFrac   // clouds reflect city light
      for (const dir of LD_DIRS) {
        const score = s.lightDome.scores[dir] ?? 0
        if (score < 0.03) continue
        const pos = this.projectAltAz(cam, 3, LD_DIR_AZ[dir], scale)
        if (!pos) continue
        const r = (30 + 18 * Math.log1p(score)) / 200 * w * 1.9
        const op = Math.min(0.55, (0.12 + 0.15 * Math.log1p(score)) * cloudBoost)
        const g = d.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, r)
        g.addColorStop(0, `rgba(255,190,110,${op})`)
        g.addColorStop(0.4, `rgba(255,170,90,${op * 0.45})`)
        g.addColorStop(1, 'rgba(255,170,90,0)')
        d.fillStyle = g
        d.fillRect(pos.x - r, pos.y - r, 2 * r, 2 * r)
      }
    }

    // 3. Milky Way band: additive soft blobs.
    d.globalCompositeOperation = 'lighter'
    const mwR = Math.max(6, w * 0.085)
    for (let i = 0; i < this.mw.n; i++) {
      const a = this.mwAlpha[i]
      if (a < 0.004) continue
      const dz = this.mwE[i] * cam.fx + this.mwN[i] * cam.fy + this.mwU[i] * cam.fz
      if (dz <= 0.01) continue
      const x = (cam.cx + cam.F * ((this.mwE[i] * cam.rx + this.mwN[i] * cam.ry) / dz)) * scale
      const y = (cam.cy - cam.F * ((this.mwE[i] * cam.ux + this.mwN[i] * cam.uy + this.mwU[i] * cam.uz) / dz)) * scale
      if (x < -mwR || x > w + mwR || y < -mwR || y > h + mwR) continue
      const r = mwR * (0.7 + 0.6 * this.mw.weight[i])
      d.globalAlpha = Math.min(0.24, 0.065 * a)
      const sprite = this.mw.warmth[i] > 0.45 ? this.mwSpriteWarm : this.mwSpriteCool
      d.drawImage(sprite, x - r, y - r, 2 * r, 2 * r)
    }
    d.globalAlpha = 1

    // 4. Moon halo (drawn before haze/clouds so they sit in front of it).
    // Fades with cloud cover — fully hidden behind a 100% overcast deck.
    const moon = this.moon
    if (moon && moon.alt > 0 && s.illumPct >= 1 && this.cloudFrac < 0.98) {
      const pos = this.projectAltAz(cam, moon.alt, moon.az, scale)
      if (pos) {
        const illum = s.illumPct / 100
        const aod = this.aod ?? 0.10
        const r = (20 + 60 * illum) * (1 + 2 * aod) * (w / 420)
        const op = Math.min(0.5, 0.10 + 0.45 * illum) * (1 - this.cloudFrac)
        const g = d.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, r)
        g.addColorStop(0, `rgba(210,222,250,${op})`)
        g.addColorStop(0.35, `rgba(200,214,246,${op * 0.35})`)
        g.addColorStop(1, 'rgba(200,214,246,0)')
        d.globalCompositeOperation = 'lighter'
        d.fillStyle = g
        d.fillRect(pos.x - r, pos.y - r, 2 * r, 2 * r)
      }
    }
    d.globalCompositeOperation = 'source-over'

    // 5. Haze: aerosol scattering brightens the sky just above the horizon.
    const aodHaze = this.aod ?? 0.10
    const hazeAlpha = Math.min(0.5, 0.08 + 1.2 * aodHaze)
    const hazeTop = horizonY - h * 0.22
    if (horizonY > 0) {
      const hg = d.createLinearGradient(0, hazeTop, 0, horizonY)
      const hc: [number, number, number] = [
        Math.min(255, bg.horizon[0] + 26),
        Math.min(255, bg.horizon[1] + 26),
        Math.min(255, bg.horizon[2] + 30),
      ]
      hg.addColorStop(0, rgb(hc, 0))
      hg.addColorStop(1, rgb(hc, hazeAlpha))
      d.fillStyle = hg
      d.fillRect(0, Math.max(0, hazeTop), w, Math.min(h, horizonY) - Math.max(0, hazeTop))
    }

    // 6. Cloud veil — approaches an opaque flat deck as cover → 100%.
    if (this.cloudFrac > 0.02) {
      d.fillStyle = `rgba(38,42,53,${Math.min(0.92, 0.75 * this.cloudFrac ** 1.3)})`
      d.fillRect(0, 0, w, h)
    }
  }

  // ── Point stars, batched by color bin (mag-ascending within each bin) ────────
  private drawStars(cam: ReturnType<SkyRenderer['camera']>) {
    const cat = this.catalog
    if (!cat) return
    const ctx = this.ctx
    const { order, binStart, mag } = cat
    const faintAt = this.globalLim - FAINT_COLOR_MARGIN
    // Star size grows only gently with canvas width (sqrt, capped): point sources
    // should stay near-point at any card size, not scale like the image.
    const cssW = this.W / this.dpr
    const rScale = this.dpr * Math.min(1.7, Math.max(0.75, Math.sqrt(cssW / 420)))
    const wLim = this.W + 4, hLim = this.H + 4
    const TWO_PI = 2 * Math.PI

    for (let b = 0; b < COLOR_BINS; b++) {
      const c = STAR_COLORS[b]
      ctx.fillStyle = `rgb(${c[0]},${c[1]},${c[2]})`
      let faint = false
      for (let j = binStart[b]; j < binStart[b + 1]; j++) {
        const i = order[j]
        const m = mag[i]
        if (m > this.magStopBound) break     // bin is mag-sorted
        const a = this.sAlpha[i]
        if (a <= 0) continue
        const E = this.sE[i], N = this.sN[i], U = this.sU[i]
        const dz = E * cam.fx + N * cam.fy + U * cam.fz
        if (dz <= 0.001) continue
        const x = cam.cx + cam.F * ((E * cam.rx + N * cam.ry) / dz)
        if (x < -4 || x > wLim) continue
        const y = cam.cy - cam.F * ((E * cam.ux + N * cam.uy + U * cam.uz) / dz)
        if (y < -4 || y > hLim) continue
        if (!faint && m > faintAt) {
          const fc = STAR_COLORS_FAINT[b]
          ctx.fillStyle = `rgb(${fc[0]},${fc[1]},${fc[2]})`
          faint = true
        }
        const r = starRadius(m) * rScale
        ctx.globalAlpha = a
        if (r <= 1.4) {
          // Sub-1.5px points: a rect is indistinguishable from a disc and cheaper.
          ctx.fillRect(x - r, y - r, 2 * r, 2 * r)
        } else {
          ctx.beginPath()
          ctx.arc(x, y, r, 0, TWO_PI)
          ctx.fill()
        }
        if (m < 1.2) {
          ctx.globalAlpha = a * 0.45
          const hr = r * 5
          ctx.drawImage(this.haloSprite, x - hr, y - hr, 2 * hr, 2 * hr)
        }
      }
    }
    ctx.globalAlpha = 1

    // Labels for the brightest named stars (canvas text → red-mode CSS filter
    // applies). Font grows with the same sqrt-of-width scale as the stars so
    // names stay readable on a full-width desktop card.
    ctx.font = `${Math.round(10 * rScale)}px "IBM Plex Mono", monospace`
    ctx.fillStyle = 'rgba(200,212,238,0.72)'
    for (const [i, name] of cat.names) {
      if (!this.labelIdx.has(i) || this.sAlpha[i] <= 0) continue
      const E = this.sE[i], N = this.sN[i], U = this.sU[i]
      const dz = E * cam.fx + N * cam.fy + U * cam.fz
      if (dz <= 0.001) continue
      const x = cam.cx + cam.F * ((E * cam.rx + N * cam.ry) / dz)
      const y = cam.cy - cam.F * ((E * cam.ux + N * cam.uy + U * cam.uz) / dz)
      if (x < 0 || x > this.W || y < 0 || y > this.H) continue
      ctx.fillText(name.toUpperCase(), x + 3 * rScale, y + 1.8 * rScale)
    }
  }

  // ── Moon disc with phase (terminator ellipse) ────────────────────────────────
  private drawMoon(cam: ReturnType<SkyRenderer['camera']>) {
    const moon = this.moon
    const s = this.statics!
    if (!moon || moon.alt <= 0) return
    // The disc dims behind cloud and disappears entirely at full overcast.
    const cloudDim = Math.pow(Math.max(0, 1 - this.cloudFrac), 1.5)
    if (cloudDim < 0.03) return
    const pos = this.projectAltAz(cam, moon.alt, moon.az)
    if (!pos) return
    const ctx = this.ctx
    const r = Math.max(5, this.W * 0.016)

    // Screen-space "toward zenith" at the moon's position.
    const upPos = this.projectAltAz(cam, Math.min(89.9, moon.alt + 2), moon.az)
    let upX = 0, upY = -1
    if (upPos) {
      const dx = upPos.x - pos.x, dy = upPos.y - pos.y
      const len = Math.hypot(dx, dy)
      if (len > 1e-6) { upX = dx / len; upY = dy / len }
    }
    // Position angle of the bright limb: bearing from moon toward the sun,
    // measured from zenith direction, positive toward increasing azimuth.
    const dAz = (this.sunAzDeg - moon.az) * DEG
    const sAlt = this.sunAltDeg * DEG, mAlt = moon.alt * DEG
    const chi = Math.atan2(
      Math.cos(sAlt) * Math.sin(dAz),
      Math.sin(sAlt) * Math.cos(mAlt) - Math.cos(sAlt) * Math.sin(mAlt) * Math.cos(dAz),
    )
    // Rotate screen-up by chi (screen y is down, so "toward +azimuth" is clockwise).
    const limbAngle = Math.atan2(upY, upX) + chi

    const k = Math.min(1, Math.max(0, s.illumPct / 100))
    const lowAlt = Math.min(1, Math.max(0, (15 - moon.alt) / 15))
    const bright = `rgb(${Math.round(238 - 6 * lowAlt)},${Math.round(236 - 30 * lowAlt)},${Math.round(226 - 60 * lowAlt)})`

    ctx.save()
    ctx.translate(pos.x, pos.y)
    ctx.rotate(limbAngle)
    ctx.globalAlpha = cloudDim
    // Shadowed disc (earthshine hint)
    ctx.beginPath()
    ctx.arc(0, 0, r, 0, 2 * Math.PI)
    ctx.fillStyle = 'rgba(58,62,74,0.85)'
    ctx.fill()
    // Lit region: bright-limb semicircle (+x) closed by the terminator ellipse.
    const rx = r * Math.abs(2 * k - 1)
    ctx.beginPath()
    ctx.arc(0, 0, r, -Math.PI / 2, Math.PI / 2)
    if (k >= 0.5) {
      ctx.ellipse(0, 0, rx, r, 0, Math.PI / 2, 3 * Math.PI / 2, false)  // bulge past center
    } else {
      ctx.ellipse(0, 0, rx, r, 0, Math.PI / 2, -Math.PI / 2, true)      // crescent
    }
    ctx.closePath()
    ctx.fillStyle = bright
    ctx.fill()
    ctx.restore()
  }
}

/** Soft radial-gradient sprite (white core → transparent edge). */
function makeRadialSprite(size: number, coreColor: string): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const ctx = c.getContext('2d')!
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  g.addColorStop(0, coreColor)
  g.addColorStop(0.5, coreColor.replace(/[\d.]+\)$/, m => `${parseFloat(m) * 0.35})`))
  g.addColorStop(1, coreColor.replace(/[\d.]+\)$/, '0)'))
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  return c
}
