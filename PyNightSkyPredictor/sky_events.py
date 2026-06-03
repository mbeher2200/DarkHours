#!/usr/bin/env python3
"""Sun and moon event calculator for astronomical photography planning."""

import concurrent.futures as _futures
import json
import time as _time
import logging
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

from skyfield.api import Loader, load, wgs84
from skyfield import almanac

from . import cache as _cache

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


# Dark-cycle windows are cached through the Cache port (local files or DynamoDB,
# Per-window DynamoDB keys: dark_cycle|{lat:.3f}|{lon:.3f}|{window_start}.
# Each window is a small independent item so reads are targeted rather than
# loading a single growing blob for all locations.
# Module-level dict acts as an in-process layer — warm containers skip DynamoDB
# entirely for locations already computed in this container's lifetime.
_mem_dark_cycle: dict[str, dict] = {}


def _dark_cycle_db_key(lat: float, lon: float, window_start: date) -> str:
    return f"dark_cycle|{lat:.3f}|{lon:.3f}|{window_start.isoformat()}"


def _compute_dark_hours_cycle(lat: float, lon: float, target_date: date, tz) -> list:
    """Compute dark sky hours for 30 consecutive nights centred on target_date."""
    ts  = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=15)
    d1 = target_date + timedelta(days=17)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    # Three targeted risings_and_settings calls instead of dark_twilight_day:
    #   sun at -0.8333° → Sunrise/Sunset       (step=0.25 vs dark_twilight_day step=0.04)
    #   sun at -18.0°   → Astronomical night begins/ends
    #   moon            → Moonrise/Moonset
    # All three are independent and run in parallel threads.
    sun   = eph["sun"]
    f_hor  = almanac.risings_and_settings(eph, sun,         observer, horizon_degrees=-0.8333)
    f_ast  = almanac.risings_and_settings(eph, sun,         observer, horizon_degrees=-18.0)
    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)

    def _timed(fn, *args):
        t0_ = _time.monotonic()
        result = fn(*args)
        return result, round((_time.monotonic() - t0_) * 1000)

    _t_wall0 = _time.monotonic()
    with _futures.ThreadPoolExecutor(max_workers=3) as _pool:
        _hor_f  = _pool.submit(_timed, almanac.find_discrete, t0, t1, f_hor)
        _ast_f  = _pool.submit(_timed, almanac.find_discrete, t0, t1, f_ast)
        _moon_f = _pool.submit(_timed, almanac.find_discrete, t0, t1, f_moon)
        (hor_times,  hor_rising),  _hor_ms  = _hor_f.result()
        (ast_times,  ast_rising),  _ast_ms  = _ast_f.result()
        (moon_times, moon_rising), _moon_ms = _moon_f.result()
    _wall_ms = round((_time.monotonic() - _t_wall0) * 1000)

    log.info("dark_cycle threads hor=%dms ast=%dms moon=%dms wall=%dms",
             _hor_ms, _ast_ms, _moon_ms, _wall_ms)

    all_events = []
    for t, rising in zip(hor_times, hor_rising):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Sunrise" if rising else "Sunset"})
    for t, rising in zip(ast_times, ast_rising):
        # rising=True  → sun crosses -18° going up   → astronomical night ends
        # rising=False → sun crosses -18° going down  → astronomical night begins
        all_events.append({"time": t.utc_datetime(),
                           "label": "Astronomical night ends" if rising
                           else "Astronomical night begins"})
    for t, rising in zip(moon_times, moon_rising):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Moonrise" if rising else "Moonset"})

    all_events.sort(key=lambda e: e["time"])

    _t_loop0 = _time.monotonic()
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

    log.info("dark_cycle loop=%dms", round((_time.monotonic() - _t_loop0) * 1000))
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

    Three-layer lookup:
      1. Module-level dict (_mem_dark_cycle) — zero-cost on warm containers.
      2. DynamoDB per-window key — shared across containers, no TTL (astronomical
         data is immutable for a given location+window).
      3. Compute fresh via Skyfield (~410ms at 3008 MB), then populate both layers.
    """
    window_start = target_date - timedelta(days=14)
    mem_key      = f"{lat:.3f},{lon:.3f}:{window_start.isoformat()}"
    db_key       = _dark_cycle_db_key(lat, lon, window_start)

    # 1. In-process cache — exact hit
    if mem_key in _mem_dark_cycle:
        log.debug("Dark cycle mem-cache hit (exact) for %s", mem_key)
        return _dark_stats(_mem_dark_cycle[mem_key]["dark_hours"], 14)

    # 1b. In-process cache — overlap: any cached window for this location covers target_date
    loc_prefix = f"{lat:.3f},{lon:.3f}:"
    for mk, entry in _mem_dark_cycle.items():
        if not mk.startswith(loc_prefix):
            continue
        cached_ws  = date.fromisoformat(entry["window_start"])
        cached_end = cached_ws + timedelta(days=len(entry["dark_hours"]) - 1)
        if cached_ws <= target_date <= cached_end:
            tonight_idx = (target_date - cached_ws).days
            log.debug("Dark cycle mem-cache hit (overlap) for %s idx %d", mk, tonight_idx)
            return _dark_stats(entry["dark_hours"], tonight_idx)

    # 2. DynamoDB cache
    cached = _cache.get(db_key)
    if cached:
        log.debug("Dark cycle DynamoDB hit for %s", db_key)
        _mem_dark_cycle[mem_key] = cached
        return _dark_stats(cached["dark_hours"], 14)

    # 3. Compute and persist
    log.debug("Dark cycle cache miss for %s — computing 30-night window", mem_key)
    dark_hours = _compute_dark_hours_cycle(lat, lon, target_date, tz)
    entry = {"window_start": window_start.isoformat(), "dark_hours": dark_hours}
    try:
        _cache.set(db_key, entry)
    except Exception as e:
        log.warning("Dark cycle cache write failed (non-fatal): %s", e)
    _mem_dark_cycle[mem_key] = entry

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
