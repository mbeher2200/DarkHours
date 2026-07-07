import React from 'react'
import type { Direction, LightDomeSummary } from '../types'

// ── Light dome direction/glow utilities ──────────────────────────────────────
// Defined before MilkyWayDome so archSegmentBrightness (below) can call glowToward.

export const LD_DIRS: Direction[] = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
export const LD_DIR_AZ: Record<Direction, number> = { N:0, NE:45, E:90, SE:135, S:180, SW:225, W:270, NW:315 }
// Thresholds mirror light_dome.py (minor_threshold / major_threshold) so the colour
// scale is comparable across sites: green→amber at MINOR, →red by MAJOR.
export const LD_MINOR = 0.25
export const LD_MAJOR = 3.0
// Log-interpolated colour stops (RGB), keyed on the thresholds, using the app's own
// quality ramp (excellent → good → fair → poor). The darkest end fades to BLACK — the
// "excellent of excellent" = true darkness. Glow rises: black → green → blue → lilac → rose.
export const LD_STOPS: [number, [number, number, number]][] = [
  [0,        [0, 0, 0]],        // darkness — the best (excellent-of-excellent)
  [0.03,     [74, 94, 168]],    // --excellent blue
  [0.12,     [92, 184, 92]],    // --good green
  [LD_MINOR, [240, 173, 78]],   // --fair amber — a minor dome
  [0.9,      [217, 83, 79]],    // --poor red
  [LD_MAJOR, [150, 40, 40]],    // deep --poor — a major dome
]
// Bloom-legibility transform: real dome heights are ~1° (a sub-pixel rim sliver), so
// for *display* we scale them up and floor them. This shapes the on-screen bloom, not
// the physics. Directions with no flagged dome get a small default height.
export const LD_THETA_K = 5
export const LD_THETA_FLOOR_DEG = 6
export const LD_THETA_DEFAULT_DEG = 4
export const LD_SIZE = 300            // CSS px; disk + room for N/E/S/W labels

export function ldColor(v: number): [number, number, number] {
  if (v <= LD_STOPS[0][0]) return LD_STOPS[0][1]
  for (let i = 1; i < LD_STOPS.length; i++) {
    const [hv, hc] = LD_STOPS[i]
    if (v <= hv) {
      const [lv, lc] = LD_STOPS[i - 1]
      const t = (Math.log(Math.max(v, 1e-4)) - Math.log(Math.max(lv, 1e-4))) /
                (Math.log(hv) - Math.log(Math.max(lv, 1e-4)))
      const k = Math.max(0, Math.min(1, t))
      return [lc[0] + (hc[0] - lc[0]) * k, lc[1] + (hc[1] - lc[1]) * k, lc[2] + (hc[2] - lc[2]) * k]
    }
  }
  return LD_STOPS[LD_STOPS.length - 1][1]
}

// Tent-interpolate a per-direction array at an arbitrary azimuth (partition of unity
// across the two nearest cardinals — same scheme as light_dome.glow_toward).
export function ldTent(arr: number[], azDeg: number): number {
  const p = (((azDeg % 360) + 360) % 360) / 45
  const lo = Math.floor(p) % 8
  const hi = (lo + 1) % 8
  const f = p - Math.floor(p)
  return arr[lo] * (1 - f) + arr[hi] * f
}

// Mirrors light_dome.glow_toward(): score(az) / (1 + (alt/θ(az))²)
export function glowToward(summary: LightDomeSummary, azDeg: number, altDeg: number): number {
  const scores8  = LD_DIRS.map(d => summary.scores[d] ?? 0)
  const heights8 = LD_DIRS.map(d => summary.dome_heights[d] ?? 0)
  const score    = ldTent(scores8, azDeg)
  const theta    = ldTent(heights8, azDeg)
  const alt      = Math.max(0, altDeg)
  if (theta <= 0) return alt === 0 ? score : 0
  return score / (1 + (alt / theta) ** 2)
}

export function glowLabel(g: number): string {
  if (g < 0.03)     return 'negligible'
  if (g < LD_MINOR) return 'minor'
  if (g < LD_MAJOR) return 'moderate'
  return 'major'
}

// CSS colour for a glow value, reusing the LD stop palette.
// Negligible glow returns {} so the element inherits var(--text-dim) from CSS;
// the LD ramp starts at black (the zero-glow "excellent" end) which is invisible
// on dark backgrounds, so we only apply inline colour once the glow is meaningful.
export function glowStyle(g: number): React.CSSProperties {
  if (g < 0.03) return {}
  const [r, gr, b] = ldColor(g)
  return { color: `rgb(${Math.round(r)},${Math.round(gr)},${Math.round(b)})` }
}

// Sky background brightness for MW arch visibility — distinct from glowToward().
// dome_heights are geometric angles (1-3° for a city 30+ mi away) but city glow
// scatters through the atmosphere and degrades sky brightness well above that.
// A 40° characteristic altitude matches observer perception: score at the horizon
// falls to ~50% at 40°, ~20% at 80° (zenith). Uses direction scores only, no heights.
export function archGlowAt(summary: LightDomeSummary, azDeg: number, altDeg: number): number {
  const scores8 = LD_DIRS.map(d => summary.scores[d] ?? 0)
  const score   = ldTent(scores8, azDeg)
  const alt     = Math.max(0, altDeg)
  return score / (1 + (alt / 40) ** 2)
}

// ── Milky Way brightness model ────────────────────────────────────────────────
// Two-component empirical model: compact Gaussian bulge + linear disk + floor.
// sigma=0.28 chosen to match known bright regions (core→scutum→cygnus→anticenter).
export function intrinsicBrightness(l_deg: number): number {
  const norm  = ((l_deg % 360) + 360) % 360
  const delta = Math.min(norm, 360 - norm)    // 0° at core, 180° at anticenter
  const x     = delta / 180                    // normalised [0, 1]
  const bulge = 0.70 * Math.exp(-x * x / (2 * 0.28 * 0.28))
  const disk  = 0.18 * (1 - x * 0.7)
  return Math.max(0, Math.min(1, 0.12 + bulge + disk))
}

// Exponential attenuation from a light-dome glow index → brightness multiplier [0,1].
// glow=0→1.0, glow=0.25(LD_MINOR)→0.82, glow=1.0→0.45, glow=3.0(LD_MAJOR)→0.09
export function washoutFactor(glow: number): number {
  return Math.exp(-0.8 * glow)
}

// Per-segment arch brightness: intrinsic galactic profile × light-dome washout.
// Returns {glowOpacity, coreOpacity} for the two rendering layers.
export function archSegmentBrightness(
  l: number, alt: number, az: number,
  lightDome: LightDomeSummary | null
): { glowOpacity: number; coreOpacity: number } {
  const I    = intrinsicBrightness(l)
  const glow = lightDome ? archGlowAt(lightDome, az, alt) : 0
  const W    = washoutFactor(glow)
  const B    = I * W
  return { glowOpacity: B * 0.35, coreOpacity: B * 0.85 }
}

// Orthographic projection of the sky dome, camera centered on the galactic core azimuth.
