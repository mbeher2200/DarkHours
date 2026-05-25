#!/usr/bin/env python3
"""
Krisciunas & Schaefer (1991) moonlight model and sky-brightness constants.

Public API
----------
ks_delta_mag(illumination_pct, sep_deg, moon_alt_deg, sky_sqm) -> float
    Sky surface brightness increase Δ mag/arcsec² from scattered moonlight.

ks_moon_credit(illumination_pct) -> float
    0–1 credit representing how usable moon-up time is; 0 = moon washes sky.

moon_wash_severity(illumination_pct, sep_deg, moon_alt_deg) -> str | None
    Classify moon interference as None, 'minor', 'moderate', or 'severe'.

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
# Krisciunas & Schaefer (1991) model constants
# ---------------------------------------------------------------------------

_KS_K_EXT       = 0.172   # typical V-band atmospheric extinction coefficient
KS_NATURAL_SKY  = 21.6    # Bortle 2 dark-sky baseline (mag/arcsec²); conservative

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


def ks_delta_mag(
    illumination_pct: float,
    sep_deg: float,
    moon_alt_deg: float,
    sky_sqm: float = KS_NATURAL_SKY,
) -> float:
    """
    Return sky surface brightness increase Δ mag/arcsec² from scattered moonlight
    using the Krisciunas & Schaefer (1991) model (PASP 103, 1033).

    Returns 0.0 when illumination is zero or the moon is below the horizon.
    sky_sqm is used for the natural-sky baseline I_sky denominator.
    """
    if illumination_pct <= 0 or moon_alt_deg <= 0:
        return 0.0

    illum  = illumination_pct / 100.0
    alpha  = math.degrees(math.acos(max(-1.0, min(1.0, 2.0 * illum - 1.0))))
    V_moon = -12.73 + 0.026 * alpha + 4e-9 * alpha**4
    I_moon = 10 ** (-0.4 * (V_moon + 16.57))

    alt    = max(1.0, moon_alt_deg)
    X_moon = 1.0 / math.cos(math.radians(90.0 - alt))
    ext    = 10 ** (-0.4 * _KS_K_EXT * X_moon)

    rho     = max(0.1, sep_deg)
    rho_rad = math.radians(rho)
    if rho > 10.0:
        f_rho = 10 ** 5.36 * (1.06 + math.cos(rho_rad) ** 2)
    else:
        f_rho = 6.2e7 / rho ** 2

    I_scatter = f_rho * ext * I_moon
    I_sky     = 10 ** ((27.78 - sky_sqm) / 2.5)
    return 2.5 * math.log10(1.0 + I_scatter / I_sky)


# Fixed geometry used for site-wide K&S credit evaluation (not per-target).
# 90° separation = darkest accessible sky (cos²ρ minimum in the scattering function).
# 30° altitude   = representative mid-sky moon position.
_KS_CREDIT_SEP_DEG = 90.0
_KS_CREDIT_ALT_DEG = 30.0

# Illumination below which the moon's sky brightening is imperceptible-to-minor at
# 90° separation regardless of altitude.  Used as the crescent-exemption threshold
# for the "Prime Dark Sky Hours" display in predictor.py.
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
    """
    delta = ks_delta_mag(illumination_pct, _KS_CREDIT_SEP_DEG, _KS_CREDIT_ALT_DEG)
    return max(0.0, 1.0 - delta / _KS_SEVERE_THRESH)


def moon_wash_severity(
    illumination_pct: float,
    sep_deg: float | None,
    moon_alt_deg: float | None = None,
) -> str | None:
    """
    Classify moon interference as None, 'minor', 'moderate', or 'severe'.

    Uses ks_delta_mag internally; sep_deg and moon_alt_deg default to 45°
    when not provided (conservative mid-sky estimate).

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
    )
    if delta_mag < _KS_MINOR_THRESH:
        return None
    if delta_mag < KS_MODERATE_THRESH:
        return "minor"
    if delta_mag < _KS_SEVERE_THRESH:
        return "moderate"
    return "severe"
