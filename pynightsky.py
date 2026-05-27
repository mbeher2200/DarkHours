#!/usr/bin/env python3
"""PyNightSky — command-line interface for the night sky predictor."""

import calendar as _cal
import itertools
import logging
import threading
import time
from datetime import date, datetime

from zoneinfo import ZoneInfo

import location as loc
import trip as _trip
from darksky import find_nearby, _MAX_SEARCH_RADIUS
from format_ctx import FormatCtx, detect_units
from predictor import assemble_night
from render_calendar import print_calendar
from render_report import print_report, print_targets, print_nearby

log = logging.getLogger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Night sky events for astronomical photography.")

    where = parser.add_mutually_exclusive_group()
    where.add_argument("--location", "-l", metavar="NAME",
                       help="Location name or city (geocoded and cached)")
    where.add_argument("--coords", "-c", nargs=2, type=float, metavar=("LAT", "LON"),
                       help="Decimal-degree coordinates, e.g. -c 40.7128 -74.0060")

    parser.add_argument("--date", "-d", default=None,
                        metavar="DATE",
                        help="Date (YYYY-MM-DD, default: today) or month (YYYY-MM) with --calendar")
    parser.add_argument("--calendar", action="store_true",
                        help="Show a month-view calendar of night scores (use --date YYYY-MM to pick a month)")
    parser.add_argument("--save-location", metavar="NAME",
                        help="Save --coords under a name for future use")
    parser.add_argument("--list-locations", action="store_true",
                        help="Show all saved/cached locations and exit")
    parser.add_argument("--weather", "-w", action="store_true",
                        help="Include weather forecast for the night (requires internet)")
    parser.add_argument("--targets", "-t", action="store_true",
                        help="Show prime targets for the night (no moon interference, peak ≥40°, window ≥1h)")
    parser.add_argument("--show-nearby", metavar="MILES", nargs="?", const=60, type=int,
                        help=f"Show darker sky areas and light domes within MILES radius (default 60, max {_MAX_SEARCH_RADIUS})")
    parser.add_argument("--units", choices=["imperial", "si"], default=None,
                        help="Unit system for temperature and wind speed (default: auto-detect from locale)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print debug information to stderr")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="[%(name)s] %(message)s",
    )

    units = args.units if args.units else detect_units()
    log.debug("Unit system: %s", units)

    if args.list_locations:
        locations = loc.list_all()
        if not locations:
            print("No saved locations yet.")
        else:
            print("\nSaved locations:")
            for name, entry in locations.items():
                print(f"  {name:<20}  {entry['lat']:.4f}, {entry['lon']:.4f}  ({entry['display_name']})")
        print()
        return

    if not args.location and not args.coords:
        parser.error("Provide --location NAME or --coords LAT LON")

    if args.location:
        try:
            lat, lon, display_name, tz_name = loc.resolve(args.location)
        except (ValueError, RuntimeError) as e:
            print(f"Error: {e}")
            raise SystemExit(1)
        tz = ZoneInfo(tz_name)
    else:
        lat, lon = args.coords
        display_name = f"{lat:.4f}°, {lon:.4f}°"
        tz = loc.timezone_for(lat, lon)
        if args.save_location:
            loc.save(args.save_location, lat, lon, display_name=f"{lat:.4f}°, {lon:.4f}°")

    log.debug("Resolved location: lat=%.4f, lon=%.4f, tz=%s", lat, lon, str(tz))

    ctx = FormatCtx(tz=tz, units=units)

    # ── Calendar mode ────────────────────────────────────────────────────────
    if args.calendar:
        d_arg = args.date
        if d_arg is None:
            today      = date.today()
            date_start = today.replace(day=1)
        else:
            try:
                date_start = datetime.strptime(d_arg, "%Y-%m").date().replace(day=1)
            except ValueError:
                try:
                    date_start = date.fromisoformat(d_arg).replace(day=1)
                except ValueError:
                    print(f"Error: '{d_arg}' is not a valid date (expected YYYY-MM or YYYY-MM-DD).")
                    raise SystemExit(1)

        last_day = _cal.monthrange(date_start.year, date_start.month)[1]
        date_end = date_start.replace(day=last_day)

        loc_dict = {"lat": lat, "lon": lon, "display_name": display_name, "tz_name": str(tz)}

        def _progress(done, total):
            print(f"  Computing...  {done}/{total}", end="\r", flush=True)
            if done == total:
                print(" " * 30, end="\r", flush=True)

        trip_report = _trip.plan_trip(
            [loc_dict], date_start, date_end,
            fetch_weather=args.weather,
            progress_fn=_progress,
        )
        print_calendar(trip_report.nights, display_name, date_start, date_end, lat, lon, ctx)
        return

    # ── Single-night mode ────────────────────────────────────────────────────
    d_arg = args.date or date.today().isoformat()
    try:
        target = date.fromisoformat(d_arg)
    except ValueError:
        print(f"Error: '{d_arg}' is not a valid date (expected YYYY-MM-DD).")
        raise SystemExit(1)

    try:
        report = assemble_night(lat, lon, target, tz, display_name=display_name,
                                fetch_targets=args.targets)
    except ValueError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

    print_report(report, ctx, show_weather=args.weather)
    if args.targets:
        print_targets(report, ctx)
    if args.show_nearby:
        import sys
        radius = args.show_nearby
        if radius > _MAX_SEARCH_RADIUS:
            print(f"  --show-nearby: radius capped at {_MAX_SEARCH_RADIUS} mi "
                  f"(requested {radius} mi — beyond this the area search becomes unreliable "
                  f"and the Trip Builder is a better fit for long-range planning).")
            radius = _MAX_SEARCH_RADIUS
        _result  = [None]
        _done    = threading.Event()

        def _run():
            _result[0] = find_nearby(lat, lon, radius)
            _done.set()

        threading.Thread(target=_run, daemon=True).start()

        base = f"  Scanning nearby skies within {ctx.fmt_dist(radius)}"
        if sys.stdout.isatty():
            frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
            while not _done.wait(timeout=0.1):
                print(f"\r{base}  {next(frames)}", end="", flush=True)
            print(f"\r{' ' * (len(base) + 4)}\r", end="", flush=True)
        else:
            _done.wait()

        print_nearby(_result[0], ctx)


if __name__ == "__main__":
    main()
