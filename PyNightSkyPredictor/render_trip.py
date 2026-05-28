"""render_trip.py — trip comparison output for tripbuilder.

print_matrix() and print_ranked() receive a TripReport and print it to stdout.
Neither needs FormatCtx — trip renderers contain no timezone or units formatting.
"""

from datetime import date, timedelta

from .trip import NightSummary, TripReport


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


def print_matrix(report: TripReport) -> None:
    """Print the location × date score matrix."""
    locs        = report.locations
    short_names = [_short_name(l["display_name"]) for l in locs]
    col_w       = max(max(len(n) for n in short_names), 5)  # min 5 for "10.0~"
    date_w      = 6   # "Jun  1"
    sep_w       = 2   # spaces between columns

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
        d     = report.date_start + timedelta(days=i)
        cells = [_score_cell(index.get((l["lat"], l["lon"], d)), col_w) for l in locs]
        print(f"{_fmt_date(d)}  " + ("  " * sep_w).join(cells))

    print(divider)

    # Summary rows
    for label, fn in (("Average", lambda ns: sum(n.score for n in ns) / len(ns)),
                      ("Best",    lambda ns: max(n.score for n in ns))):
        cells = []
        for l in locs:
            loc_nights = [n for n in report.nights
                          if n.lat == l["lat"] and n.lon == l["lon"]
                          and n.score is not None]
            val = f"{fn(loc_nights):.1f}" if loc_nights else "—"
            cells.append(f"{val:>{col_w}}")
        print(f"{label:<{date_w}}  " + ("  " * sep_w).join(cells))

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


def print_ranked(report: TripReport, top: int) -> None:
    """Print the top-N ranked nights table."""
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
        wx     = f"{n.weather_score:.1f} ~" if n.weather_informed else "—"
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

    headers    = ("Rank", "Date", "Location", "Score", "Lunar", "Dark", "Bortle", "Weather")
    right_cols = {0, 3, 4, 5, 6, 7}   # Rank, Score, Lunar, Dark, Bortle, Weather
    widths     = [
        max(len(headers[i]), max(len(r[i]) for r in rows))
        for i in range(len(headers))
    ]

    def _row(vals):
        parts = [
            f"{v:>{widths[i]}}" if i in right_cols else f"{v:<{widths[i]}}"
            for i, v in enumerate(vals)
        ]
        print("  " + "  ".join(parts))

    print("Top Nights:\n")
    _row(headers)
    print("  " + "  ".join("─" * w for w in widths))
    for r in rows:
        _row(r)
    print()
