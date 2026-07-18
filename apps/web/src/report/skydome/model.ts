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
import { LD_DIRS, archGlowAt } from '../glow'

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
// Reference for the LP shades: stacked exposure from a Bortle ~7 suburb —
// zenith stays near-black blue-grey (~13,18,26), horizon a muted steel.
const SKY_LP_ZENITH = [15, 19, 28]      // heavily light-polluted zenith
const SKY_LP_HORIZON = [36, 43, 55]
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

/**
 * Horizon light-dome glow tint by site darkness. An isolated small-town dome
 * seen from a dark site reads warm amber, but broad suburban/urban skyglow is
 * a cool steel blue-grey (reference: stacked long exposure from a Bortle ~6–7
 * suburb — shades run blue-grey to black, no amber). Cools from warm below
 * ~SQM 21.3 to fully steel by ~18.8.
 */
export function domeGlowColor(sqm: number): {
  inner: [number, number, number]
  outer: [number, number, number]
} {
  const cool = Math.min(1, Math.max(0, (21.3 - sqm) / 2.5))
  return {
    inner: mix3([255, 190, 110], [112, 128, 156], cool),
    outer: mix3([255, 170, 90], [92, 108, 134], cool),
  }
}

// ── Milky Way band ────────────────────────────────────────────────────────────
// The band itself is a real-sky texture (see mwtex.ts) rendered per-pixel in
// render.ts; the models here are its brightness scalers.

/** Texture luma 1.0 → this much canvas luma (0..255 units) at zero dimming. */
export const MW_GAIN = 0.65 * 255

/**
 * Light-pollution washout of the band as a whole: the MW's ~21.5–22 mag/arcsec²
 * surface brightness loses contrast against a bright sky background everywhere,
 * not just inside horizon domes. 1 below SQM≈21, gone by SQM≈18.8.
 */
export function mwLpFactor(sqm: number): number {
  return Math.min(1, Math.max(0, (sqm - 18.8) / 2.2))
}

/**
 * Unresolved-starlight grain fades before the band does: 0 when the effective
 * limiting magnitude drops to 5 (moonlight/LP/twilight), full by ~6.2.
 */
export function grainDarknessFactor(globalLim: number): number {
  return Math.min(1, Math.max(0, (globalLim - 5) / 1.2))
}

// ── Zodiacal light ────────────────────────────────────────────────────────────
// Tuned-for-visualization cone along the ecliptic: falls off exponentially with
// elongation from the sun, Gaussian in ecliptic latitude with a width that
// grows with elongation (narrow bright cone near the horizon, broad faint band
// further out).

/** Relative surface brightness 0..~0.5 from elongation/latitude in degrees. */
export function zodiacalBrightness(elongDeg: number, betaDeg: number): number {
  const e = Math.max(20, elongDeg)
  const bw = 8 + 0.15 * e
  return Math.pow(10, -e / 60) * Math.exp(-(betaDeg * betaDeg) / (bw * bw))
}

/**
 * Visibility gate 0..1: needs a dark site (fades out toward urban SQM), full
 * astronomical darkness (fades in as the sun sinks from −12° to −16°), and no
 * significant moonlight.
 */
export function zodiacalGate(sqm: number, sunAltDeg: number, moonPen: number): number {
  const dark = Math.min(1, Math.max(0, (sqm - 20.2) / 1.3))
  const tw = Math.min(1, Math.max(0, (-sunAltDeg - 12) / 4))
  const moon = Math.max(0, 1 - moonPen / 0.8)
  return dark * tw * moon
}
