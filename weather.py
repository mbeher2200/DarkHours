#!/usr/bin/env python3
"""
Weather forecast abstraction for night sky planning.

Adding a new provider:
  1. Subclass WeatherProvider
  2. Implement forecast(lat, lon) → list[WeatherPoint]
  3. Pass an instance to set_provider() or use it directly

All providers must populate WeatherPoint with standardised units.
Fields a provider cannot supply should be left as None.
"""

import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standardised data model
# ---------------------------------------------------------------------------

@dataclass
class WeatherPoint:
    """One forecast moment in standardised units."""
    time: datetime                    # UTC, timezone-aware
    cloud_cover_pct: Optional[int]    # 0–100
    seeing_arcsec: Optional[float]    # arcseconds, lower = better
    transparency: Optional[str]       # "Excellent" / "Good" / "Fair" / "Poor"
    humidity_pct: Optional[int]       # 0–100
    wind_speed_ms: Optional[float]    # m/s
    lifted_index: Optional[int]       # positive = stable, negative = unstable
    precip_type: Optional[str]        # "none" | "rain" | "snow" | "frzr" | "icep"
    temperature_c: Optional[float]    # °C
    feels_like_c: Optional[float]     # °C apparent temperature


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
            feels_like_c=h.get("apparent_temperature", [None] * len(h["time"]))[i],
        ))
    return points


# ---------------------------------------------------------------------------
# Open-Meteo provider (primary — 7-day forecast)
# ---------------------------------------------------------------------------

class OpenMeteoProvider(WeatherProvider):
    name = "Open-Meteo"
    _URL = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude={lat}&longitude={lon}"
        "&hourly=cloud_cover,temperature_2m,apparent_temperature"
        ",relative_humidity_2m,wind_speed_10m,rain,snowfall"
        "&wind_speed_unit=ms"
        "&timezone=GMT"
        "&forecast_days=7"
    )

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon)
        log.debug("Open-Meteo request: %s", url)

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Open-Meteo request failed: {e}")

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
        ",relative_humidity_2m,wind_speed_10m,rain,snowfall"
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
            with urllib.request.urlopen(url, timeout=10) as resp:
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
        ",relative_humidity_2m,wind_speed_10m,rain,snowfall"
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
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Open-Meteo Historical request failed: {e}")

        h = data["hourly"]
        log.debug("Open-Meteo Historical returned %d hourly points", len(h["time"]))
        return _parse_open_meteo_hourly(h)


# ---------------------------------------------------------------------------
# 7Timer ASTRO provider
# ---------------------------------------------------------------------------

class SevenTimerProvider(WeatherProvider):
    name = "7Timer"
    _URL = "https://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=astro&output=json"

    _CLOUD_PCT   = {1: 3, 2: 12, 3: 25, 4: 37, 5: 50, 6: 62, 7: 75, 8: 87, 9: 97}
    _SEEING_ARCSEC = {1: 0.4, 2: 0.6, 3: 0.87, 4: 1.12, 5: 1.37, 6: 1.75, 7: 2.25, 8: 3.0}
    _TRANSP_LABEL = {
        1: "Excellent", 2: "Excellent",
        3: "Good",      4: "Good",
        5: "Fair",      6: "Fair",
        7: "Poor",      8: "Poor",
    }
    _WIND_MS = {1: 0.2, 2: 1.5, 3: 3.3, 4: 5.5, 5: 8.0, 6: 11.0, 7: 13.9, 8: 17.2}

    @staticmethod
    def _rh2m_to_pct(idx: int) -> int:
        return max(0, min(100, (idx + 4) * 5 + 2))

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon)
        log.debug("7Timer request: %s", url)

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"7Timer request failed: {e}")

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
            ))

        return points


# ---------------------------------------------------------------------------
# Conditions rating
# ---------------------------------------------------------------------------

def rate_conditions(p: WeatherPoint) -> int:
    """
    Rate sky conditions for astrophotography from 1 (unusable) to 10 (perfect).

    Weights (normalised automatically when a field is unavailable):
      cloud cover  50%  — gates everything; heavy cloud = low ceiling on score
      seeing       20%  — atmospheric steadiness
      transparency 15%  — sky clarity / extinction
      wind         10%  — vibration, tracking, and turbulence
      humidity      5%  — transparency and dew risk

    Precipitation of any kind caps the score at 1.
    """
    if p.precip_type and p.precip_type not in ("none", None):
        return 1

    scores  = {}
    weights = {}

    if p.cloud_cover_pct is not None:
        # Non-linear: penalise heavy cloud more steeply above 50%
        c = p.cloud_cover_pct / 100
        scores["cloud"]  = max(0.0, 1 - c ** 0.7)
        weights["cloud"] = 0.50

    if p.seeing_arcsec is not None:
        # 0.4" (best) → 1.0,  3.0" (worst) → 0.0
        scores["seeing"]  = max(0.0, (3.0 - p.seeing_arcsec) / 2.6)
        weights["seeing"] = 0.20

    if p.transparency is not None:
        scores["transp"]  = {"Excellent": 1.0, "Good": 0.75, "Fair": 0.4, "Poor": 0.1}.get(p.transparency, 0.5)
        weights["transp"] = 0.15

    if p.wind_speed_ms is not None:
        scores["wind"]  = max(0.0, 1 - p.wind_speed_ms / 12)
        weights["wind"] = 0.10

    if p.humidity_pct is not None:
        # Below 50% = no penalty; above 90% = zero
        scores["humid"]  = max(0.0, 1 - max(0, p.humidity_pct - 50) / 40)
        weights["humid"] = 0.05

    if not scores:
        return 5  # no data

    total_weight  = sum(weights[k] for k in scores)
    weighted_sum  = sum(scores[k] * weights[k] for k in scores) / total_weight
    return max(1, min(10, round(weighted_sum * 10)))


# ---------------------------------------------------------------------------
# Module-level interface
# ---------------------------------------------------------------------------

_provider: WeatherProvider = OpenMeteoProvider()


def set_provider(provider: WeatherProvider):
    """Replace the active weather provider globally."""
    global _provider
    _provider = provider
    log.debug("Weather provider set to: %s", provider.name)


def get_provider() -> WeatherProvider:
    return _provider


def forecast(lat: float, lon: float) -> list:
    """Fetch a forecast using the active provider."""
    log.debug("Fetching weather via %s", _provider.name)
    return _provider.forecast(lat, lon)
