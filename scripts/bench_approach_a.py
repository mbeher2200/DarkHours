#!/usr/bin/env python3
"""
Confirm approach A accuracy vs Brute across a full lunar cycle.

  Brute  – current: parallel bulk find_discrete + 30 per-night twilight searches
  A      – parallel bulk find_discrete + fixed twilight offset from tonight's sky_events

Tests all 30 days of one lunar cycle (one date per day).
Locations: Denver CO · Seattle WA · Miami FL
Reports: score drift, tonight_hours drift, max 30-night array drift, timing.
Also prints the night-by-night array for the single worst-case date.

Usage:
  source .venv/bin/activate
  python scripts/bench_approach_a.py
"""

import concurrent.futures
import sys
import time
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo
from skyfield import almanac
from skyfield.api import load, wgs84

sys.path.insert(0, ".")
from PyNightSkyPredictor import sky_events as se
from PyNightSkyPredictor.sky_events import (
    _compute_dark_hours_cycle,
    _dark_stats,
    dark_moon_intervals,
    find_event,
)

# ── Config ────────────────────────────────────────────────────────────────────
LOCS = [
    ("Denver  CO", 39.74, -104.98, ZoneInfo("America/Denver")),
    ("Seattle WA", 47.61, -122.33, ZoneInfo("America/Los_Angeles")),
    ("Miami   FL", 25.77,  -80.19, ZoneInfo("America/New_York")),
]

BASE  = date(2026, 6, 27)
DATES = [BASE + timedelta(days=i) for i in range(30)]   # full lunar cycle


# ── Brute (current) ───────────────────────────────────────────────────────────

def run_brute(lat, lon, tz, d):
    t0  = time.monotonic()
    hrs = _compute_dark_hours_cycle(lat, lon, d, tz)
    ms  = round((time.monotonic() - t0) * 1000)
    return _dark_stats(hrs, 14), hrs, ms


# ── Approach A ────────────────────────────────────────────────────────────────

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

    sky_ev = se.sky_events(lat, lon, target_date)
    t_set  = next((e["time"] for e in sky_ev
                   if e["label"] == "Sunset"
                   and e["time"].astimezone(tz).date() == target_date), None)
    t_rise = find_event(sky_ev, "Sunrise", after=t_set)                   if t_set else None
    t_nb   = find_event(sky_ev, "Astronomical night begins", after=t_set) if t_set else None
    t_ne   = find_event(sky_ev, "Astronomical night ends", after=t_nb or t_set) if t_set else None

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def moon_phase_label(d: date) -> str:
    ts  = load.timescale()
    eph = se._ephemeris()
    deg = almanac.moon_phase(eph, ts.utc(d.year, d.month, d.day, 12)).degrees
    labels = [
        (22.5,  "New       "), (67.5,  "Wax Cres  "), (112.5, "1st Qtr   "),
        (157.5, "Wax Gib   "), (202.5, "Full      "), (247.5, "Wan Gib   "),
        (292.5, "3rd Qtr   "), (337.5, "Wan Cres  "),
    ]
    for threshold, name in labels:
        if deg < threshold:
            return name
    return "New       "


def max_drift(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def print_night_array(d, brute_hrs, a_hrs):
    """Print the 30-night dark-hours array side by side."""
    print(f"\n  Night-by-night for {d}  (offset = days from tonight):")
    print(f"  {'Offset':>6}  {'Brute':>6}  {'A':>6}  {'Δ':>6}")
    print(f"  {'──────':>6}  {'──────':>6}  {'──────':>6}  {'──────':>6}")
    for i, (bh, ah) in enumerate(zip(brute_hrs, a_hrs)):
        offset = i - 14
        marker = " ◀ tonight" if offset == 0 else ""
        delta  = ah - bh
        print(f"  {offset:>+6}  {bh:>6.2f}  {ah:>6.2f}  {delta:>+6.2f}{marker}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Testing {len(DATES)} dates: {DATES[0]} → {DATES[-1]}")
    print("Pre-warming sky_events cache...")
    for _, lat, lon, tz in LOCS:
        for d in DATES:
            se.sky_events(lat, lon, d)
    print("Cache warm.\n")

    for loc_name, lat, lon, tz in LOCS:
        print(f"\n{'═'*88}")
        print(f"  {loc_name}")
        print(f"{'─'*88}")
        print(f"  {'Date':<12}  {'Phase':<10}  "
              f"{'Brute':>5}  {'A':>5}  {'Δscore':>7}  "
              f"{'B.hrs':>5}  {'A.hrs':>5}  {'Δhrs':>6}  "
              f"{'Δmax':>6}  {'Brute':>7}  {'A':>5}")
        print(f"  {'────────────':<12}  {'──────────':<10}  "
              f"{'─────':>5}  {'─────':>5}  {'───────':>7}  "
              f"{'─────':>5}  {'─────':>5}  {'──────':>6}  "
              f"{'──────':>6}  {'───────':>7}  {'─────':>5}")

        worst_date      = None
        worst_drift     = 0.0
        worst_b_hrs     = []
        worst_a_hrs     = []
        total_brute_ms  = 0
        total_a_ms      = 0

        for d in DATES:
            phase = moon_phase_label(d)
            print(f"  {d} {phase} ...", end="\r", flush=True)

            b_stats, b_hrs, b_ms = run_brute(lat, lon, tz, d)
            a_stats, a_hrs, a_ms = run_a(lat, lon, tz, d)
            total_brute_ms += b_ms
            total_a_ms     += a_ms

            d_score = a_stats["score"]         - b_stats["score"]
            d_hrs   = a_stats["tonight_hours"] - b_stats["tonight_hours"]
            d_max   = max_drift(b_hrs, a_hrs)

            if d_max > worst_drift:
                worst_drift  = d_max
                worst_date   = d
                worst_b_hrs  = b_hrs
                worst_a_hrs  = a_hrs

            print(f"  {d}  {phase}  "
                  f"{b_stats['score']:5.1f}  {a_stats['score']:5.1f}  {d_score:>+7.2f}  "
                  f"{b_stats['tonight_hours']:5.2f}  {a_stats['tonight_hours']:5.2f}  {d_hrs:>+6.2f}  "
                  f"{d_max:6.2f}h  {b_ms:5}ms  {a_ms:3}ms")

        avg_brute = total_brute_ms // len(DATES)
        avg_a     = total_a_ms     // len(DATES)
        print(f"\n  Average timing: Brute {avg_brute}ms  A {avg_a}ms  "
              f"({round((1 - avg_a/avg_brute)*100)}% faster)")
        print(f"  Worst Δmax: {worst_drift:.2f}h on {worst_date}")

        if worst_date:
            print_night_array(worst_date, worst_b_hrs, worst_a_hrs)

    print(f"\n{'═'*88}")
    print("  Score = 0-10.  Δmax = max |Brute - A| across all 30 nights in window.")
    print()


if __name__ == "__main__":
    main()
