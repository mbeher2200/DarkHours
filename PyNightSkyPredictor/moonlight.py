#!/usr/bin/env python3
"""
Scattered-moonlight model and sky-brightness constants.

Hybrid of Krisciunas & Schaefer (1991, PASP 103, 1033) and Winkler (2022,
MNRAS 514, 208): K&S lunar illuminance and optical pathlength, Winkler's
single-scatter kernel (his eq. 7) with the two-component Rayleigh +
Henyey-Greenstein phase function (eqs. 10/12).  Aerosol optical depth is a
live input: smoke/haze both dims the lunar beam and amplifies the forward-
scattered aureole via the Mie term.

Public API
----------
ks_delta_mag(illumination_pct, sep_deg, moon_alt_deg, sky_sqm, aod, target_alt_deg) -> float
    Sky surface brightness increase Δ mag/arcsec² from scattered moonlight.

ks_moon_credit(illumination_pct) -> float
    0–1 credit representing how usable moon-up time is; 0 = moon washes sky.

moon_wash_severity(illumination_pct, sep_deg, moon_alt_deg) -> str | None
    Classify moon interference as None, 'minor', 'moderate', or 'severe'.

k_ext_from_aod(aod) -> float
    V-band extinction coefficient (mag/airmass) for a given aerosol optical depth.

nelm_from_sqm(sqm) -> float
    Naked-eye limiting magnitude for a given sky surface brightness.

Constants exported for use by targets.py and predictor.py:
    KS_CRESCENT_EXEMPTION_PCT — illumination threshold below which the moon
        is treated as imperceptible-to-minor regardless of altitude.
    KS_NATURAL_SKY            — Bortle 2 dark-sky SQM baseline (mag/arcsec²).
    KS_MODERATE_THRESH        — Δmag threshold for "moderate" moon interference.
    PHOTO_SB_CONTRAST, VISUAL_SB_CONTRAST, MW_PHOTO_SB_CONTRAST, etc.
        — per-target-type contrast headroom constants for usability cutoffs.
"""

import math

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

_KS_K_EXT        = 0.172      # legacy K&S V-band extinction coefficient; the reference-AOD anchor
KS_NATURAL_SKY   = 21.6      # Bortle 2 dark-sky baseline (mag/arcsec²); conservative
_KS_MEAN_DIST_KM = 384_400.0  # mean Earth-Moon distance used by K&S (1991)

# Optical-depth decomposition (V band).  k (mag/airmass) = _MAG_PER_TAU · τ_total.
_MAG_PER_TAU = 2.5 * math.log10(math.e)   # 1.0857362… — Bouguer mag-per-optical-depth
_TAU_RAY     = 0.1066   # Rayleigh scattering, sea level
_TAU_ABS     = 0.016    # ozone/trace absorption (scatters nothing)
# Reference aerosol optical depth, derived so k(_AOD_REF) == _KS_K_EXT exactly:
# aod=None therefore reproduces the legacy fixed-extinction behaviour by construction.
_AOD_REF = _KS_K_EXT / _MAG_PER_TAU - _TAU_RAY - _TAU_ABS   # ≈ 0.0358
_AOD_CAP = 3.0          # extreme smoke; single-scatter model validity guard
_HG_G    = 0.8          # Henyey-Greenstein asymmetry parameter (Winkler 2022, §4.3)

# Normalisation anchoring the Winkler kernel to the legacy K&S intensity at the
# ks_moon_credit proxy geometry (sep 90°, moon alt 30°, target alt 45°, aod=None):
# legacy I_scatter/I_moon there = 10^5.36·1.06 · 10^(−0.4·0.172·2.0) = 176891.052…,
# so ks_moon_credit (and everything derived from it: moon_score, calendar
# dark-cycle scores) is bit-identical to the pre-Winkler model at reference AOD.
# Derivation: scripts/verify_moonwash_grid.py; pinned by tests/test_moonlight.py.
_KS_NORM = 24130491.121213324

# Severity thresholds in Δ mag/arcsec² (sky brightening from dark-sky baseline)
_KS_MINOR_THRESH    = 0.10   # < 0.10 → None   : imperceptible
KS_MODERATE_THRESH  = 0.50   # 0.10–0.50 → minor
_KS_SEVERE_THRESH   = 1.50   # 0.50–1.50 → moderate  /  ≥ 1.50 → severe

# Sky contrast thresholds for per-target usability cutoffs.
# Extended objects (nebulae/galaxies): object surface brightness must be this many
# mag/arcsec² brighter than the (moon-brightened) sky background.
#
# Calibration (Bortle 1 site, SQM 22.0):
#   Faint targets (SB ≈ 17) have 5 mag of contrast on a dark night.
#   PHOTO_SB_CONTRAST = 3.2 → photo cutoff when Δμ > SQM − SB − 3.2:
#     Veil/NAN (SB 17–17.5): cut at Δμ ≈ 1.0–1.5 (moderate→severe transition)
#     Dumbbell/Ring (SB 13–13.5): cut only at Δμ > 5 — effectively never
#   VISUAL_SB_CONTRAST = 1.5 → visual window extends ~30–60 min past photo cutoff
# Extended objects (nebulae / galaxies): object SB must exceed sky background by this margin.
# Calibrated against real-world Bortle astrophotography limits (broadband, no filter):
#   Bortle 9 (SQM 17.0): SB limit ≈ 13.8  →  Dumbbell/Helix (SB 13.5) just survive
#   Bortle 8 (SQM 18.0): SB limit ≈ 14.8  →  Eagle/Trifid (SB 14.5) just survive
#   Bortle 6 (SQM 20.0): SB limit ≈ 16.8  →  Veil/Rosette (SB 17.0) just fail — need B5
#   Bortle 5 (SQM 20.5): SB limit ≈ 17.3  →  Veil/Rosette survive; NAN (17.5) needs B4
PHOTO_SB_CONTRAST  = 3.2
VISUAL_SB_CONTRAST = 1.5   # visual: 1.5 mag/arcsec² headroom (telescope needed)

# Compact objects (clusters): usable while integrated magnitude < site_sqm - Δμ - offset.
# Calibrated against Bortle-class astrophotography limits (integrated mag scale):
#   Bortle 9 (SQM 17.0): photo limit ≈ mag 4.0  →  offset = 13.0
#   Bortle 8 (SQM 18.0): photo limit ≈ mag 5.0
#   Bortle 7 (SQM 19.0): photo limit ≈ mag 6.0
#   Bortle 5 (SQM 20.5): photo limit ≈ mag 7.5
#   Bortle 1 (SQM 22.0): photo limit ≈ mag 9.0
# Visual offset is 2 mag more lenient (telescope can reach deeper in degraded skies).
COMPACT_PHOTO_OFFSET  = 13.0
COMPACT_VISUAL_OFFSET = 11.0

# Planets: point-source-like, so slightly more lenient than extended clusters.
# Apparent magnitude computed dynamically via Skyfield's planetary_magnitude().
# Calibration anchors:
#   Uranus  (+5.8): accessible from Bortle 8+ (SQM 18.0 − 12.0 = 6.0 > 5.8)
#   Neptune (+7.8): accessible from Bortle 6+ (SQM 20.0 − 12.0 = 8.0 > 7.8)
#   All bright planets (Venus/Jupiter/Mars/Saturn) pass at any Bortle class.
PLANET_PHOTO_OFFSET  = 12.0
PLANET_VISUAL_OFFSET = 10.0

# Milky Way band: wide-field photography needs less contrast than telescope DSO work.
# Calibrated against Bortle-class MW visibility:
#   Bortle 7 (SQM 19.0): Core (SB 17.0) and Cygnus (SB 18.0) just accessible
#   Bortle 6 (SQM 20.0): Cepheus (SB 18.5) accessible
#   Bortle 5 (SQM 20.5): Perseus/Norma (SB 19.0) accessible
#   Bortle 4 (SQM 21.5): Anticenter (SB 19.5) accessible
MW_PHOTO_SB_CONTRAST  = 1.5
MW_VISUAL_SB_CONTRAST = 1.0


def _pathlength(alt_deg: float) -> float:
    """
    Relative optical pathlength (airmass) toward altitude alt_deg, using the
    K&S (1991) form X(z) = (1 − 0.96·sin²z)^(−1/2) — finite at the horizon
    (X = 5.0), unlike the plain secant Winkler (2022) adopts.
    """
    z = math.radians(90.0 - max(0.0, alt_deg))
    return (1.0 - 0.96 * math.sin(z) ** 2) ** -0.5


def k_ext_from_aod(aod: "float | None") -> float:
    """V-band extinction coefficient (mag/airmass) for a given AOD; None → reference sky (0.172)."""
    tau_m = _AOD_REF if aod is None else min(max(0.0, aod), _AOD_CAP)
    return _MAG_PER_TAU * (_TAU_RAY + tau_m + _TAU_ABS)


def nelm_from_sqm(sqm: float) -> float:
    """
    Naked-eye limiting magnitude for a sky of surface brightness sqm
    (mag/arcsec²), via the standard Schaefer-derived conversion
    (Unihedron form): NELM = 7.93 − 5·log10(10^(4.316 − SQM/5) + 1).
    """
    return 7.93 - 5.0 * math.log10(10 ** (4.316 - sqm / 5.0) + 1.0)


def ks_delta_mag(
    illumination_pct: float,
    sep_deg: float,
    moon_alt_deg: float,
    sky_sqm: float = KS_NATURAL_SKY,
    moon_earth_dist_km: float = _KS_MEAN_DIST_KM,
    aod: "float | None" = None,
    target_alt_deg: float = 45.0,
) -> float:
    """
    Return sky surface brightness increase Δ mag/arcsec² from scattered moonlight.

    Hybrid model: K&S (1991) lunar illuminance + Winkler (2022, MNRAS 514, 208)
    single-scatter kernel (his eq. 7) with the two-component Rayleigh +
    Henyey-Greenstein phase function (eqs. 10/12).  Returns 0.0 when
    illumination is zero or the moon is below the horizon.  sky_sqm is used
    for the natural-sky baseline I_sky denominator.

    moon_earth_dist_km — actual Earth-Moon distance at observation time (km).
    K&S (1991) assumes the Moon at its mean distance (384,400 km); passing the
    true distance corrects the ±8.5 % variation via the inverse-square law,
    removing up to ±0.35 mag/arcsec² error on supermoon / micromoon nights.
    Defaults to the mean distance so callers without per-sample ephemeris data
    remain accurate on average.

    aod — aerosol optical depth (V band, dimensionless).  None means the
    reference clear sky (_AOD_REF), which reproduces the legacy fixed-k
    behaviour by construction.  Higher AOD both dims the lunar beam and
    amplifies the forward-scattered aureole (Mie term), so smoke/haze
    brightens the sky near the moon while dimming it far away.

    target_alt_deg — altitude of the observed sky position; longer slant
    paths at low altitude scatter more moonlight into the line of sight.
    """
    if illumination_pct <= 0 or moon_alt_deg <= 0:
        return 0.0

    illum  = illumination_pct / 100.0
    alpha  = math.degrees(math.acos(max(-1.0, min(1.0, 2.0 * illum - 1.0))))
    V_moon = -12.73 + 0.026 * alpha + 4e-9 * alpha**4
    I_moon = 10 ** (-0.4 * (V_moon + 16.57))
    I_moon *= (_KS_MEAN_DIST_KM / moon_earth_dist_km) ** 2  # inverse-square distance correction

    # Optical depths: Rayleigh + aerosol scatter; ozone only absorbs.
    tau_m = _AOD_REF if aod is None else min(max(0.0, aod), _AOD_CAP)
    tau_s = _TAU_RAY + tau_m
    tau   = tau_s + _TAU_ABS

    # Two-component phase function at scattering angle ρ (= moon-target
    # separation for single scatter).  Winkler (2022) eqs. 10/12: the Mie
    # weight is the aerosol optical depth itself, so the forward-scattering
    # aureole grows with aerosol load with no ad-hoc scaling constant.
    rho     = math.radians(max(0.1, sep_deg))
    cos_rho = math.cos(rho)
    p_ray   = 3.0 / (16.0 * math.pi) * (1.0 + cos_rho ** 2)
    p_mie   = (1.0 - _HG_G ** 2) / (
        4.0 * math.pi * (1.0 + _HG_G ** 2 - 2.0 * _HG_G * cos_rho) ** 1.5
    )
    p = (_TAU_RAY * p_ray + tau_m * p_mie) / tau_s

    # Winkler eq. 7 single-scatter kernel: the line-of-sight airmass X_t
    # carries the leading factor; the bracket integrates beam extinction
    # (X_m) against line-of-sight extinction (X_t) along the path.
    X_t = _pathlength(target_alt_deg)
    X_m = _pathlength(moon_alt_deg)
    if abs(X_m - X_t) < 1e-6:
        kernel = tau * X_t * math.exp(-tau * X_t)
    else:
        kernel = X_t * (math.exp(-tau * X_t) - math.exp(-tau * X_m)) / (X_m - X_t)

    I_scatter = _KS_NORM * p * (tau_s / tau) * kernel * I_moon
    I_sky     = 10 ** ((27.78 - sky_sqm) / 2.5)
    return 2.5 * math.log10(1.0 + I_scatter / I_sky)


# Fixed geometry used for site-wide K&S credit evaluation (not per-target).
# 90° separation = darkest accessible sky (cos²ρ minimum in the scattering function).
# 30° altitude   = representative mid-sky moon position.
_KS_CREDIT_SEP_DEG = 90.0
_KS_CREDIT_ALT_DEG = 30.0

# Illumination below which the moon's sky brightening is imperceptible-to-minor at
# 90° separation regardless of altitude.  Used as the crescent-exemption threshold
# for the "Clear Dark Sky Hours" display in predictor.py.
KS_CRESCENT_EXEMPTION_PCT = 20.0


def ks_moon_credit(illumination_pct: float) -> float:
    """
    Return a 0–1 credit for moon-up time based on actual K&S sky brightening.

    Evaluates K&S at the site-wide proxy geometry (90° separation, 30° altitude)
    and normalises so that Δmag = _KS_SEVERE_THRESH (1.5) maps to 0 credit.

    Replaces the naive (1 − illum/100) approximation used in moon_score:

      illumination   naive credit   K&S credit
        5%  crescent    0.95          0.96   — essentially unchanged
       15%  crescent    0.85          0.86   — unchanged (minor impact preserved)
       50%  quarter     0.50          0.31   — correctly penalised (Δ1.03 = severe)
       75%  gibbous     0.25          0.00   — correctly zeroed (Δ1.73 > severe)
      100%  full        0.00          0.00   — unchanged

    The key win is the 30–75% range where the naive formula is far too generous.

    Deliberately evaluated at aod=None (reference sky): this feeds nightly
    planning scores (moon_score, calendar dark cycles) which must not wobble
    with 30-minute weather refetches, and the calendar path has no weather at
    all.  Do not "fix" this by passing live AOD here.
    """
    delta = ks_delta_mag(illumination_pct, _KS_CREDIT_SEP_DEG, _KS_CREDIT_ALT_DEG)
    return max(0.0, 1.0 - delta / _KS_SEVERE_THRESH)


def moon_wash_severity(
    illumination_pct: float,
    sep_deg: float | None,
    moon_alt_deg: float | None = None,
    aod: float | None = None,
    target_alt_deg: float = 45.0,
) -> str | None:
    """
    Classify moon interference as None, 'minor', 'moderate', or 'severe'.

    Uses ks_delta_mag internally; sep_deg and moon_alt_deg default to 45°
    when not provided (conservative mid-sky estimate).  aod / target_alt_deg
    pass through to the scattering model.

    Severity thresholds (Δ mag/arcsec² relative to a Bortle-2 dark sky):
      None       < 0.10  — negligible
      'minor'   0.10–0.50 — slight brightening
      'moderate' 0.50–1.50 — noticeable; low-SB targets impacted
      'severe'   ≥ 1.50  — sky substantially brighter; deep DSO work limited
    """
    delta_mag = ks_delta_mag(
        illumination_pct,
        sep_deg      if sep_deg      is not None else 45.0,
        moon_alt_deg if moon_alt_deg is not None else 45.0,
        aod=aod,
        target_alt_deg=target_alt_deg,
    )
    if delta_mag < _KS_MINOR_THRESH:
        return None
    if delta_mag < KS_MODERATE_THRESH:
        return "minor"
    if delta_mag < _KS_SEVERE_THRESH:
        return "moderate"
    return "severe"
