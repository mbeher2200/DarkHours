#!/usr/bin/env python3
"""
Compare dark-hours scoring approaches across 6 moon phases.

  Brute  – current: two bulk find_discrete (parallel, ~110ms) + 30 narrow
            find_discrete calls for per-night astronomical twilight (~302ms)
  A      – keep bulk find_discrete for accurate sun/moon times; replace
            per-night twilight search with a fixed offset from tonight's
            sky_events (already cached)
  B      – full extrapolation from tonight's sky_events — moon drifts by
            one lunar day (24h 50m 28s) per night, sunset drifts by one
            solar day; zero Skyfield event searches

Tests 6 dates evenly spaced across one lunar cycle (~5 days apart), run locally.
Requires: de421.bsp in darkhours/ and a primed local file cache
          (sky_events will be cold on first run, warm on repeat runs).

Usage:
  source .venv/bin/activate
  python scripts/bench_dark_cycle_heuristic.py
"""

import concurrent.futures
import sys
import time
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo
from skyfield import almanac
from skyfield.api import load, wgs84

sys.path.insert(0, ".")
from darkhours import sky_events as se
from darkhours.sky_events import (
    _compute_dark_hours_cycle,
    _dark_stats,
    dark_moon_intervals,
    find_event,
)

# ── Locations ─────────────────────────────────────────────────────────────────
LOCS = [
    ("Denver  CO", 39.74, -104.98, ZoneInfo("America/Denver")),
    ("Seattle WA", 47.61, -122.33, ZoneInfo("America/Los_Angeles")),
    ("Miami   FL", 25.77,  -80.19, ZoneInfo("America/New_York")),
]

# ── 6 dates evenly across one lunar cycle ─────────────────────────────────────
BASE  = date(2026, 6, 27)
DATES = [BASE + timedelta(days=i * 5) for i in range(6)]

# Moon rises/sets ~50m 28s later each night on average
LUNAR_DAY_SECS = 24 * 3600 + 50 * 60 + 28


# ── Helpers ───────────────────────────────────────────────────────────────────

def moon_phase_label(d: date) -> str:
    ts  = load.timescale()
    eph = se._ephemeris()
    deg = almanac.moon_phase(eph, ts.utc(d.year, d.month, d.day, 12)).degrees
    labels = [
        (22.5,  "New"),
        (67.5,  "Wax Crescent"),
        (112.5, "First Quarter"),
        (157.5, "Wax Gibbous"),
        (202.5, "Full"),
        (247.5, "Wan Gibbous"),
        (292.5, "Last Quarter"),
        (337.5, "Wan Crescent"),
    ]
    for threshold, name in labels:
        if deg < threshold:
            return name
    return "New"


def max_drift(brute_hrs, other_hrs):
    return max(abs(a - b) for a, b in zip(brute_hrs, other_hrs))


# ── Approach: Brute (current) ─────────────────────────────────────────────────

def run_brute(lat, lon, tz, d):
    t0 = time.monotonic()
    hrs = _compute_dark_hours_cycle(lat, lon, d, tz)
    ms  = round((time.monotonic() - t0) * 1000)
    return _dark_stats(hrs, 14), hrs, ms


# ── Approach A: bulk find_discrete + fixed twilight offset ────────────────────

def run_a(lat, lon, tz, target_date):
    t0 = time.monotonic()

    eph      = se._ephemeris()
    ts       = load.timescale()
    observer = wgs84.latlon(lat, lon)

    d0    = target_date - timedelta(days=15)
    d1    = target_date + timedelta(days=17)
    t0_sf = ts.utc(d0.year, d0.month, d0.day)
    t1_sf = ts.utc(d1.year, d1.month, d1.day)

    f_hor  = almanac.risings_and_settings(eph, eph["sun"],  observer, horizon_degrees=-0.8333)
    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        hf = pool.submit(almanac.find_discrete, t0_sf, t1_sf, f_hor)
        mf = pool.submit(almanac.find_discrete, t0_sf, t1_sf, f_moon)
        hor_times,  hor_r  = hf.result()
        moon_times, moon_r = mf.result()

    all_ev = []
    for t, r in zip(hor_times, hor_r):
        all_ev.append({"time": t.utc_datetime(), "label": "Sunrise" if r else "Sunset"})
    for t, r in zip(moon_times, moon_r):
        all_ev.append({"time": t.utc_datetime(), "label": "Moonrise" if r else "Moonset"})
    all_ev.sort(key=lambda e: e["time"])

    # Twilight offsets from tonight's cached sky_events
    sky_ev = se.sky_events(lat, lon, target_date)
    t_set  = next((e["time"] for e in sky_ev
                   if e["label"] == "Sunset"
                   and e["time"].astimezone(tz).date() == target_date), None)
    t_rise = find_event(sky_ev, "Sunrise", after=t_set)                    if t_set else None
    t_nb   = find_event(sky_ev, "Astronomical night begins", after=t_set)  if t_set else None
    t_ne   = find_event(sky_ev, "Astronomical night ends",   after=t_nb or t_set) if t_set else None

    tw_after  = (t_nb   - t_set ).total_seconds() if (t_set and t_nb)   else None
    tw_before = (t_rise - t_ne  ).total_seconds() if (t_rise and t_ne)  else None

    dark_hours = []
    for offset in range(-14, 16):
        night_date = target_date + timedelta(days=offset)
        sunset = next(
            (e["time"] for e in all_ev
             if e["label"] == "Sunset"
             and e["time"].astimezone(tz).date() == night_date),
            None,
        )
        if not sunset or tw_after is None or tw_before is None:
            dark_hours.append(0.0)
            continue
        sunrise = find_event(all_ev, "Sunrise", after=sunset)
        if not sunrise:
            dark_hours.append(0.0)
            continue

        night_start = sunset  + timedelta(seconds=tw_after)
        night_end   = sunrise - timedelta(seconds=tw_before)
        if night_end <= night_start:
            dark_hours.append(0.0)
            continue

        ivs = dark_moon_intervals(all_ev, night_start, night_end)
        dark_hours.append(sum((e - s).total_seconds() for s, e in ivs) / 3600)

    ms = round((time.monotonic() - t0) * 1000)
    return _dark_stats(dark_hours, 14), dark_hours, ms


# ── Approach B: full extrapolation from tonight's sky_events ─────────────────

def run_b(lat, lon, tz, target_date):
    t0 = time.monotonic()

    sky_ev = se.sky_events(lat, lon, target_date)

    # Tonight's anchor times
    t_set  = next((e["time"] for e in sky_ev
                   if e["label"] == "Sunset"
                   and e["time"].astimezone(tz).date() == target_date), None)
    t_rise = find_event(sky_ev, "Sunrise", after=t_set)                    if t_set else None
    t_nb   = find_event(sky_ev, "Astronomical night begins", after=t_set)  if t_set else None
    t_ne   = find_event(sky_ev, "Astronomical night ends",   after=t_nb or t_set) if t_set else None

    if not all([t_set, t_rise, t_nb, t_ne]):
        # No astronomical night tonight — can't extrapolate (e.g. high lat in summer)
        ms = round((time.monotonic() - t0) * 1000)
        return ({"tonight_hours": 0.0, "mean_hours": 0.0,
                 "stdev_hours": 0.0, "score": 0.0},
                [0.0] * 30, ms)

    tw_after  = (t_nb   - t_set ).total_seconds()  # sunset → astro night start
    tw_before = (t_rise - t_ne  ).total_seconds()  # astro night end → sunrise

    # Best moonrise/moonset anchor: event closest to tonight's sunset
    lo = t_set  - timedelta(hours=24)
    hi = t_rise + timedelta(hours=24)
    mr_list = [e["time"] for e in sky_ev if e["label"] == "Moonrise" and lo <= e["time"] <= hi]
    ms_list = [e["time"] for e in sky_ev if e["label"] == "Moonset"  and lo <= e["time"] <= hi]
    anchor_mr = min(mr_list, key=lambda t: abs((t - t_set).total_seconds()), default=None)
    anchor_ms = min(ms_list, key=lambda t: abs((t - t_set).total_seconds()), default=None)

    SOLAR = 86400.0
    LUNAR = float(LUNAR_DAY_SECS)

    dark_hours = []
    for offset in range(-14, 16):
        sunset  = t_set  + timedelta(seconds=offset * SOLAR)
        sunrise = t_rise + timedelta(seconds=offset * SOLAR)

        night_start = sunset  + timedelta(seconds=tw_after)
        night_end   = sunrise - timedelta(seconds=tw_before)
        if night_end <= night_start:
            dark_hours.append(0.0)
            continue

        # Build synthetic moon events: ±1 lunar day around target offset so that
        # dark_moon_intervals can determine moon_up state at night_start correctly.
        moon_ev = []
        for label, anchor in [("Moonrise", anchor_mr), ("Moonset", anchor_ms)]:
            if anchor is None:
                continue
            for adj in (-1, 0, 1):
                moon_ev.append({
                    "time":  anchor + timedelta(seconds=(offset + adj) * LUNAR),
                    "label": label,
                })
        moon_ev.sort(key=lambda e: e["time"])

        ivs = dark_moon_intervals(moon_ev, night_start, night_end)
        dark_hours.append(sum((e - s).total_seconds() for s, e in ivs) / 3600)

    ms_el = round((time.monotonic() - t0) * 1000)
    return _dark_stats(dark_hours, 14), dark_hours, ms_el


# ── Output ────────────────────────────────────────────────────────────────────

HDR = ("  {:<12}  {:<14}  {:>5}  {:>5}  {:>5}  "
       "{:>5}  {:>5}  {:>5}  "
       "{:>6}  {:>6}  "
       "{:>6}  {:>5}  {:>5}  {:>4}")
ROW = ("  {:<12}  {:<14}  {:>5.1f}  {:>5.1f}  {:>5.1f}  "
       "{:>5.2f}  {:>5.2f}  {:>5.2f}  "
       "{:>+6.2f}  {:>+6.2f}  "
       "{:>5.2f}h  {:>3}ms  {:>3}ms  {:>2}ms")

def print_loc(loc_name, rows):
    print(f"\n{'═'*110}")
    print(f"  {loc_name}")
    print(f"{'─'*110}")
    print(HDR.format(
        "Date", "Phase",
        "Score", "A", "B",
        "Hrs", "A", "B",
        "B Δscore", "B Δhrs",
        "B Δmax", "Brute", "A", "B",
    ))
    print(HDR.format(
        "────────────", "──────────────",
        "─────", "─────", "─────",
        "─────", "─────", "─────",
        "──────", "──────",
        "──────", "─────", "───", "──",
    ))
    for row in rows:
        print(ROW.format(*row))


def main():
    print(f"Dates: {[str(d) for d in DATES]}")
    print("Pre-warming sky_events cache for all dates/locations...")
    for _, lat, lon, tz in LOCS:
        for d in DATES:
            se.sky_events(lat, lon, d)
    print("Cache warm.\n")

    for loc_name, lat, lon, tz in LOCS:
        rows = []
        for d in DATES:
            phase = moon_phase_label(d)
            print(f"  {loc_name}  {d}  {phase:<14} ...", end="\r", flush=True)

            b_stats,  b_hrs,  b_ms  = run_brute(lat, lon, tz, d)
            a_stats,  a_hrs,  a_ms  = run_a(lat, lon, tz, d)
            bb_stats, bb_hrs, bb_ms = run_b(lat, lon, tz, d)

            b_delta_score = bb_stats["score"]         - b_stats["score"]
            b_delta_hrs   = bb_stats["tonight_hours"] - b_stats["tonight_hours"]
            b_max_drift   = max_drift(b_hrs, bb_hrs)

            rows.append((
                str(d), phase,
                b_stats["score"],  a_stats["score"],  bb_stats["score"],
                b_stats["tonight_hours"], a_stats["tonight_hours"], bb_stats["tonight_hours"],
                b_delta_score, b_delta_hrs,
                b_max_drift,
                b_ms, a_ms, bb_ms,
            ))

        print_loc(loc_name, rows)

    print(f"\n{'═'*110}")
    print("  Score  = 0-10 (tonight / cycle_max × 10).")
    print("  Hrs    = tonight's dark hours (astronomical night, moon below horizon).")
    print("  B Δ*   = approach B minus Brute (score and tonight_hours).")
    print("  B Δmax = max absolute dark-hours drift across all 30 nights in the window.")
    print()


if __name__ == "__main__":
    main()
