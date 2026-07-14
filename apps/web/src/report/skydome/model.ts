// Sky-condition models for the dome renderer. Everything here is a *labeled
// estimate* tuned for visualization, not photometry:
//
//   NELM        — naked-eye limiting magnitude from SQM via the standard
//                 Schaefer-derived conversion NELM = 7.93 − 5·log10(1 + 10^(4.316 − SQM/5)).
//                 SQM 22.0 → 6.6, 21.0 → 6.0, 18.0 → 4.0. Clamped at 6.7 (the
//                 catalog's completeness limit — 12,000th star is mag 6.77).
//   Moon        — Δm = 2.2·(illum)^1.4·√sin(moonAlt), a K&S-flavored global
//                 sky-brightening approximation (full moon overhead ≈ 2.2 mag).
//   Light dome  — Δm = 0.8686·archGlowAt(az, alt): the exact magnitude form of
//                 glow.tsx washoutFactor(g) = e^(−0.8g) (2.5·log10(e^0.8g) = 0.8686g),
//                 so the canvas agrees with the existing badge semantics.
//   Extinction  — Rozenberg airmass X = 1/(sinAlt + 0.025·e^(−11·sinAlt)) with
//                 k = 0.16 + 1.09·AOD mag/airmass (AOD from hourly weather).
//   Twilight    — t = clamp((sunAlt + 18)/12, 0..1): 0 in astronomical darkness,
//                 1 at civil twilight; subtracts up to ~4.5 mag and lifts the
//                 sky background toward deep blue.

import type { LightDomeSummary } from '../../types'
import { LD_DIRS, archGlowAt, intrinsicBrightness } from '../glow'
import { galToRaDec } from './astro'

export const CATALOG_LIMIT_MAG = 6.7

// SQM by Bortle class 1–9, used when the raster lookup returned no SQM value.
const BORTLE_SQM = [22.0, 21.7, 21.4, 20.8, 20.1, 19.3, 18.6, 18.0, 17.5]

export function sqmFromBortle(bortle: number): number {
  return BORTLE_SQM[Math.min(8, Math.max(0, Math.round(bortle) - 1))]
}

export function nelmFromSqm(sqm: number): number {
  const nelm = 7.93 - 5 * Math.log10(1 + Math.pow(10, 4.316 - sqm / 5))
  return Math.min(CATALOG_LIMIT_MAG, nelm)
}

/** Global magnitude loss from moonlight (0 when the moon is below the horizon). */
export function moonPenaltyMag(illumPct: number, moonAltDeg: number): number {
  if (moonAltDeg <= 0 || illumPct <= 0) return 0
  const sinAlt = Math.sin(moonAltDeg * Math.PI / 180)
  return 2.2 * Math.pow(illumPct / 100, 1.4) * Math.sqrt(sinAlt)
}

/** 0 = astronomical darkness, 1 = bright twilight (sun at −6° or higher). */
export function twilightFactor(sunAltDeg: number): number {
  return Math.min(1, Math.max(0, (sunAltDeg + 18) / 12))
}

export function twilightPenaltyMag(sunAltDeg: number): number {
  return 4.5 * Math.pow(twilightFactor(sunAltDeg), 1.5)
}

/** Rozenberg airmass from sin(altitude); valid to the horizon. */
export function airmass(sinAlt: number): number {
  const s = Math.max(0, sinAlt)
  return 1 / (s + 0.025 * Math.exp(-11 * s))
}

/** Total extinction coefficient (mag/airmass): Rayleigh+ozone base plus aerosols. */
export function extinctionCoeff(aod: number | null): number {
  return 0.16 + 1.09 * (aod ?? 0.10)
}

/** Directional magnitude loss from horizon light domes (glow.tsx-consistent). */
export function lightDomePenaltyMag(
  summary: LightDomeSummary | null, azDeg: number, altDeg: number,
): number {
  if (!summary) return 0
  return 0.8686 * archGlowAt(summary, azDeg, altDeg)
}

/** Tent-interpolated 8-direction scores as a plain array (renderer hot loop). */
export function domeScores8(summary: LightDomeSummary | null): number[] {
  if (!summary) return new Array(8).fill(0)
  return LD_DIRS.map(d => summary.scores[d] ?? 0)
}

// ── Star appearance ───────────────────────────────────────────────────────────

/** Draw radius (CSS px) by magnitude tier. */
export function starRadius(mag: number): number {
  if (mag < 0) return 2.5
  if (mag < 1.5) return 2.0
  if (mag < 3.5) return 1.4
  return 1.0
}

/**
 * Alpha from the star's margin below the local limiting magnitude.
 * Full brightness ~3 mag above the limit, fading to 0 at the limit.
 */
export function starAlpha(marginMag: number): number {
  if (marginMag <= 0) return 0
  return Math.pow(Math.min(1, marginMag / 3), 0.8)
}

// B−V color table, 16 bins spanning B−V ∈ [−0.4, 2.0) (bin width 0.15) —
// approximate blackbody hues from hot blue-white through solar white to deep orange.
export const STAR_COLORS: [number, number, number][] = [
  [155, 176, 255], [166, 185, 255], [178, 195, 255], [192, 207, 255],
  [206, 218, 255], [221, 230, 255], [237, 242, 255], [251, 251, 255],
  [255, 249, 245], [255, 243, 231], [255, 234, 214], [255, 224, 196],
  [255, 213, 179], [255, 203, 164], [255, 193, 151], [255, 183, 138],
]

/** Desaturated variant for faint stars (scotopic vision sees little color). */
export const STAR_COLORS_FAINT: [number, number, number][] = STAR_COLORS.map(
  ([r, g, b]) => {
    const grey = 0.3 * r + 0.55 * g + 0.15 * b
    const mix = (c: number) => Math.round(c * 0.35 + grey * 0.65)
    return [mix(r), mix(g), mix(b)]
  },
)

/** Stars fainter than (localLimit − FAINT_COLOR_MARGIN) draw desaturated. */
export const FAINT_COLOR_MARGIN = 2.0

// ── Sky background ────────────────────────────────────────────────────────────

const lerp = (a: number, b: number, t: number) => a + (b - a) * t
const mix3 = (a: number[], b: number[], t: number) =>
  [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)] as [number, number, number]

const SKY_DARK_ZENITH = [4, 7, 16]      // pristine dark zenith
const SKY_DARK_HORIZON = [10, 14, 26]   // natural airglow near the horizon
const SKY_LP_ZENITH = [24, 30, 48]      // heavily light-polluted zenith
const SKY_LP_HORIZON = [46, 52, 72]
const SKY_TWILIGHT = [38, 62, 110]      // deep twilight blue
const SKY_CLOUD = [34, 38, 48]          // overcast grey

export interface SkyBackground {
  zenith: [number, number, number]
  horizon: [number, number, number]
}

/**
 * Sky background colors from light pollution (SQM), twilight, and cloud cover.
 * lpFrac 0 = SQM 22 (pristine), 1 = SQM 17.5 (urban).
 */
export function skyBackground(sqm: number, sunAltDeg: number, cloudFrac: number): SkyBackground {
  const lpFrac = Math.min(1, Math.max(0, (22.0 - sqm) / 4.5))
  const tw = twilightFactor(sunAltDeg)
  let zenith = mix3(SKY_DARK_ZENITH, SKY_LP_ZENITH, lpFrac)
  let horizon = mix3(SKY_DARK_HORIZON, SKY_LP_HORIZON, lpFrac)
  zenith = mix3(zenith, SKY_TWILIGHT, tw * 0.8)
  horizon = mix3(horizon, SKY_TWILIGHT, tw)
  // Clouds grey the sky toward overcast (they reflect any ground light).
  const cloudMix = cloudFrac * (0.35 + 0.45 * lpFrac)
  zenith = mix3(zenith, SKY_CLOUD, cloudMix)
  horizon = mix3(horizon, SKY_CLOUD, cloudMix)
  return { zenith, horizon }
}

export const rgb = (c: [number, number, number], a = 1) =>
  `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a})`

// ── Milky Way band samples ────────────────────────────────────────────────────
// Deterministic point cloud along the galactic plane: l every 2°, b ∈ {0,±2,±4,±7},
// weight = intrinsicBrightness(l) × Gaussian(b) × Great Rift attenuation, with a
// hash-jitter so the band doesn't read as a grid. ~1,260 samples, precomputed to
// equatorial and reused across ticks exactly like catalog stars.

export interface MwSamples {
  n: number
  raRad: Float32Array
  sinDec: Float32Array
  cosDec: Float32Array
  weight: Float32Array
  /** 0 = cool silvery disk, 1 = warm creamy bulge (peaks at the galactic core). */
  warmth: Float32Array
}

/** Deterministic hash → [−1, 1). */
function jitter(i: number, salt: number): number {
  let h = (i * 374761393 + salt * 668265263) | 0
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return (((h ^ (h >>> 16)) >>> 0) / 4294967296) * 2 - 1
}

/** Great Rift: dust lane dimming the band from Aquila through the core.
 *  Strong attenuation — in wide-field panoramas the rift reads nearly black. */
function riftFactor(l: number, b: number): number {
  const inRiftL = l >= 320 || l <= 65
  return inRiftL && b >= -5 && b <= 0.5 ? 0.18 : 1.0
}

const MW_B_STEPS = [0, 2, -2, 4, -4, 7, -7]

export function buildMwSamples(): MwSamples {
  const raList: number[] = []
  const sinList: number[] = []
  const cosList: number[] = []
  const wList: number[] = []
  const warmList: number[] = []
  let i = 0
  for (let l = 0; l < 360; l += 2) {
    for (const b of MW_B_STEPS) {
      i++
      const lj = l + jitter(i, 17)
      const bj = b + jitter(i, 31)
      const w = intrinsicBrightness(lj)
        * Math.exp(-(bj * bj) / (4.2 * 4.2))
        * riftFactor(((lj % 360) + 360) % 360, bj)
      if (w < 0.02) continue
      const { raDeg, decDeg } = galToRaDec(lj, bj)
      raList.push(raDeg * Math.PI / 180)
      const dec = decDeg * Math.PI / 180
      sinList.push(Math.sin(dec))
      cosList.push(Math.cos(dec))
      wList.push(w)
      // Warm creamy tint near the bulge, cool silver along the outer disk.
      const norm = ((lj % 360) + 360) % 360
      const dl = Math.min(norm, 360 - norm)
      warmList.push(Math.exp(-(dl * dl) / (55 * 55)))
    }
  }
  return {
    n: wList.length,
    raRad: Float32Array.from(raList),
    sinDec: Float32Array.from(sinList),
    cosDec: Float32Array.from(cosList),
    weight: Float32Array.from(wList),
    warmth: Float32Array.from(warmList),
  }
}
