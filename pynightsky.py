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
from milky_way import milky_way_arch_summary, mw_theoretical_core_max
from moonlight import moon_wash_severity, KS_CRESCENT_EXEMPTION_PCT
from targets import DEFAULT_MIN_ELEVATION

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


def _is_prime(target, min_peak_alt: float, min_window_hours: float,
              dark_intervals: list | None = None) -> bool:
    """True if the target has a clean window meeting altitude and duration thresholds.

    Milky Way targets skip the altitude floor (the arch is inherently low from
    mid-latitudes) but still require the minimum window duration — without it,
    setting waypoints with 1–30 minute windows show up as prime.

    When every window has moon_interference=True (K&S ≥ 0.50 at some sample),
    fall back to checking overlap with the geometric dark intervals (moon
    physically below the horizon).  A target whose overnight window straddles
    moonset gains a genuine moon-free sub-period; if that sub-period is long
    enough, the target qualifies.  On full-moon nights dark_intervals=[] so no
    window can pass this fallback — the moon-dominated message fires correctly.
    """
    clean = [w for w in target.windows if not w.moon_interference]
    if not clean:
        # No fully K&S-clean windows.  Check whether any window has a moon-free
        # overlap with the geometric dark intervals (moonset → astronomical end).
        if not dark_intervals:
            return False
        for w in target.windows:
            for di_start, di_end in dark_intervals:
                overlap_s = max(w.start, di_start)
                overlap_e = min(w.end,   di_end)
                overlap_h = (overlap_e - overlap_s).total_seconds() / 3600
                if overlap_h >= min_window_hours:
                    if target.type == "milky_way":
                        return True
                    return w.peak_alt_deg >= min_peak_alt
        return False

    best = max(clean, key=lambda w: w.peak_alt_deg)
    duration_h = (best.end - best.start).total_seconds() / 3600
    if target.type == "milky_way":
        return duration_h >= min_window_hours
    return best.peak_alt_deg >= min_peak_alt and duration_h >= min_window_hours


def _print_targets(report: NightReport, prime_only: bool = False) -> None:
    targets = report.visible_targets

    if prime_only:
        cfg = _cfg.load()["prime_targets"]
        min_alt = cfg["min_peak_altitude_deg"]
        min_hrs = cfg["min_window_hours"]
        targets = [t for t in targets if _is_prime(t, min_alt, min_hrs,
                                                    dark_intervals=report.dark_intervals)]

    label = "Prime Targets" if prime_only else "Visible Targets"

    if not targets:
        # Distinguish three cases:
        # 1. Prime filter culprit — there are visible targets but none meet the
        #    altitude / duration thresholds (moon interference is the usual reason).
        # 2. Moon culprit — a bright moon dominated the entire astronomical night
        #    and K&S suppressed all catalog objects at the source.
        # 3. Light pollution culprit — site SQM is too high for any catalog target
        #    even on a hypothetical moonless night.
        all_visible = report.visible_targets
        # Moon up all astronomical night with significant illumination.
        # Check this FIRST — it explains the empty list whether or not targets
        # exist at all (bright targets survive K&S but still fail _is_prime
        # because every window has moon_interference=True).
        moon_dominated = (
            not report.dark_intervals          # no moon-free geometric intervals
            and report.night_start is not None # but there IS astronomical darkness
            and report.night_end is not None
            and report.illumination_pct > KS_CRESCENT_EXEMPTION_PCT
        )
        if moon_dominated:
            print(f"{label}:  none — {report.phase_name}"
                  f" ({report.illumination_pct:.0f}% illuminated) up throughout"
                  f" astronomical darkness; no moon-free window for prime targets.\n")
        elif prime_only and all_visible:
            # Targets exist but none met altitude / duration thresholds.
            print(f"{label}:  none found for this night.\n")
        else:
            lp = report.light_pollution
            if lp and lp.get("bortle_class") is not None:
                bc      = lp["bortle_class"]
                sqm     = lp.get("sqm")
                sqm_str = f", SQM {sqm:.1f}" if sqm is not None else ""
                print(f"{label}:  none — site light pollution (Bortle {bc}{sqm_str})"
                      f" exceeds astrophotography contrast limits for all catalog objects.\n")
            else:
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

    # Moon timing — used for sky condition labelling and arch-window clipping.
    # The illumination gate is removed here; the K&S model inside moon_wash_severity
    # returns None for any (illumination, separation) combination that produces
    # negligible sky brightening (< 0.10 Δ mag/arcsec²).
    _moonrise = next((e["time"] for e in report.events if e["label"] == "Moonrise"), None)
    _moonset  = next((e["time"] for e in report.events if e["label"] == "Moonset"),  None)

    def _moon_up_at(dt: "datetime") -> bool:
        """True if the moon is above the horizon at time dt."""
        if _moonrise and _moonset:
            return _moonrise <= dt <= _moonset if _moonrise < _moonset else dt >= _moonrise or dt <= _moonset
        if _moonrise:
            return dt >= _moonrise   # moon rises, doesn't set before dawn
        if _moonset:
            return dt <= _moonset    # moon was already up, sets during night
        return False                 # no moon event found

    # Pre-build rows tagged with type so we can insert group headers
    tagged_rows = []
    for target in targets:
        window = _best_window(target)
        condition = _sky_condition(window.peak_time,
                                   report.dark_intervals,
                                   report.night_start,
                                   report.night_end)
        _peak_moonup = _moon_up_at(window.peak_time)
        if _peak_moonup:
            sev = moon_wash_severity(
                report.illumination_pct,
                window.moon_sep_at_peak_deg,
                window.moon_alt_at_peak_deg,
            )
            if sev is not None:
                condition = f"Moon wash · {sev}"
        flags = []
        if window.moon_interference:
            flags.append("moon")
        if target.note:
            flags.append(target.note)
        display_name = target.name + " Meteor Shower" if target.type == "meteor_shower" else target.name

        # ── Window / Best Viewing display ────────────────────────────────────
        # Milky Way waypoints: clip to moonrise (keeps table consistent with the
        # arch summary which also clips at moonrise using the 25 % illum threshold).
        # All other targets: use per-target photo_cutoff / visual_cutoff computed
        # via K&S at every sample, so bright objects keep their full window and
        # faint ones can be cut even before moonrise.

        az_card   = f"{window.peak_az_deg:.0f}°({_cardinal(window.peak_az_deg)})"
        arch_note = ""
        if target.type == "milky_way" and window.arch_angle_deg is not None:
            a         = window.arch_angle_deg
            quality   = "steep" if a >= 60 else ("moderate" if a >= 35 else "flat")
            arch_note = f"  arch {a:.0f}° ({quality})"

        def _alt_at(clip_dt):
            """Piecewise-linear altitude estimate at clip_dt within this window."""
            if clip_dt <= window.peak_time:
                t0, a0 = window.start.timestamp(),     window.start_alt_deg
                t1, a1 = window.peak_time.timestamp(), window.peak_alt_deg
            else:
                t0, a0 = window.peak_time.timestamp(), window.peak_alt_deg
                t1, a1 = window.end.timestamp(),       window.end_alt_deg
            frac = (clip_dt.timestamp() - t0) / (t1 - t0) if t1 > t0 else 0.5
            return round(a0 + max(0.0, min(1.0, frac)) * (a1 - a0))

        # All target types use per-target K&S photo/visual cutoffs.
        # photo_cutoff = last sample where astrophotography sky contrast is adequate.
        # visual_cutoff = last sample where visual observation is adequate (looser).
        # arch_note is non-empty only for milky_way waypoints; safe to always append.
        _photo_end  = window.photo_cutoff
        _visual_end = window.visual_cutoff

        _has_clip = (
            _photo_end is not None
            and _photo_end > window.start
            and _photo_end < window.end
        )

        if _has_clip:
            alt_clip = _alt_at(_photo_end)
            peak_str = f"{_fmt_time(_photo_end)} @ {alt_clip}°  {az_card}{arch_note}"

            # Annotate how much longer the target is visually usable beyond
            # the photo cutoff.  visual_cutoff=None means usable all the
            # way to window.end (the astronomer can still observe visually).
            vis_note = ""
            vis_boundary = _visual_end if _visual_end is not None else window.end
            if (vis_boundary.timestamp() - _photo_end.timestamp()) >= 600:
                extra_min = round((vis_boundary.timestamp() - _photo_end.timestamp()) / 60)
                vis_note  = f"  +{extra_min}m visual"

            win_str = (f"{_fmt_time(window.start)} @ {window.start_alt_deg:.0f}°"
                       f" – {_fmt_time(_photo_end)} @ {alt_clip}°{vis_note}")
        else:
            peak_str = (f"{_fmt_time(window.peak_time)} @ {window.peak_alt_deg:.0f}°"
                        f"  {az_card}{arch_note}")
            win_str  = (f"{_fmt_time(window.start)} @ {window.start_alt_deg:.0f}°"
                        f" – {_fmt_time(window.end)} @ {window.end_alt_deg:.0f}°")

        tagged_rows.append((
            target.type,
            display_name,
            peak_str,
            condition,
            win_str,
            "  ".join(flags),
        ))

    # ── Milky Way arch summary ───────────────────────────────────────────────
    # Compute first so it can be printed above the table, then referenced
    # again when deciding how to render the per-waypoint section in the loop.
    #
    # all_mw    — every visible MW target (unfiltered); used for the arch summary
    #             so waypoint counts are accurate regardless of prime filtering.
    # mw_visible — MW targets that pass the prime/all filter; used for the table
    #             and "Visible band" listing (so 1-minute setting waypoints are
    #             excluded when prime_only is True).
    all_mw     = [t for t in report.visible_targets if t.type == "milky_way"]
    mw_visible = [t for t in targets               if t.type == "milky_way"]
    mw_summary = None
    if all_mw:
        mw_summary = milky_way_arch_summary(
            all_mw,
            lat=report.lat,
            moonrise=_moonrise,
            moonset=_moonset,
            moon_illumination_pct=report.illumination_pct,
        )

    # Print MW summary block before the table
    if all_mw:
        core_max = mw_theoretical_core_max(report.lat)
        if mw_summary is not None:
            ms      = mw_summary
            arch_h  = ms["arch_hours"]
            arch_hm = f"{int(arch_h)}h {int((arch_h % 1) * 60):02d}m"

            moon_flag = "  ·  moon penalty" if ms["moon_penalised"] else ""
            print(f"  Milky Way: {ms['local_score']}/10"
                  f"  (Altitude {ms['alt_score']}/10"
                  f"  ·  Waypoints {ms['cov_score']}/10"
                  f"  ·  Window {ms['win_score']}/10{moon_flag})")

            span       = f"{_fmt_time(ms['arch_start'])} – {_fmt_time(ms['arch_end'])}"
            core_ratio = f"{ms['core_peak_alt_deg']}°/{round(core_max)}°"
            moon_note  = "  ·  moon-limited" if ms["moon_limited"] else ""
            print(f"  Visible  {span}  ·  {arch_hm}"
                  f"  ·  Core {core_ratio}"
                  f"  ·  {ms['n_visible']} of {ms['n_max_possible']} waypoints visible{moon_note}")

            core_card = _cardinal(ms["core_peak_az_deg"])
            if ms["core_peak_in_window"]:
                best_label, best_t = "Best time  ", _fmt_time(ms["core_peak_time"])
            else:
                best_label, best_t = "Best before", _fmt_time(ms["arch_end"])
            best_line = f"  {best_label}   {best_t}  —  core {ms['core_peak_alt_deg']}° {core_card}"
            if ms["farthest_name"] and ms["farthest_peak_alt_deg"] is not None:
                far_card   = _cardinal(ms["farthest_peak_az_deg"])
                best_line += (f", arch sweeps to {ms['farthest_name']}"
                              f" ({ms['farthest_peak_alt_deg']}° {far_card})")
            print(best_line)
        else:
            # Core absent from visible targets.  Two possible causes:
            #   (a) Geometrically unreachable — core_max ≤ min elevation floor.
            #   (b) K&S-suppressed — moon is near the galactic center this night,
            #       driving Δmag so high that no sample passes the photo contrast
            #       check.  The core IS above the horizon; moonlight is the culprit.
            lat_abs = abs(report.lat)
            hem     = "N" if report.lat >= 0 else "S"
            if core_max > DEFAULT_MIN_ELEVATION:
                # Case (b): moon proximity suppressed the core.
                print(f"  Milky Way:  Core moon-washed — moon near galactic center"
                      f"  (geometric ceiling: {core_max:.0f}°)")
            else:
                # Case (a): core never clears the elevation floor at this latitude.
                print(f"  Milky Way:  Core below horizon from {lat_abs:.0f}°{hem}"
                      f"  (geometric ceiling: {core_max:.0f}°)")
            # mw_visible is prime-filtered, so short-window setting waypoints
            # (which only have a few minutes above 10° before setting) are excluded.
            vis_names = ", ".join(
                t.name for t in sorted(
                    mw_visible,
                    key=lambda t: max(w.peak_alt_deg for w in t.windows),
                    reverse=True,
                )
            )
            if vis_names:
                print(f"  Visible band:  {vis_names}")
        print()   # two blank lines separate the MW block from the table header
        print()

    # ── Targets table ────────────────────────────────────────────────────────
    data_rows = [(name, peak, cond, win, flags) for _, name, peak, cond, win, flags in tagged_rows]
    headers   = ("Target", "Best Viewing", "Sky", "Astrophotography Window", "")
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
                print()  # blank line between sections
            if ttype == "milky_way":
                print()  # visual gap before MW rows; label already shown above table
            else:
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
            "Weather Pending" if report.wx_pending
            else ("Weather N/A" if (report.wx_no_data or report.wx_archive_error)
                  else (f"Weather {comp.get('weather')}" if report.weather_score is not None else "Weather —"))
        )
        parts = [
            f"Lunar {comp.get('moon', '—')}",
            f"Dark Hours {comp.get('dark', '—')}",
            wx_part,
            f"Bortle {comp.get('bortle', '—')}" if report.bortle_score is not None else "Bortle —",
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
