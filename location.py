#!/usr/bin/env python3
"""Location resolution: named presets, geocoding cache, and Nominatim lookup."""

import json
import logging
import re
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from timezonefinder import TimezoneFinder

CACHE_FILE = Path.home() / ".night-sky-predictor" / "locations.json"
USER_AGENT = "night-sky-predictor/1.0"


def _load() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


_US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _geocode_query(name: str) -> str:
    """Return the query to send to Nominatim, adding ', US' for bare zip codes."""
    if _US_ZIP_RE.match(name.strip()):
        return f"{name.strip()}, US"
    return name


def _tz_name_for(lat: float, lon: float) -> str:
    tz_name = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    if not tz_name:
        raise ValueError(f"Could not determine timezone for {lat}, {lon}")
    return tz_name


def resolve(name: str) -> tuple:
    """
    Resolve a location name to (lat, lon, display_name, tz_name).

    Checks the local cache first. On a miss, geocodes via Nominatim and
    caches the result (including timezone) so subsequent lookups are instant
    and fully offline.
    """
    cache = _load()
    key = name.strip().lower()

    if key in cache:
        entry = cache[key]
        # Migrate older entries that predate tz_name caching
        if "tz_name" not in entry:
            log.debug("Migrating '%s': adding tz_name", key)
            entry["tz_name"] = _tz_name_for(entry["lat"], entry["lon"])
            cache[key] = entry
            _save(cache)
        log.debug("Cache hit for '%s': lat=%s, lon=%s, tz=%s",
                  key, entry["lat"], entry["lon"], entry["tz_name"])
        return entry["lat"], entry["lon"], entry["display_name"], entry["tz_name"]

    # Cache miss — geocode via Nominatim (OpenStreetMap)
    query = _geocode_query(name)
    log.debug("Cache miss for '%s', geocoding via Nominatim (query: '%s')...", key, query)
    try:
        geolocator = Nominatim(user_agent=USER_AGENT)
        result = geolocator.geocode(query, timeout=10)
    except GeocoderTimedOut:
        raise RuntimeError(f"Geocoding timed out for {name!r}. Check your connection.")
    except GeocoderServiceError as e:
        raise RuntimeError(f"Geocoding service error: {e}")

    if result is None:
        raise ValueError(f"Location not found: {name!r}")

    entry = {
        "lat": result.latitude,
        "lon": result.longitude,
        "display_name": result.address,
        "tz_name": _tz_name_for(result.latitude, result.longitude),
    }
    cache[key] = entry
    _save(cache)
    log.debug("Geocoded '%s': lat=%s, lon=%s, tz=%s, cached to %s",
              key, entry["lat"], entry["lon"], entry["tz_name"], CACHE_FILE)

    return entry["lat"], entry["lon"], entry["display_name"], entry["tz_name"]


def save(name: str, lat: float, lon: float, display_name: str = None):
    """Explicitly save a named location (e.g. 'home', 'dark site')."""
    cache = _load()
    cache[name.strip().lower()] = {
        "lat": lat,
        "lon": lon,
        "display_name": display_name or name,
        "tz_name": _tz_name_for(lat, lon),
    }
    _save(cache)
    print(f"Saved '{name}' → {lat}, {lon}")


def timezone_for(lat: float, lon: float) -> ZoneInfo:
    """Return the ZoneInfo for the given coordinates (used for raw --coords input)."""
    return ZoneInfo(_tz_name_for(lat, lon))


def list_all() -> dict:
    """Return all saved/cached locations keyed by name."""
    return _load()
