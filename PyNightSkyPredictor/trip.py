#!/usr/bin/env python3
"""Trip planning engine — compare dark-sky locations across a date range.

Follows the same pattern as predictor.py: a clean function that returns
a dataclass, no printing. The CLI (tripbuilder.py) handles all output.

assemble_night() in predictor.py is the core engine — plan_trip() calls
it for every (location × date) combination and wraps results in
NightSummary / TripReport dataclasses.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as _utc

from zoneinfo import ZoneInfo

from . import cache as _cache
from . import predictor as _pred

log = logging.getLogger(__name__)

_ASTRO_TTL      = 24 * 3600   # 24 hours — stable for astronomical data
_WEATHER_TTL    =      3600   # 1 hour   — weather forecast changes
_FORECAST_DAYS  = 16          # Open-Meteo forecast window (days from today)


@dataclass
class NightSummary:
    """Lightweight night result for trip comparison, derived from NightReport."""
    date:             date
    display_name:     str
    lat:              float
    lon:              float
    score:            float | None
    score_components: dict          # {moon, dark, weather, bortle}
    phase_name:       str
    illumination_pct: float
    moon_distance_km: float
    moon_special:     str | None       # 'supermoon' | 'micromoon' | None
    moon_eclipses:    list             # list[dict] — eclipses during this night
    dark_hours:       float
    bortle_score:     float | None
    weather_score:    float | None
    weather_informed: bool          # True if weather data was included in score
    wx_pending:       bool
    wx_no_data:       bool


@dataclass
class TripReport:
    """Full result of a trip plan across locations and a date range."""
    date_start: date
    date_end:   date
    locations:  list   # [{"display_name", "lat", "lon", "tz_name"}, ...]
    nights:     list   # list[NightSummary] — all (location × date) combos
    ranked:     list   # list[NightSummary] — sorted best → worst by score


# ---------------------------------------------------------------------------
# Cache serialisation
# ---------------------------------------------------------------------------

def _eclipses_to_json(eclipses: list) -> list:
    """Serialise eclipse dicts for JSON storage — convert datetime → ISO string."""
    return [
        {**e, "time": e["time"].isoformat()}
        for e in eclipses
    ]


def _eclipses_from_json(raw: list) -> list:
    """Restore eclipse dicts from JSON — convert ISO string → UTC-aware datetime."""
    result = []
    for e in raw:
        t = e["time"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t)
            if t.tzinfo is None:
                t = t.replace(tzinfo=_utc.utc)
        result.append({**e, "time": t})
    return result


def _to_dict(s: NightSummary) -> dict:
    return {
        "date":             s.date.isoformat(),
        "display_name":     s.display_name,
        "lat":              s.lat,
        "lon":              s.lon,
        "score":            s.score,
        "score_components": s.score_components,
        "phase_name":       s.phase_name,
        "illumination_pct": s.illumination_pct,
        "moon_distance_km": s.moon_distance_km,
        "moon_special":     s.moon_special,
        "moon_eclipses":    _eclipses_to_json(s.moon_eclipses),
        "dark_hours":       s.dark_hours,
        "bortle_score":     s.bortle_score,
        "weather_score":    s.weather_score,
        "weather_informed": s.weather_informed,
        "wx_pending":       s.wx_pending,
        "wx_no_data":       s.wx_no_data,
    }


def _from_dict(d: dict) -> NightSummary:
    return NightSummary(
        date             = date.fromisoformat(d["date"]),
        display_name     = d["display_name"],
        lat              = d["lat"],
        lon              = d["lon"],
        score            = d["score"],
        score_components = d["score_components"],
        phase_name       = d["phase_name"],
        illumination_pct = d["illumination_pct"],
        moon_distance_km = d.get("moon_distance_km", 384_400),
        moon_special     = d.get("moon_special"),
        moon_eclipses    = _eclipses_from_json(d.get("moon_eclipses", [])),
        dark_hours       = d["dark_hours"],
        bortle_score     = d["bortle_score"],
        weather_score    = d["weather_score"],
        weather_informed = d["weather_informed"],
        wx_pending       = d.get("wx_pending", False),
        wx_no_data       = d.get("wx_no_data", False),
    )


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _cache_key(lat: float, lon: float, d: date, with_weather: bool) -> str:
    # v3: added moon_special and moon_eclipses fields to NightSummary
    return f"night_v3:{lat:.4f},{lon:.4f},{d.isoformat()},wx={int(with_weather)}"


def _within_forecast_window(d: date) -> bool:
    return (d - datetime.now(_utc).date()).days <= _FORECAST_DAYS


def fetch_night(
    lat: float,
    lon: float,
    d: date,
    tz: ZoneInfo,
    display_name: str,
    fetch_weather: bool = True,
) -> NightSummary | None:
    """
    Return a NightSummary for one location and date, using cache where possible.

    Weather is only fetched for dates within the 16-day forecast window;
    beyond that the score uses astronomical factors only (weights
    redistribute automatically in rate_night).
    """
    use_weather = fetch_weather and _within_forecast_window(d)
    key = _cache_key(lat, lon, d, use_weather)

    cached = _cache.get(key)
    if cached is not None:
        return _from_dict(cached)

    try:
        report = _pred.assemble_night(
            lat, lon, d, tz,
            display_name=display_name,
            fetch_weather=use_weather,
        )
    except ValueError as e:
        log.warning("Skipping %s on %s: %s", display_name, d, e)
        return None

    weather_informed = (
        use_weather
        and report.weather_score is not None
        and not report.wx_no_data
        and not report.wx_pending
    )

    summary = NightSummary(
        date             = report.date,
        display_name     = report.display_name,
        lat              = report.lat,
        lon              = report.lon,
        score            = report.score,
        score_components = report.score_components,
        phase_name       = report.phase_name,
        illumination_pct = report.illumination_pct,
        moon_distance_km = report.moon_distance_km,
        moon_special     = report.moon_special,
        moon_eclipses    = report.moon_eclipses,
        dark_hours       = report.dark_hours,
        bortle_score     = report.bortle_score,
        weather_score    = report.weather_score,
        weather_informed = weather_informed,
        wx_pending       = report.wx_pending,
        wx_no_data       = report.wx_no_data,
    )

    ttl = _WEATHER_TTL if weather_informed else _ASTRO_TTL
    _cache.set(key, _to_dict(summary), ttl_seconds=ttl)
    return summary


def plan_trip(
    locations: list,
    date_start: date,
    date_end: date,
    fetch_weather: bool = True,
    progress_fn=None,
) -> TripReport:
    """
    Compute night scores for every (location × date) combination.

    Args:
        locations:    list of dicts with keys lat, lon, display_name, tz_name
        date_start:   first night of the range
        date_end:     last night of the range (inclusive)
        fetch_weather: include weather for dates within the forecast window
        progress_fn:  optional callable(completed, total) for progress reporting

    Returns:
        TripReport with all nights and a ranked list sorted best → worst.
    """
    n_days = (date_end - date_start).days + 1
    total  = n_days * len(locations)
    nights: list = []
    _lock  = threading.Lock()
    _done  = [0]

    def _fetch_one(loc, d):
        tz = ZoneInfo(loc["tz_name"])
        return fetch_night(loc["lat"], loc["lon"], d, tz, loc["display_name"], fetch_weather)

    tasks = [
        (loc, date_start + timedelta(days=i))
        for loc in locations
        for i in range(n_days)
    ]
    max_workers = min(20, len(tasks))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_fetch_one, loc, d): (loc, d) for loc, d in tasks}
        for fut in as_completed(futs):
            summary = fut.result()
            with _lock:
                _done[0] += 1
                if progress_fn:
                    progress_fn(_done[0], total)
                if summary is not None:
                    nights.append(summary)

    ranked = sorted(
        [n for n in nights if n.score is not None],
        key=lambda n: n.score,
        reverse=True,
    )

    return TripReport(
        date_start = date_start,
        date_end   = date_end,
        locations  = locations,
        nights     = nights,
        ranked     = ranked,
    )
