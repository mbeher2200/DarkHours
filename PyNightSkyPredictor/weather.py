#!/usr/bin/env python3
"""
Weather forecast abstraction for night sky planning.

Single provider: Open-Meteo (global, no API key, one HTTP call).
Seeing / transparency are sourced separately from 7Timer ASTRO and merged
into the primary points by nearest timestamp.  7Timer derives seeing from
Cn² profile integration through GFS — the only free scientifically grounded
seeing source.  When 7Timer is unavailable those fields stay None and
rate_conditions() redistributes their weights automatically.

Adding a new provider:
  1. Subclass WeatherProvider
  2. Implement forecast(lat, lon) → list[WeatherPoint]
  3. Pass an instance to set_provider() or use it directly

All providers must populate WeatherPoint with standardised units.
Fields a provider cannot supply should be left as None.
"""

import concurrent.futures as _futures
import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from . import _http
from . import provider_health as _ph
from dataclasses import dataclass, replace as _dc_replace
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standardised data model
# ---------------------------------------------------------------------------

@dataclass
class WeatherPoint:
    """One forecast moment in standardised units."""
    time:            datetime           # UTC, timezone-aware
    cloud_cover_pct: Optional[int]      # 0–100
    seeing_arcsec:   Optional[float]    # arcseconds, lower = better (7Timer only)
    transparency:    Optional[str]      # "Excellent" / "Good" / "Fair" / "Poor"
    humidity_pct:    Optional[int]      # 0–100
    wind_speed_ms:   Optional[float]    # m/s
    lifted_index:    Optional[int]      # positive = stable, negative = unstable
    precip_type:          Optional[str]  # "none" | "rain" | "snow" | "frzr" | "icep"
    temperature_c:        Optional[float]  # °C
    feels_like_c:         Optional[float]  # °C apparent temperature
    dew_point_c:             Optional[float] = None  # °C (spread = temperature_c − dew_point_c)
    wind_direction_deg:      Optional[float] = None  # degrees from north (meteorological)
    precip_probability_pct:  Optional[int]  = None  # 0–100 % chance of precipitation
    weather_code:            Optional[int]  = None  # WMO weather interpretation code


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class WeatherProvider(ABC):
    name: str = "Unknown"

    @abstractmethod
    def forecast(self, lat: float, lon: float) -> list:
        """
        Return a list of WeatherPoints sorted by time (UTC).
        Implementations should cover at least the next 24 hours.
        """
        ...


# ---------------------------------------------------------------------------
# Shared Open-Meteo hourly parser (forecast + historical use the same format)
# ---------------------------------------------------------------------------

def _parse_open_meteo_hourly(h: dict) -> list:
    """Parse an Open-Meteo ``hourly`` JSON dict → list[WeatherPoint]."""
    points = []
    for i, t_str in enumerate(h["time"]):
        t = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)

        rain     = h["rain"][i] or 0
        snowfall = h["snowfall"][i] or 0
        if snowfall > 0:
            precip_type = "snow"
        elif rain > 0:
            precip_type = "rain"
        else:
            precip_type = "none"

        n = len(h["time"])
        points.append(WeatherPoint(
            time=t,
            cloud_cover_pct=h["cloud_cover"][i],
            seeing_arcsec=None,
            transparency=None,
            humidity_pct=h["relative_humidity_2m"][i],
            wind_speed_ms=h["wind_speed_10m"][i],
            lifted_index=None,
            precip_type=precip_type,
            temperature_c=h["temperature_2m"][i],
            feels_like_c=h.get("apparent_temperature", [None] * n)[i],
            dew_point_c=h.get("dewpoint_2m",           [None] * n)[i],
            wind_direction_deg=h.get("wind_direction_10m",       [None] * n)[i],
            precip_probability_pct=h.get("precipitation_probability", [None] * n)[i],
            weather_code=h.get("weather_code", [None] * n)[i],
        ))
    return points


# ---------------------------------------------------------------------------
# Open-Meteo provider (primary — 7-day forecast)
# ---------------------------------------------------------------------------

class OpenMeteoProvider(WeatherProvider):
    """
    Open-Meteo hourly forecast — global, no API key, single HTTP call.

    Uses ``best_match`` model selection: Open-Meteo automatically picks the
    highest-resolution model available for the location and forecast horizon
    (HRRR for CONUS near-term, GFS/ECMWF elsewhere or further out).
    """
    name = "Open-Meteo"
    _URL = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&hourly=cloud_cover,temperature_2m,apparent_temperature"
        ",relative_humidity_2m,wind_speed_10m,wind_direction_10m,rain,snowfall,dewpoint_2m"
        ",precipitation_probability,weather_code"
        "&wind_speed_unit=ms"
        "&timezone=GMT"
        "&forecast_days=7"
        "&models=best_match"
    )

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon)
        log.debug("Open-Meteo request: %s", url)

        try:
            with _http.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            _ph.record("open_meteo", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
            raise RuntimeError(f"Open-Meteo request failed: {e}")
        except Exception as e:
            _ph.record("open_meteo", "error", str(e)[:120])
            raise RuntimeError(f"Open-Meteo request failed: {e}")

        _ph.record("open_meteo", "ok")
        h = data["hourly"]
        log.debug("Open-Meteo returned %d hourly points", len(h["time"]))
        return _parse_open_meteo_hourly(h)


# ---------------------------------------------------------------------------
# Open-Meteo Recent-Past provider (main API, past_days parameter — up to 92 days back)
# ---------------------------------------------------------------------------

class OpenMeteoPastProvider(WeatherProvider):
    """
    Recent historical data via Open-Meteo's ``past_days`` parameter.

    Uses the same reliable main API endpoint as the forecast provider,
    so it is not subject to the archive-api outages that affect ERA5.
    Supports up to 92 days back from today (free-tier limit).

    Example::

        p = OpenMeteoPastProvider(past_days=30)
        points = p.forecast(lat, lon)   # returns 30+ days of hourly data
    """
    name = "Open-Meteo Recent"
    _URL = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&past_days={past_days}&forecast_days=1"
        "&hourly=cloud_cover,temperature_2m,apparent_temperature"
        ",relative_humidity_2m,wind_speed_10m,wind_direction_10m,rain,snowfall,dewpoint_2m"
        ",precipitation_probability,weather_code"
        "&wind_speed_unit=ms"
        "&timezone=GMT"
    )
    _MAX_PAST_DAYS = 92  # free-tier limit

    def __init__(self, past_days: int):
        self.past_days = min(past_days, self._MAX_PAST_DAYS)

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon, past_days=self.past_days)
        log.debug("Open-Meteo Recent request: %s", url)

        try:
            with _http.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Open-Meteo Recent request failed: {e}")

        h = data["hourly"]
        log.debug("Open-Meteo Recent returned %d hourly points", len(h["time"]))
        return _parse_open_meteo_hourly(h)


# ---------------------------------------------------------------------------
# Open-Meteo Historical provider (ERA5 reanalysis — 1940 to ~5 days ago)
# ---------------------------------------------------------------------------

class OpenMeteoHistoricalProvider(WeatherProvider):
    """
    ERA5 reanalysis archive via Open-Meteo.

    Same variables as the forecast provider; data is typically available
    up to ~5 days before today. Construct with ISO date strings and call
    forecast(lat, lon) normally.

    Example::

        p = OpenMeteoHistoricalProvider("2025-01-15", "2025-01-16")
        points = p.forecast(lat, lon)
    """
    name = "Open-Meteo Historical"
    _URL = (
        "https://archive-api.open-meteo.com/v1/archive"
        "?latitude={lat}&longitude={lon}"
        "&start_date={start}&end_date={end}"
        "&hourly=cloud_cover,temperature_2m,apparent_temperature"
        ",relative_humidity_2m,wind_speed_10m,wind_direction_10m,rain,snowfall,dewpoint_2m"
        "&wind_speed_unit=ms"
        "&timezone=GMT"
    )

    def __init__(self, start_date: str, end_date: str):
        """
        Parameters
        ----------
        start_date, end_date:
            ISO date strings (``YYYY-MM-DD``). To cover a full astronomical
            night pass the calendar date of sunset and the next calendar date.
        """
        self.start_date = start_date
        self.end_date   = end_date

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon,
                               start=self.start_date, end=self.end_date)
        log.debug("Open-Meteo Historical request: %s", url)

        try:
            with _http.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Open-Meteo Historical request failed: {e}")

        h = data["hourly"]
        log.debug("Open-Meteo Historical returned %d hourly points", len(h["time"]))
        return _parse_open_meteo_hourly(h)


# ---------------------------------------------------------------------------
# 7Timer ASTRO provider (seeing + transparency via Cn² profile integration)
# ---------------------------------------------------------------------------

class SevenTimerProvider(WeatherProvider):
    name = "7Timer"
    _URL = "https://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=astro&output=json"

    _CLOUD_PCT     = {1: 3, 2: 12, 3: 25, 4: 37, 5: 50, 6: 62, 7: 75, 8: 87, 9: 97}
    _SEEING_ARCSEC = {1: 0.4, 2: 0.6, 3: 0.87, 4: 1.12, 5: 1.37, 6: 1.75, 7: 2.25, 8: 3.0}
    _TRANSP_LABEL  = {
        1: "Excellent", 2: "Excellent",
        3: "Good",      4: "Good",
        5: "Fair",      6: "Fair",
        7: "Poor",      8: "Poor",
    }
    _WIND_MS  = {1: 0.2, 2: 1.5, 3: 3.3, 4: 5.5, 5: 8.0, 6: 11.0, 7: 13.9, 8: 17.2}
    _WIND_DIR = {"N": 0, "NE": 45, "E": 90, "SE": 135,
                 "S": 180, "SW": 225, "W": 270, "NW": 315}

    @staticmethod
    def _rh2m_to_pct(idx: int) -> int:
        return max(0, min(100, (idx + 4) * 5 + 2))

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon)
        log.debug("7Timer request: %s", url)

        try:
            with _http.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            _ph.record("seven_timer", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
            raise RuntimeError(f"7Timer request failed: {e}")
        except Exception as e:
            _ph.record("seven_timer", "error", str(e)[:120])
            raise RuntimeError(f"7Timer request failed: {e}")

        _ph.record("seven_timer", "ok")
        init_str = data["init"]
        init = datetime(
            int(init_str[0:4]), int(init_str[4:6]), int(init_str[6:8]),
            int(init_str[8:10]), tzinfo=timezone.utc,
        )
        log.debug("7Timer init: %s  (%d points)", init, len(data["dataseries"]))

        points = []
        for entry in data["dataseries"]:
            t    = init + timedelta(hours=entry["timepoint"])
            wind = entry.get("wind10m") or {}
            temp = entry.get("temp2m")
            points.append(WeatherPoint(
                time=t,
                cloud_cover_pct=self._CLOUD_PCT.get(entry.get("cloudcover")),
                seeing_arcsec=self._SEEING_ARCSEC.get(entry.get("seeing")),
                transparency=self._TRANSP_LABEL.get(entry.get("transparency")),
                humidity_pct=self._rh2m_to_pct(entry["rh2m"]) if "rh2m" in entry else None,
                wind_speed_ms=self._WIND_MS.get(wind.get("speed")),
                lifted_index=entry.get("lifted_index"),
                precip_type=entry.get("prec_type"),
                temperature_c=float(temp) if temp is not None else None,
                feels_like_c=None,
                wind_direction_deg=self._WIND_DIR.get(wind.get("direction")),
            ))

        return points


# ---------------------------------------------------------------------------
# 7Timer seeing blend
# ---------------------------------------------------------------------------

import bisect
from dataclasses import replace as _dc_replace

def _merge_7timer(points: list, seven: list) -> list:
    """Merge pre-fetched 7Timer ASTRO seeing/transparency into WeatherPoints."""
    if not seven:
        return points

    # Pre-extract epochs once for O(log M) bisection
    seven_epochs = [s.time.timestamp() for s in seven]

    result = []
    for p in points:
        p_epoch = p.time.timestamp()
        idx = bisect.bisect_left(seven_epochs, p_epoch)

        # Find closest neighbor between the left and right insertion bounds
        if idx == 0:
            nearest = seven[0]
        elif idx >= len(seven):
            nearest = seven[-1]
        else:
            before, after = seven[idx - 1], seven[idx]
            if (p_epoch - seven_epochs[idx - 1]) <= (seven_epochs[idx] - p_epoch):
                nearest = before
            else:
                nearest = after

        gap_secs = abs((nearest.time - p.time).total_seconds())
        if gap_secs <= 5400:   # within 90 minutes
            p = _dc_replace(p,
                seeing_arcsec=nearest.seeing_arcsec,
                transparency=nearest.transparency,
                lifted_index=nearest.lifted_index,
            )
        result.append(p)
    return result


# ---------------------------------------------------------------------------
# Conditions rating
# ---------------------------------------------------------------------------

def rate_conditions(p: 'WeatherPoint') -> int:
    """
    Rate sky conditions for astrophotography from 1 (unusable) to 10 (perfect).

    Uses a multiplicative limiting-factor model for dealbreakers (clouds, wind, transparency)
    and an additive quality model for atmospheric steadiness (seeing, humidity).
    """
    # Precipitation is an immediate hard gate
    if p.precip_type and p.precip_type.lower() not in ("none", "", None):
        return 1

    # --- 1. THE LIMITERS (Multiplicative Penalties) ---
    # These factors act as heavy gates. A score of 0.0 here ruins the whole night.
    limiters = []

    if p.cloud_cover_pct is not None:
        # Non-linear drop-off using a 1.5 power curve.
        # e.g., 50% clouds = 0.65 multiplier. 70% clouds = 0.41. 100% = 0.0.
        cloud_score = max(0.0, 1.0 - (p.cloud_cover_pct / 100.0) ** 1.5)
        limiters.append(cloud_score)

    if p.wind_speed_ms is not None:
        # Wind force scales with velocity squared (dynamic pressure).
        # We cap it at 17 m/s (~38 mph), which makes extreeme conditions.
        wind_score = max(0.0, 1.0 - (p.wind_speed_ms / 17.0) ** 2)
        limiters.append(wind_score)

    if p.transparency is not None:
        # Poor transparency acts as a strong blocker for faint targets.
        transp_score = {"Excellent": 1.0, "Good": 0.8, "Fair": 0.4, "Poor": 0.1}.get(p.transparency, 0.5)
        limiters.append(transp_score)

    # --- 2. THE QUALITY FACTORS (Additive Base) ---
    # These determine the overall "goodness" of the night if the limiters allow it.
    base_factors = []

    if p.seeing_arcsec is not None:
        # Roughly linear scaling: 1.0" or less is excellent, 4.0" is poor.
        seeing_score = max(0.0, min(1.0, (4.0 - p.seeing_arcsec) / 3.0))
        base_factors.append(seeing_score)

    if p.humidity_pct is not None:
        # Penalizes high humidity due to dew/fog risk. Starts dropping linearly after 50%.
        humid_score = max(0.0, 1.0 - max(0.0, p.humidity_pct - 50.0) / 50.0)
        base_factors.append(humid_score)

    # Calculate base quality (average of seeing and humidity)
    # If neither is provided, assume perfect base conditions (1.0) before applying limiters.
    base_score = sum(base_factors) / len(base_factors) if base_factors else 1.0

    # Apply limiters multiplicatively
    final_score = base_score
    for limiter in limiters:
        final_score *= limiter

    # Scale to 1-10 range and round safely
    return max(1, min(10, round(final_score * 10)))


# ---------------------------------------------------------------------------
# Module-level interface
# ---------------------------------------------------------------------------

_provider: WeatherProvider | None = None   # None = auto-select (OpenMeteoProvider)


def set_provider(provider: WeatherProvider) -> None:
    """
    Override automatic provider selection with an explicit provider.

    Call with no argument (or set to None) to restore auto-selection::

        wx.set_provider(wx.SevenTimerProvider())   # force 7Timer for all locations
        wx.set_provider(None)                       # restore auto-select
    """
    global _provider
    _provider = provider
    log.debug("Weather provider explicitly set to: %s",
              provider.name if provider else "auto")


def get_provider() -> WeatherProvider | None:
    """Return the explicitly-set provider, or None if auto-selection is active."""
    return _provider


def forecast(lat: float, lon: float) -> tuple[list, str]:
    """
    Fetch a forecast for the given coordinates via Open-Meteo, then blend
    seeing / transparency / lifted_index from 7Timer ASTRO.

    Returns
    -------
    points : list[WeatherPoint]
    source : str
        Human-readable description of data sources used, e.g.
        "Open-Meteo" or "Open-Meteo + 7Timer".

    7Timer is fetched concurrently with Open-Meteo so its latency is
    hidden rather than added on top.
    """
    with _futures.ThreadPoolExecutor(max_workers=1) as _pool:
        _seven_future = _pool.submit(SevenTimerProvider().forecast, lat, lon)

        primary = _provider if _provider is not None else OpenMeteoProvider()
        primary_err: str | None = None
        try:
            points = primary.forecast(lat, lon)
            primary_name = primary.name
        except RuntimeError as e:
            primary_err = str(e)
            log.warning("Primary weather (%s) failed, falling back to 7Timer: %s",
                        primary.name, e, extra={"service": primary.name.lower()})
            # Fall back to 7Timer as full primary — it carries cloud/temp/wind/precip
            try:
                points = _seven_future.result()
                return points, "7Timer"
            except Exception as e2:
                log.error("7Timer also failed: %s", e2, extra={"service": "7timer"})
                raise RuntimeError(f"{primary_err}; 7Timer also failed: {e2}") from e2

        try:
            seven = _seven_future.result()
        except Exception as e:
            log.warning("7Timer unavailable — proceeding without seeing data: %s", e,
                        extra={"service": "7timer"})
            return points, primary_name

    blended    = _merge_7timer(points, seven)
    has_seeing = any(p.seeing_arcsec is not None for p in blended)
    source     = f"{primary_name} + 7Timer" if has_seeing else primary_name
    return blended, source
