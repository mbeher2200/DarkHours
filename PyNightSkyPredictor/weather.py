#!/usr/bin/env python3
"""
Weather forecast abstraction for night sky planning.

Provider hierarchy (automatic, based on coordinates):
  1. NOAA/NWS  — US locations; NAM-based, no API key; accurate cloud data
  2. Open-Meteo — global fallback

Seeing / transparency are sourced separately from 7Timer ASTRO and merged
into primary-provider points by nearest timestamp.  7Timer derives seeing
from Cn² profile integration through GFS — the only free scientifically
grounded seeing source.  When 7Timer is unavailable those fields stay None
and rate_conditions() redistributes their weights automatically.

Adding a new provider:
  1. Subclass WeatherProvider
  2. Implement forecast(lat, lon) → list[WeatherPoint]
  3. Pass an instance to set_provider() or use it directly

All providers must populate WeatherPoint with standardised units.
Fields a provider cannot supply should be left as None.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from . import _http
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
    precip_type:     Optional[str]      # "none" | "rain" | "snow" | "frzr" | "icep"
    temperature_c:   Optional[float]    # °C
    feels_like_c:    Optional[float]    # °C apparent temperature
    dew_point_c:        Optional[float] = None  # °C (spread = temperature_c − dew_point_c)
    wind_direction_deg: Optional[float] = None  # degrees from north (meteorological)


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
            dew_point_c=h.get("dewpoint_2m",        [None] * len(h["time"]))[i],
            wind_direction_deg=h.get("wind_direction_10m", [None] * len(h["time"]))[i],
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
        ",relative_humidity_2m,wind_speed_10m,wind_direction_10m,rain,snowfall,dewpoint_2m"
        "&wind_speed_unit=ms"
        "&timezone=GMT"
        "&forecast_days=7"
    )

    def forecast(self, lat: float, lon: float) -> list:
        url = self._URL.format(lat=lat, lon=lon)
        log.debug("Open-Meteo request: %s", url)

        try:
            with _http.urlopen(url, timeout=10) as resp:
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
        ",relative_humidity_2m,wind_speed_10m,wind_direction_10m,rain,snowfall,dewpoint_2m"
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
# NOAA / NWS provider (US locations — NAM-based, no API key required)
# ---------------------------------------------------------------------------

def _noaa_iso_hours(dur: str) -> float:
    """Parse an ISO 8601 duration string (e.g. 'PT6H', 'P1DT3H') → hours (float)."""
    days  = int(m.group(1)) if (m := re.search(r'(\d+)D', dur)) else 0
    hrs   = int(m.group(1)) if (m := re.search(r'(\d+)H', dur)) else 0
    mins  = int(m.group(1)) if (m := re.search(r'(\d+)M', dur)) else 0
    return days * 24 + hrs + mins / 60


def _noaa_expand(values: list) -> dict:
    """
    Expand NWS ISO 8601 interval values into a {datetime (UTC): value} dict.

    NWS grid data uses entries like::

        {"validTime": "2026-05-26T20:00:00+00:00/PT3H", "value": 45}

    meaning the value applies for 3 hours.  This function expands each entry
    into individual hourly keys.
    """
    result = {}
    for item in values:
        if item.get("value") is None:
            continue
        t_str, dur = item["validTime"].split("/")
        start  = datetime.fromisoformat(t_str).astimezone(timezone.utc)
        n_hrs  = max(1, int(_noaa_iso_hours(dur)))
        for h in range(n_hrs):
            t = (start + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
            if t not in result:
                result[t] = item["value"]
    return result


def _noaa_precip_type(wx_list) -> str:
    """Derive precip_type from a NWS weather value list."""
    for entry in (wx_list or []):
        if entry.get("coverage") in (None, "none", ""):
            continue
        kind = (entry.get("weather") or "").lower()
        if not kind:
            continue
        # Order matters: snow_showers must hit snow before the shower check
        if "thunderstorm" in kind:
            return "rain"
        if "snow" in kind or "blizzard" in kind or "ice_crystal" in kind:
            return "snow"
        if "freezing" in kind or "ice_pellet" in kind:
            return "frzr"
        if "rain" in kind or "drizzle" in kind or "shower" in kind:
            return "rain"
    return "none"


def _parse_noaa_grid(data: dict) -> list:
    """Parse a NWS forecastGridData response → list[WeatherPoint]."""
    props = data["properties"]

    sky        = _noaa_expand(props.get("skyCover",            {}).get("values", []))
    temp       = _noaa_expand(props.get("temperature",          {}).get("values", []))
    dewpt      = _noaa_expand(props.get("dewpoint",             {}).get("values", []))  # °C
    humid      = _noaa_expand(props.get("relativeHumidity",     {}).get("values", []))
    wind       = _noaa_expand(props.get("windSpeed",            {}).get("values", []))  # km/h
    wind_dir   = _noaa_expand(props.get("windDirection",        {}).get("values", []))  # degrees
    wind_chill = _noaa_expand(props.get("windChill",            {}).get("values", []))  # °C
    heat_index = _noaa_expand(props.get("heatIndex",            {}).get("values", []))  # °C
    wx_vals    = _noaa_expand(props.get("weather",              {}).get("values", []))

    points = []
    for t in sorted(sky.keys()):
        # At the NWS 7-day boundary skyCover extends slightly further than
        # temperature/humidity/wind.  Drop hours with no temperature — they
        # produce misleading cloud-only rows and can't be scored meaningfully.
        if t not in temp:
            continue
        wind_kmh = wind.get(t)

        # Apparent temperature: prefer windChill (cold/windy), then heatIndex
        # (hot/humid); NWS only populates these when conditions warrant them.
        # When neither applies, conditions are mild and apparent temp ≈ actual
        # temp, so fall back to temperature_c to keep the column populated.
        wc = wind_chill.get(t)
        hi = heat_index.get(t)
        if wc is not None:
            feels = wc
        elif hi is not None:
            feels = hi
        else:
            feels = temp.get(t)   # mild — apparent temp equals actual temp

        points.append(WeatherPoint(
            time            = t,
            cloud_cover_pct = round(sky[t])       if t in sky   else None,
            seeing_arcsec   = None,               # filled later by 7Timer blend
            transparency    = None,
            humidity_pct    = round(humid[t])     if t in humid else None,
            wind_speed_ms   = round(wind_kmh / 3.6, 2) if wind_kmh is not None else None,
            lifted_index    = None,
            precip_type     = _noaa_precip_type(wx_vals.get(t)),
            temperature_c      = temp.get(t),
            feels_like_c       = feels,
            dew_point_c        = dewpt.get(t),
            wind_direction_deg = wind_dir.get(t),
        ))
    return points


class NOAAProvider(WeatherProvider):
    """
    NOAA / NWS hourly forecast for US locations.

    Uses the NWS ``forecastGridData`` endpoint which provides accurate
    NAM-model sky cover percentages, temperature, wind, humidity, and
    precipitation type.  No API key required.

    Raises RuntimeError("Location not covered by NOAA/NWS") for coordinates
    outside NWS coverage (i.e. anywhere outside the US and territories).
    """
    name    = "NOAA/NWS"
    _POINTS = "https://api.weather.gov/points/{lat},{lon}"
    _HEADERS = {
        "User-Agent": "PyNightSkyPredictor/1.0",
        "Accept":     "application/geo+json",
    }

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers=self._HEADERS)
        with _http.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())

    def forecast(self, lat: float, lon: float) -> list:
        # Step 1: resolve NWS grid point for these coordinates
        try:
            meta = self._get(self._POINTS.format(lat=f"{lat:.4f}", lon=f"{lon:.4f}"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError("Location not covered by NOAA/NWS")
            raise RuntimeError(f"NOAA points lookup failed: HTTP {e.code}")
        except Exception as e:
            raise RuntimeError(f"NOAA points lookup failed: {e}")

        grid_url = meta["properties"]["forecastGridData"]
        log.debug("NOAA grid URL: %s", grid_url)

        # Step 2: fetch grid data (sky cover, temperature, wind, precipitation)
        try:
            data = self._get(grid_url)
        except Exception as e:
            raise RuntimeError(f"NOAA grid data request failed: {e}")

        points = _parse_noaa_grid(data)
        log.debug("NOAA returned %d hourly points", len(points))
        return points


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
                wind_direction_deg=self._WIND_DIR.get(wind.get("direction")),
            ))

        return points


# ---------------------------------------------------------------------------
# 7Timer seeing blend
# ---------------------------------------------------------------------------

def _blend_7timer(points: list, lat: float, lon: float) -> list:
    """
    Fetch 7Timer ASTRO and merge seeing_arcsec / transparency / lifted_index
    into existing WeatherPoints by nearest timestamp (within 90 minutes).

    7Timer provides these fields at 3-hour intervals so multiple consecutive
    hourly points will share the same seeing value — this is correct behaviour
    since 7Timer's resolution is 3 hours.

    On any 7Timer failure the points are returned unchanged.  rate_conditions()
    redistributes the seeing/transparency weights automatically when those
    fields are None, so partial data degrades gracefully.
    """
    try:
        seven = SevenTimerProvider().forecast(lat, lon)
    except Exception as e:
        log.debug("7Timer unavailable — proceeding without seeing data: %s", e)
        return points

    result = []
    for p in points:
        nearest  = min(seven, key=lambda s: abs((s.time - p.time).total_seconds()))
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

def rate_conditions(p: WeatherPoint) -> int:
    """
    Rate sky conditions for astrophotography from 1 (unusable) to 10 (perfect).

    Weights (normalised automatically when a field is unavailable):
      cloud cover  50%  — gates everything; heavy cloud = low ceiling on score
      seeing       20%  — atmospheric steadiness (populated when 7Timer available)
      transparency 15%  — sky clarity / extinction (populated when 7Timer available)
      wind         10%  — vibration, tracking, and turbulence
      humidity      5%  — transparency and dew risk

    Precipitation of any kind caps the score at 1.

    When seeing and transparency are None (Open-Meteo or NOAA without 7Timer
    blend), their combined 35% redistributes to the remaining factors.  This
    means nights with 7Timer data available are scored on more information
    than nights without — which is the correct behaviour.
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

    total_weight = sum(weights[k] for k in scores)
    weighted_sum = sum(scores[k] * weights[k] for k in scores) / total_weight
    return max(1, min(10, round(weighted_sum * 10)))


# ---------------------------------------------------------------------------
# Module-level interface
# ---------------------------------------------------------------------------

_provider: WeatherProvider | None = None   # None = auto-select by coordinates


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
    Fetch a forecast for the given coordinates using the best available provider,
    then blend seeing / transparency / lifted_index from 7Timer ASTRO.

    Returns
    -------
    points : list[WeatherPoint]
    source : str
        Human-readable description of data sources used, e.g.
        "NOAA/NWS + 7Timer" or "Open-Meteo".

    Provider selection (when not overridden via set_provider):
      • NOAA/NWS    — US locations (continental US, Alaska, Hawaii, territories)
      • Open-Meteo  — all other locations

    Seeing is always sourced from 7Timer ASTRO regardless of primary provider.
    If 7Timer is unavailable, those fields remain None and rate_conditions()
    redistributes their weights automatically — the score is still valid, just
    computed from fewer factors.
    """
    if _provider is not None:
        log.debug("Using explicit provider: %s", _provider.name)
        primary_name = _provider.name
        points = _provider.forecast(lat, lon)
    else:
        # Auto-select: try NOAA, fall back to Open-Meteo for non-US coordinates
        try:
            points = NOAAProvider().forecast(lat, lon)
            primary_name = "NOAA/NWS"
            log.debug("Using NOAA/NWS for %.4f, %.4f", lat, lon)
        except RuntimeError as e:
            if "not covered" in str(e).lower():
                log.debug("Outside NOAA coverage — using Open-Meteo for %.4f, %.4f", lat, lon)
                points = OpenMeteoProvider().forecast(lat, lon)
                primary_name = "Open-Meteo"
            else:
                raise

    blended    = _blend_7timer(points, lat, lon)
    has_seeing = any(p.seeing_arcsec is not None for p in blended)
    source     = f"{primary_name} + 7Timer" if has_seeing else primary_name
    return blended, source
