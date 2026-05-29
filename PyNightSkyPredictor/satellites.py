#!/usr/bin/env python3
"""
Satellite pass prediction — ISS and other bright satellites.

TLE data from Celestrak; cached with a strict 6-hour TTL to stay within
Celestrak's acceptable-use policy.  Do NOT reduce the TTL.

Passes are computed with a coarse→fine approach:
  1. Coarse scan (10 s steps) across the full pass to locate the minimum
     Moon separation and get rise / peak / set geometry.
  2. Fine scan (0.1 s steps over a ±5 s window) only when the coarse
     minimum falls inside the Moon-transit candidate threshold (~1°).
     Both scans are fully vectorised — total cost ≈ 2 ms per pass.

Public API:
    satellite_passes(lat, lon, t_start, t_end) → list[SatPass]
    ISS_NORAD_ID                               → 25544
"""

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from skyfield.api import EarthSatellite, Loader, load, wgs84

from . import cache as _cache

log = logging.getLogger(__name__)

# Loader rooted at the package directory so de421.bsp resolves correctly.
_load = Loader(str(Path(__file__).resolve().parent))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISS_NORAD_ID  = 25544
_TLE_TTL      = 6 * 3600           # exactly 6 h — Celestrak rate-limit compliance
_MIN_PASS_ALT = 10.0               # degrees — floor passed to find_events()
_COARSE_STEP  = 10                 # seconds — coarse scan step across each pass
_FINE_HALFWIN = 5.0                # seconds — ± window around coarse minimum for fine scan
_FINE_STEP    = 0.1                # seconds — fine scan step
_MOON_RADIUS  = 0.26               # degrees — half of Moon's ~0.52° angular disc
_FINE_TRIGGER = _MOON_RADIUS * 4   # degrees — coarse min below this triggers fine scan
_USER_AGENT   = "PyNightSkyPredictor/1.0 (open-source astronomical observation planner)"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SatPass:
    """One satellite pass over the observer."""
    satellite_name:       str
    rise_time:            datetime         # UTC; rises above _MIN_PASS_ALT
    peak_time:            datetime         # UTC; maximum altitude
    set_time:             datetime         # UTC; falls below _MIN_PASS_ALT
    peak_alt_deg:         float
    peak_az_deg:          float
    rise_az_deg:          float
    set_az_deg:           float
    duration_min:         float
    in_sunlight:          bool             # False → ISS in Earth's shadow, not visible
    # Moon proximity — all None when Moon is below the horizon at pass time
    moon_sep_deg:         Optional[float]  # angular sep from Moon at pass peak
    moon_transit:         bool             # True → min sep < Moon's angular radius
    moon_transit_time:    Optional[datetime]   # UTC time of closest approach (fine scan only)
    moon_transit_sep_deg: Optional[float]      # minimum sep in degrees (fine scan only)


# ---------------------------------------------------------------------------
# TLE fetch and cache
# ---------------------------------------------------------------------------

def _fetch_tle_raw(norad_id: int) -> str:
    """
    Fetch the raw 3-line TLE text from Celestrak for the given NORAD ID.
    Raises RuntimeError on any network or format failure.
    """
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 3:
            raise RuntimeError(
                f"Celestrak returned fewer than 3 TLE lines for NORAD {norad_id}"
            )
        log.debug("Fetched fresh TLE for NORAD %d (%d bytes)", norad_id, len(text))
        return text
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Celestrak HTTP {e.code} for NORAD {norad_id}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Celestrak unreachable: {e.reason}") from e


def _get_tle(norad_id: int) -> tuple[str, str, str]:
    """
    Return (name, line1, line2) for *norad_id*.
    Uses a 6-hour disk cache to comply with Celestrak rate limits.
    Raises RuntimeError if the TLE cannot be obtained.
    """
    key    = f"tle|{norad_id}"
    cached = _cache.get(key)
    if cached is not None:
        lines = [l.strip() for l in cached.splitlines() if l.strip()]
        if len(lines) >= 3:
            log.debug("TLE cache hit for NORAD %d", norad_id)
            return lines[0], lines[1], lines[2]

    raw = _fetch_tle_raw(norad_id)
    _cache.set(key, raw, ttl_seconds=_TLE_TTL)
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) < 3:
        raise RuntimeError(f"Malformed TLE for NORAD {norad_id}: {raw!r}")
    return lines[0], lines[1], lines[2]


# ---------------------------------------------------------------------------
# Pass computation helpers
# ---------------------------------------------------------------------------

def _group_passes(times, events) -> list[tuple]:
    """
    Collect complete (rise_t, peak_t, set_t) triplets from find_events() output.

    find_events() uses event codes 0=rise, 1=culmination, 2=set.
    Partial passes at window boundaries (missing rise or set event) are
    silently discarded.
    """
    passes = []
    i, n = 0, len(events)
    while i <= n - 3:
        if events[i] == 0 and events[i + 1] == 1 and events[i + 2] == 2:
            passes.append((times[i], times[i + 1], times[i + 2]))
            i += 3
        else:
            i += 1
    return passes


def _az_alt(satellite, observer, t) -> tuple[float, float]:
    """Return (azimuth_deg, altitude_deg) for *satellite* from *observer* at time *t*."""
    topo       = (satellite - observer).at(t)
    alt, az, _ = topo.altaz()
    return float(az.degrees), float(alt.degrees)


# ---------------------------------------------------------------------------
# Moon proximity — coarse → fine
# ---------------------------------------------------------------------------

def _moon_proximity(satellite, observer, planets, ts, pass_group: tuple) -> dict:
    """
    Compute Moon angular separation across a pass using the coarse→fine approach.

    Stage 1 (always): 10-second vectorised scan across the full pass.
    Stage 2 (conditional): 0.1-second scan in a ±5-second window around the
      coarse minimum — runs only when coarse min < _FINE_TRIGGER (~1°).

    Returns a dict with keys:
        moon_sep_deg        — sep at pass peak; None if Moon is below horizon
        moon_transit        — True if ISS crosses Moon's disc (sep < _MOON_RADIUS)
        moon_transit_time   — UTC datetime of minimum separation (fine scan result)
        moon_transit_sep_deg — minimum angular separation in degrees (fine scan)
    """
    t_rise, t_peak, t_set = pass_group
    earth = planets["earth"]
    moon  = planets["moon"]

    # GCRS position of the ground observer
    observer_pos = earth + observer

    # ── Coarse scan ──────────────────────────────────────────────────────────
    duration_s = (t_set.tt - t_rise.tt) * 86400.0
    n_coarse   = max(3, int(duration_s / _COARSE_STEP) + 1)
    t_arr      = ts.tt_jd(np.linspace(t_rise.tt, t_set.tt, n_coarse))

    sat_topo  = (satellite - observer).at(t_arr)
    moon_app  = observer_pos.at(t_arr).observe(moon).apparent()
    seps      = sat_topo.separation_from(moon_app).degrees   # numpy array

    min_idx    = int(np.argmin(seps))
    coarse_min = float(seps[min_idx])

    # Sep at pass peak and Moon altitude (to check if Moon is up)
    sat_pk    = (satellite - observer).at(t_peak)
    moon_pk   = observer_pos.at(t_peak).observe(moon).apparent()
    sep_pk    = float(sat_pk.separation_from(moon_pk).degrees)
    moon_alt  = float(moon_pk.altaz()[0].degrees)
    sep_out   = round(sep_pk, 1) if moon_alt > 0 else None

    result = {
        "moon_sep_deg":         sep_out,
        "moon_transit":         False,
        "moon_transit_time":    None,
        "moon_transit_sep_deg": None,
    }

    # ── Fine scan ─────────────────────────────────────────────────────────────
    if coarse_min < _FINE_TRIGGER:
        half   = _FINE_HALFWIN / 86400.0
        t_lo   = max(t_rise.tt, float(t_arr[min_idx].tt) - half)
        t_hi   = min(t_set.tt,  float(t_arr[min_idx].tt) + half)
        n_fine = max(3, int((t_hi - t_lo) * 86400.0 / _FINE_STEP) + 1)
        t_fine = ts.tt_jd(np.linspace(t_lo, t_hi, n_fine))

        sat_f  = (satellite - observer).at(t_fine)
        moon_f = observer_pos.at(t_fine).observe(moon).apparent()
        seps_f = sat_f.separation_from(moon_f).degrees

        fi       = int(np.argmin(seps_f))
        fine_min = float(seps_f[fi])

        result["moon_transit_sep_deg"] = round(fine_min, 4)
        result["moon_transit_time"]    = t_fine[fi].utc_datetime()
        if fine_min < _MOON_RADIUS:
            result["moon_transit"] = True

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def satellite_passes(
    lat:      float,
    lon:      float,
    t_start:  datetime,
    t_end:    datetime,
    norad_id: int = ISS_NORAD_ID,
) -> list[SatPass]:
    """
    Return all passes of *norad_id* over (*lat*, *lon*) between *t_start* and *t_end*.

    Passes are included regardless of sunlight status; see SatPass.in_sunlight.
    Moon proximity is computed for every pass.

    Returns an empty list if TLE data is unavailable, Celestrak is unreachable,
    or no passes occur during the window.
    """
    try:
        name, line1, line2 = _get_tle(norad_id)
    except RuntimeError as e:
        log.warning("TLE unavailable for NORAD %d: %s", norad_id, e)
        return []

    try:
        ts       = load.timescale()
        planets  = _load("de421.bsp")
        observer = wgs84.latlon(lat, lon)

        satellite = EarthSatellite(line1, line2, name, ts)
        t0        = ts.from_datetime(t_start)
        t1        = ts.from_datetime(t_end)

        times, events = satellite.find_events(
            observer, t0, t1, altitude_degrees=_MIN_PASS_ALT
        )
        if len(times) == 0:
            return []

        groups  = _group_passes(times, events)
        results = []

        for group in groups:
            t_rise, t_peak, t_set = group

            rise_az, _        = _az_alt(satellite, observer, t_rise)
            peak_az, peak_alt = _az_alt(satellite, observer, t_peak)
            set_az,  _        = _az_alt(satellite, observer, t_set)

            sunlit  = bool(satellite.at(t_peak).is_sunlit(planets))
            dur_min = (t_set.tt - t_rise.tt) * 86400.0 / 60.0

            moon_data = _moon_proximity(satellite, observer, planets, ts, group)

            results.append(SatPass(
                satellite_name       = name,
                rise_time            = t_rise.utc_datetime(),
                peak_time            = t_peak.utc_datetime(),
                set_time             = t_set.utc_datetime(),
                peak_alt_deg         = round(peak_alt, 1),
                peak_az_deg          = round(peak_az,  1),
                rise_az_deg          = round(rise_az,  1),
                set_az_deg           = round(set_az,   1),
                duration_min         = round(dur_min,  1),
                in_sunlight          = sunlit,
                moon_sep_deg         = moon_data["moon_sep_deg"],
                moon_transit         = moon_data["moon_transit"],
                moon_transit_time    = moon_data["moon_transit_time"],
                moon_transit_sep_deg = moon_data["moon_transit_sep_deg"],
            ))

        return results

    except Exception as e:
        log.warning("Satellite pass computation failed: %s", e, exc_info=True)
        return []
