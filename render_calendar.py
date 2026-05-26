"""render_calendar.py — calendar view renderer for the night sky predictor.

print_calendar() receives a list of NightSummary objects (from trip.plan_trip)
and a FormatCtx (for timezone/unit formatting) and prints a chronological
calendar of night scores.
"""

from datetime import date

import darksky as _ds
from format_ctx import FormatCtx, lp_str


def print_calendar(summaries: list, display_name: str,
                   date_start: date, date_end: date,
                   lat: float, lon: float,
                   ctx: FormatCtx) -> None:
    """Print a chronological calendar of night scores across a date range."""
    if date_start.month == date_end.month and date_start.year == date_end.year:
        period_str = date_start.strftime("%B %Y")
    else:
        period_str = f"{date_start.strftime('%b %Y')} – {date_end.strftime('%b %Y')}"

    print(f"\nCalendar — {display_name}")
    lp = lp_str(_ds.lookup(lat, lon))
    bortle_score = next((s.bortle_score for s in summaries if s.bortle_score is not None), None)
    if lp:
        lp_suffix = f"  ·  Score {bortle_score}/10" if bortle_score is not None else ""
        print(f"Light Pollution:    {lp}{lp_suffix}")
    print(f"{period_str}\n")

    # Column headers — exact names as in the nightly report
    headers = ("Date", "Night Quality Score", "Prime Dark Hours", "Weather", "Moon")
    aligns  = ("l",    "r",                   "r",                "r",       "l")

    # Per-row value formatters
    def _date_str(s):
        return s.date.isoformat()   # → "2026-08-03"

    def _score_str(s):
        return f"{s.score:.1f}/10" if s.score is not None else "—"

    def _dark_str(s):
        h = s.dark_hours
        return f"{int(h)}h {int((h % 1) * 60):02d}m"

    def _wx_str(s):
        if s.weather_informed and s.weather_score is not None:
            return f"{s.weather_score:.1f}"
        if s.wx_pending:
            return "~"
        return "—"

    def _moon_str(s):
        lunar_score = s.score_components.get("moon")
        base_str    = f"{lunar_score}" if lunar_score is not None else "—"
        tags = []
        if s.moon_special:
            tags.append(s.moon_special.title())
        for e in s.moon_eclipses:
            kind    = e["kind"].capitalize()
            mag_str = (f"umbral {e['umbral_magnitude']:.3f}"
                       if e["kind"] in ("partial", "total")
                       else f"penumbral {e['penumbral_magnitude']:.3f}")
            tags.append(f"{kind} lunar eclipse at {ctx.fmt_time(e['time'])}  (mag {mag_str})")
        tag_str = ("  ·  *** " + "  ·  ".join(tags) + " ***") if tags else ""
        return f"{base_str}{tag_str}"

    # Compute column widths: max(header width, widest data value)
    date_w  = max(len(headers[0]), max((len(_date_str(s))  for s in summaries), default=0))
    score_w = max(len(headers[1]), max((len(_score_str(s)) for s in summaries), default=0))
    dark_w  = max(len(headers[2]), max((len(_dark_str(s))  for s in summaries), default=0))
    wx_w    = max(len(headers[3]), max((len(_wx_str(s))    for s in summaries), default=0))
    moon_w  = max(len(headers[4]), max((len(_moon_str(s))  for s in summaries), default=0))
    widths  = (date_w, score_w, dark_w, wx_w, moon_w)

    def _row(vals):
        parts = [
            f"{v:>{w}}" if a == "r" else f"{v:<{w}}"
            for v, w, a in zip(vals[:-1], widths[:-1], aligns[:-1])
        ]
        parts.append(vals[-1])   # Moon — no trailing padding on last col
        print("  " + "  ".join(parts))

    _row(headers)
    sep_widths = widths[:-1] + (len(headers[-1]),)  # Moon: header width only (data overflows freely)
    _row(["-" * w for w in sep_widths])

    for s in summaries:
        _row([_date_str(s), _score_str(s), _dark_str(s), _wx_str(s), _moon_str(s)])

    # Best nights footer
    ranked = sorted(
        [s for s in summaries if s.score is not None],
        key=lambda s: s.score,
        reverse=True,
    )
    if ranked:
        top_str = "  ·  ".join(
            f"{s.date.strftime('%b %-d')} ({s.score:.1f}/10)"
            for s in ranked[:3]
        )
        print(f"\n  Best nights:  {top_str}")
    print()
