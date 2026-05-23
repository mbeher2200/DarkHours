#!/usr/bin/env python3
"""Sun and moon event calculator for astronomical photography planning."""

import json
import locale
import logging
import math
import os
import platform
import statistics
import subprocess
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from skyfield.api import load, wgs84
from skyfield import almanac

log = logging.getLogger(__name__)

PHASE_NAMES = [
    (0,   "New Moon"),
    (45,  "Waxing Crescent"),
    (90,  "First Quarter"),
    (135, "Waxing Gibbous"),
    (180, "Full Moon"),
    (225, "Waning Gibbous"),
    (270, "Third Quarter"),
    (315, "Waning Crescent"),
]


def _ephemeris():
    return load("de421.bsp")


def sky_events(lat: float, lon: float, target_date: date) -> list:
    """
    Return all sky events in a 3-day window around target_date.

    Searching 3 days ensures the full night is captured regardless of UTC offset.
    Each event is a dict with 'time' (UTC timezone-aware datetime) and 'label'.
    """
    ts = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=1)
    d1 = target_date + timedelta(days=2)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    events = []

    # Sunrise / sunset
    f_sun = almanac.sunrise_sunset(eph, observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_sun)):
        events.append({"time": t.utc_datetime(), "label": "Sunrise" if rising else "Sunset"})

    # Moonrise / moonset
    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_moon)):
        events.append({"time": t.utc_datetime(), "label": "Moonrise" if rising else "Moonset"})

    # Night / astronomical twilight boundaries only
    f_twilight = almanac.dark_twilight_day(eph, observer)
    times_tw, phases_tw = almanac.find_discrete(t0, t1, f_twilight)
    for i, (t, phase) in enumerate(zip(times_tw, phases_tw)):
        prev = phases_tw[i - 1] if i > 0 else None
        if prev is not None and {int(phase), int(prev)} == {0, 1}:
            label = "Night begins" if phase == 0 else "Night ends"
            events.append({"time": t.utc_datetime(), "label": label})

    events.sort(key=lambda e: e["time"])
    log.debug("Raw events (UTC) over 3-day window:")
    for e in events:
        log.debug("  %s  %s", e["time"].strftime("%Y-%m-%d %H:%M"), e["label"])
    return events


def dark_moon_intervals(events: list, night_start, night_end, illumination: float) -> list:
    """Return (start, end) UTC datetime pairs when moon is below horizon within [night_start, night_end]."""
    log.debug("Night window (UTC): %s → %s", night_start.strftime("%H:%M"), night_end.strftime("%H:%M"))
    log.debug("Illumination: %.1f%%", illumination)
    if illumination < 5:  # new moon — negligible light, count full night
        log.debug("New moon — treating full night as dark")
        return [(night_start, night_end)]

    moon_events = [(e["time"], e["label"]) for e in events
                   if e["label"] in ("Moonrise", "Moonset")]

    # Determine if moon is already up at the start of astronomical night
    moon_up = False
    for t, label in reversed(moon_events):
        if t <= night_start:
            moon_up = (label == "Moonrise")
            break

    intervals = []
    cursor = night_start
    for t, label in moon_events:
        if t <= night_start or t >= night_end:
            continue
        if not moon_up:
            intervals.append((cursor, t))
        cursor = t
        moon_up = (label == "Moonrise")

    if not moon_up:
        intervals.append((cursor, night_end))

    log.debug("Moon up at night start: %s", moon_up)
    log.debug("Dark intervals (UTC): %s", [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in intervals])
    return intervals


_DARK_CYCLE_CACHE = Path.home() / ".night-sky-predictor" / "dark_cycle_cache.json"


def _load_dark_cycle_cache() -> dict:
    try:
        return json.loads(_DARK_CYCLE_CACHE.read_text()) if _DARK_CYCLE_CACHE.exists() else {}
    except Exception:
        return {}


def _save_dark_cycle_cache(cache: dict):
    _DARK_CYCLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _DARK_CYCLE_CACHE.write_text(json.dumps(cache, indent=2))


def _compute_dark_hours_cycle(lat: float, lon: float, target_date: date, tz) -> list:
    """Compute dark sky hours for 30 consecutive nights centred on target_date."""
    ts  = load.timescale()
    eph = _ephemeris()
    observer = wgs84.latlon(lat, lon)

    d0 = target_date - timedelta(days=15)
    d1 = target_date + timedelta(days=17)
    t0 = ts.utc(d0.year, d0.month, d0.day)
    t1 = ts.utc(d1.year, d1.month, d1.day)

    all_events = []
    f_sun = almanac.sunrise_sunset(eph, observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_sun)):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Sunrise" if rising else "Sunset"})

    f_moon = almanac.risings_and_settings(eph, eph["moon"], observer)
    for t, rising in zip(*almanac.find_discrete(t0, t1, f_moon)):
        all_events.append({"time": t.utc_datetime(),
                           "label": "Moonrise" if rising else "Moonset"})

    f_tw = almanac.dark_twilight_day(eph, observer)
    times_tw, phases_tw = almanac.find_discrete(t0, t1, f_tw)
    for i, (t, phase) in enumerate(zip(times_tw, phases_tw)):
        prev = phases_tw[i - 1] if i > 0 else None
        if prev is not None and {int(phase), int(prev)} == {0, 1}:
            label = "Night begins" if phase == 0 else "Night ends"
            all_events.append({"time": t.utc_datetime(), "label": label})

    all_events.sort(key=lambda e: e["time"])

    hours = []
    for offset in range(-14, 16):
        night_date = target_date + timedelta(days=offset)
        sunset = next(
            (e["time"] for e in all_events
             if e["label"] == "Sunset"
             and e["time"].astimezone(tz).date() == night_date),
            None,
        )
        if not sunset:
            hours.append(0.0)
            continue
        sunrise = _find(all_events, "Sunrise", after=sunset)
        if not sunrise:
            hours.append(0.0)
            continue
        night_start = _find(all_events, "Night begins", after=sunset, before=sunrise)
        night_end   = _find(all_events, "Night ends", after=night_start or sunset, before=sunrise)
        if not night_start or not night_end:
            hours.append(0.0)
            continue
        _, illum   = moon_phase_info(sunset)
        intervals  = dark_moon_intervals(all_events, night_start, night_end, illum)
        total_secs = sum((e - s).total_seconds() for s, e in intervals)
        hours.append(total_secs / 3600)

    return hours


def _dark_stats(dark_hours: list, tonight_idx: int) -> dict:
    """Derive mean, stdev, and Gaussian-CDF score from a dark-hours array."""
    tonight = dark_hours[tonight_idx]
    mean_h  = statistics.mean(dark_hours)
    stdev_h = statistics.stdev(dark_hours) if len(dark_hours) > 1 else 0.0
    if stdev_h > 0:
        z     = (tonight - mean_h) / stdev_h
        score = 0.5 * (1 + math.erf(z / math.sqrt(2))) * 10
    else:
        score = 5.0
    log.debug("Dark cycle: tonight=%.2fh  mean=%.2fh  stdev=%.2fh  score=%.1f",
              tonight, mean_h, stdev_h, score)
    return {
        "tonight_hours": round(tonight, 2),
        "mean_hours":    round(mean_h,  1),
        "stdev_hours":   round(stdev_h, 1),
        "score":         round(min(10.0, max(0.0, score)), 1),
    }


def lunar_cycle_dark_analysis(lat: float, lon: float, target_date: date, tz) -> dict:
    """
    Return dark sky stats for a 30-night window centred on target_date.

    Results are cached on disk keyed by location + window start date.
    A cache hit means no ephemeris work at all — the analysis is free on
    subsequent runs for any date within the same 30-night window.
    """
    loc_key      = f"{lat:.3f},{lon:.3f}"
    window_start = target_date - timedelta(days=14)

    cache = _load_dark_cycle_cache()
    entry = cache.get(loc_key)

    if entry and entry.get("window_start") == window_start.isoformat():
        log.debug("Dark cycle cache hit for %s (window %s)", loc_key, window_start)
        tonight_idx = 14   # target_date is always the centre
        return _dark_stats(entry["dark_hours"], tonight_idx)

    log.debug("Dark cycle cache miss for %s — computing 30-night window", loc_key)
    dark_hours = _compute_dark_hours_cycle(lat, lon, target_date, tz)

    cache[loc_key] = {
        "window_start": window_start.isoformat(),
        "dark_hours":   dark_hours,
    }
    _save_dark_cycle_cache(cache)

    return _dark_stats(dark_hours, 14)


def moon_phase_info(at_utc: object) -> tuple:
    """Return (phase_name, illumination_pct) at the given UTC datetime."""
    ts = load.timescale()
    eph = _ephemeris()
    t = ts.from_datetime(at_utc)
    angle = almanac.moon_phase(eph, t).degrees
    illumination = round((1 - math.cos(math.radians(angle))) / 2 * 100, 1)
    phase_name = next(name for thresh, name in reversed(PHASE_NAMES) if angle >= thresh)
    log.debug("Moon phase angle: %.2f°  →  %s  (%.1f%% illuminated)", angle, phase_name, illumination)
    return phase_name, illumination


_TZ = None     # set in main() to the location's local timezone
_units = "si"  # "imperial" or "si"; set in main() via locale detection or --units flag


def _detect_units() -> str:
    """Return 'imperial' for US locale, 'si' otherwise."""
    # Environment variables (reliable on Linux; often C.UTF-8 on macOS even for US users)
    for var in ("LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES"):
        if os.environ.get(var, "").startswith("en_US"):
            return "imperial"
    # macOS: query the system locale directly — unaffected by shell LANG
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
    hour = int(local.strftime("%I"))  # 1–12, no leading zero
    return local.strftime(f"%b %-d, {hour:>2}:%M %p %Z")


def _fmt_time(dt):
    return _local(dt).strftime("%-I:%M %p")


def _temp(c):
    """Format a temperature value according to the active unit system."""
    if c is None:
        return "—"
    if _units == "imperial":
        return f"{round(c * 9 / 5 + 32)}°F"
    return f"{c:.1f}°C"


def _wind(ms):
    """Format a wind speed value according to the active unit system."""
    if ms is None:
        return "—"
    if _units == "imperial":
        return f"{ms * 2.237:.1f}mph"
    return f"{ms:.1f}m/s"


def _find(events, label, after=None, before=None):
    """Return the first event matching label within the optional time bounds."""
    for e in events:
        if e["label"] != label:
            continue
        if after is not None and e["time"] <= after:
            continue
        if before is not None and e["time"] >= before:
            continue
        return e["time"]
    return None


def _rate_night(
    moon_score: float,
    dark_score: float,
    weather_score: float | None,
    bortle_score: float | None,
) -> dict:
    """
    Compute an overall night rating (0–10) from component scores (each 0–10).

    Uses a weighted geometric mean combined with a minimum-factor penalty so
    that a single very bad factor tanks the overall score even when everything
    else is excellent.

    Weights (redistribute automatically when a factor is unavailable):
      Weather   40%  — clouds / precip make the night unusable
      Moon      25%  — illumination washes out faint targets
      Dark time 25%  — moon-free hours within astronomical night
      Bortle    10%  — site light pollution (fixed for a location)

    Formula: score = 10 × weighted_geometric_mean × sqrt(min_factor / 10)
    The sqrt(min) term is the deal-breaker multiplier — a factor of 3/10
    applies a ~0.55× penalty on top of the geometric mean.
    """
    named = {
        "weather": (weather_score, 0.40),
        "moon":    (moon_score,    0.25),
        "dark":    (dark_score,    0.25),
        "bortle":  (bortle_score,  0.10),
    }

    available = {k: (s, w) for k, (s, w) in named.items() if s is not None}
    if not available:
        return {"score": None, "components": {}}

    # Normalise weights so they sum to 1 even when factors are missing
    total_w = sum(w for _, w in available.values())
    norm    = {k: w / total_w for k, (_, w) in available.items()}

    # Weighted geometric mean in [0, 1] space
    wgm = 1.0
    for k, (s, _) in available.items():
        wgm *= (s / 10) ** norm[k]

    # Deal-breaker penalty: sqrt of the worst normalised factor
    min_s = min(s for s, _ in available.values()) / 10
    score = round(10 * wgm * (min_s ** 0.5), 1)

    components = {k: round(s, 1) for k, (s, _) in available.items()}
    return {"score": score, "components": components}


def _weighted_weather_score(
    night_points: list,
    night_start,   # datetime | None  — start of astronomical darkness
    night_end,     # datetime | None  — end   of astronomical darkness
    rate_fn,       # callable: WeatherPoint → int (1–10)
) -> float | None:
    """
    Weighted average weather score across night_points.

    Points that fall inside the astronomical darkness window
    (night_start → night_end) receive 3× weight; twilight / buffer
    points receive 1×.  When there is no darkness window (polar summer,
    etc.) all points are equal-weighted.

    Returns None if night_points is empty.
    """
    if not night_points:
        return None

    pairs = [
        (rate_fn(p),
         3.0 if (night_start and night_end
                 and night_start <= p.time <= night_end)
         else 1.0)
        for p in night_points
    ]
    total_w = sum(w for _, w in pairs)
    return round(sum(r * w for r, w in pairs) / total_w, 1)


def _find_last(events, label, before):
    """Return the last event matching label that occurs before a given time."""
    match = None
    for e in events:
        if e["label"] == label and e["time"] < before:
            match = e["time"]
    return match


def main():
    import argparse
    import location as loc

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
    parser.add_argument("--units", choices=["imperial", "si"], default=None,
                        help="Unit system for temperature and wind speed (default: auto-detect from locale)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print debug information to stderr")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="[%(name)s] %(message)s",
    )

    global _units
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

    # Resolve coordinates
    global _TZ
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
        tz_name = str(_TZ)
        if args.save_location:
            loc.save(args.save_location, lat, lon, display_name=f"{lat:.4f}°, {lon:.4f}°")

    log.debug("Resolved location: lat=%.4f, lon=%.4f, tz=%s", lat, lon, tz_name)

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Error: '{args.date}' is not a valid date (expected YYYY-MM-DD).")
        raise SystemExit(1)

    events = sky_events(lat, lon, target)

    # Find sunset on the target date (local), then sunrise after it
    sunset = next(
        (e["time"] for e in events
         if e["label"] == "Sunset" and _local(e["time"]).date() == target),
        None,
    )
    if not sunset:
        print("No sunset found for this date and location.")
        raise SystemExit(1)

    sunrise = _find(events, "Sunrise", after=sunset)
    if not sunrise:
        print("No sunrise found after sunset.")
        raise SystemExit(1)

    log.debug("Night anchor (UTC): sunset=%s  sunrise=%s",
              sunset.strftime("%Y-%m-%d %H:%M"), sunrise.strftime("%Y-%m-%d %H:%M"))

    # Moon: last moonrise before sunrise, first moonset after sunset
    moonrise = _find_last(events, "Moonrise", before=sunrise)
    moonset = _find(events, "Moonset", after=sunset)
    log.debug("Moon: moonrise=%s  moonset=%s",
              moonrise.strftime("%H:%M UTC") if moonrise else "None",
              moonset.strftime("%H:%M UTC") if moonset else "None")

    # Timeline bounds
    start = min(sunset, moonrise) if moonrise and moonrise < sunset else sunset
    end = max(sunrise, moonset) if moonset and moonset > sunrise else sunrise

    # Collect and display events within the window
    night_events = [
        (e["time"], e["label"]) for e in events
        if start <= e["time"] <= end
    ]

    phase_name, illumination = moon_phase_info(sunset)

    night_start = _find(events, "Night begins", after=sunset, before=sunrise)
    night_end = _find(events, "Night ends", after=night_start or sunset, before=sunrise)

    if night_start and night_end:
        intervals = dark_moon_intervals(events, night_start, night_end, illumination)
        total_secs = sum((e - s).total_seconds() for s, e in intervals)
        total_hrs = total_secs / 3600
        duration_str = f"{int(total_hrs)}h {int((total_hrs % 1) * 60)}m"
        tz_label = _local(night_start).strftime("%Z")
        spans = ",  ".join(f"{_fmt_time(s)} – {_fmt_time(e)}" for s, e in intervals)
        dark_str = f"{duration_str}  ({spans} {tz_label})" if intervals else "None (moon up all night)"
        astro_night_hrs = (night_end - night_start).total_seconds() / 3600
    else:
        dark_str = "None (no astronomical darkness at this latitude/date)"
        astro_night_hrs = 0.0
        total_hrs = 0.0

    # --- Component scores ---
    # Moon: linear, 0% illuminated = 10, fully lit = 0
    moon_score = round(10 * (1 - illumination / 100), 1)

    # Dark time: scored against the full lunar cycle distribution for this location
    cycle = lunar_cycle_dark_analysis(lat, lon, target, _TZ)
    dark_score = cycle["score"]

    # Light pollution (Bortle)
    import darksky as _ds
    ds_info   = _ds.lookup(lat, lon)
    lp_line   = _ds.summary_line(lat, lon) if ds_info is not None else None
    # below_detection → bortle_class is None; exclude from score rather than guess
    bortle_score = (
        round(max(0.0, (9 - ds_info["bortle_class"]) / 8 * 10), 1)
        if ds_info and ds_info["bortle_class"] is not None
        else None
    )

    # Weather — always fetched for the rating; table shown only with --weather
    import weather as wx
    night_points  = []
    weather_score = None
    wx_error         = None
    wx_pending       = False  # future date beyond forecast range
    wx_no_data       = False  # past date, API returned no usable points
    wx_archive_error = False  # past date > 92 days, ERA5 archive unreachable
    try:
        from datetime import timezone as _tz
        now = __import__("datetime").datetime.now(_tz.utc)

        if sunrise < now:
            # Night is in the past — pick the best available historical source.
            # ≤ 92 days ago: main API with past_days (fast, same endpoint as forecast).
            # > 92 days ago: ERA5 archive API (may be slow or temporarily unavailable).
            try:
                days_ago = (date.today() - target).days
                if days_ago <= wx.OpenMeteoPastProvider._MAX_PAST_DAYS:
                    provider = wx.OpenMeteoPastProvider(days_ago + 2)  # +2 for night overlap
                else:
                    start = target.strftime("%Y-%m-%d")
                    end   = (target + timedelta(days=1)).strftime("%Y-%m-%d")
                    provider = wx.OpenMeteoHistoricalProvider(start, end)

                points  = provider.forecast(lat, lon)
                before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                during  = [p for p in points if sunset < p.time < sunrise]
                after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                if during or after:
                    # Sanity check: API sometimes returns all-null values for
                    # dates near the edge of its retention window.  Treat that
                    # as no data rather than scoring every hour at 5/10.
                    if any(p.cloud_cover_pct is not None for p in night_points):
                        weather_score = _weighted_weather_score(
                            night_points, night_start, night_end, wx.rate_conditions
                        )
                    else:
                        wx_no_data   = True
                        night_points = []
                else:
                    wx_no_data   = True
                    night_points = []
            except RuntimeError:
                if days_ago > wx.OpenMeteoPastProvider._MAX_PAST_DAYS:
                    wx_archive_error = True   # ERA5 outage, not a data lag
                else:
                    wx_no_data = True
                night_points = []
        else:
            points = wx.forecast(lat, lon)
            # Constrain windows to within 6 h of the night boundary so
            # stale points from the wrong week can't anchor the score.
            before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
            during  = [p for p in points if sunset < p.time < sunrise]
            after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
            night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

            if during or after:
                if any(p.cloud_cover_pct is not None for p in night_points):
                    weather_score = _weighted_weather_score(
                        night_points, night_start, night_end, wx.rate_conditions
                    )
                else:
                    wx_no_data   = True
                    night_points = []
            else:
                wx_pending   = True
                night_points = []
    except RuntimeError as e:
        wx_error = str(e)

    # --- Overall rating ---
    rating = _rate_night(moon_score, dark_score, weather_score, bortle_score)

    # --- Header ---
    print(f"\nDate:      {target}")
    print(f"Location:  {display_name}  ({lat:.4f}°)")
    print(f"Moon:      {phase_name}  |  {illumination}% illuminated")
    if lp_line:
        print(f"Darkness:  {lp_line}")
    cycle_str = (f"avg {cycle['mean_hours']}h  ±{cycle['stdev_hours']}h over lunar cycle")
    print(f"Dark sky:  {dark_str}  ·  {cycle_str}")

    if rating["score"] is not None:
        comp = rating["components"]
        parts = []
        parts.append(f"Moon {comp.get('moon', '—')}")
        parts.append(f"Dark {comp.get('dark', '—')}")
        wx_part = ("Wx Pending" if wx_pending
                   else ("Wx N/A" if (wx_no_data or wx_archive_error)
                         else (f"Wx {comp.get('weather')}" if weather_score is not None else "Wx —")))
        parts.append(wx_part)
        parts.append(f"Bortle {comp.get('bortle', '—') if bortle_score is not None else '—'}")
        print(f"Night score:  {rating['score']}/10  ({' · '.join(parts)})")
    print()

    # --- Timeline ---
    col_w = max((len(_fmt(dt)) for dt, _ in night_events), default=25)
    for dt, label in night_events:
        print(f"  {_fmt(dt):<{col_w}}  {label}")
    print()

    # --- Weather table (opt-in) ---
    if args.weather:
        if wx_error:
            print(f"Weather unavailable: {wx_error}\n")
        elif wx_archive_error:
            print("Historical weather archive temporarily unavailable "
                  "(archive-api.open-meteo.com is down).\n")
        elif wx_no_data:
            print("Historical weather data unavailable for this date.\n")
        elif wx_pending:
            print("Weather forecast not yet available for this date.\n")
        elif not night_points:
            print("No weather data available for this night.\n")
        else:
                # Only show columns that have data
                has_temp   = any(p.temperature_c   is not None for p in night_points)
                has_feels  = any(p.feels_like_c    is not None for p in night_points)
                has_seeing = any(p.seeing_arcsec   is not None for p in night_points)
                has_transp = any(p.transparency    is not None for p in night_points)

                # Define columns as (header, alignment)
                cols  = [("Time", "l"), ("Wx Rating", "r")]
                cols += [("Cloud",  "r")]
                cols += [("Temp",   "r")] if has_temp   else []
                cols += [("Feels",  "r")] if has_feels  else []
                cols += [("Seeing", "r")] if has_seeing else []
                cols += [("Transp", "l")] if has_transp else []
                cols += [("Humid", "r"), ("Wind", "r"), ("Precip", "l")]

                # Build all row data first so we can measure actual widths
                rows = []
                for p in night_points:
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

                # Derive column widths from the widest value in each column
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


if __name__ == "__main__":
    main()
