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
    arch_angle_deg: float | None = None   # milky_way only: plane angle from horizon


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
                    moon_astr, illumination_pct: float, night_date,
                    min_elevation: float, moon_min_sep: float,
                    moon_max_illum: float,
                    obs_start: datetime, obs_end: datetime) -> "VisibleTarget | None":
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
    mask    = np.array([obs_start <= dt <= obs_end for dt in sample_dts])
    obs_alt = alt_deg[mask]
    obs_az  = az_deg[mask]
    obs_sep = sep_deg[mask]
    obs_dts = [dt for dt, m in zip(sample_dts, mask) if m]

    windows_with_idx = _find_windows(obs_alt, obs_az, obs_dts, min_elev)
    if not windows_with_idx:
        return None

    windows = []
    for window, indices in windows_with_idx:
        window.moon_interference = _moon_interferes(obs_sep, indices, illumination_pct,
                                                    moon_min_sep, moon_max_illum)

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

    t_array   = ts.from_datetimes(sample_dts)
    moon_astr = observer.at(t_array).observe(eph["moon"])
    night_date = sunset.date()

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
                moon_astr, illumination_pct, night_date,
                min_elevation, moon_min_sep, moon_max_illum,
                obs_start, obs_end,
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


def milky_way_arch_summary(mw_targets: list, lat: float = 0.0) -> dict | None:
    """
    Synthesise a Milky Way visibility summary from pre-computed visible targets.

    mw_targets — list of VisibleTarget objects with type == "milky_way".
    lat        — observer latitude in decimal degrees (used for quality score).

    Returns None if the Galactic Core is not visible tonight.

    Returned dict keys
    ------------------
    arch_start / arch_end    datetime  — full-arch window (core ∩ far-end overlap,
                                         or core window alone if no overlap exists)
    arch_hours               float     — arch window duration in hours
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

    arch_hours = (arch_end - arch_start).total_seconds() / 3600

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
    moon_penalty = 0.7 if core_w.moon_interference else 1.0
    raw          = 0.50 * alt_frac + 0.30 * cov_frac + 0.20 * win_frac
    local_score  = round(min(10.0, raw * moon_penalty * 10), 1)

    return {
        "arch_start":            arch_start,
        "arch_end":              arch_end,
        "arch_hours":            round(arch_hours, 1),
        "n_visible":             len(mw_targets),
        "n_max_possible":        n_max,
        "n_total":               len(_MW_WAYPOINT_ORDER),
        "local_score":           local_score,
        "core_peak_time":        core_w.peak_time,
        "core_peak_alt_deg":     round(core_w.peak_alt_deg),
        "core_peak_az_deg":      round(core_w.peak_az_deg),
        "arch_angle_deg":        core_w.arch_angle_deg,
        "farthest_name":         farthest.name if farthest else None,
        "farthest_peak_alt_deg": round(farthest_w.peak_alt_deg) if farthest_w else None,
        "farthest_peak_az_deg":  round(farthest_w.peak_az_deg)  if farthest_w else None,
    }
