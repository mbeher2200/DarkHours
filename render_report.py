"""render_report.py — terminal renderer for the single-night report and targets table.

Public API:
    print_report(report, ctx, show_weather)
    print_targets(report, ctx, prime_only)
"""

import logging
from datetime import timedelta

import config as _cfg
import weather as wx
from format_ctx import FormatCtx, lp_str, cardinal
from milky_way import milky_way_arch_summary, mw_theoretical_core_max
from moonlight import moon_wash_severity, KS_CRESCENT_EXEMPTION_PCT
from predictor import NightReport
from targets import DEFAULT_MIN_ELEVATION, is_prime

log = logging.getLogger(__name__)

_CLEAR_CLOUD_THRESHOLD = 30   # % cloud cover at or below which a dark hour counts as "clear"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clear_dark_intervals(dark_intervals: list, weather_points: list) -> tuple:
    """
    Clip dark_intervals to only the hours where cloud_cover_pct <= _CLEAR_CLOUD_THRESHOLD.

    Each weather point is treated as covering the 1-hour window starting at its
    timestamp.  Partial hours at interval boundaries are prorated correctly.

    Returns (clipped_intervals, total_clear_hours).
    Falls back to (dark_intervals, total_hours) if no cloud data is available.
    """
    cloud_by_hour = {
        p.time.replace(minute=0, second=0, microsecond=0): p.cloud_cover_pct
        for p in weather_points
        if p.cloud_cover_pct is not None
    }
    if not cloud_by_hour:
        total = sum((e - s).total_seconds() / 3600 for s, e in dark_intervals)
        return dark_intervals, total

    result = []
    for ivl_start, ivl_end in dark_intervals:
        h         = ivl_start.replace(minute=0, second=0, microsecond=0)
        seg_start = seg_end = None
        while h < ivl_end:
            cloud    = cloud_by_hour.get(h)
            seg_s    = max(h, ivl_start)
            seg_e    = min(h + timedelta(hours=1), ivl_end)
            is_clear = cloud is None or cloud <= _CLEAR_CLOUD_THRESHOLD
            if is_clear:
                if seg_start is None:
                    seg_start = seg_s
                seg_end = seg_e
            else:
                if seg_start is not None:
                    result.append((seg_start, seg_end))
                    seg_start = seg_end = None
            h += timedelta(hours=1)
        if seg_start is not None:
            result.append((seg_start, seg_end))

    total = round(sum((e - s).total_seconds() / 3600 for s, e in result), 2)
    return result, total


def _lp_line(report: NightReport) -> str | None:
    """Format the light pollution summary line from a NightReport."""
    return lp_str(report.light_pollution)


def _seeing_score(arcsec: float) -> int:
    """Convert seeing (arcseconds) to a 1–10 score. Uses same curve as rate_conditions()."""
    return max(1, min(10, round((3.0 - arcsec) / 2.6 * 10)))


def _transparency_score(label: str) -> int:
    """Convert a 7Timer transparency label to a 1–10 score. Mirrors rate_conditions() weights."""
    return {"Excellent": 10, "Good": 8, "Fair": 4, "Poor": 1}.get(label, 5)




def _best_weather_window(clear_intervals, weather_points, ctx):
    """
    Find the clear dark interval with the highest average weather score.

    Returns (start, end, avg_score) for the best interval, or None if there
    are no weather points within any clear interval.
    """
    if not clear_intervals or not weather_points:
        return None

    best = None
    for ivl_start, ivl_end in clear_intervals:
        pts = [p for p in weather_points if ivl_start <= p.time < ivl_end]
        if not pts:
            continue
        avg = round(sum(wx.rate_conditions(p) for p in pts) / len(pts), 1)
        if best is None or avg > best[2]:
            best = (ivl_start, ivl_end, avg)
    return best


def _night_verdict(report: NightReport, clear_hours: float) -> str | None:
    """Generate a plain-English one-line verdict for the night."""
    score = report.score
    if score is None:
        return None

    comp = report.score_components
    # Identify the single most limiting component
    worst_key = min(comp, key=lambda k: comp[k]) if comp else None

    def _limit_phrase() -> str:
        if worst_key == "moon":
            return (f"{report.phase_name.lower()}"
                    f" ({report.illumination_pct:.0f}% illuminated)")
        if worst_key == "dark":
            if clear_hours == 0:
                return "no clear dark sky hours"
            return f"short dark window ({clear_hours:.1f}h)"
        if worst_key == "weather":
            pts = report.weather_points
            if pts:
                avg_cloud = sum(p.cloud_cover_pct or 0 for p in pts) / len(pts)
                has_precip = any(
                    p.precip_type and p.precip_type not in ("none", None)
                    for p in pts
                )
                if has_precip:
                    types = {p.precip_type for p in pts
                             if p.precip_type and p.precip_type not in ("none", None)}
                    return "snow forecast" if "snow" in types else "rain forecast"
                if avg_cloud > 70:
                    return "heavy cloud cover forecast"
                if avg_cloud > 40:
                    return "partial cloud cover forecast"
                return "poor sky conditions"
            return "weather data limited"
        if worst_key == "bortle":
            lp = report.light_pollution
            bc = lp.get("bortle_class") if lp else None
            return f"severe light pollution (Bortle {bc})" if bc else "severe light pollution"
        return ""

    phrase = _limit_phrase()
    suffix = f" — {phrase}" if phrase else ""

    if score >= 9.0:
        return "Excellent — ideal conditions"
    if score >= 7.5:
        return f"Good{suffix}"
    if score >= 5.5:
        return f"Fair{suffix}"
    if score >= 3.0:
        return f"Poor{suffix}"
    if score > 0:
        return f"Very poor{suffix}"
    return f"Pass{suffix}"


def _sky_condition(peak_time, dark_intervals, night_start, night_end) -> str:
    """Classify peak_time as 'Dark sky', 'Astro night', or 'Twilight'."""
    for s, e in (dark_intervals or []):
        if s <= peak_time <= e:
            return "Dark sky"
    if night_start and night_end and night_start <= peak_time <= night_end:
        return "Astro night"
    return "Twilight"


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------

def print_report(report: NightReport, ctx: FormatCtx, show_weather: bool) -> None:
    """Print the standard single-night report to stdout."""
    # Dark time string — weather-adjusted when cloud data is available
    if report.dark_intervals and report.weather_points:
        disp_intervals, disp_hours = _clear_dark_intervals(
            report.dark_intervals, report.weather_points
        )
    else:
        disp_intervals = report.dark_intervals
        disp_hours     = report.dark_hours

    if report.night_start and report.night_end and report.dark_intervals:
        if disp_intervals:
            h            = disp_hours
            duration_str = f"{int(h)}h {int((h % 1) * 60)}m"
            tz_label     = ctx.local(report.night_start).strftime("%Z")
            spans        = ",  ".join(
                f"{ctx.fmt_time(s)} – {ctx.fmt_time(e)}" for s, e in disp_intervals
            )
            dark_str = f"{duration_str}  ({spans} {tz_label})"
        else:
            dark_str = (f"None (overcast — {report.dark_hours:.1f}h "
                        f"astronomical dark obscured by cloud)")
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
    tags = []
    if report.moon_special:
        tags.append(report.moon_special.title())
    for e in report.moon_eclipses:
        kind    = e["kind"].capitalize()
        mag_str = (f"umbral {e['umbral_magnitude']:.3f}"
                   if e["kind"] in ("partial", "total")
                   else f"penumbral {e['penumbral_magnitude']:.3f}")
        tags.append(f"{kind} lunar eclipse at {ctx.fmt_time(e['time'])}  (mag {mag_str})")
    tag_str = ("  ·  *** " + "  ·  ".join(tags) + " ***") if tags else ""
    print(f"Moon:               {report.phase_name}  |  {report.illumination_pct}% illuminated"
          f"  |  {report.moon_distance_km:,} km{tag_str}")
    if report.active_showers:
        shower_parts = [f"{s['name']} · {s['note']} · ZHR {s['zhr']}"
                        for s in report.active_showers]
        print(f"Meteor Showers:     {',  '.join(shower_parts)}")
    cycle     = report.dark_cycle
    cycle_str = f"avg {cycle['mean_hours']}h  ±{cycle['stdev_hours']}h over lunar cycle"
    print(f"Clear Dark Sky Hours:  {dark_str}  ·  {cycle_str}")

    if report.score is not None:
        comp   = report.score_components
        wx_part = (
            "Weather Pending" if report.wx_pending
            else ("Weather N/A" if (report.wx_no_data or report.wx_archive_error)
                  else (f"Weather {comp.get('weather')}" if report.weather_score is not None
                        else "Weather —"))
        )
        parts = [
            f"Lunar {comp.get('moon', '—')}",
            f"Dark Hours {comp.get('dark', '—')}",
            wx_part,
            f"Bortle {comp.get('bortle', '—')}" if report.bortle_score is not None
            else "Bortle —",
        ]
        print(f"Night Quality Score:  {report.score}/10  ({' · '.join(parts)})")
        bw = _best_weather_window(disp_intervals, report.weather_points, ctx)
        if bw:
            bw_start, bw_end, bw_score = bw
            print(f"  Best window:  {ctx.fmt_time(bw_start)} – {ctx.fmt_time(bw_end)}"
                  f"  ·  avg {bw_score}/10")
        verdict = _night_verdict(report, disp_hours)
        if verdict:
            print(f"  Verdict:      {verdict}")
    print()

    # Sky Events timeline
    tz_label = ctx.local(report.sunset).strftime("%Z")
    col_w    = max((len(ctx.fmt(e["time"])) for e in report.events), default=25)
    ev_w     = max(len("Event"), max(len(e["label"]) for e in report.events))
    print("Night Timeline:\n")
    print(f"  {f'Time ({tz_label})':<{col_w}}  {'Event':<{ev_w}}")
    print(f"  {'-' * col_w}  {'-' * ev_w}")
    for e in report.events:
        print(f"  {ctx.fmt(e['time']):<{col_w}}  {e['label']}")
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
            has_dew_pt = any(p.dew_point_c    is not None for p in pts)
            has_feels  = any(p.feels_like_c   is not None for p in pts)
            has_seeing = any(p.seeing_arcsec  is not None for p in pts)
            has_transp = any(p.transparency   is not None for p in pts)

            wx_tz  = ctx.local(pts[0].time).strftime("%Z")
            src    = f"  [{report.wx_source}]" if report.wx_source else ""
            print(f"Weather{src}:\n")
            cols  = [(f"Time ({wx_tz})", "l"), ("Wx Rating", "r"), ("Cloud Cover", "r")]
            cols += [("Temp",         "r")] if has_temp   else []
            cols += [("Dew Pt",       "r")] if has_dew_pt else []
            cols += [("Feels",        "r")] if has_feels  else []
            cols += [("Seeing",       "l")] if has_seeing else []
            cols += [("Transparency", "r")] if has_transp else []
            cols += [("Humidity", "r"), ("Wind", "r"), ("Precip", "l")]

            rows = []
            for p in pts:
                row  = [ctx.fmt(p.time), f"{wx.rate_conditions(p)}/10"]
                row += [f"{p.cloud_cover_pct}%" if p.cloud_cover_pct is not None else "—"]
                row += [ctx.temp(p.temperature_c)]  if has_temp   else []
                row += [ctx.temp(p.dew_point_c)]   if has_dew_pt else []
                row += [ctx.temp(p.feels_like_c)]  if has_feels  else []
                row += [f"{_seeing_score(p.seeing_arcsec)}/10 ({p.seeing_arcsec:.2f}\")" if p.seeing_arcsec is not None else "—"] if has_seeing else []
                row += [f"{_transparency_score(p.transparency)}/10" if p.transparency is not None else "—"] if has_transp else []
                row += [
                    f"{p.humidity_pct}%" if p.humidity_pct is not None else "—",
                    ctx.wind(p.wind_speed_ms, p.wind_direction_deg),
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


def print_targets(report: NightReport, ctx: FormatCtx, prime_only: bool = False) -> None:
    """Print the visible targets table to stdout."""
    targets = report.visible_targets

    if prime_only:
        cfg     = _cfg.load()["prime_targets"]
        min_alt = cfg["min_peak_altitude_deg"]
        min_hrs = cfg["min_window_hours"]
        targets = [t for t in targets if is_prime(t, min_alt, min_hrs,
                                                   dark_intervals=report.dark_intervals)]

    label = "Prime Targets" if prime_only else "Visible Targets"

    if not targets:
        all_visible = report.visible_targets
        moon_dominated = (
            not report.dark_intervals
            and report.night_start is not None
            and report.night_end is not None
            and report.illumination_pct > KS_CRESCENT_EXEMPTION_PCT
        )
        if moon_dominated:
            print(f"{label}:  none — {report.phase_name}"
                  f" ({report.illumination_pct:.0f}% illuminated) up throughout"
                  f" astronomical darkness; no moon-free window for prime targets.\n")
        elif prime_only and all_visible:
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

    targets = sorted(
        targets,
        key=lambda t: (_TYPE_ORDER.get(t.type, 99), _best_window(t).peak_time),
    )

    tz_label  = ctx.local(report.sunset).strftime("%Z")
    hdr_range = f"{ctx.fmt_time(report.sunset)} – {ctx.fmt_time(report.sunrise)} {tz_label}"
    print(f"{label}  ({hdr_range}):\n")

    _moonrise = next((e["time"] for e in report.events if e["label"] == "Moonrise"), None)
    _moonset  = next((e["time"] for e in report.events if e["label"] == "Moonset"),  None)

    def _moon_up_at(dt) -> bool:
        if _moonrise and _moonset:
            return _moonrise <= dt <= _moonset if _moonrise < _moonset else dt >= _moonrise or dt <= _moonset
        if _moonrise:
            return dt >= _moonrise
        if _moonset:
            return dt <= _moonset
        return False

    tagged_rows = []
    for target in targets:
        window    = _best_window(target)
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
        display_name = (target.name + " Meteor Shower"
                        if target.type == "meteor_shower" else target.name)

        az_card   = f"{window.peak_az_deg:.0f}°({cardinal(window.peak_az_deg)})"
        arch_note = ""
        if target.type == "milky_way" and window.arch_angle_deg is not None:
            a         = window.arch_angle_deg
            quality   = "steep" if a >= 60 else ("moderate" if a >= 35 else "flat")
            arch_note = f"  arch {a:.0f}° ({quality})"

        def _alt_at(clip_dt):
            if clip_dt <= window.peak_time:
                t0, a0 = window.start.timestamp(),     window.start_alt_deg
                t1, a1 = window.peak_time.timestamp(), window.peak_alt_deg
            else:
                t0, a0 = window.peak_time.timestamp(), window.peak_alt_deg
                t1, a1 = window.end.timestamp(),       window.end_alt_deg
            frac = (clip_dt.timestamp() - t0) / (t1 - t0) if t1 > t0 else 0.5
            return round(a0 + max(0.0, min(1.0, frac)) * (a1 - a0))

        _photo_end  = window.photo_cutoff
        _visual_end = window.visual_cutoff

        _has_clip = (
            _photo_end is not None
            and _photo_end > window.start
            and _photo_end < window.end
        )

        if _has_clip:
            alt_clip = _alt_at(_photo_end)
            peak_str = f"{ctx.fmt_time(_photo_end)} @ {alt_clip}°  {az_card}{arch_note}"

            vis_note = ""
            vis_boundary = _visual_end if _visual_end is not None else window.end
            if (vis_boundary.timestamp() - _photo_end.timestamp()) >= 600:
                extra_min = round((vis_boundary.timestamp() - _photo_end.timestamp()) / 60)
                vis_note  = f"  +{extra_min}m visual"

            win_str = (f"{ctx.fmt_time(window.start)} @ {window.start_alt_deg:.0f}°"
                       f" – {ctx.fmt_time(_photo_end)} @ {alt_clip}°{vis_note}")
        else:
            peak_str = (f"{ctx.fmt_time(window.peak_time)} @ {window.peak_alt_deg:.0f}°"
                        f"  {az_card}{arch_note}")
            win_str  = (f"{ctx.fmt_time(window.start)} @ {window.start_alt_deg:.0f}°"
                        f" – {ctx.fmt_time(window.end)} @ {window.end_alt_deg:.0f}°")

        tagged_rows.append((
            target.type,
            display_name,
            peak_str,
            condition,
            win_str,
            "  ".join(flags),
        ))

    # Milky Way arch summary
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

            span       = f"{ctx.fmt_time(ms['arch_start'])} – {ctx.fmt_time(ms['arch_end'])}"
            core_ratio = f"{ms['core_peak_alt_deg']}°/{round(core_max)}°"
            moon_note  = "  ·  moon-limited" if ms["moon_limited"] else ""
            print(f"  Visible  {span}  ·  {arch_hm}"
                  f"  ·  Core {core_ratio}"
                  f"  ·  {ms['n_visible']} of {ms['n_max_possible']} waypoints visible{moon_note}")

            core_card = cardinal(ms["core_peak_az_deg"])
            if ms["core_peak_in_window"]:
                best_label, best_t = "Best time  ", ctx.fmt_time(ms["core_peak_time"])
            else:
                best_label, best_t = "Best before", ctx.fmt_time(ms["arch_end"])
            best_line = f"  {best_label}   {best_t}  —  core {ms['core_peak_alt_deg']}° {core_card}"
            if ms["farthest_name"] and ms["farthest_peak_alt_deg"] is not None:
                far_card   = cardinal(ms["farthest_peak_az_deg"])
                best_line += (f", arch sweeps to {ms['farthest_name']}"
                              f" ({ms['farthest_peak_alt_deg']}° {far_card})")
            print(best_line)
        else:
            lat_abs = abs(report.lat)
            hem     = "N" if report.lat >= 0 else "S"
            if core_max > DEFAULT_MIN_ELEVATION:
                print(f"  Milky Way:  Core moon-washed — moon near galactic center"
                      f"  (geometric ceiling: {core_max:.0f}°)")
            else:
                print(f"  Milky Way:  Core below horizon from {lat_abs:.0f}°{hem}"
                      f"  (geometric ceiling: {core_max:.0f}°)")
            vis_names = ", ".join(
                t.name for t in sorted(
                    mw_visible,
                    key=lambda t: max(w.peak_alt_deg for w in t.windows),
                    reverse=True,
                )
            )
            if vis_names:
                print(f"  Visible band:  {vis_names}")
        print()
        print()

    # Targets table
    data_rows = [(name, peak, cond, win, flags)
                 for _, name, peak, cond, win, flags in tagged_rows]
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
                print()
            if ttype == "milky_way":
                print()
            else:
                print(f"  {_TYPE_LABELS.get(ttype, ttype)}")
            current_type = ttype
        _row((name, peak, cond, win, flags))

    print()
