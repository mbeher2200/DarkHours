#!/usr/bin/env python3
"""PyNightSky — command-line interface for the night sky predictor."""

import logging
import os
import platform
import subprocess
from datetime import date

from zoneinfo import ZoneInfo

import config as _cfg
import location as loc
import weather as wx
from predictor import NightReport, assemble_night

log = logging.getLogger(__name__)

_units = "si"
_TZ    = None


def _detect_units() -> str:
    """Return 'imperial' for US locale, 'si' otherwise."""
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


def _local(dt):
    return dt.astimezone(_TZ)


def _fmt(dt):
    local = _local(dt)
    hour  = int(local.strftime("%I"))
    return local.strftime(f"%b %-d, {hour:>2}:%M %p")


def _fmt_time(dt):
    local = _local(dt)
    hour  = int(local.strftime("%I"))
    return local.strftime(f"{hour:>2}:%M %p")


def _temp(c):
    if c is None:
        return "—"
    if _units == "imperial":
        return f"{round(c * 9 / 5 + 32)}°F"
    return f"{c:.1f}°C"


def _wind(ms):
    if ms is None:
        return "—"
    if _units == "imperial":
        return f"{ms * 2.237:.1f}mph"
    return f"{ms:.1f}m/s"


def _lp_line(report: NightReport) -> str | None:
    """Format the light pollution summary line from the report's raw lookup data."""
    info = report.light_pollution
    if info is None:
        return None
    if info.get("below_detection"):
        return "Light pollution data unavailable"
    if info.get("sqm") is None:
        return None
    return (f"SQM {info['sqm']}  ·  Zone {info['lp_zone']}"
            f"  ·  Bortle {info['bortle_class']}"
            f"  ({info['bortle_desc']})  [{info['source']}]")


def _cardinal(az_deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(az_deg / 45) % 8]


def _sky_condition(peak_time, dark_intervals, night_start, night_end) -> str:
    """Classify peak_time as 'Dark sky', 'Astro night', or 'Twilight'."""
    for s, e in (dark_intervals or []):
        if s <= peak_time <= e:
            return "Dark sky"
    if night_start and night_end and night_start <= peak_time <= night_end:
        return "Astro night"
    return "Twilight"


def _is_prime(target, min_peak_alt: float, min_window_hours: float) -> bool:
    """True if the target has a clean window meeting altitude and duration thresholds.

    Milky Way targets are prime whenever they're visible during dark sky
    without moon interference — no altitude or duration threshold applied.
    """
    clean = [w for w in target.windows if not w.moon_interference]
    if not clean:
        return False
    if target.type == "milky_way":
        return True
    best = max(clean, key=lambda w: w.peak_alt_deg)
    duration_h = (best.end - best.start).total_seconds() / 3600
    return best.peak_alt_deg >= min_peak_alt and duration_h >= min_window_hours


def _print_targets(report: NightReport, prime_only: bool = False) -> None:
    targets = report.visible_targets

    if prime_only:
        cfg = _cfg.load()["prime_targets"]
        min_alt = cfg["min_peak_altitude_deg"]
        min_hrs = cfg["min_window_hours"]
        targets = [t for t in targets if _is_prime(t, min_alt, min_hrs)]

    label = "Prime Targets" if prime_only else "Visible Targets"

    if not targets:
        print(f"{label}:  none found for this night.\n")
        return

    _TYPE_ORDER = {
        "meteor_shower": 0,
        "milky_way":     1,
        "cluster":       2,
        "planet":        3,
        "nebula":        4,
        "galaxy":        5,
    }
    _TYPE_LABELS = {
        "meteor_shower": "Meteor Showers",
        "milky_way":     "Milky Way",
        "cluster":       "Clusters",
        "planet":        "Planets",
        "nebula":        "Nebulae",
        "galaxy":        "Galaxies",
    }

    def _best_window(t):
        clean = [w for w in t.windows if not w.moon_interference]
        pool  = clean if clean else t.windows
        return max(pool, key=lambda w: w.peak_alt_deg)

    # Group by type order, then sort each group chronologically by peak time
    targets = sorted(
        targets,
        key=lambda t: (_TYPE_ORDER.get(t.type, 99), _best_window(t).peak_time),
    )

    tz_label  = _local(report.sunset).strftime("%Z")
    hdr_range = f"{_fmt_time(report.sunset)} – {_fmt_time(report.sunrise)} {tz_label}"
    print(f"{label}  ({hdr_range}):\n")

    # Pre-build rows tagged with type so we can insert group headers
    tagged_rows = []
    for target in targets:
        window = _best_window(target)
        condition = _sky_condition(window.peak_time,
                                   report.dark_intervals,
                                   report.night_start,
                                   report.night_end)
        flags = []
        if window.moon_interference:
            flags.append("moon")
        if target.note:
            flags.append(target.note)
        display_name = target.name + " Meteor Shower" if target.type == "meteor_shower" else target.name
        tagged_rows.append((
            target.type,
            display_name,
            f"{_fmt_time(window.peak_time)} @ {window.peak_alt_deg:.0f}°  {window.peak_az_deg:.0f}°({_cardinal(window.peak_az_deg)})",
            condition,
            f"{_fmt_time(window.start)} @ {window.start_alt_deg:.0f}° – {_fmt_time(window.end)} @ {window.end_alt_deg:.0f}°",
            "  ".join(flags),
        ))

    data_rows = [(name, peak, cond, win, flags) for _, name, peak, cond, win, flags in tagged_rows]
    headers   = ("Target", "Best Viewing", "Sky", "Window", "")
    widths    = [
        max(len(headers[i]), max(len(r[i]) for r in data_rows))
        for i in range(len(headers))
    ]

    def _row(vals):
        name, peak, cond, win, flags = vals
        print(f"  {name:<{widths[0]}}  {peak:<{widths[1]}}  {cond:<{widths[2]}}  {win:<{widths[3]}}"
              + (f"   {flags}" if flags else ""))

    _row(headers)
    print(f"  {'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  {'-' * widths[3]}")

    current_type = None
    for ttype, name, peak, cond, win, flags in tagged_rows:
        if ttype != current_type:
            if current_type is not None:
                print()
            print(f"  {_TYPE_LABELS.get(ttype, ttype)}")
            current_type = ttype
        _row((name, peak, cond, win, flags))

    print()


def _print_report(report: NightReport, show_weather: bool) -> None:
    # Dark time string
    if report.night_start and report.night_end and report.dark_intervals:
        h            = report.dark_hours
        duration_str = f"{int(h)}h {int((h % 1) * 60)}m"
        tz_label     = _local(report.night_start).strftime("%Z")
        spans        = ",  ".join(
            f"{_fmt_time(s)} – {_fmt_time(e)}" for s, e in report.dark_intervals
        )
        dark_str = f"{duration_str}  ({spans} {tz_label})"
    elif report.night_start and report.night_end:
        dark_str = "None (moon up all night)"
    else:
        dark_str = "None (no astronomical darkness at this latitude/date)"

    # Header
    print(f"\nDate:               {report.date}")
    print(f"Location:           {report.display_name}  ({report.lat:.4f}°, {report.lon:.4f}°)")
    lp = _lp_line(report)
    if lp:
        print(f"Light Pollution:    {lp}")
    print(f"Moon:               {report.phase_name}  |  {report.illumination_pct}% illuminated")
    cycle     = report.dark_cycle
    cycle_str = f"avg {cycle['mean_hours']}h  ±{cycle['stdev_hours']}h over lunar cycle"
    print(f"Prime Dark Sky Hours:  {dark_str}  ·  {cycle_str}")

    if report.score is not None:
        comp  = report.score_components
        wx_part = (
            "Wx Pending" if report.wx_pending
            else ("Wx N/A" if (report.wx_no_data or report.wx_archive_error)
                  else (f"Wx {comp.get('weather')}" if report.weather_score is not None else "Wx —"))
        )
        parts = [
            f"Moon {comp.get('moon', '—')}",
            f"Dark {comp.get('dark', '—')}",
            wx_part,
            f"Bortle {comp.get('bortle', '—') if report.bortle_score is not None else '—'}",
        ]
        print(f"Night Quality Score:  {report.score}/10  ({' · '.join(parts)})")
    print()

    # Sky Events
    tz_label = _local(report.sunset).strftime("%Z")
    col_w    = max((len(_fmt(e["time"])) for e in report.events), default=25)
    ev_w     = max(len("Event"), max(len(e["label"]) for e in report.events))
    print("Night Timeline:\n")
    print(f"  {f'Time ({tz_label})':<{col_w}}  {'Event':<{ev_w}}")
    print(f"  {'-' * col_w}  {'-' * ev_w}")
    for e in report.events:
        print(f"  {_fmt(e['time']):<{col_w}}  {e['label']}")
    print()

    # Weather table (opt-in)
    if show_weather:
        if report.wx_error:
            print(f"Weather unavailable: {report.wx_error}\n")
        elif report.wx_archive_error:
            print("Historical weather archive temporarily unavailable "
                  "(archive-api.open-meteo.com is down).\n")
        elif report.wx_no_data:
            print("Historical weather data unavailable for this date.\n")
        elif report.wx_pending:
            print("Weather forecast not yet available for this date.\n")
        elif not report.weather_points:
            print("No weather data available for this night.\n")
        else:
            pts        = report.weather_points
            has_temp   = any(p.temperature_c  is not None for p in pts)
            has_feels  = any(p.feels_like_c   is not None for p in pts)
            has_seeing = any(p.seeing_arcsec  is not None for p in pts)
            has_transp = any(p.transparency   is not None for p in pts)

            wx_tz = _local(pts[0].time).strftime("%Z")
            print("Weather:\n")
            cols  = [(f"Time ({wx_tz})", "l"), ("Wx Rating", "r"), ("Cloud", "r")]
            cols += [("Temp",   "r")] if has_temp   else []
            cols += [("Feels",  "r")] if has_feels  else []
            cols += [("Seeing", "r")] if has_seeing else []
            cols += [("Transp", "l")] if has_transp else []
            cols += [("Humid", "r"), ("Wind", "r"), ("Precip", "l")]

            rows = []
            for p in pts:
                row  = [_fmt(p.time), f"{wx.rate_conditions(p)}/10"]
                row += [f"{p.cloud_cover_pct}%" if p.cloud_cover_pct is not None else "—"]
                row += [_temp(p.temperature_c)] if has_temp   else []
                row += [_temp(p.feels_like_c)]  if has_feels  else []
                row += [f"{p.seeing_arcsec:.2f}\"" if p.seeing_arcsec is not None else "—"] if has_seeing else []
                row += [p.transparency or "—"] if has_transp else []
                row += [
                    f"{p.humidity_pct}%" if p.humidity_pct is not None else "—",
                    _wind(p.wind_speed_ms),
                    p.precip_type.capitalize() if p.precip_type and p.precip_type != "none" else "None",
                ]
                rows.append(row)

            headers = [h for h, _ in cols]
            aligns  = [a for _, a in cols]
            widths  = [
                max(len(headers[i]), max(len(r[i]) for r in rows))
                for i in range(len(headers))
            ]

            def _row(vals):
                parts = [
                    f"{v:>{w}}" if a == "r" else f"{v:<{w}}"
                    for v, a, w in zip(vals, aligns, widths)
                ]
                print("  " + "  ".join(parts))

            _row(headers)
            _row(["-" * w for w in widths])
            for row in rows:
                _row(row)
            print()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Night sky events for astronomical photography.")

    where = parser.add_mutually_exclusive_group()
    where.add_argument("--location", "-l", metavar="NAME",
                       help="Location name or city (geocoded and cached)")
    where.add_argument("--coords", "-c", nargs=2, type=float, metavar=("LAT", "LON"),
                       help="Decimal-degree coordinates, e.g. -c 40.7128 -74.0060")

    parser.add_argument("--date", "-d", default=date.today().isoformat(),
                        metavar="YYYY-MM-DD", help="Date to predict (default: today)")
    parser.add_argument("--save-location", metavar="NAME",
                        help="Save --coords under a name for future use")
    parser.add_argument("--list-locations", action="store_true",
                        help="Show all saved/cached locations and exit")
    parser.add_argument("--weather", "-w", action="store_true",
                        help="Include weather forecast for the night (requires internet)")
    parser.add_argument("--targets", "-t", action="store_true",
                        help="Show visible targets summary for the night")
    parser.add_argument("--prime-targets", "-p", action="store_true",
                        help="Show only prime targets (no moon interference, peak ≥40°, window ≥1h)")
    parser.add_argument("--units", choices=["imperial", "si"], default=None,
                        help="Unit system for temperature and wind speed (default: auto-detect from locale)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print debug information to stderr")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="[%(name)s] %(message)s",
    )

    global _units, _TZ
    _units = args.units if args.units else _detect_units()
    log.debug("Unit system: %s  (LANG=%s, platform=%s)",
              _units, os.environ.get("LANG"), platform.system())

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
        _TZ = ZoneInfo(tz_name)
    else:
        lat, lon = args.coords
        display_name = f"{lat:.4f}°, {lon:.4f}°"
        _TZ = loc.timezone_for(lat, lon)
        if args.save_location:
            loc.save(args.save_location, lat, lon, display_name=f"{lat:.4f}°, {lon:.4f}°")

    log.debug("Resolved location: lat=%.4f, lon=%.4f, tz=%s", lat, lon, str(_TZ))

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Error: '{args.date}' is not a valid date (expected YYYY-MM-DD).")
        raise SystemExit(1)

    fetch_targets = args.targets or args.prime_targets

    try:
        report = assemble_night(lat, lon, target, _TZ, display_name=display_name,
                                fetch_targets=fetch_targets)
    except ValueError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

    _print_report(report, show_weather=args.weather)
    if fetch_targets:
        _print_targets(report, prime_only=args.prime_targets)


if __name__ == "__main__":
    main()
