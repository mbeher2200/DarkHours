// Imperative canvas renderer for the realistic sky dome.
//
// Two-stage pipeline:
//   tick(time+conditions)  — per scrub step: horizontal ENU unit vectors for
//     every star/grain point (typed arrays, 2 trig calls each), per-star
//     dimming (extinction, light dome, moon, twilight, clouds), the visible
//     count, and the ENU→galactic/ecliptic frame matrices for the band pass.
//   render()               — per view frame: camera basis from heading/tilt,
//     3 dot products + gnomonic projection per point, draws batched by color
//     bin. Diffuse light (sky gradient, dome glows, moon halo, haze, cloud
//     veil) renders on a 1/3-resolution offscreen canvas scaled up; the Milky
//     Way band + zodiacal light render per-pixel from the real-sky texture
//     (mwtex.ts) on a 1/6-resolution layer composited into the diffuse canvas.
//
// The projection is IDENTICAL to the SVG overlay's project() in SkyDome.tsx:
// gnomonic, f = 100/tan(60°) in the 180×120 viewBox space, center (100, 100).

import type { LightDomeSummary } from '../../types'
import { ldTent, LD_DIR_AZ, LD_DIRS } from '../glow'
import {
  enuToEclMatrix, enuToGalMatrix, lstRad, sunState, moonState, type MoonState,
} from './astro'
import type { StarCatalog } from './catalog'
import { COLOR_BINS } from './catalog'
import {
  airmass, domeScores8, extinctionCoeff, grainDarknessFactor, moonPenaltyMag,
  MW_GAIN, mwLpFactor, nelmFromSqm, rgb, skyBackground, starAlpha, starRadius,
  twilightPenaltyMag, zodiacalBrightness, zodiacalGate,
  STAR_COLORS, STAR_COLORS_FAINT, FAINT_COLOR_MARGIN,
} from './model'
import { MW_B_MAX, type GrainPoints, type MwTexture } from './mwtex'

const DEG = Math.PI / 180
const LN10_04 = 0.4 * Math.LN10          // 10^(−0.4·dm) = e^(−LN10_04·dm)
/** The band/zodiacal layer renders at 1/this of full canvas resolution. */
const MW_RES_DIV = 8
/** Zodiacal cone gain: canvas luma at zodiacalBrightness = 1 and zero dimming.
 *  Tuned so the cone reads as the "false dawn" wedge at a dark site — clearly
 *  present near the horizon, fading out by ~60° elongation. */
const ZL_GAIN = 150
/** Peak grain-point alpha (texture luma 1, dark site, zenith). */
const GRAIN_ALPHA = 0.45
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
  private mwCanvas: HTMLCanvasElement
  private mwCtx: CanvasRenderingContext2D
  private mwImage: ImageData | null = null
  private noiseCanvas: HTMLCanvasElement
  private noisePattern: CanvasPattern | null = null

  private W = 0
  private H = 0
  private dpr = 1

  private statics: SkyStatics | null = null
  private catalog: StarCatalog | null = null
  private mwTex: MwTexture | null = null
  private grain: GrainPoints | null = null

  // Per-tick state
  private sE!: Float32Array; private sN!: Float32Array; private sU!: Float32Array
  private sAlpha!: Float32Array
  private gE!: Float32Array; private gN!: Float32Array; private gU!: Float32Array
  private gAlpha!: Float32Array
  private grainOn = false
  private moon: MoonState | null = null
  private sunAltDeg = -90
  private sunAzDeg = 0
  private cloudFrac = 0
  private aod: number | null = null
  private magStopBound = 7
  private globalLim = 6.5
  // Cached per-tick inputs for the per-pixel band/zodiacal pass (drawMwLayer)
  private mGal: number[] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
  private mEcl: number[] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
  private glowLut = new Float32Array(512)
  private scores8: number[] = new Array(8).fill(0)
  private extK = 0.27
  private bandScale = 0
  private zodScale = 0
  private sunEclCos = 1
  private sunEclSin = 0

  // View
  private headingDeg = 180
  private tiltDeg = 0

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')!
    this.diffuse = document.createElement('canvas')
    this.dctx = this.diffuse.getContext('2d')!
    this.mwCanvas = document.createElement('canvas')
    this.mwCtx = this.mwCanvas.getContext('2d')!
    this.haloSprite = makeRadialSprite(32, 'rgba(255,255,255,0.55)')
    this.noiseCanvas = makeNoiseCanvas(128)
  }

  setSize(cssW: number, cssH: number, dpr: number) {
    this.dpr = Math.min(2, dpr)
    this.W = Math.max(1, Math.round(cssW * this.dpr))
    this.H = Math.max(1, Math.round(cssH * this.dpr))
    if (this.canvas.width !== this.W) this.canvas.width = this.W
    if (this.canvas.height !== this.H) this.canvas.height = this.H
    this.diffuse.width = Math.max(1, Math.round(this.W / 3))
    this.diffuse.height = Math.max(1, Math.round(this.H / 3))
    this.mwCanvas.width = Math.max(1, Math.round(this.W / MW_RES_DIV))
    this.mwCanvas.height = Math.max(1, Math.round(this.H / MW_RES_DIV))
    this.mwImage = null   // re-allocated lazily at the new size
  }

  setStatics(s: SkyStatics) { this.statics = s }

  setMwTexture(tex: MwTexture, grain: GrainPoints) {
    this.mwTex = tex
    this.grain = grain
    this.gE = new Float32Array(grain.n)
    this.gN = new Float32Array(grain.n)
    this.gU = new Float32Array(grain.n)
    this.gAlpha = new Float32Array(grain.n)
    this.grainOn = false
  }

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
    this.scores8 = scores8
    this.extK = k
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

    // Band + zodiacal layer inputs: frame matrices and global brightness scales.
    // Per-direction dimming (light dome, extinction) happens per pixel in
    // drawMwLayer; everything global — moon, twilight, LP washout, cloud —
    // folds into one scale here (same 10^(−0.4·Δm) mapping as washoutFactor).
    const mwCloud = Math.pow(Math.max(0, 1 - cloudFrac), 1.5)
    this.mGal = enuToGalMatrix(s.lat, lst)
    this.mEcl = enuToEclMatrix(s.lat, lst, utcMs)
    this.bandScale = (MW_GAIN / 255) * mwLpFactor(s.sqm)
      * Math.exp(-LN10_04 * (moonPen + twPen)) * mwCloud
    this.zodScale = ZL_GAIN * zodiacalGate(s.sqm, sun.alt, moonPen) * mwCloud
    const sunLamRad = sun.eclipticLonDeg * DEG
    this.sunEclCos = Math.cos(sunLamRad)
    this.sunEclSin = Math.sin(sunLamRad)

    // Grain points: unresolved starlight along the band. Same per-point math as
    // catalog stars, but alpha-scaled by texture luma and gated on darkness.
    const grain = this.grain
    const gFac = grainDarknessFactor(globalLim) * starCloudDim
    this.grainOn = !!grain && gFac > 0.02
    if (grain && this.grainOn) {
      for (let i = 0; i < grain.n; i++) {
        const ha = lst - grain.raRad[i]
        const cosHa = Math.cos(ha)
        const U = grain.sinDec[i] * sinLat + grain.cosDec[i] * cosLat * cosHa
        if (U <= 0) { this.gAlpha[i] = 0; continue }
        const E = -grain.cosDec[i] * Math.sin(ha)
        const N = grain.sinDec[i] * cosLat - grain.cosDec[i] * sinLat * cosHa
        this.gE[i] = E; this.gN[i] = N; this.gU[i] = U
        const altDeg = Math.asin(Math.min(1, U)) / DEG
        const azDeg = (Math.atan2(E, N) / DEG + 360) % 360
        const glow = ldTent(scores8, azDeg) / (1 + (altDeg / 40) ** 2)
        const dm = 0.8686 * glow + k * (airmass(U) - 1)
        this.gAlpha[i] = GRAIN_ALPHA * grain.w[i] * Math.exp(-LN10_04 * dm) * gFac
      }
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
    // Full-res noise at ~2% alpha: dithers away Mach banding in the smooth
    // upscaled gradients and reads as faint sensor grain, not a pattern.
    if (!this.noisePattern) this.noisePattern = ctx.createPattern(this.noiseCanvas, 'repeat')
    if (this.noisePattern) {
      ctx.globalAlpha = 0.02
      ctx.fillStyle = this.noisePattern
      ctx.fillRect(0, 0, this.W, this.H)
      ctx.globalAlpha = 1
    }
    this.drawGrain(cam)
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

    // 3. Milky Way band + zodiacal light: per-pixel sky-texture layer rendered
    // at 1/MW_RES_DIV resolution, composited additively with a slight blur so
    // the upscale stays soft (the grain pass supplies high-frequency detail).
    if ((this.mwTex && this.bandScale > 0.001) || this.zodScale > 0.01) {
      this.drawMwLayer(cam)
      // The 1/8→1/3 smoothed upscale is blur enough; ctx.filter would cost
      // several ms of software convolution per frame.
      d.globalCompositeOperation = 'lighter'
      d.drawImage(this.mwCanvas, 0, 0, w, h)
      d.globalCompositeOperation = 'source-over'
    }

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

  // ── Milky Way band + zodiacal light: per-pixel ray pass at 1/6 resolution ────
  // For every pixel of the small layer: unproject to an ENU unit ray, rotate it
  // into galactic coordinates for a bilinear band-texture sample and into
  // ecliptic coordinates for the zodiacal cone, then apply the per-direction
  // dimming (light-dome tent + airmass extinction — global terms were already
  // folded into bandScale/zodScale at tick time).
  private drawMwLayer(cam: ReturnType<SkyRenderer['camera']>) {
    const mc = this.mwCanvas
    const w = mc.width, h = mc.height
    if (!this.mwImage) this.mwImage = this.mwCtx.createImageData(w, h)
    const px = this.mwImage.data
    px.fill(0)

    const tex = this.mwTex
    const bandOn = !!tex && this.bandScale > 0.001
    const zodOn = this.zodScale > 0.01
    const scale = w / this.W
    const cx = cam.cx * scale, cy = cam.cy * scale, F = cam.F * scale
    const m = this.mGal, me = this.mEcl
    const k = this.extK
    const bandScale = this.bandScale, zodScale = this.zodScale
    const sunC = this.sunEclCos, sunS = this.sunEclSin

    // Light-dome glow by azimuth, tabulated once per frame (the per-pixel tent
    // interpolation + degree conversion was a measurable chunk of the loop).
    const GLOW_N = 512
    const glowLut = this.glowLut
    for (let i = 0; i < GLOW_N; i++) glowLut[i] = ldTent(this.scores8, (i * 360) / GLOW_N)
    const AZ_TO_LUT = GLOW_N / (2 * Math.PI)
    const SIN_BMAX = Math.sin(MW_B_MAX * DEG)
    const COS_ELONG_MIN = -0.26     // ε ≳ 105°: zodiacal contribution < 1 luma

    for (let py = 0; py < h; py++) {
      const dy = (cy - (py + 0.5)) / F
      const bxr = cam.fx + dy * cam.ux
      const byr = cam.fy + dy * cam.uy
      const bzr = cam.fz + dy * cam.uz
      if (bzr < 0) continue             // U is constant per row: row below horizon
      for (let ix = 0; ix < w; ix++) {
        const dx = (ix + 0.5 - cx) / F
        let E = bxr + dx * cam.rx
        let N = byr + dx * cam.ry
        let U = bzr                       // cam.rz === 0
        const inv = 1 / Math.sqrt(E * E + N * N + U * U)
        E *= inv; N *= inv; U *= inv

        // altDeg ≈ asin(U) in degrees (5th-order series; < 0.5° error at 60°,
        // only shapes the glow-vs-altitude falloff so that's plenty).
        const altDeg = U * (1 + U * U * (0.1667 + 0.075 * U * U)) * 57.29578
        let az = Math.atan2(E, N) * AZ_TO_LUT
        if (az < 0) az += GLOW_N
        const glow = glowLut[az | 0] / (1 + (altDeg / 40) ** 2)
        const att = Math.exp(-LN10_04 * (0.8686 * glow + k * (airmass(U) - 1)))
        if (att < 0.02) continue

        let r = 0, g = 0, b = 0
        if (bandOn && tex) {
          const gz = m[6] * E + m[7] * N + m[8] * U
          if (gz > -SIN_BMAX && gz < SIN_BMAX) {
            const bDeg = Math.asin(gz) / DEG
            const gx = m[0] * E + m[1] * N + m[2] * U
            const gy = m[3] * E + m[4] * N + m[5] * U
            const lDeg = (Math.atan2(gy, gx) / DEG + 360) % 360
            const { w: tw, h: th, data: td } = tex
            // Bilinear sample: wrap in longitude, clamp in latitude.
            const xf = (lDeg / 360) * tw - 0.5
            let x0 = Math.floor(xf)
            const fx = xf - x0
            x0 = ((x0 % tw) + tw) % tw
            const x1 = x0 + 1 === tw ? 0 : x0 + 1
            const yf = Math.min(th - 1, Math.max(0, ((MW_B_MAX - bDeg) / (2 * MW_B_MAX)) * th - 0.5))
            const y0 = Math.floor(yf)
            const fy = yf - y0
            const y1 = Math.min(th - 1, y0 + 1)
            const i00 = (y0 * tw + x0) << 2, i10 = (y0 * tw + x1) << 2
            const i01 = (y1 * tw + x0) << 2, i11 = (y1 * tw + x1) << 2
            const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy)
            const w01 = (1 - fx) * fy, w11 = fx * fy
            const s = bandScale * att
            r += (td[i00] * w00 + td[i10] * w10 + td[i01] * w01 + td[i11] * w11) * s
            g += (td[i00 + 1] * w00 + td[i10 + 1] * w10 + td[i01 + 1] * w01 + td[i11 + 1] * w11) * s
            b += (td[i00 + 2] * w00 + td[i10 + 2] * w10 + td[i01 + 2] * w01 + td[i11 + 2] * w11) * s
          }
        }
        if (zodOn) {
          const ex = me[0] * E + me[1] * N + me[2] * U
          const ey = me[3] * E + me[4] * N + me[5] * U
          const cosElong = ex * sunC + ey * sunS
          if (cosElong > COS_ELONG_MIN) {
            const ez = me[6] * E + me[7] * N + me[8] * U
            const betaDeg = Math.asin(Math.max(-1, Math.min(1, ez))) / DEG
            const elongDeg = Math.acos(Math.min(1, cosElong)) / DEG
            const zb = zodiacalBrightness(elongDeg, betaDeg) * zodScale * att
            if (zb > 0.4) { r += zb; g += zb * 0.97; b += zb * 0.88 }   // warm white
          }
        }

        if (r + g + b < 1.2) continue
        // ±1-LSB ordered-ish dither so the smooth glow doesn't band at 8 bits.
        let hsh = (ix * 374761393 + py * 668265263) | 0
        hsh = Math.imul(hsh ^ (hsh >>> 13), 1274126177)
        const dith = (((hsh >>> 16) & 255) / 255 - 0.5) * 2.4
        const idx = (py * w + ix) << 2
        px[idx] = r + dith
        px[idx + 1] = g + dith
        px[idx + 2] = b + dith
        px[idx + 3] = 255
      }
    }
    this.mwCtx.putImageData(this.mwImage, 0, 0)
  }

  // ── Unresolved-starlight grain: dim 1px points at full resolution ────────────
  private drawGrain(cam: ReturnType<SkyRenderer['camera']>) {
    const grain = this.grain
    if (!grain || !this.grainOn) return
    const ctx = this.ctx
    const cssW = this.W / this.dpr
    const rScale = this.dpr * Math.min(1.7, Math.max(0.75, Math.sqrt(cssW / 420)))
    const s = Math.max(1, Math.round(0.8 * rScale))
    const half = s / 2
    const wLim = this.W + 2, hLim = this.H + 2
    ctx.fillStyle = 'rgb(206,211,224)'
    for (let i = 0; i < grain.n; i++) {
      const a = this.gAlpha[i]
      if (a <= 0.01) continue
      const E = this.gE[i], N = this.gN[i], U = this.gU[i]
      const dz = E * cam.fx + N * cam.fy + U * cam.fz
      if (dz <= 0.001) continue
      const x = cam.cx + cam.F * ((E * cam.rx + N * cam.ry) / dz)
      if (x < -2 || x > wLim) continue
      const y = cam.cy - cam.F * ((E * cam.ux + N * cam.uy + U * cam.uz) / dz)
      if (y < -2 || y > hLim) continue
      ctx.globalAlpha = a
      ctx.fillRect(x - half, y - half, s, s)
    }
    ctx.globalAlpha = 1
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

/** Tiling grey-noise canvas for the anti-banding overlay (deterministic hash). */
function makeNoiseCanvas(size: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = c.height = size
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(size, size)
  const px = img.data
  for (let i = 0; i < size * size; i++) {
    let h = (i * 2654435761) | 0
    h = Math.imul(h ^ (h >>> 15), 2246822519)
    h = Math.imul(h ^ (h >>> 13), 3266489917)
    const v = ((h ^ (h >>> 16)) >>> 24) & 255
    const idx = i << 2
    px[idx] = px[idx + 1] = px[idx + 2] = v
    px[idx + 3] = 255
  }
  ctx.putImageData(img, 0, 0)
  return c
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
