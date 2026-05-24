#!/usr/bin/env python3
"""Night sky prediction engine — assembles a NightReport for a given location and date."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import darksky as _ds
import scoring
import sky_events as se
import weather as wx

log = logging.getLogger(__name__)


@dataclass
class NightReport:
    # Location & date
    date: date
    lat: float
    lon: float
    display_name: str
    tz_name: str

    # Sky events in the night window (UTC-aware datetimes)
    events: list          # [{"time": datetime, "label": str}, ...]

    # Key event times (UTC, timezone-aware)
    sunset: datetime
    sunrise: datetime
    night_start: datetime | None
    night_end: datetime | None
    moonrise: datetime | None
    moonset: datetime | None

    # Moon
    phase_name: str
    illumination_pct: float
    moon_score: float

    # Dark time
    dark_intervals: list  # [(start_utc, end_utc), ...]
    dark_hours: float     # total moon-free dark hours tonight
    dark_cycle: dict      # {tonight_hours, mean_hours, stdev_hours, score}
    dark_score: float

    # Light pollution (raw darksky.lookup() result)
    light_pollution: dict | None
    bortle_score: float | None

    # Weather
    weather_points: list  # list[WeatherPoint]
    weather_score: float | None
    wx_pending: bool
    wx_no_data: bool
    wx_archive_error: bool
    wx_error: str | None

    # Overall
    score: float | None
    score_components: dict  # {moon, dark, weather, bortle}

    # Visible targets (populated when fetch_targets=True)
    visible_targets: list = field(default_factory=list)


def assemble_night(
    lat: float,
    lon: float,
    target: date,
    tz: ZoneInfo,
    display_name: str = "",
    fetch_weather: bool = True,
    fetch_targets: bool = False,
) -> NightReport:
    """
    Compute a complete NightReport for the given location and date.

    Raises ValueError if no sunset or sunrise can be found for the
    date/location (e.g. polar day/night).
    """
    def _local(dt):
        return dt.astimezone(tz)

    events = se.sky_events(lat, lon, target)

    # --- Key event times ---
    sunset = next(
        (e["time"] for e in events
         if e["label"] == "Sunset" and _local(e["time"]).date() == target),
        None,
    )
    if not sunset:
        raise ValueError(f"No sunset found for {target} at {lat:.4f}, {lon:.4f}")

    sunrise = se.find_event(events, "Sunrise", after=sunset)
    if not sunrise:
        raise ValueError(f"No sunrise found after sunset on {target}")

    moonrise    = se.find_last_event(events, "Moonrise", before=sunrise)
    moonset     = se.find_event(events, "Moonset", after=sunset)
    night_start = se.find_event(events, "Astronomical night begins", after=sunset, before=sunrise)
    night_end   = se.find_event(events, "Astronomical night ends",   after=night_start or sunset, before=sunrise)

    # Events within the display window (sunset/moonrise → sunrise/moonset)
    window_start = min(sunset, moonrise) if moonrise and moonrise < sunset else sunset
    window_end   = max(sunrise, moonset) if moonset  and moonset  > sunrise else sunrise
    night_events = [e for e in events if window_start <= e["time"] <= window_end]

    # --- Moon ---
    phase_name, illumination = se.moon_phase_info(sunset)

    # --- Dark intervals ---
    if night_start and night_end:
        intervals          = se.dark_moon_intervals(events, night_start, night_end)
        dark_hours_tonight = sum((e - s).total_seconds() for s, e in intervals) / 3600
        total_astro_hours  = (night_end - night_start).total_seconds() / 3600

        # Moon score: weight illumination by the fraction of dark time the moon
        # is actually up.  Moon-free time scores full marks; moonlit time scores
        # proportionally to how dim the moon is.
        #
        #   score = 10 × (moon_free_frac  +  moonlit_frac × (1 − illum/100))
        #
        # A full moon rising 20 min before dawn no longer scores 0/10.
        # A full moon up all night still scores 0/10.
        moonlit_frac = 1.0 - (dark_hours_tonight / total_astro_hours) if total_astro_hours > 0 else 0.0
        moon_score   = round(10 * ((1 - moonlit_frac) + moonlit_frac * (1 - illumination / 100)), 1)
    else:
        # No astronomical darkness (polar summer / always dark) — timing
        # is undefined; fall back to illumination-only score.
        intervals          = []
        dark_hours_tonight = 0.0
        moon_score         = round(10 * (1 - illumination / 100), 1)

    # --- Lunar cycle dark analysis ---
    cycle      = se.lunar_cycle_dark_analysis(lat, lon, target, tz)
    dark_score = cycle["score"]

    # --- Light pollution ---
    ds_info      = _ds.lookup(lat, lon)
    bortle_score = (
        round(max(0.0, (10 - ds_info["bortle_class"]) / 9 * 10), 1)
        if ds_info and ds_info["bortle_class"] is not None
        else None
    )

    # --- Weather ---
    night_points     = []
    weather_score    = None
    wx_error         = None
    wx_pending       = False
    wx_no_data       = False
    wx_archive_error = False

    if fetch_weather:
        try:
            now = datetime.now(timezone.utc)
            if sunrise < now:
                try:
                    days_ago = (date.today() - target).days
                    if days_ago <= wx.OpenMeteoPastProvider._MAX_PAST_DAYS:
                        provider = wx.OpenMeteoPastProvider(days_ago + 2)
                    else:
                        start_str = target.strftime("%Y-%m-%d")
                        end_str   = (target + timedelta(days=1)).strftime("%Y-%m-%d")
                        provider  = wx.OpenMeteoHistoricalProvider(start_str, end_str)

                    points  = provider.forecast(lat, lon)
                    before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                    during  = [p for p in points if sunset < p.time < sunrise]
                    after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                    night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                    if during or after:
                        if any(p.cloud_cover_pct is not None for p in night_points):
                            weather_score = scoring.weighted_weather_score(
                                night_points, night_start, night_end, wx.rate_conditions
                            )
                        else:
                            wx_no_data   = True
                            night_points = []
                    else:
                        wx_no_data   = True
                        night_points = []
                except RuntimeError:
                    wx_archive_error = days_ago > wx.OpenMeteoPastProvider._MAX_PAST_DAYS
                    wx_no_data       = not wx_archive_error
                    night_points     = []
            else:
                points  = wx.forecast(lat, lon)
                before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                during  = [p for p in points if sunset < p.time < sunrise]
                after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                if during or after:
                    if any(p.cloud_cover_pct is not None for p in night_points):
                        weather_score = scoring.weighted_weather_score(
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

    # --- Visible targets ---
    target_list = []
    if fetch_targets:
        import targets as _tgt
        target_list = _tgt.visible_targets(lat, lon, sunset, sunrise, illumination,
                                            night_start=night_start, night_end=night_end)

    # --- Overall rating ---
    rating = scoring.rate_night(moon_score, dark_score, weather_score, bortle_score)

    return NightReport(
        date=target,
        lat=lat,
        lon=lon,
        display_name=display_name,
        tz_name=str(tz),
        events=night_events,
        sunset=sunset,
        sunrise=sunrise,
        night_start=night_start,
        night_end=night_end,
        moonrise=moonrise,
        moonset=moonset,
        phase_name=phase_name,
        illumination_pct=illumination,
        moon_score=moon_score,
        dark_intervals=intervals,
        dark_hours=round(dark_hours_tonight, 2),
        dark_cycle=cycle,
        dark_score=dark_score,
        light_pollution=ds_info,
        bortle_score=bortle_score,
        weather_points=night_points,
        weather_score=weather_score,
        wx_pending=wx_pending,
        wx_no_data=wx_no_data,
        wx_archive_error=wx_archive_error,
        wx_error=wx_error,
        score=rating["score"],
        score_components=rating["components"],
        visible_targets=target_list,
    )
