// Astronomy math for the sky-dome renderer.
// GMST + equatorial→horizontal are the same Meeus Ch.12 formulas used by
// galToAltAz/moonAltAz in MilkyWay.tsx; sun and moon positions are Meeus
// low-precision series — plenty for a visualization (sun ~0.01°, moon ~0.3°).

const toRad = (d: number) => d * Math.PI / 180
const toDeg = (r: number) => r * 180 / Math.PI
const mod360 = (x: number) => ((x % 360) + 360) % 360

/** Greenwich Mean Sidereal Time in degrees (Meeus Ch.12). */
export function gmstDeg(utcMs: number): number {
  const jd = utcMs / 86_400_000 + 2_440_587.5
  const D  = jd - 2_451_545.0
  const T  = D / 36_525.0
  return mod360(280.46061837 + 360.98564736629 * D + 0.000387933 * T * T - T * T * T / 38_710_000)
}

/** Local sidereal time in radians. */
export function lstRad(utcMs: number, lonDeg: number): number {
  return toRad(gmstDeg(utcMs) + lonDeg)
}

/** Equatorial (J2000 decimal degrees) → horizontal alt/az in degrees. */
export function eqToAltAz(
  raDeg: number, decDeg: number,
  latDeg: number, lonDeg: number, utcMs: number,
): { alt: number; az: number } {
  const ha  = lstRad(utcMs, lonDeg) - toRad(raDeg)
  const dec = toRad(decDeg)
  const lat = toRad(latDeg)
  const sinAlt = Math.sin(dec) * Math.sin(lat) + Math.cos(dec) * Math.cos(lat) * Math.cos(ha)
  const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)))
  const az  = Math.atan2(
    -Math.cos(dec) * Math.sin(ha),
    Math.sin(dec) * Math.cos(lat) - Math.cos(dec) * Math.sin(lat) * Math.cos(ha),
  )
  return { alt: toDeg(alt), az: mod360(toDeg(az)) }
}

/** Obliquity of the ecliptic in radians. */
function obliquityRad(T: number): number {
  return toRad(23.4393 - 0.013004 * T)
}

/**
 * Low-precision solar position (Meeus Ch.25, ~0.01°).
 * Returns horizontal alt/az plus apparent ecliptic longitude (degrees) —
 * the longitude feeds the moon-phase elongation.
 */
export function sunState(
  latDeg: number, lonDeg: number, utcMs: number,
): { alt: number; az: number; eclipticLonDeg: number } {
  const jd = utcMs / 86_400_000 + 2_440_587.5
  const T  = (jd - 2_451_545.0) / 36_525.0
  const L0 = mod360(280.46646 + 36000.76983 * T + 0.0003032 * T * T)
  const M  = toRad(mod360(357.52911 + 35999.05029 * T - 0.0001537 * T * T))
  const C  = (1.914602 - 0.004817 * T - 0.000014 * T * T) * Math.sin(M)
           + (0.019993 - 0.000101 * T) * Math.sin(2 * M)
           + 0.000289 * Math.sin(3 * M)
  const lambda = mod360(L0 + C)               // true (≈apparent) longitude
  const lamR = toRad(lambda)
  const eps  = obliquityRad(T)
  const ra   = toDeg(Math.atan2(Math.cos(eps) * Math.sin(lamR), Math.cos(lamR)))
  const dec  = toDeg(Math.asin(Math.sin(eps) * Math.sin(lamR)))
  const { alt, az } = eqToAltAz(mod360(ra), dec, latDeg, lonDeg, utcMs)
  return { alt, az, eclipticLonDeg: lambda }
}

export interface MoonState {
  alt: number
  az: number
  raDeg: number
  decDeg: number
  eclipticLonDeg: number
  /** Sun→moon elongation in degrees [0,360): <180 waxing, >180 waning. */
  elongationDeg: number
  waxing: boolean
}

/**
 * Simplified moon position (Meeus Ch.47 largest terms, ~0.3°) — the same
 * series as moonAltAz in MilkyWay.tsx, extended with ecliptic longitude and
 * sun elongation for the phase disc (terminator side + bright-limb angle).
 */
export function moonState(latDeg: number, lonDeg: number, utcMs: number): MoonState {
  const r  = toRad
  const JD = utcMs / 86_400_000 + 2_440_587.5
  const D  = JD - 2_451_545.0
  const T  = D / 36_525.0

  // Fundamental arguments (degrees)
  const Lp = mod360(218.3164477 + 481267.88123421 * T)
  const Mp = mod360(134.9633964 + 477198.8675055  * T)
  const M  = mod360(357.5291092 + 35999.0502909   * T)
  const Dg = mod360(297.8501921 + 445267.1114034  * T)
  const F  = mod360(93.2720950  + 483202.0175233  * T)

  const sumL = (
    + 6288774 * Math.sin(r(Mp))
    + 1274027 * Math.sin(r(2 * Dg - Mp))
    +  658314 * Math.sin(r(2 * Dg))
    +  213618 * Math.sin(r(2 * Mp))
    -  185116 * Math.sin(r(M))
    -  114332 * Math.sin(r(2 * F))
    +   58793 * Math.sin(r(2 * Dg - 2 * Mp))
    +   57066 * Math.sin(r(2 * Dg - M - Mp))
    +   53322 * Math.sin(r(2 * Dg + Mp))
    +   45758 * Math.sin(r(2 * Dg - M))
  ) / 1e6

  const sumB = (
    + 5128122 * Math.sin(r(F))
    +  280602 * Math.sin(r(Mp + F))
    +  277693 * Math.sin(r(Mp - F))
    +  173237 * Math.sin(r(2 * Dg - F))
    +   55413 * Math.sin(r(2 * Dg - Mp + F))
    +   46271 * Math.sin(r(2 * Dg - Mp - F))
  ) / 1e6

  const lambdaDeg = mod360(Lp + sumL)
  const lam = r(lambdaDeg)
  const bet = r(sumB)
  const eps = obliquityRad(T)

  const ra  = Math.atan2(Math.sin(lam) * Math.cos(eps) - Math.tan(bet) * Math.sin(eps), Math.cos(lam))
  const dec = Math.asin(Math.sin(bet) * Math.cos(eps) + Math.cos(bet) * Math.sin(eps) * Math.sin(lam))
  const raDeg  = mod360(toDeg(ra))
  const decDeg = toDeg(dec)
  const { alt, az } = eqToAltAz(raDeg, decDeg, latDeg, lonDeg, utcMs)

  const sunLon = sunState(latDeg, lonDeg, utcMs).eclipticLonDeg
  const elongationDeg = mod360(lambdaDeg - sunLon)
  return {
    alt, az, raDeg, decDeg,
    eclipticLonDeg: lambdaDeg,
    elongationDeg,
    waxing: elongationDeg < 180,
  }
}

// IAU (1958) galactic → ICRS rotation matrix (mirrors milky_way.py exactly).
export const GAL_TO_ICRS = [
  [-0.0548755604, +0.4941094279, -0.8676661490],
  [-0.8734370902, -0.4448296300, -0.1980763734],
  [-0.4838350155, +0.7469822445, +0.4559837762],
] as const

/** Galactic l/b (degrees) → equatorial J2000 RA/Dec (degrees). */
export function galToRaDec(lDeg: number, bDeg: number): { raDeg: number; decDeg: number } {
  const l = toRad(lDeg), b = toRad(bDeg)
  const xg = Math.cos(b) * Math.cos(l)
  const yg = Math.cos(b) * Math.sin(l)
  const zg = Math.sin(b)
  const R  = GAL_TO_ICRS
  const xi = R[0][0] * xg + R[0][1] * yg + R[0][2] * zg
  const yi = R[1][0] * xg + R[1][1] * yg + R[1][2] * zg
  const zi = R[2][0] * xg + R[2][1] * yg + R[2][2] * zg
  return {
    raDeg: mod360(toDeg(Math.atan2(yi, xi))),
    decDeg: toDeg(Math.asin(Math.max(-1, Math.min(1, zi)))),
  }
}
