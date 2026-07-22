#!/usr/bin/env python3
"""
Live haze cross-check — WAQI (World Air Quality Index, aqicn.org) real-time
station readings.

This is NOT a general air-quality/health feature. The only thing it answers
is: "does a nearby ground station's live particulate reading say it's hazy
right now?" — as a cross-check against the forecast-driven Haze icon
(icons.tsx), whose PM2.5/AOD come from Open-Meteo's CAMS forecast and can
diverge from a fast-moving real-world event (e.g. wildfire smoke) that
ground stations already see.

Only the pollutant-specific PM2.5 (fallback PM10) sub-index is used, never
WAQI's blended top-level `aqi` — that number is max() over ALL pollutants
(including gas-phase ones like ozone that don't scatter starlight), so using
it directly would produce false positives/negatives for exactly what this
module cares about.

WAQI's `geo:lat;lon` query returns whatever station it considers "nearest",
even when that station is hundreds or thousands of km away in a
sparse-coverage region — silently attributing a reading to conditions that
have nothing to do with the queried location. `current_haze()` rejects any
station beyond MAX_STATION_DISTANCE_KM, treating it the same as "no PM data
nearby" rather than showing a misleading cross-check.

Public API:
    current_haze(lat, lon) -> dict | None
        {"pm_value": int, "pollutant": "pm25"|"pm10", "hazy": bool,
         "station": str | None, "observed_at": str | None, "stale": bool}
        None when unavailable: no token configured, fetch failed with no
        stale cache to fall back on, the nearest station reports neither
        PM2.5 nor PM10, or the nearest station is too far away to be
        regionally representative.
"""
import json
import logging
import math
import os
import threading
import urllib.error

from . import _http
from . import cache as _cache
from . import circuit_breaker as _cb
from . import provider_health as _ph
from . import rate_limiter as _rl

log = logging.getLogger(__name__)

WAQI_URL = "https://api.waqi.info/feed/geo:{lat};{lon}/?token={token}"

AQICN_TTL = 1800  # 30 min — WAQI stations update roughly hourly; matches the
                  # KP_TTL precedent in aurora.py for a short-lived upstream value.

_TOKEN = os.environ.get("AQICN_TOKEN", "")

# Sub-index cutoff for "hazy". EPA's own PM2.5 breakpoint table puts AQI 100
# at ~35.4 ug/m3, which lines up with this app's existing forecast-side haze
# threshold (icons.tsx pmHazy: pm25 > 35 ug/m3). PM10's breakpoint table is
# coarser-particle-scaled, so this isn't an identical physical severity for a
# PM10 fallback reading — it's a reasonable proxy, not an exact equivalence.
HAZY_SUBINDEX = 100

# WAQI's "nearest station" can be arbitrarily far away in sparse-coverage
# regions (observed: a Kampala/Nairobi query returning a station in Mayotte,
# ~2500 km away). 100 km mirrors this app's own existing notion of "nearby"
# (the Find Sky Nearby search's 60 mi default radius, ~97 km) — a station
# further than that isn't a meaningful stand-in for local haze conditions.
MAX_STATION_DISTANCE_KM = 100.0

_EARTH_RADIUS_KM = 6371.0

# Per-location locks (not a single global lock): unlike aurora.py's two
# genuinely-global SWPC products, this cache key is per-(lat, lon) — a
# /calendar multi-night fan-out for one location would otherwise thunder-herd
# a single shared lock for no reason. Mirrors weather.py's lock_for.
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


def lock_for(lat: float, lon: float) -> threading.Lock:
    key = f"{lat:.2f},{lon:.2f}"
    with _fetch_locks_guard:
        lock = _fetch_locks.get(key)
        if lock is None:
            lock = _fetch_locks[key] = threading.Lock()
        return lock


def _cache_key(lat: float, lon: float) -> str:
    return f"aqicn|{lat:.2f}|{lon:.2f}"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _fetch_url(lat: float, lon: float, timeout: int = 10) -> str:
    """GET the WAQI feed for (lat, lon); provider-health accounting."""
    url = WAQI_URL.format(lat=lat, lon=lon, token=_TOKEN)
    if not _cb.allow("waqi"):
        raise _cb.unavailable("waqi")
    try:
        with _rl.acquire("waqi"), _http.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        # Reachability success for the breaker; content-level verdicts (bad
        # JSON, non-"ok" status in _parse) are deliberately not counted.
        _cb.on_success("waqi")
        return text
    except urllib.error.HTTPError as e:
        _ph.record("waqi", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
        _cb.on_failure("waqi")
        raise RuntimeError(f"WAQI HTTP {e.code}") from e
    except urllib.error.URLError as e:
        _ph.record("waqi", "error", str(e.reason)[:120])
        _cb.on_failure("waqi")
        raise RuntimeError(f"WAQI unreachable: {e.reason}") from e


def _parse(text: str, lat: float, lon: float) -> dict | None:
    """WAQI feed JSON -> the current_haze contract (minus `stale`, filled in
    by the caller). None when the response is malformed, the station reports
    an error status, there's no PM2.5/PM10 reading to cross-check with, or
    the nearest station is further than MAX_STATION_DISTANCE_KM away.
    Records provider_health for a well-formed response; parse-level failures
    (bad JSON, non-"ok" status) are also recorded so /healthz reflects them."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as e:
        _ph.record("waqi", "error", f"bad JSON: {str(e)[:100]}")
        raise RuntimeError("WAQI response was not valid JSON") from e

    if payload.get("status") != "ok":
        _ph.record("waqi", "error", f"status={payload.get('status')}")
        raise RuntimeError(f"WAQI status={payload.get('status')}")

    data = payload.get("data") or {}
    city = data.get("city") or {}

    geo = city.get("geo")
    if (not isinstance(geo, (list, tuple)) or len(geo) != 2
            or not all(isinstance(v, (int, float)) for v in geo)):
        # Can't verify the station is nearby — treat like "no usable data"
        # rather than trust an unverifiable reading. WAQI always includes
        # geo in practice, so this is a defensive edge case, not the norm.
        _ph.record("waqi", "ok")
        return None
    distance_km = _haversine_km(lat, lon, geo[0], geo[1])
    if distance_km > MAX_STATION_DISTANCE_KM:
        log.info("AQICN nearest station %.0f km away (> %.0f km cutoff) for (%.2f, %.2f) — skipping",
                  distance_km, MAX_STATION_DISTANCE_KM, lat, lon)
        _ph.record("waqi", "ok")
        return None

    iaqi = data.get("iaqi") or {}
    pm25 = (iaqi.get("pm25") or {}).get("v")
    pm10 = (iaqi.get("pm10") or {}).get("v")
    if pm25 is not None:
        pm_value, pollutant = pm25, "pm25"
    elif pm10 is not None:
        pm_value, pollutant = pm10, "pm10"
    else:
        _ph.record("waqi", "ok")  # station reached fine, just no PM sensor
        return None

    _ph.record("waqi", "ok")
    return {
        "pm_value": round(float(pm_value)),
        "pollutant": pollutant,
        "hazy": float(pm_value) > HAZY_SUBINDEX,
        "station": city.get("name"),
        "observed_at": (data.get("time") or {}).get("iso"),
    }


def current_haze(lat: float, lon: float) -> dict | None:
    """Fresh cache -> single-flight fetch -> stale-cache fallback -> None.

    Never raises; this is a live cross-check, not a pipeline dependency — any
    failure (missing token, network error, malformed response, no PM data at
    the nearest station, nearest station too far away) degrades to None or a
    stale reading, same "enrichment, never fatal" philosophy as
    weather._fetch_air_quality.
    """
    if not _TOKEN:
        return None

    key = _cache_key(lat, lon)
    cached = _cache.get(key)
    if cached is not None:
        return {**cached, "stale": False}

    with lock_for(lat, lon):
        cached = _cache.get(key)
        if cached is not None:
            return {**cached, "stale": False}
        try:
            fresh = _parse(_fetch_url(lat, lon), lat, lon)
            if fresh is not None:
                _cache.set(key, fresh, ttl_seconds=AQICN_TTL)
                return {**fresh, "stale": False}
        except Exception as e:
            log.warning("AQICN fetch failed for %s: %s", key, e)

    stale = _cache.get_stale(key)
    return {**stale, "stale": True} if stale is not None else None
