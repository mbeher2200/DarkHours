#!/usr/bin/env python3
"""Sun and moon event calculator for astronomical photography planning."""

import concurrent.futures as _futures
import json
import threading
import time as _time
import logging
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from skyfield.api import Loader, load, wgs84
from skyfield import almanac

from . import cache as _cache

log = logging.getLogger(__name__)

# The four principal phases are instants (phase angle exactly 0/90/180/270),
# not 45°-wide spans. Label them only within ±half a day of the event —
# ±6.1° at the mean synodic rate of 12.19°/day — matching the almanac
# convention that "Third Quarter" is the day of the quarter, and every other
# day between quarters is crescent/gibbous. (The previous table treated each
# name as a band *starting at* the instant, so e.g. three days after the
# quarter — an obvious waning crescent — still read "Third Quarter".)
PRINCIPAL_PHASES = [
    (0,   "New Moon"),
    (90,  "First Quarter"),
    (180, "Full Moon"),
    (270, "Third Quarter"),
    (360, "New Moon"),
]
INTERMEDIATE_NAMES = ["Waxing Crescent", "Waxing Gibbous", "Waning Gibbous", "Waning Crescent"]
PRINCIPAL_WINDOW_DEG = 6.1


def phase_name_from_angle(angle_deg: float) -> str:
    """Phase name for a sun–moon elongation angle (0° = new, 180° = full)."""
    a = angle_deg % 360.0
    for p_angle, p_name in PRINCIPAL_PHASES:
        if abs(a - p_angle) <= PRINCIPAL_WINDOW_DEG:
            return p_name
    return INTERMEDIATE_NAMES[int(a // 90.0)]


_load = Loader(str(Path(__file__).resolve().parent))


def _ephemeris():
    return _load("de421.bsp")


# In-process cache for sky events (layer 1). Astronomical events for a given
# (lat, lon, date) are immutable — no TTL needed in either layer.
_mem_sky_events: dict[str, list] = {}


def _sky_events_db_key(lat: float, lon: float, d: date) -> str:
    return f"sky_events|{lat:.2f}|{lon:.2f}|{d.isoformat()}"


def _compute_sky_events(lat: float, lon: float, target_date: date) -> list:
    """Compute sky events via Skyfield (~80–170 ms). Called only on cache miss."""
    ts  = load.timescale()
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


def sky_events(lat: float, lon: float, target_date: date) -> list:
    """
    Return all sky events in a 3-day window around target_date.

    Two-layer cache (in-process dict → DynamoDB) with no TTL: astronomical event
    times are deterministic and immutable for a given (lat, lon, date).
    Warm-container repeat queries return in ~0 ms; cross-container or post-restart
    queries return in ~5 ms (DynamoDB); first-ever hit computes in 80–170 ms.
    """
    db_key  = _sky_events_db_key(lat, lon, target_date)

    # Layer 1: in-process — zero cost on a warm container
    if db_key in _mem_sky_events:
        return _mem_sky_events[db_key]

    # Layer 2: DynamoDB — ~5 ms, shared across containers
    cached = _cache.get(db_key)
    if cached:
        events = [
            {"time": datetime.fromisoformat(e["time"]), "label": e["label"]}
            for e in cached["events"]
        ]
        _mem_sky_events[db_key] = events
        return events

    # Layer 3: compute via Skyfield
    _ephemeris()  # ensure load/wgs84/almanac are initialised
    events = _compute_sky_events(lat, lon, target_date)

    serialised = [{"time": e["time"].isoformat(), "label": e["label"]} for e in events]
    try:
        _cache.set(db_key, {"events": serialised})
    except Exception as exc:
        log.warning("sky_events cache write failed (non-fatal): %s", exc)
    _mem_sky_events[db_key] = events
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
# Per-window DynamoDB keys: dark_cycle|{lat:.2f}|{lon:.2f}|{window_start}.
# Each window is a small independent item so reads are targeted rather than
# loading a single growing blob for all locations.
# Module-level dict acts as an in-process layer — warm containers skip DynamoDB
# entirely for locations already computed in this container's lifetime.
_mem_dark_cycle: dict[str, dict] = {}

# Per-location locks serialize the expensive Skyfield compute path in
# lunar_cycle_dark_analysis. Without this, a /calendar request dispatches
# ~20 nights at once to a shared thread pool, and every one of them misses
# the still-empty cache and redundantly computes its own ~30-night window
# (profiling: 20/30 calls paying the full ~3s cost instead of ~1-3). The
# lock lets concurrent callers wait for an in-flight computation and reuse
# it via the overlap check below, instead of racing past it.
_dark_cycle_locks: dict[str, threading.Lock] = {}
_dark_cycle_locks_guard = threading.Lock()


def _dark_cycle_lock_for(lat: float, lon: float) -> threading.Lock:
    key = f"{lat:.2f},{lon:.2f}"
    with _dark_cycle_locks_guard:
        lock = _dark_cycle_locks.get(key)
        if lock is None:
            lock = _dark_cycle_locks[key] = threading.Lock()
        return lock


def _dark_cycle_overlap_hit(loc_prefix: str, target_date: date) -> dict | None:
    """Dark stats from any cached window for this location that covers target_date."""
    for mk, entry in _mem_dark_cycle.items():
        if not mk.startswith(loc_prefix):
            continue
        cached_ws  = date.fromisoformat(entry["window_start"])
        cached_end = cached_ws + timedelta(days=len(entry["nights"]) - 1)
        if cached_ws <= target_date <= cached_end:
            tonight_idx = (target_date - cached_ws).days
            log.debug("Dark cycle mem-cache hit (overlap) for %s idx %d", mk, tonight_idx)
            return _dark_stats(entry["nights"], tonight_idx)
    return None


def _dark_cycle_db_key(lat: float, lon: float, window_start: date) -> str:
    # v2: each night is a record (sunset/sunrise/night_start/night_end/dark_hours)
    # instead of a bare dark-hours float, so assemble_night()'s calendar path can
    # derive moon_score/weather-windowing inputs from this window instead of an
    # independent sky_events() call.
    # v3: same shape, but twilight boundaries are now exact per night. v2 windows
    # computed from a shoulder-season target_date were poisoned (every night
    # zeroed when tonight lacked astronomical night — see _compute_dark_hours_cycle),
    # and with no TTL they'd be served forever; the bump orphans them all at once
    # (scripts/purge_dark_cycle_v2.py deletes the orphaned DynamoDB items).
    return f"dark_cycle_v3|{lat:.2f}|{lon:.2f}|{window_start.isoformat()}"


def _nights_to_json(nights: list[dict]) -> list[dict]:
    """Serialize a per-night dark-cycle window for cache storage (datetimes -> ISO strings)."""
    def _iso(t):
        return t.isoformat() if t is not None else None
    return [
        {
            "sunset":      _iso(n["sunset"]),
            "sunrise":     _iso(n["sunrise"]),
            "night_start": _iso(n["night_start"]),
            "night_end":   _iso(n["night_end"]),
            "dark_hours":  n["dark_hours"],
        }
        for n in nights
    ]


def _nights_from_json(raw: list[dict]) -> list[dict]:
    """Restore a per-night dark-cycle window from cache storage (ISO strings -> datetimes)."""
    def _dt(s):
        if s is None:
            return None
        t = datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    return [
        {
            "sunset":      _dt(n["sunset"]),
            "sunrise":     _dt(n["sunrise"]),
            "night_start": _dt(n["night_start"]),
            "night_end":   _dt(n["night_end"]),
            "dark_hours":  n["dark_hours"],
        }
        for n in raw
    ]


def _compute_dark_hours_cycle(lat: float, lon: float, target_date: date, tz) -> list:
    """Compute dark sky hours for 30 consecutive nights centred on target_date.

    Two parallel find_discrete calls cover the full 32-day window: one
    dark_twilight_day search yields sunset/sunrise (phase 3↔4, the same
    −0.8333° boundary the old sun risings_and_settings search used — verified
    identical to the second) *and* per-night astronomical-night boundaries
    (phase 0↔1); the other covers moonrise/moonset.

    Twilight boundaries are exact per night, not extrapolated. An earlier
    version derived them from tonight's sky_events as a fixed offset applied
    to all 30 nights; that broadcast turned "no astronomical night *tonight*"
    into "no darkness for the whole window", zeroing real dark nights at
    shoulder season (high-lat late summer, where darkness returns mid-window)
    — and the poisoned windows were then served to every other date they
    covered via the no-TTL caches. dark_twilight_day samples at 0.04 days
    (~58 min), which also catches the sub-hour astro-night dips right at the
    seasonal transition that a default 0.25-day risings_and_settings step
    provably skips (4 consecutive real nights missed at 55°N, Aug 2026).

    Cost: the twilight search is ~35 ms dearer than the old sun-horizon
    search, but drops the internal sky_events() dependency, and runs once per
    (location, window) before both cache layers make it permanent.
    """
    ts  = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=15)
    d1 = target_date + timedelta(days=17)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    f_twilight = almanac.dark_twilight_day(eph, observer)
    f_moon     = almanac.risings_and_settings(eph, eph["moon"], observer)

    def _timed(fn, *args):
        t0_ = _time.monotonic()
        result = fn(*args)
        return result, round((_time.monotonic() - t0_) * 1000)

    _t_wall0 = _time.monotonic()
    with _futures.ThreadPoolExecutor(max_workers=2) as _pool:
        _tw_f   = _pool.submit(_timed, almanac.find_discrete, t0, t1, f_twilight)
        _moon_f = _pool.submit(_timed, almanac.find_discrete, t0, t1, f_moon)
        (tw_times,   tw_phases),   _tw_ms   = _tw_f.result()
        (moon_times, moon_rising), _moon_ms = _moon_f.result()
    _wall_ms = round((_time.monotonic() - _t_wall0) * 1000)

    log.info("dark_cycle threads twilight=%dms moon=%dms wall=%dms",
             _tw_ms, _moon_ms, _wall_ms)

    all_events = []
    # Phase 3↔4 transitions are sunset/sunrise; 0↔1 are astronomical-night
    # boundaries. The very first transition after t0 has no predecessor to
    # classify it and is dropped — harmless, because the earliest event the
    # night loop consumes (sunset of target−14) lies ≥29 h into the window,
    # with the full morning twilight ladder of target−15 in between. Same
    # convention as _compute_sky_events.
    prev = None
    for t, phase in zip(tw_times, tw_phases):
        if prev is not None:
            pair = {int(phase), int(prev)}
            if pair == {3, 4}:
                all_events.append({"time": t.utc_datetime(),
                                   "label": "Sunrise" if phase == 4 else "Sunset"})
            elif pair == {0, 1}:
                all_events.append({"time": t.utc_datetime(),
                                   "label": "Astronomical night begins" if phase == 0
                                            else "Astronomical night ends"})
        prev = phase
    for t, rising in zip(moon_times, moon_rising):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Moonrise" if rising else "Moonset"})

    all_events.sort(key=lambda e: e["time"])

    _t_loop0 = _time.monotonic()
    nights = []
    for offset in range(-14, 16):
        night_date = target_date + timedelta(days=offset)
        sunset = next(
            (e["time"] for e in all_events
             if e["label"] == "Sunset"
             and e["time"].astimezone(tz).date() == night_date),
            None,
        )
        sunrise = find_event(all_events, "Sunrise", after=sunset) if sunset else None

        night_start = night_end = None
        dark_hours  = 0.0
        if sunset and sunrise:
            night_start = find_event(all_events, "Astronomical night begins",
                                     after=sunset, before=sunrise)
            if night_start is not None:
                night_end = find_event(all_events, "Astronomical night ends",
                                       after=night_start, before=sunrise)
            if night_start is not None and night_end is None:
                # The sun must recross −18° before it can reach the horizon, so
                # a begins without an ends means clipped input, not geometry.
                log.warning("dark_cycle: unpaired astro-night begins for %s at offset %+d",
                            night_date, offset)
                night_start = None
            if night_start and night_end:
                intervals  = dark_moon_intervals(all_events, night_start, night_end)
                total_secs = sum((e - s).total_seconds() for s, e in intervals)
                dark_hours = total_secs / 3600

        nights.append({
            "sunset":      sunset,
            "sunrise":     sunrise,
            "night_start": night_start,
            "night_end":   night_end,
            "dark_hours":  dark_hours,
        })

    log.info("dark_cycle loop=%dms", round((_time.monotonic() - _t_loop0) * 1000))
    return nights


def _dark_stats(nights: list, tonight_idx: int) -> dict:
    """Derive mean, stdev, and ratio-to-maximum score from a dark-hours window.

    Score = tonight / cycle_max × 10.  The best night of the cycle earns 10;
    every other night scales linearly from there.  Zero dark hours = 0.

    Also returns a read-only copy of the requested night's own record (sunset/
    sunrise/night_start/night_end/dark_hours) so assemble_night()'s calendar
    path can derive moon_score/weather-windowing inputs from it instead of an
    independent sky_events() call. A copy, not a reference — nights[tonight_idx]
    may be a live entry inside the shared _mem_dark_cycle cache.
    """
    dark_hours   = [n["dark_hours"] for n in nights]
    tonight_rec  = dict(nights[tonight_idx])
    tonight      = dark_hours[tonight_idx]
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
        "tonight":       tonight_rec,
    }


def lunar_cycle_dark_analysis(lat: float, lon: float, target_date: date, tz) -> dict:
    """
    Return dark sky stats for a 30-night window centred on target_date.

    Three-layer lookup:
      1. Module-level dict (_mem_dark_cycle) — zero-cost on warm containers,
         including an overlap check: any cached window for this location that
         covers target_date is reused, not just an exact window_start match.
      2. DynamoDB per-window key — shared across containers, no TTL (astronomical
         data is immutable for a given location+window).
      3. Compute fresh via Skyfield (~2-3s at 3008 MB — see scripts/profile_calendar.py),
         then populate both layers.

    Steps 2-3 run under a per-location lock (_dark_cycle_lock_for) so concurrent
    callers for overlapping windows wait for an in-flight computation and reuse
    it via the overlap check, rather than each redundantly computing their own.
    """
    window_start = target_date - timedelta(days=14)
    mem_key      = f"{lat:.2f},{lon:.2f}:{window_start.isoformat()}"
    db_key       = _dark_cycle_db_key(lat, lon, window_start)
    loc_prefix   = f"{lat:.2f},{lon:.2f}:"

    # 1. In-process cache — exact hit
    if mem_key in _mem_dark_cycle:
        log.debug("Dark cycle mem-cache hit (exact) for %s", mem_key)
        return _dark_stats(_mem_dark_cycle[mem_key]["nights"], 14)

    # 1b. In-process cache — overlap
    hit = _dark_cycle_overlap_hit(loc_prefix, target_date)
    if hit is not None:
        return hit

    with _dark_cycle_lock_for(lat, lon):
        # Re-check — a concurrent caller for this location may have just
        # finished and populated the cache while we were waiting for the lock.
        if mem_key in _mem_dark_cycle:
            log.debug("Dark cycle mem-cache hit (exact, post-lock) for window starting %s", window_start.isoformat())
            return _dark_stats(_mem_dark_cycle[mem_key]["nights"], 14)
        hit = _dark_cycle_overlap_hit(loc_prefix, target_date)
        if hit is not None:
            return hit

        # 2. DynamoDB cache — stored as ISO strings (json.dumps can't handle
        # raw datetimes), so parse back into real datetimes before caching
        # in-process or handing off to _dark_stats.
        cached = _cache.get(db_key)
        if cached:
            log.debug("Dark cycle DynamoDB hit for window starting %s", window_start.isoformat())
            nights = _nights_from_json(cached["nights"])
            _mem_dark_cycle[mem_key] = {"window_start": cached["window_start"], "nights": nights}
            return _dark_stats(nights, 14)

        # 3. Compute and persist
        log.debug("Dark cycle cache miss — computing 30-night window starting %s", window_start.isoformat())
        nights = _compute_dark_hours_cycle(lat, lon, target_date, tz)
        entry = {"window_start": window_start.isoformat(), "nights": nights}
        try:
            _cache.set(db_key, {"window_start": window_start.isoformat(),
                                "nights": _nights_to_json(nights)})
        except Exception as e:
            log.warning("Dark cycle cache write failed (non-fatal): %s", e)
        _mem_dark_cycle[mem_key] = entry

        return _dark_stats(nights, 14)


def moon_phase_info(at_utc: object) -> tuple:
    """Return (phase_name, illumination_pct) at the given UTC datetime."""
    ts  = load.timescale()
    eph = _ephemeris()
    t   = ts.from_datetime(at_utc)
    angle        = almanac.moon_phase(eph, t).degrees
    illumination = round((1 - math.cos(math.radians(angle))) / 2 * 100, 1)
    phase_name   = phase_name_from_angle(angle)
    log.debug("Moon phase angle: %.2f°  →  %s  (%.1f%% illuminated)", angle, phase_name, illumination)
    return phase_name, illumination


def moon_altitude_track(lat: float, lon: float, times_utc: list) -> list[float]:
    """Return moon altitude in degrees at each UTC datetime in times_utc.

    Requires the de421.bsp ephemeris (eph marker). Used by milky_way_arch_summary
    to build per-sample moon altitude for best-viewing-time scoring.
    """
    import numpy as np
    ts       = load.timescale()
    eph      = _ephemeris()
    observer = wgs84.latlon(lat, lon)
    t_arr    = ts.from_datetimes(times_utc)
    alt_arr, _, _ = observer.at(t_arr).observe(eph["moon"]).apparent().altaz()
    return list(alt_arr.degrees)


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
