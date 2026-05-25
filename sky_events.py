#!/usr/bin/env python3
"""Sun and moon event calculator for astronomical photography planning."""

import json
import logging
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

from skyfield.api import Loader, load, wgs84
from skyfield import almanac

log = logging.getLogger(__name__)

PHASE_NAMES = [
    (0,   "New Moon"),
    (45,  "Waxing Crescent"),
    (90,  "First Quarter"),
    (135, "Waxing Gibbous"),
    (180, "Full Moon"),
    (225, "Waning Gibbous"),
    (270, "Third Quarter"),
    (315, "Waning Crescent"),
]


_load = Loader(str(Path(__file__).resolve().parent))


def _ephemeris():
    return _load("de421.bsp")


def sky_events(lat: float, lon: float, target_date: date) -> list:
    """
    Return all sky events in a 3-day window around target_date.

    Searching 3 days ensures the full night is captured regardless of UTC offset.
    Each event is a dict with 'time' (UTC timezone-aware datetime) and 'label'.
    """
    ts = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=1)
    d1 = target_date + timedelta(days=2)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    events = []

    # Sunrise / sunset
    f_sun = almanac.sunrise_sunset(eph, observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_sun)):
        events.append({"time": t.utc_datetime(), "label": "Sunrise" if rising else "Sunset"})

    # Moonrise / moonset
    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_moon)):
        events.append({"time": t.utc_datetime(), "label": "Moonrise" if rising else "Moonset"})

    # Night / astronomical twilight boundaries only
    f_twilight = almanac.dark_twilight_day(eph, observer)
    times_tw, phases_tw = almanac.find_discrete(t0, t1, f_twilight)
    for i, (t, phase) in enumerate(zip(times_tw, phases_tw)):
        prev = phases_tw[i - 1] if i > 0 else None
        if prev is not None and {int(phase), int(prev)} == {0, 1}:
            label = "Astronomical night begins" if phase == 0 else "Astronomical night ends"
            events.append({"time": t.utc_datetime(), "label": label})

    events.sort(key=lambda e: e["time"])
    log.debug("Raw events (UTC) over 3-day window:")
    for e in events:
        log.debug("  %s  %s", e["time"].strftime("%Y-%m-%d %H:%M"), e["label"])
    return events


def dark_moon_intervals(events: list, night_start, night_end) -> list:
    """Return (start, end) UTC datetime pairs when moon is below horizon within [night_start, night_end]."""
    log.debug("Night window (UTC): %s → %s", night_start.strftime("%H:%M"), night_end.strftime("%H:%M"))

    moon_events = [(e["time"], e["label"]) for e in events
                   if e["label"] in ("Moonrise", "Moonset")]

    moon_up = False
    for t, label in reversed(moon_events):
        if t <= night_start:
            moon_up = (label == "Moonrise")
            break

    intervals = []
    cursor = night_start
    for t, label in moon_events:
        if t <= night_start or t >= night_end:
            continue
        if not moon_up:
            intervals.append((cursor, t))
        cursor = t
        moon_up = (label == "Moonrise")

    if not moon_up:
        intervals.append((cursor, night_end))

    log.debug("Moon up at night start: %s", moon_up)
    log.debug("Dark intervals (UTC): %s", [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in intervals])
    return intervals


_DARK_CYCLE_CACHE = Path.home() / ".pynightsky-predictor" / "dark_cycle_cache.json"


def _load_dark_cycle_cache() -> dict:
    try:
        return json.loads(_DARK_CYCLE_CACHE.read_text()) if _DARK_CYCLE_CACHE.exists() else {}
    except Exception:
        return {}


def _save_dark_cycle_cache(cache: dict):
    _DARK_CYCLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _DARK_CYCLE_CACHE.write_text(json.dumps(cache, indent=2))


def _compute_dark_hours_cycle(lat: float, lon: float, target_date: date, tz) -> list:
    """Compute dark sky hours for 30 consecutive nights centred on target_date."""
    ts  = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=15)
    d1 = target_date + timedelta(days=17)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    all_events = []
    f_sun = almanac.sunrise_sunset(eph, observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_sun)):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Sunrise" if rising else "Sunset"})

    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_moon)):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Moonrise" if rising else "Moonset"})

    f_tw = almanac.dark_twilight_day(eph, observer)
    times_tw, phases_tw = almanac.find_discrete(t0, t1, f_tw)
    for i, (t, phase) in enumerate(zip(times_tw, phases_tw)):
        prev = phases_tw[i - 1] if i > 0 else None
        if prev is not None and {int(phase), int(prev)} == {0, 1}:
            label = "Astronomical night begins" if phase == 0 else "Astronomical night ends"
            all_events.append({"time": t.utc_datetime(), "label": label})

    all_events.sort(key=lambda e: e["time"])

    hours = []
    for offset in range(-14, 16):
        night_date = target_date + timedelta(days=offset)
        sunset = next(
            (e["time"] for e in all_events
             if e["label"] == "Sunset"
             and e["time"].astimezone(tz).date() == night_date),
            None,
        )
        if not sunset:
            hours.append(0.0)
            continue
        sunrise = find_event(all_events, "Sunrise", after=sunset)
        if not sunrise:
            hours.append(0.0)
            continue
        night_start = find_event(all_events, "Astronomical night begins", after=sunset, before=sunrise)
        night_end   = find_event(all_events, "Astronomical night ends", after=night_start or sunset, before=sunrise)
        if not night_start or not night_end:
            hours.append(0.0)
            continue
        intervals  = dark_moon_intervals(all_events, night_start, night_end)
        total_secs = sum((e - s).total_seconds() for s, e in intervals)
        hours.append(total_secs / 3600)

    return hours


def _dark_stats(dark_hours: list, tonight_idx: int) -> dict:
    """Derive mean, stdev, and ratio-to-maximum score from a dark-hours array.

    Score = tonight / cycle_max × 10.  The best night of the cycle earns 10;
    every other night scales linearly from there.  Zero dark hours = 0.
    """
    tonight = dark_hours[tonight_idx]
    mean_h  = statistics.mean(dark_hours)
    stdev_h = statistics.stdev(dark_hours) if len(dark_hours) > 1 else 0.0
    max_h   = max(dark_hours)
    score   = (tonight / max_h * 10) if max_h > 0 else 0.0
    log.debug("Dark cycle: tonight=%.2fh  mean=%.2fh  stdev=%.2fh  max=%.2fh  score=%.1f",
              tonight, mean_h, stdev_h, max_h, score)
    return {
        "tonight_hours": round(tonight, 2),
        "mean_hours":    round(mean_h,  1),
        "stdev_hours":   round(stdev_h, 1),
        "score":         round(min(10.0, max(0.0, score)), 1),
    }


def lunar_cycle_dark_analysis(lat: float, lon: float, target_date: date, tz) -> dict:
    """
    Return dark sky stats for a 30-night window centred on target_date.

    Cache keys are "lat,lon:window_start" so each 30-night window is stored
    independently — different lunar cycles for the same location never
    overwrite each other.

    Lookup strategy:
      1. Try the ideal key (window centred on target_date).
      2. Scan for any cached window that already contains target_date and
         reuse it with the correct index (avoids recomputing overlapping
         windows for nearby dates).
      3. Compute fresh, store under the ideal key, and return.
    """
    loc_prefix   = f"{lat:.3f},{lon:.3f}"
    window_start = target_date - timedelta(days=14)
    ideal_key    = f"{loc_prefix}:{window_start.isoformat()}"

    cache = _load_dark_cycle_cache()

    # 1. Exact hit — target is the centre of a cached window
    if ideal_key in cache:
        entry = cache[ideal_key]
        log.debug("Dark cycle cache hit (exact) for %s", ideal_key)
        return _dark_stats(entry["dark_hours"], 14)

    # 2. Target falls inside another cached window for this location
    for key, entry in cache.items():
        if not key.startswith(loc_prefix + ":"):
            continue
        cached_start = date.fromisoformat(entry["window_start"])
        cached_end   = cached_start + timedelta(days=len(entry["dark_hours"]) - 1)
        if cached_start <= target_date <= cached_end:
            tonight_idx = (target_date - cached_start).days
            log.debug("Dark cycle cache hit (overlap) for %s (window %s, idx %d)",
                      loc_prefix, cached_start, tonight_idx)
            return _dark_stats(entry["dark_hours"], tonight_idx)

    # 3. Cache miss — compute and store under the ideal key
    log.debug("Dark cycle cache miss for %s — computing 30-night window", loc_prefix)
    dark_hours = _compute_dark_hours_cycle(lat, lon, target_date, tz)

    cache[ideal_key] = {
        "window_start": window_start.isoformat(),
        "dark_hours":   dark_hours,
    }
    _save_dark_cycle_cache(cache)

    return _dark_stats(dark_hours, 14)


def moon_phase_info(at_utc: object) -> tuple:
    """Return (phase_name, illumination_pct) at the given UTC datetime."""
    ts  = load.timescale()
    eph = _ephemeris()
    t   = ts.from_datetime(at_utc)
    angle        = almanac.moon_phase(eph, t).degrees
    illumination = round((1 - math.cos(math.radians(angle))) / 2 * 100, 1)
    phase_name   = next(name for thresh, name in reversed(PHASE_NAMES) if angle >= thresh)
    log.debug("Moon phase angle: %.2f°  →  %s  (%.1f%% illuminated)", angle, phase_name, illumination)
    return phase_name, illumination


def find_event(events: list, label: str, after=None, before=None):
    """Return the first event matching label within the optional time bounds."""
    for e in events:
        if e["label"] != label:
            continue
        if after  is not None and e["time"] <= after:
            continue
        if before is not None and e["time"] >= before:
            continue
        return e["time"]
    return None


def find_last_event(events: list, label: str, before):
    """Return the last event matching label that occurs before a given time."""
    match = None
    for e in events:
        if e["label"] == label and e["time"] < before:
            match = e["time"]
    return match
