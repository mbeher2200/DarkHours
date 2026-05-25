#!/usr/bin/env python3
"""Visible target computation for night sky planning."""

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from skyfield.api import Star, load, wgs84

import config as _cfg

try:
    from skyfield.magnitudelib import planetary_magnitude as _planetary_magnitude
except ImportError:
    _planetary_magnitude = None  # Skyfield < 1.39; planet brightness filtering disabled

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Galactic coordinate conversion
# ---------------------------------------------------------------------------

# IAU (1958) galactic ↔ ICRS rotation matrix, J2000 epoch.
# Each column is the ICRS unit vector of the galactic x/y/z axis:
#   x → galactic centre  (l=0°,  b=0°)
#   y → l=90°,           b=0°
#   z → galactic north pole (b=90°)
# Reference: Blaauw et al. 1960; Murray 1989.
_GAL_TO_ICRS = [
    [-0.0548755604, +0.4941094279, -0.8676661490],
    [-0.8734370902, -0.4448296300, -0.1980763734],
    [-0.4838350155, +0.7469822445, +0.4559837762],
]


def gal_to_radec(l_deg: float, b_deg: float) -> tuple[float, float]:
    """Convert galactic (l, b) to ICRS equatorial (RA hours, Dec degrees), J2000."""
    l = math.radians(l_deg)
    b = math.radians(b_deg)
    xg = math.cos(b) * math.cos(l)
    yg = math.cos(b) * math.sin(l)
    zg = math.sin(b)
    R  = _GAL_TO_ICRS
    xi = R[0][0]*xg + R[0][1]*yg + R[0][2]*zg
    yi = R[1][0]*xg + R[1][1]*yg + R[1][2]*zg
    zi = R[2][0]*xg + R[2][1]*yg + R[2][2]*zg
    dec = math.degrees(math.asin(max(-1.0, min(1.0, zi))))
    ra  = math.atan2(yi, xi)
    if ra < 0:
        ra += 2 * math.pi
    return ra * 12 / math.pi, dec   # (RA hours, Dec degrees)


def _arch_angle(alt1: float, az1: float, alt2: float, az2: float) -> float:
    """
    Angle of the galactic plane at point 1, relative to the horizon (degrees).

    Projects points onto the tangent plane at point 1 using a local
    East-Up coordinate system, then returns arctan(|Δup| / |Δeast|).
      0°  → band runs horizontally along the horizon
      90° → band rises straight up (ideal arch)

    Valid for angular separations < ~40°.
    """
    daz = az2 - az1
    if daz >  180: daz -= 360
    if daz < -180: daz += 360
    dx = daz * math.cos(math.radians(alt1))   # East component
    dy = alt2 - alt1                            # Up component
    return math.degrees(math.atan2(abs(dy), abs(dx)))


_c = _cfg.load()["targets"]
DEFAULT_MIN_ELEVATION  = float(_c["min_elevation_deg"])
DEFAULT_MOON_MIN_SEP   = float(_c["moon_min_separation_deg"])
DEFAULT_MOON_MAX_ILLUM = float(_c["moon_max_illumination_pct"])
SAMPLE_INTERVAL_MIN    = 10

_CATALOG_PATH = Path(__file__).parent / "targets.json"

_PLANET_BODIES = {
    "mercury": "mercury",
    "venus":   "venus",
    "mars":    "mars barycenter",
    "jupiter": "jupiter barycenter",
    "saturn":  "saturn barycenter",
    "uranus":  "uranus barycenter",
    "neptune": "neptune barycenter",
}


@dataclass
class TargetWindow:
    start: datetime
    end: datetime
    start_alt_deg: float
    end_alt_deg: float
    peak_time: datetime
    peak_alt_deg: float
    peak_az_deg: float = 0.0
    moon_interference: bool = False
    arch_angle_deg: float | None = None        # milky_way only: plane angle from horizon
    moon_sep_at_peak_deg: float | None = None  # angular separation from moon at peak time
    moon_alt_at_peak_deg: float | None = None  # moon altitude at peak time (for K&S model)
    photo_cutoff: "datetime | None" = None     # last sample where astrophotography is viable
    visual_cutoff: "datetime | None" = None    # last sample where visual observation is viable
    ks_computed: bool = False                  # True when K&S was run and the full window is viable


@dataclass
class VisibleTarget:
    name: str
    type: str
    windows: list      # list[TargetWindow]
    note: str | None   # e.g. "3 days before peak" for meteor showers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ephemeris():
    return load("de421.bsp")


def _parse_ra(s: str) -> float:
    """'05h 35m 17s' → decimal hours."""
    parts = [p for p in re.split(r"[hms\s]+", s.strip()) if p]
    h   = float(parts[0])
    m   = float(parts[1]) if len(parts) > 1 else 0.0
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return h + m / 60 + sec / 3600


def _parse_dec(s: str) -> float:
    """'±DD° MM' SS"' → signed decimal degrees."""
    s    = s.strip()
    sign = -1 if s.startswith("-") else 1
    parts = [p for p in re.split(r"[°'\"°\s]+", s.lstrip("+-")) if p]
    d   = float(parts[0])
    m   = float(parts[1]) if len(parts) > 1 else 0.0
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return sign * (d + m / 60 + sec / 3600)


def _sky_object(entry: dict) -> Star:
    """Build a Skyfield Star from a catalog entry.

    Supports three coordinate formats:
      galactic_l / galactic_b  — converted via IAU rotation matrix (milky_way)
      ra / dec                 — standard equatorial J2000
      radiant_ra / radiant_dec — meteor shower radiants
    """
    if "galactic_l" in entry:
        ra_h, dec_d = gal_to_radec(entry["galactic_l"], entry.get("galactic_b", 0.0))
        return Star(ra_hours=ra_h, dec_degrees=dec_d)
    ra_key  = "ra"  if "ra"  in entry else "radiant_ra"
    dec_key = "dec" if "dec" in entry else "radiant_dec"
    return Star(ra_hours=_parse_ra(entry[ra_key]),
                dec_degrees=_parse_dec(entry[dec_key]))


def _make_window(alt_deg, az_deg, sample_dts, start_idx, end_idx):
    """Return (TargetWindow, [indices]) for a contiguous above-threshold segment."""
    indices     = list(range(start_idx, end_idx + 1))
    seg         = alt_deg[start_idx:end_idx + 1]
    peak_offset = int(np.argmax(seg))
    peak_idx    = start_idx + peak_offset
    return (
        TargetWindow(
            start=sample_dts[start_idx],
            end=sample_dts[end_idx],
            start_alt_deg=float(alt_deg[start_idx]),
            end_alt_deg=float(alt_deg[end_idx]),
            peak_time=sample_dts[peak_idx],
            peak_alt_deg=float(alt_deg[peak_idx]),
            peak_az_deg=float(az_deg[peak_idx]),
        ),
        indices,
    )


def _find_windows(alt_deg, az_deg, sample_dts: list, min_elev: float) -> list:
    """Return list of (TargetWindow, [indices]) for each above-threshold segment."""
    result    = []
    in_window = False
    start_idx = None

    for i, alt in enumerate(alt_deg):
        above = bool(alt >= min_elev)
        if above and not in_window:
            in_window = True
            start_idx = i
        elif not above and in_window:
            in_window = False
            result.append(_make_window(alt_deg, az_deg, sample_dts, start_idx, i - 1))

    if in_window:
        result.append(_make_window(alt_deg, az_deg, sample_dts, start_idx, len(sample_dts) - 1))

    return result


def _moon_interferes(sep_deg, window_indices: list, illumination_pct: float,
                     min_sep: float, max_illum: float) -> bool:
    """True if moon is bright enough and close enough to any part of the visible window."""
    if illumination_pct < max_illum or not window_indices:
        return False
    return bool(np.any(sep_deg[window_indices] < min_sep))


def _meteor_shower_note(entry: dict, night_date) -> str | None:
    """
    Return a proximity string (e.g. '3 days before peak') or None if the
    night is outside the shower's active window.

    Handles year-boundary showers (e.g. Quadrantids: peak Jan 3, active Dec–Jan)
    by trying adjacent years and picking the closest peak.
    """
    from datetime import date as _date

    peak_month = entry["peak_month"]
    peak_day   = entry["peak_day"]
    half       = entry["active_window_days"] // 2

    best_delta = None
    for year_offset in (0, -1, 1):
        try:
            peak  = _date(night_date.year + year_offset, peak_month, peak_day)
            delta = (night_date - peak).days
            if best_delta is None or abs(delta) < abs(best_delta):
                best_delta = delta
        except ValueError:
            continue

    if best_delta is None or abs(best_delta) > half:
        return None

    if best_delta == 0:
        return "Peak night"
    n = abs(best_delta)
    direction = "before" if best_delta < 0 else "after"
    return f"{n} day{'s' if n != 1 else ''} {direction} peak"


# ---------------------------------------------------------------------------
# Per-target computation
# ---------------------------------------------------------------------------

def _compute_target(entry: dict, observer, eph, t_array, sample_dts: list,
                    moon_astr, moon_alt_deg_all,
                    illumination_pct: float, night_date,
                    min_elevation: float, moon_min_sep: float,
                    moon_max_illum: float,
                    obs_start: datetime, obs_end: datetime,
                    sky_sqm: float | None = None) -> "VisibleTarget | None":
    name     = entry["name"]
    ttype    = entry["type"]
    min_elev = entry.get("min_elevation", min_elevation)

    note = None
    if ttype == "meteor_shower":
        note = _meteor_shower_note(entry, night_date)
        if note is None:
            return None  # outside active window

    try:
        if ttype == "planet":
            key = _PLANET_BODIES.get(name.lower())
            if not key:
                log.warning("Unknown planet %r — skipping", name)
                return None
            body = eph[key]
        else:
            body = _sky_object(entry)
    except (KeyError, ValueError) as e:
        log.warning("Skipping target %r: %s", name, e)
        return None

    astrometric = observer.at(t_array).observe(body)
    alt, az, _  = astrometric.apparent().altaz()
    alt_deg     = alt.degrees
    az_deg      = az.degrees
    sep_deg     = astrometric.separation_from(moon_astr).degrees

    # Clip to the effective observation window for this target type
    mask             = np.array([obs_start <= dt <= obs_end for dt in sample_dts])
    obs_sample_idxs  = np.where(mask)[0]          # indices into t_array / sample_dts
    obs_alt          = alt_deg[mask]
    obs_az           = az_deg[mask]
    obs_sep          = sep_deg[mask]
    obs_moon_alt     = moon_alt_deg_all[mask]      # moon altitude at each masked sample
    obs_dts          = [dt for dt, m in zip(sample_dts, mask) if m]

    windows_with_idx = _find_windows(obs_alt, obs_az, obs_dts, min_elev)
    if not windows_with_idx:
        return None

    # Hoist catalog photometric data — same values for every window of this target.
    sb  = entry.get("surface_brightness")   # mag/arcsec² (extended objects)
    mag = entry.get("magnitude")             # integrated V mag (any object)
    _sqm = sky_sqm if sky_sqm is not None else _KS_NATURAL_SKY

    # For planets, override mag with the dynamically-computed apparent magnitude.
    # Skyfield's planetary_magnitude() accounts for phase angle and distance, so
    # Mars near opposition (-2.9) and Mars at aphelion (+1.8) are handled correctly.
    # We evaluate at the observation-window midpoint — magnitude drifts < 0.01 mag/night.
    if ttype == "planet" and _planetary_magnitude is not None and len(obs_sample_idxs) > 0:
        try:
            mid_i        = int(obs_sample_idxs[len(obs_sample_idxs) // 2])
            planet_astr  = observer.at(t_array[mid_i]).observe(body)
            mag          = float(_planetary_magnitude(planet_astr))
            log.debug("Planet %r apparent magnitude: %.2f", name, mag)
        except Exception as e:
            log.debug("planetary_magnitude failed for %r: %s", name, e)

    # has_catalog_data: True when we can evaluate site photo-viability.
    # Meteor showers are exempt — their activity is gated by peak_day window,
    # not sky brightness (individual meteors are bright transient events).
    has_catalog_data = (
        (sb is not None or mag is not None)
        and ttype not in ("meteor_shower",)
    )

    # Tracks whether ANY sample in ANY window passes the photo contrast check.
    # If this remains False after all windows are processed, the site's baseline
    # sky brightness (from light pollution, not just the moon) is too severe and
    # the target is suppressed entirely.
    any_photo_ok_global = False

    windows = []
    for window, indices in windows_with_idx:
        window.moon_interference = _moon_interferes(obs_sep, indices, illumination_pct,
                                                    moon_min_sep, moon_max_illum)

        # Store moon separation and altitude at peak time for the K&S sky brightness model.
        try:
            peak_obs_idx = obs_dts.index(window.peak_time)
            window.moon_sep_at_peak_deg = float(obs_sep[peak_obs_idx])
            window.moon_alt_at_peak_deg = float(obs_moon_alt[peak_obs_idx])
        except Exception as e:
            log.debug("Moon sep/alt at peak failed for %r: %s", name, e)

        # Per-sample photo/visual usability cutoffs.
        # Iterate through each sample in this window and find the last one where
        # the sky background (dark sky + K&S moon contribution) still provides
        # enough contrast for the target.  We record the LAST usable sample so
        # the cutoff datetime is inclusive — i.e. "last moment it was usable."
        if has_catalog_data:
            # Select per-type contrast / offset thresholds.
            # SB-based (extended objects): need sky − target ≥ contrast headroom.
            # Mag-based (compact objects): need mag < sky − offset.
            if ttype == "milky_way":
                photo_contrast  = _MW_PHOTO_SB_CONTRAST
                visual_contrast = _MW_VISUAL_SB_CONTRAST
            else:
                photo_contrast  = _PHOTO_SB_CONTRAST
                visual_contrast = _VISUAL_SB_CONTRAST

            if ttype == "planet":
                compact_photo  = _PLANET_PHOTO_OFFSET
                compact_visual = _PLANET_VISUAL_OFFSET
            else:
                compact_photo  = _COMPACT_PHOTO_OFFSET
                compact_visual = _COMPACT_VISUAL_OFFSET

            win_indices = [i for i, dt in enumerate(obs_dts)
                           if window.start <= dt <= window.end]
            last_photo_ok  = None
            last_visual_ok = None
            for i in win_indices:
                sep      = float(obs_sep[i])
                malt     = float(obs_moon_alt[i])
                delta    = _ks_delta_mag(illumination_pct, sep, malt, _sqm)
                sky_now  = _sqm - delta   # effective sky brightness this sample

                if sb is not None:
                    photo_ok  = sb  < sky_now - photo_contrast
                    visual_ok = sb  < sky_now - visual_contrast
                else:
                    photo_ok  = mag < sky_now - compact_photo
                    visual_ok = mag < sky_now - compact_visual

                if photo_ok:
                    last_photo_ok = obs_dts[i]
                    any_photo_ok_global = True
                if visual_ok:
                    last_visual_ok = obs_dts[i]

            # Only set a cutoff if it falls before the natural window end
            # (otherwise the target is usable all the way through).
            if last_photo_ok is not None and last_photo_ok < window.end:
                window.photo_cutoff = last_photo_ok
            elif last_photo_ok is not None:
                # K&S was computed and every sample in this window passes —
                # mark explicitly so the arch summary doesn't fall back to the
                # legacy moonrise heuristic.
                window.ks_computed = True
            if last_visual_ok is not None and last_visual_ok < window.end:
                window.visual_cutoff = last_visual_ok

        # For Milky Way waypoints, compute the arch angle (plane vs horizon)
        # using a reference point 30° further along the galactic plane.
        if ttype == "milky_way" and "galactic_l" in entry:
            try:
                ref_l = (entry["galactic_l"] + 30) % 360
                ref_b = entry.get("galactic_b", 0.0)
                ref_ra, ref_dec = gal_to_radec(ref_l, ref_b)
                ref_star = Star(ra_hours=ref_ra, dec_degrees=ref_dec)
                # Evaluate reference at the window's peak time only
                peak_idx = sample_dts.index(window.peak_time)
                t_peak   = t_array[peak_idx]
                ref_alt, ref_az, _ = (
                    observer.at(t_peak).observe(ref_star).apparent().altaz()
                )
                window.arch_angle_deg = round(
                    _arch_angle(window.peak_alt_deg, window.peak_az_deg,
                                float(ref_alt.degrees), float(ref_az.degrees)), 1
                )
            except Exception as e:
                log.debug("Arch angle computation failed for %r: %s", name, e)

        windows.append(window)

    # Suppress targets with catalog data where no sample passes the photo contrast
    # check.  This happens when the site's baseline sky brightness (light pollution)
    # is already too high — the moon is irrelevant, the sky itself is too bright.
    # Planets and meteor showers are exempt (handled above via has_catalog_data).
    if has_catalog_data and not any_photo_ok_global:
        log.debug(
            "Suppressing %r — zero photo-viable samples at site SQM %.1f",
            name, _sqm,
        )
        return None

    return VisibleTarget(name=name, type=ttype, windows=windows, note=note)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_targets() -> list:
    """Load and return raw catalog entries. Returns [] on missing or malformed file."""
    if not _CATALOG_PATH.exists():
        log.warning("targets.json not found at %s", _CATALOG_PATH)
        return []
    try:
        return json.loads(_CATALOG_PATH.read_text())
    except Exception as e:
        log.warning("Failed to load targets.json: %s", e)
        return []


def visible_targets(
    lat: float,
    lon: float,
    sunset: datetime,
    sunrise: datetime,
    illumination_pct: float,
    night_start: datetime | None = None,
    night_end: datetime | None   = None,
    min_elevation: float = DEFAULT_MIN_ELEVATION,
    moon_min_sep: float  = DEFAULT_MOON_MIN_SEP,
    moon_max_illum: float = DEFAULT_MOON_MAX_ILLUM,
    sky_sqm: float | None = None,
) -> list:
    """
    Return targets visible during the night.

    DSOs and meteor showers are clipped to astronomical darkness
    (night_start–night_end). Planets use the full sunset–sunrise window
    since they are often worth observing during twilight.
    Falls back to sunset/sunrise for both if night bounds are unavailable.
    """
    catalog = load_targets()
    if not catalog:
        return []

    ts       = load.timescale()
    eph      = _ephemeris()
    observer = eph["earth"] + wgs84.latlon(lat, lon)

    # Full sample window sunset→sunrise (needed for planet twilight coverage)
    total_min  = int((sunrise - sunset).total_seconds() / 60)
    sample_dts = [
        sunset + timedelta(minutes=i)
        for i in range(0, total_min + SAMPLE_INTERVAL_MIN, SAMPLE_INTERVAL_MIN)
    ]
    if sample_dts and sample_dts[-1] > sunrise:
        sample_dts[-1] = sunrise

    t_array    = ts.from_datetimes(sample_dts)
    moon_astr  = observer.at(t_array).observe(eph["moon"])
    moon_alt_v, _, _ = moon_astr.apparent().altaz()
    moon_alt_deg_all = moon_alt_v.degrees          # ndarray, one value per sample
    night_date = sunset.date()

    # Use provided SQM or fall back to the K&S natural-sky baseline (Bortle 2).
    _sky_sqm = sky_sqm if sky_sqm is not None else _KS_NATURAL_SKY

    dark_start = night_start or sunset
    dark_end   = night_end   or sunrise

    results = []
    for entry in catalog:
        is_planet = entry["type"] == "planet"
        obs_start = sunset     if is_planet else dark_start
        obs_end   = sunrise    if is_planet else dark_end
        try:
            result = _compute_target(
                entry, observer, eph, t_array, sample_dts,
                moon_astr, moon_alt_deg_all,
                illumination_pct, night_date,
                min_elevation, moon_min_sep, moon_max_illum,
                obs_start, obs_end,
                sky_sqm=_sky_sqm,
            )
        except Exception as e:
            log.warning("Error computing target %r: %s", entry.get("name"), e)
            result = None

        if result is not None:
            results.append(result)

    results.sort(key=lambda t: max(w.peak_alt_deg for w in t.windows), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Milky Way arch synthesis
# ---------------------------------------------------------------------------

# Ten waypoints at uniform 36° galactic-longitude intervals.
# Each pair offset by 180° is symmetric: same |dec|, opposite sign.
#   Core ↔ Anticenter, Scutum ↔ Monoceros, Cygnus ↔ Puppis,
#   Cepheus ↔ Carina, Perseus/Cassiopeia ↔ Norma
# This ensures n_visible / n_total is a true fractional sky-coverage metric:
# every visible waypoint represents a distinct 36° slice of the galactic plane.
_MW_WAYPOINT_ORDER = [
    "Galactic Core",       # l=0°   dec -29°  summer anchor
    "Scutum Star Cloud",   # l=36°  dec  +3°  ↔ Monoceros
    "Cygnus Star Cloud",   # l=72°  dec +34°  northern arch peak  ↔ Puppis
    "Cepheus Cloud",       # l=108° dec +59°  ↔ Carina Arm
    "Perseus/Cassiopeia",  # l=144° dec +56°  ↔ Norma
    "Galactic Anticenter", # l=180° dec +29°  ↔ Galactic Core
    "Monoceros",           # l=216° dec  -3°  winter southern band
    "Puppis Star Cloud",   # l=252° dec -34°  southern arch peak
    "Carina Arm",          # l=288° dec -59°  SH showpiece
    "Norma Star Cloud",    # l=324° dec -56°  connects back toward core
]

# Approximate galactic-core declination for theoretical-max calculations.
_GALACTIC_CORE_DEC = -29.0


def mw_max_visible(lat: float) -> int:
    """
    Return the maximum number of MW waypoints that can ever be visible
    (peak altitude ≥ 10°) from the given latitude.

    Uses the nominal declinations of the 10 uniform waypoints.
    """
    # Nominal decs from gal_to_radec at each l, b=0 (pre-computed)
    _WAYPOINT_DECS = [-28.9, +2.7, +34.1, +59.3, +56.1,
                      +28.9, -2.7, -34.1, -59.3, -56.1]
    return sum(1 for dec in _WAYPOINT_DECS if 90 - abs(lat - dec) >= 10)


def mw_theoretical_core_max(lat: float) -> float:
    """Theoretical maximum altitude the galactic core can reach from this latitude."""
    return max(0.0, 90.0 - abs(lat - _GALACTIC_CORE_DEC))


# Krisciunas & Schaefer (1991) model constants
_KS_K_EXT        = 0.172   # typical V-band atmospheric extinction coefficient
_KS_NATURAL_SKY  = 21.6    # Bortle 2 dark-sky baseline (mag/arcsec²); conservative

# Severity thresholds in Δ mag/arcsec² (sky brightening from dark-sky baseline)
_KS_MINOR_THRESH    = 0.10   # < 0.10 → None   : imperceptible
_KS_MODERATE_THRESH = 0.50   # 0.10–0.50 → minor
_KS_SEVERE_THRESH   = 1.50   # 0.50–1.50 → moderate  /  ≥ 1.50 → severe

# Sky contrast thresholds for per-target usability cutoffs.
# Extended objects (nebulae/galaxies): object surface brightness must be this many
# mag/arcsec² brighter than the (moon-brightened) sky background.
#
# Calibration (Bortle 1 site, SQM 22.0):
#   Faint targets (SB ≈ 17) have 5 mag of contrast on a dark night.
#   _PHOTO_SB_CONTRAST = 3.5 → photo cutoff when Δμ > SQM − SB − 3.5:
#     Veil/NAN (SB 17–17.5): cut at Δμ ≈ 1.0–1.5 (moderate→severe transition)
#     Dumbbell/Ring (SB 13–13.5): cut only at Δμ > 5 — effectively never
#   _VISUAL_SB_CONTRAST = 1.5 → visual window extends ~30–60 min past photo cutoff
# Extended objects (nebulae / galaxies): object SB must exceed sky background by this margin.
# Calibrated against real-world Bortle astrophotography limits (broadband, no filter):
#   Bortle 9 (SQM 17.0): SB limit ≈ 13.8  →  Dumbbell/Helix (SB 13.5) just survive
#   Bortle 8 (SQM 18.0): SB limit ≈ 14.8  →  Eagle/Trifid (SB 14.5) just survive
#   Bortle 6 (SQM 20.0): SB limit ≈ 16.8  →  Veil/Rosette (SB 17.0) just fail — need B5
#   Bortle 5 (SQM 20.5): SB limit ≈ 17.3  →  Veil/Rosette survive; NAN (17.5) needs B4
_PHOTO_SB_CONTRAST  = 3.2
_VISUAL_SB_CONTRAST = 1.5   # visual: 1.5 mag/arcsec² headroom (telescope needed)

# Compact objects (clusters): usable while integrated magnitude < site_sqm - Δμ - offset.
# Calibrated against Bortle-class astrophotography limits (integrated mag scale):
#   Bortle 9 (SQM 17.0): photo limit ≈ mag 4.0  →  offset = 13.0
#   Bortle 8 (SQM 18.0): photo limit ≈ mag 5.0
#   Bortle 7 (SQM 19.0): photo limit ≈ mag 6.0
#   Bortle 5 (SQM 20.5): photo limit ≈ mag 7.5
#   Bortle 1 (SQM 22.0): photo limit ≈ mag 9.0
# Visual offset is 2 mag more lenient (telescope can reach deeper in degraded skies).
_COMPACT_PHOTO_OFFSET  = 13.0
_COMPACT_VISUAL_OFFSET = 11.0

# Planets: point-source-like, so slightly more lenient than extended clusters.
# Apparent magnitude computed dynamically via Skyfield's planetary_magnitude().
# Calibration anchors:
#   Uranus  (+5.8): accessible from Bortle 8+ (SQM 18.0 − 12.0 = 6.0 > 5.8)
#   Neptune (+7.8): accessible from Bortle 6+ (SQM 20.0 − 12.0 = 8.0 > 7.8)
#   All bright planets (Venus/Jupiter/Mars/Saturn) pass at any Bortle class.
_PLANET_PHOTO_OFFSET  = 12.0
_PLANET_VISUAL_OFFSET = 10.0

# Milky Way band: wide-field photography needs less contrast than telescope DSO work.
# Calibrated against Bortle-class MW visibility:
#   Bortle 7 (SQM 19.0): Core (SB 17.0) and Cygnus (SB 18.0) just accessible
#   Bortle 6 (SQM 20.0): Cepheus (SB 18.5) accessible
#   Bortle 5 (SQM 20.5): Perseus/Norma (SB 19.0) accessible
#   Bortle 4 (SQM 21.5): Anticenter (SB 19.5) accessible
_MW_PHOTO_SB_CONTRAST  = 1.5
_MW_VISUAL_SB_CONTRAST = 1.0


def _ks_delta_mag(
    illumination_pct: float,
    sep_deg: float,
    moon_alt_deg: float,
    sky_sqm: float = _KS_NATURAL_SKY,
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


def moon_wash_severity(
    illumination_pct: float,
    sep_deg: float | None,
    moon_alt_deg: float | None = None,
) -> str | None:
    """
    Classify moon interference as None, 'minor', 'moderate', or 'severe'.

    Uses _ks_delta_mag internally; sep_deg and moon_alt_deg default to 45°
    when not provided (conservative mid-sky estimate).

    Severity thresholds (Δ mag/arcsec² relative to a Bortle-2 dark sky):
      None       < 0.10  — negligible
      'minor'   0.10–0.50 — slight brightening
      'moderate' 0.50–1.50 — noticeable; low-SB targets impacted
      'severe'   ≥ 1.50  — sky substantially brighter; deep DSO work limited
    """
    delta_mag = _ks_delta_mag(
        illumination_pct,
        sep_deg      if sep_deg      is not None else 45.0,
        moon_alt_deg if moon_alt_deg is not None else 45.0,
    )
    if delta_mag < _KS_MINOR_THRESH:
        return None
    if delta_mag < _KS_MODERATE_THRESH:
        return "minor"
    if delta_mag < _KS_SEVERE_THRESH:
        return "moderate"
    return "severe"


def milky_way_arch_summary(
    mw_targets: list,
    lat: float = 0.0,
    moonrise: datetime | None = None,
    moonset:  datetime | None = None,
    moon_illumination_pct: float = 0.0,
) -> dict | None:
    """
    Synthesise a Milky Way visibility summary from pre-computed visible targets.

    mw_targets            — list of VisibleTarget objects with type == "milky_way".
    lat                   — observer latitude in decimal degrees (used for quality score).
    moonrise / moonset    — times of moonrise/moonset this night (UTC-aware datetimes).
    moon_illumination_pct — moon illumination percentage (0–100).

    When the moon is bright (≥ 25 %) the arch window is clipped to the moon-free
    period: arch_end is capped at moonrise (if moon rises during the window), and
    arch_start is advanced to moonset (if the moon is already up at window start).
    The clipped duration flows into win_frac, naturally lowering the quality score.

    Returns None if the Galactic Core is not visible tonight.

    Returned dict keys
    ------------------
    arch_start / arch_end    datetime  — moon-free arch window
    arch_hours               float     — arch window duration in hours
    moon_limited             bool      — True if window was clipped by moon
    n_visible                int       — waypoints with any visible window tonight
    n_max_possible           int       — max waypoints ever visible from this latitude
    n_total                  int       — total catalog waypoints (10)
    local_score              float     — 0–10 quality score relative to lat ceiling
    core_peak_time           datetime
    core_peak_alt_deg        int       — rounded altitude in degrees
    core_peak_az_deg         int       — rounded azimuth in degrees
    arch_angle_deg           float | None
    farthest_name            str | None  — highest-peaking far-side waypoint
    farthest_peak_alt_deg    int  | None
    farthest_peak_az_deg     int  | None
    """
    if not mw_targets:
        return None

    by_name = {t.name: t for t in mw_targets}
    core = by_name.get("Galactic Core")
    if core is None:
        return None

    def _best(target):
        clean = [w for w in target.windows if not w.moon_interference]
        pool  = clean if clean else target.windows
        return max(pool, key=lambda w: w.peak_alt_deg)

    core_w = _best(core)

    # Far-end waypoint: highest-peaking visible waypoint from either arm
    # (index ≥ 2 excludes Core and Scutum — both within 36° of the core,
    # so they represent the same visual section).  Highest altitude naturally
    # selects Cygnus from the NH and Puppis/Norma from the SH.
    _FAR_SIDE = set(_MW_WAYPOINT_ORDER[2:])
    far_candidates = [
        (by_name[n], _best(by_name[n]))
        for n in _FAR_SIDE if n in by_name
    ]
    if far_candidates:
        farthest, farthest_w = max(far_candidates, key=lambda x: x[1].peak_alt_deg)
    else:
        farthest, farthest_w = None, None

    # Arch window: core ∩ far-end simultaneously above horizon.
    # Falls back to the core window when there is no overlap.
    if farthest_w:
        arch_start = max(core_w.start, farthest_w.start)
        arch_end   = min(core_w.end,   farthest_w.end)
        if arch_start >= arch_end:
            arch_start, arch_end = core_w.start, core_w.end
    else:
        arch_start, arch_end = core_w.start, core_w.end

    # Clip to the moon-affected period using per-waypoint K&S photo cutoffs.
    #
    # arch_end: use the earliest photo_cutoff among all visible waypoints.
    #   If K&S ran but produced no cutoffs, the moon brightening never exceeded
    #   the contrast threshold — trust that result and leave arch_end unchanged.
    #   Only fall back to the blunt moonrise heuristic when K&S couldn't run
    #   (no surface_brightness data in the catalog).
    #
    # arch_start: still advanced to moonset when the moon is already up at the
    #   start of the window (moonset-during-window scenario).  Per-sample cutoffs
    #   only capture the end of the usable period, not the recovery after moonset.
    _MOON_ILLUM_THRESHOLD = 25.0
    moon_limited = False

    # Collect all photo_cutoffs from visible waypoints that fall inside the arch window
    all_photo_cutoffs = [
        w.photo_cutoff
        for t in mw_targets
        for w in t.windows
        if w.photo_cutoff is not None and arch_start < w.photo_cutoff < arch_end
    ]

    # Determine whether K&S was actually run for any waypoint window.
    # ks_ran = True  → K&S was computed; if no cutoffs resulted, the moon is not
    #                   degrading these waypoints enough to clip them → trust K&S,
    #                   do NOT fall back to the blunt moonrise heuristic.
    # ks_ran = False → no surface_brightness data; fall back to moonrise as before.
    ks_ran = any(
        w.ks_computed or w.photo_cutoff is not None
        for t in mw_targets
        for w in t.windows
    )

    if all_photo_cutoffs:
        arch_end     = min(all_photo_cutoffs)
        moon_limited = True
    elif not ks_ran and moon_illumination_pct >= _MOON_ILLUM_THRESHOLD:
        # Legacy fallback: only when K&S couldn't run (no SB catalog data).
        if moonrise and arch_start < moonrise < arch_end:
            arch_end     = moonrise
            moon_limited = True

    # Moonset advance (moon already up at arch start → wait for it to set)
    if moon_illumination_pct >= _MOON_ILLUM_THRESHOLD:
        if moonset and arch_start < moonset < arch_end:
            arch_start   = moonset
            moon_limited = True

    arch_hours = max(0.0, (arch_end - arch_start).total_seconds() / 3600)

    # ── Latitude-relative quality score (0–10) ───────────────────────────────
    # How good is tonight compared to the best this latitude can ever offer?
    #   50% — core altitude vs theoretical ceiling for this lat
    #   30% — visible waypoints vs max possible from this lat
    #   20% — arch window hours (reference: 5 h = full marks)
    theo_max     = mw_theoretical_core_max(lat)
    n_max        = mw_max_visible(lat)
    alt_frac     = (core_w.peak_alt_deg / theo_max)  if theo_max > 0 else 0.0
    cov_frac     = (len(mw_targets) / n_max)         if n_max    > 0 else 0.0
    win_frac     = min(1.0, arch_hours / 5.0)
    # Moon penalty applies when the moon directly interferes with the core OR
    # when it cuts the usable arch window short (bright moon rising mid-night).
    moon_penalised = core_w.moon_interference or moon_limited
    moon_penalty   = 0.7 if moon_penalised else 1.0
    raw            = 0.50 * alt_frac + 0.30 * cov_frac + 0.20 * win_frac
    local_score    = round(min(10.0, raw * moon_penalty * 10), 1)

    core_peak_in_window = arch_start <= core_w.peak_time <= arch_end

    return {
        "arch_start":            arch_start,
        "arch_end":              arch_end,
        "arch_hours":            round(arch_hours, 1),
        "moon_limited":          moon_limited,
        "moon_penalised":        moon_penalised,
        "n_visible":             len(mw_targets),
        "n_max_possible":        n_max,
        "n_total":               len(_MW_WAYPOINT_ORDER),
        "local_score":           local_score,
        "alt_score":             round(alt_frac * 10, 1),
        "cov_score":             round(cov_frac * 10, 1),
        "win_score":             round(win_frac * 10, 1),
        "core_peak_time":        core_w.peak_time,
        "core_peak_in_window":   core_peak_in_window,
        "core_peak_alt_deg":     round(core_w.peak_alt_deg),
        "core_peak_az_deg":      round(core_w.peak_az_deg),
        "arch_angle_deg":        core_w.arch_angle_deg,
        "farthest_name":         farthest.name if farthest else None,
        "farthest_peak_alt_deg": round(farthest_w.peak_alt_deg) if farthest_w else None,
        "farthest_peak_az_deg":  round(farthest_w.peak_az_deg)  if farthest_w else None,
    }
