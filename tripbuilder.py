#!/usr/bin/env python3
"""TripBuilder — compare dark-sky locations across a date range."""

import logging
import os
import platform
import subprocess
from datetime import date

import location as loc
from trip import NightSummary, TripReport, plan_trip

log = logging.getLogger(__name__)

_units = "si"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_units() -> str:
    for var in ("LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES"):
        if os.environ.get(var, "").startswith("en_US"):
            return "imperial"
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "NSGlobalDomain", "AppleLocale"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip().startswith("en_US"):
                return "imperial"
        except Exception:
            pass
    return "si"


def _short_name(display_name: str, max_len: int = 18) -> str:
    """First segment of the geocoded name, truncated with ellipsis if needed."""
    name = display_name.split(",")[0].strip()
    return name[:max_len - 1] + "…" if len(name) > max_len else name


def _fmt_date(d: date) -> str:
    """Format date as 'Jun  1' (6 chars, right-aligned day)."""
    return f"{d.strftime('%b')} {d.day:>2}"


def _score_cell(n: NightSummary | None, width: int) -> str:
    """Format a score cell for the matrix, right-aligned to width."""
    if n is None or n.score is None:
        return f"{'—':>{width}}"
    marker = "~" if n.weather_informed else " "
    return f"{n.score:.1f}{marker}".rjust(width)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_matrix(report: TripReport) -> None:
    locs       = report.locations
    short_names = [_short_name(l["display_name"]) for l in locs]
    col_w      = max(max(len(n) for n in short_names), 5)  # min 5 for "10.0~"
    date_w     = 6   # "Jun  1"
    sep_w      = 2   # spaces between columns

    # Build lookup: (lat, lon, date) → NightSummary
    index = {(n.lat, n.lon, n.date): n for n in report.nights}

    # Header row
    header = " " * (date_w + 2) + ("  " * sep_w).join(
        f"{name:>{col_w}}" for name in short_names
    )
    divider = "─" * (date_w + 2 + (col_w + sep_w * 2) * len(locs))
    print(header)
    print(divider)

    # Date rows
    n_days = (report.date_end - report.date_start).days + 1
    for i in range(n_days):
        d    = report.date_start + __import__("datetime").timedelta(days=i)
        row  = f"{_fmt_date(d)}  "
        cells = []
        for l in locs:
            night = index.get((l["lat"], l["lon"], d))
            cells.append(_score_cell(night, col_w))
        row += ("  " * sep_w).join(cells)
        print(row)

    print(divider)

    # Summary rows
    for label, fn in (("Average", lambda ns: sum(n.score for n in ns) / len(ns)),
                      ("Best",    lambda ns: max(n.score for n in ns))):
        row = f"{label:<{date_w}}  "
        cells = []
        for l in locs:
            loc_nights = [n for n in report.nights
                          if n.lat == l["lat"] and n.lon == l["lon"]
                          and n.score is not None]
            val = f"{fn(loc_nights):.1f}" if loc_nights else "—"
            cells.append(f"{val:>{col_w}}")
        row += ("  " * sep_w).join(cells)
        print(row)

    print()

    # Legend & recommendation
    any_wx = any(n.weather_informed for n in report.nights)
    if any_wx:
        print("  ~ weather informed  (unmarked = astro only)")

    avgs = []
    for i, l in enumerate(locs):
        loc_nights = [n for n in report.nights
                      if n.lat == l["lat"] and n.lon == l["lon"]
                      and n.score is not None]
        if loc_nights:
            avgs.append((sum(n.score for n in loc_nights) / len(loc_nights), short_names[i]))

    if avgs:
        best_avg, best_name = max(avgs)
        print(f"  → Best location: {best_name}  (avg {best_avg:.1f}/10)")
    print()


def _print_ranked(report: TripReport, top: int) -> None:
    ranked = report.ranked[:top]
    if not ranked:
        print("No scoreable nights found.\n")
        return

    rows = []
    for i, n in enumerate(ranked, 1):
        comp   = n.score_components
        lunar  = str(comp.get("moon",   "—"))
        dark   = str(comp.get("dark",   "—"))
        bortle = str(comp.get("bortle", "—")) if n.bortle_score is not None else "—"
        if n.weather_informed:
            wx = f"{n.weather_score:.1f} ~"
        else:
            wx = "—"
        rows.append((
            str(i),
            _fmt_date(n.date),
            _short_name(n.display_name),
            f"{n.score:.1f}/10",
            lunar,
            dark,
            bortle,
            wx,
        ))

    headers = ("Rank", "Date", "Location", "Score", "Lunar", "Dark", "Bortle", "Weather")
    # Rank, Score, Lunar, Dark, Bortle, Weather are right-aligned; Date and Location left
    right_cols = {0, 3, 4, 5, 6, 7}
    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows))
        for i in range(len(headers))
    ]

    def _row(vals):
        parts = []
        for i, v in enumerate(vals):
            parts.append(f"{v:>{widths[i]}}" if i in right_cols else f"{v:<{widths[i]}}")
        print("  " + "  ".join(parts))

    print("Top Nights:\n")
    _row(headers)
    print("  " + "  ".join("─" * w for w in widths))
    for r in rows:
        _row(r)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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

    global _units
    _units = args.units if args.units else _detect_units()

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

    _print_matrix(report)
    _print_ranked(report, args.top)


if __name__ == "__main__":
    main()
