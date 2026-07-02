#!/usr/bin/env python3
"""Profiling harness for the /calendar (trip.plan_trip) multi-night path.

Wraps the four sub-calls assemble_night() makes per night — bortle lookup,
per-night sky events, the 30-night lunar-cycle dark-hours window, and the
weather forecast — to show which dominates wall-clock time for a calendar-
scale (multi-night) request, cold vs warm. Written to check whether
lunar_cycle_dark_analysis()'s per-night 30-night rolling window (each one
overlapping the last by 29/30 nights, no shared cache across adjacent
nights — see sky_events.py:290) is actually the bottleneck it looks like
on paper, before doing anything about it.

Usage:
    .venv/bin/python scripts/profile_calendar.py                # 30 nights
    .venv/bin/python scripts/profile_calendar.py --days 7
    .venv/bin/python scripts/profile_calendar.py --lat 44.0 --lon -110.5
"""
import argparse
import functools
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyNightSkyPredictor import darksky, sky_events as se, weather as wx, trip as _trip  # noqa: E402

_timings: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])  # name -> [total_ms, calls]
_percall: dict[str, list[float]] = defaultdict(list)  # name -> [ms, ms, ...] in call order


def _wrap(module, name):
    orig = getattr(module, name)

    @functools.wraps(orig)
    def wrapped(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return orig(*args, **kwargs)
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            _timings[name][0] += ms
            _timings[name][1] += 1
            _percall[name].append(round(ms, 1))

    setattr(module, name, wrapped)


def _reset():
    _timings.clear()
    _percall.clear()


def _report(label: str):
    print(f"\n--- {label} ---")
    total = sum(v[0] for v in _timings.values()) or 1.0
    for name, (ms, calls) in sorted(_timings.items(), key=lambda kv: -kv[1][0]):
        print(f"  {name:28s} {ms:9.1f} ms  ({calls:3d} calls, {ms / max(calls, 1):6.1f} ms/call)  {ms / total * 100:4.1f}%")
    print(f"  {'sum of wrapped calls':28s} {total:9.1f} ms")


def _report_distribution(name: str):
    calls = _percall.get(name, [])
    if not calls:
        return
    slow = [c for c in calls if c > 200]
    fast = [c for c in calls if c <= 200]
    print(f"\n  {name} per-call (call order): {calls}")
    print(f"  -> {len(slow)} calls > 200ms (full compute), {len(fast)} calls <= 200ms (cache/overlap hit)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--lat", type=float, default=44.0)
    ap.add_argument("--lon", type=float, default=-110.5)
    args = ap.parse_args()

    _wrap(darksky, "lookup")
    _wrap(se, "sky_events")
    _wrap(se, "lunar_cycle_dark_analysis")
    _wrap(wx, "forecast")

    loc = {"lat": args.lat, "lon": args.lon, "display_name": "Profile Site", "tz_name": "America/Denver"}
    start = date.today()
    end = start + timedelta(days=args.days - 1)

    print(f"plan_trip: {args.days} nights at ({args.lat}, {args.lon}), weather_horizon_days=7")

    t0 = time.perf_counter()
    report = _trip.plan_trip([loc], start, end, fetch_weather=True, weather_horizon_days=7)
    wall_cold = (time.perf_counter() - t0) * 1000.0
    print(f"\nCOLD wall clock: {wall_cold:.1f} ms  ({len(report.nights)} nights, 20-way threaded)")
    _report("COLD (first run)")
    _report_distribution("lunar_cycle_dark_analysis")

    _reset()
    t0 = time.perf_counter()
    report2 = _trip.plan_trip([loc], start, end, fetch_weather=True, weather_horizon_days=7)
    wall_warm = (time.perf_counter() - t0) * 1000.0
    print(f"\nWARM wall clock: {wall_warm:.1f} ms  ({len(report2.nights)} nights, repeat request)")
    _report("WARM (repeat, in-process + disk cache hot)")


if __name__ == "__main__":
    main()
