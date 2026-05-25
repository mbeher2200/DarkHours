#!/usr/bin/env python3
"""
Lunar distance, perigee/apogee, and eclipse event calculations.

Public API
----------
SUPERMOON_KM          float — distance threshold for supermoon classification
MICROMOON_KM          float — distance threshold for micromoon classification
MEAN_DISTANCE_KM      float — mean Earth-Moon distance

moon_distance_km(at_utc)                            -> float
find_perigees_apogees(t_start, t_end)               -> list[dict]
find_lunar_eclipses(t_start, t_end)                 -> list[dict]
moon_events_for_night(target, sunset, sunrise)      -> list[dict]
classify_full_moon(illumination_pct, distance_km)   -> str | None
"""

import logging
from datetime import date, timedelta
from pathlib import Path

from skyfield.api import Loader, load
from skyfield import eclipselib
from skyfield.searchlib import find_minima, find_maxima

log = logging.getLogger(__name__)

_load = Loader(str(Path(__file__).resolve().parent))

SUPERMOON_KM     = 362_000.0
MICROMOON_KM     = 400_500.0
MEAN_DISTANCE_KM = 384_400.0

_ECLIPSE_KIND = {0: "penumbral", 1: "partial", 2: "total"}


def _ephemeris():
    return _load("de421.bsp")


def moon_distance_km(at_utc) -> float:
    """Return Earth-Moon distance in km at the given UTC datetime."""
    ts  = load.timescale()
    eph = _ephemeris()
    t   = ts.from_datetime(at_utc)
    return eph["earth"].at(t).observe(eph["moon"]).distance().km


def _dedup_events(events: list) -> list:
    """
    Remove near-duplicate events of the same kind produced by find_minima /
    find_maxima at search-window boundaries.

    Two same-kind events within 20 days are merged into the more extreme one:
      perigee  → smallest distance_km
      apogee   → largest distance_km
    """
    deduped: list = []
    for ev in events:
        matched = False
        for i, existing in enumerate(deduped):
            if existing["kind"] != ev["kind"]:
                continue
            sep_days = abs((ev["time"] - existing["time"]).total_seconds()) / 86400
            if sep_days < 20.0:
                if ev["kind"] == "perigee" and ev["distance_km"] < existing["distance_km"]:
                    deduped[i] = ev
                elif ev["kind"] == "apogee" and ev["distance_km"] > existing["distance_km"]:
                    deduped[i] = ev
                matched = True
                break
        if not matched:
            deduped.append(ev)

    deduped.sort(key=lambda e: e["time"])
    return deduped


def find_perigees_apogees(t_start: date, t_end: date) -> list[dict]:
    """
    Return perigee and apogee events in [t_start, t_end].

    Each dict:
      time         datetime  — UTC timezone-aware
      kind         str       — 'perigee' or 'apogee'
      distance_km  float     — Earth-Moon distance at the event
    """
    ts  = load.timescale()
    eph = _ephemeris()

    t0 = ts.utc(t_start.year, t_start.month, t_start.day)
    t1 = ts.utc(t_end.year,   t_end.month,   t_end.day)

    earth = eph["earth"]
    moon  = eph["moon"]

    def moon_dist(t):
        return earth.at(t).observe(moon).distance().km

    moon_dist.step_days = 5.0

    events = []

    times_p, dists_p = find_minima(t0, t1, moon_dist)
    for t, d in zip(times_p, dists_p):
        events.append({"time": t.utc_datetime(), "kind": "perigee",
                        "distance_km": round(d)})

    times_a, dists_a = find_maxima(t0, t1, moon_dist)
    for t, d in zip(times_a, dists_a):
        events.append({"time": t.utc_datetime(), "kind": "apogee",
                        "distance_km": round(d)})

    events.sort(key=lambda e: e["time"])
    events = _dedup_events(events)

    log.debug("Perigees/apogees %s → %s: %d events", t_start, t_end, len(events))
    return events


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


def moon_events_for_night(
    target: date,
    sunset,   # UTC-aware datetime  (unused — kept for API compatibility)
    sunrise,  # UTC-aware datetime  (unused — kept for API compatibility)
) -> list[dict]:
    """
    Return moon events relevant to a given night.

    Both perigee/apogee and lunar eclipses use the same ±3-day proximity
    window around target_date.  This ensures an eclipse that occurs a day or
    two before or after the observed night (and thus outside the sunset→sunrise
    window) is still surfaced to the user as an upcoming / recent event.

    Each dict has at minimum:
      time   datetime  — UTC timezone-aware
      kind   str       — 'perigee' | 'apogee' | 'penumbral' | 'partial' | 'total'
    Plus kind-specific keys (distance_km; penumbral_magnitude / umbral_magnitude).
    """
    prox_start = target - timedelta(days=3)
    prox_end   = target + timedelta(days=4)

    peri_apo = find_perigees_apogees(prox_start, prox_end)
    eclipses  = find_lunar_eclipses(prox_start, prox_end)

    events = sorted(peri_apo + eclipses, key=lambda e: e["time"])
    log.debug(
        "Moon events for night %s: %d total (%d proximity, %d eclipse)",
        target, len(events), len(peri_apo), len(eclipses),
    )
    return events


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
