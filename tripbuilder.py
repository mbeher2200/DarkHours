#!/usr/bin/env python3
"""TripBuilder — compare dark-sky locations across a date range."""

import logging
from datetime import date

from PyNightSkyPredictor import location as loc
from PyNightSkyPredictor.format_ctx import detect_units
from PyNightSkyPredictor.render_trip import print_matrix, print_ranked
from PyNightSkyPredictor.trip import plan_trip

log = logging.getLogger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare dark-sky locations across a date range."
    )
    parser.add_argument("--locations", "-l", nargs="+", metavar="NAME",
                        required=True,
                        help="One or more location names to compare")
    parser.add_argument("--date-range", "-d", nargs=2, metavar=("START", "END"),
                        required=True,
                        help="Date range: YYYY-MM-DD YYYY-MM-DD")
    parser.add_argument("--top", "-n", type=int, default=10,
                        help="Number of top nights in the ranked list (default: 10)")
    parser.add_argument("--no-weather", action="store_true",
                        help="Astronomical factors only — skip weather fetch")
    parser.add_argument("--units", choices=["imperial", "si"], default=None,
                        help="Temperature/wind units (default: auto-detect)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print debug information to stderr")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="[%(name)s] %(message)s",
    )

    # Parse dates
    try:
        date_start = date.fromisoformat(args.date_range[0])
        date_end   = date.fromisoformat(args.date_range[1])
    except ValueError as e:
        print(f"Error: invalid date — {e}")
        raise SystemExit(1)

    if date_end < date_start:
        print("Error: end date must be on or after start date.")
        raise SystemExit(1)

    # Resolve locations
    locations = []
    for name in args.locations:
        try:
            lat, lon, display_name, tz_name = loc.resolve(name)
            locations.append({
                "lat": lat, "lon": lon,
                "display_name": display_name,
                "tz_name": tz_name,
            })
            print(f"  {display_name}  ({lat:.4f}°, {lon:.4f}°)")
        except (ValueError, RuntimeError) as e:
            print(f"Error resolving '{name}': {e}")
            raise SystemExit(1)

    print()

    n_nights = (date_end - date_start).days + 1
    total    = n_nights * len(locations)
    print(f"Computing {n_nights} nights × {len(locations)} locations ({total} total)…\n")

    def _progress(done, total):
        bar_w  = 30
        filled = int(bar_w * done / total)
        bar    = "█" * filled + "░" * (bar_w - filled)
        print(f"\r  [{bar}] {done}/{total}", end="", flush=True)

    report = plan_trip(
        locations,
        date_start,
        date_end,
        fetch_weather=not args.no_weather,
        progress_fn=_progress,
    )
    print(f"\r  [{'█' * 30}] {total}/{total} — done.\n")

    # Header
    start_str = date_start.strftime("%-b %-d").replace("  ", " ")
    end_str   = date_end.strftime("%-b %-d, %Y").replace("  ", " ")
    print(f"Trip Plan: {start_str} – {end_str}\n")

    print_matrix(report)
    print_ranked(report, args.top)


if __name__ == "__main__":
    main()
