#!/usr/bin/env python3
"""
Lunar distance and eclipse calculations.

Public API
----------
SUPERMOON_KM        float — distance threshold for supermoon classification
MICROMOON_KM        float — distance threshold for micromoon classification

moon_distance_km(at_utc)                            -> float
find_lunar_eclipses(t_start, t_end)                 -> list[dict]
eclipses_for_night(sunset, sunrise)                 -> list[dict]
classify_full_moon(illumination_pct, distance_km)   -> str | None
"""

import logging
from datetime import date, timedelta
from pathlib import Path

from skyfield.api import Loader, load
from skyfield import eclipselib

log = logging.getLogger(__name__)

_load = Loader(str(Path(__file__).resolve().parent))

SUPERMOON_KM = 362_000.0
MICROMOON_KM = 400_500.0

_ECLIPSE_KIND = {0: "penumbral", 1: "partial", 2: "total"}


def _ephemeris():
    return _load("de421.bsp")


def moon_distance_km(at_utc) -> float:
    """Return Earth-Moon distance in km at the given UTC datetime."""
    ts  = load.timescale()
    eph = _ephemeris()
    t   = ts.from_datetime(at_utc)
    return eph["earth"].at(t).observe(eph["moon"]).distance().km


def find_lunar_eclipses(t_start: date, t_end: date) -> list[dict]:
    """
    Return lunar eclipse events in [t_start, t_end].

    Each dict:
      time                  datetime  — UTC timezone-aware (mid-eclipse)
      kind                  str       — 'penumbral', 'partial', or 'total'
      penumbral_magnitude   float
      umbral_magnitude      float
    """
    ts  = load.timescale()
    eph = _ephemeris()

    t0 = ts.utc(t_start.year, t_start.month, t_start.day)
    t1 = ts.utc(t_end.year,   t_end.month,   t_end.day)

    times, codes, details = eclipselib.lunar_eclipses(t0, t1, eph)

    events = []
    for i in range(len(times)):
        events.append({
            "time":                times[i].utc_datetime(),
            "kind":                _ECLIPSE_KIND.get(int(codes[i]), "penumbral"),
            "penumbral_magnitude": round(float(details["penumbral_magnitude"][i]), 3),
            "umbral_magnitude":    round(float(details["umbral_magnitude"][i]),    3),
        })

    log.debug("Lunar eclipses %s → %s: %d events", t_start, t_end, len(events))
    return events


def eclipses_for_night(sunset, sunrise) -> list[dict]:
    """
    Return any lunar eclipses whose mid-eclipse falls within [sunset, sunrise].

    sunset / sunrise — UTC-aware datetimes bounding the night.
    """
    search_start = date(sunset.year,  sunset.month,  sunset.day)
    search_end   = date(sunrise.year, sunrise.month, sunrise.day) + timedelta(days=1)
    candidates   = find_lunar_eclipses(search_start, search_end)
    return [e for e in candidates if sunset <= e["time"] <= sunrise]


def classify_full_moon(illumination_pct: float, distance_km: float) -> str | None:
    """
    Return 'supermoon', 'micromoon', or None.

    Only applies near full moon (illumination ≥ 98 %).
    Supermoon : full moon at distance ≤ SUPERMOON_KM.
    Micromoon : full moon at distance ≥ MICROMOON_KM.
    """
    if illumination_pct < 98.0:
        return None
    if distance_km <= SUPERMOON_KM:
        return "supermoon"
    if distance_km >= MICROMOON_KM:
        return "micromoon"
    return None
