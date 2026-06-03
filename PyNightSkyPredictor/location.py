#!/usr/bin/env python3
"""Location resolution: named presets, geocoding cache, and Nominatim lookup.

Forward geocoding backend selection:
  local backend → public Nominatim (OpenStreetMap), rate-limited, cached locally.
  aws backend   → AWS Location Service (Esri place index), no rate-limit concerns.
"""

import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ports

log = logging.getLogger(__name__)
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from timezonefinder import TimezoneFinder

CACHE_FILE = Path.home() / ".pynightsky-predictor" / "locations.json"
USER_AGENT = "pynightsky-predictor/1.0"


class LocalGeocodeStore:
    """Saved/cached named locations persisted as one JSON file on local disk."""

    def __init__(self, path: Path = CACHE_FILE):
        self.path = path

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))


class DynamoGeocodeStore:
    """Saved locations persisted as a single JSON blob in the shared DynamoDB table.

    Stored under the reserved system key ``__geocode__`` (no TTL → permanent),
    so a cache flush never touches it. Mirrors the whole-dict load/save contract
    of LocalGeocodeStore.
    """

    _KEY = "__geocode__"

    def __init__(self, table_name: str | None = None):
        self._table_name = table_name
        self._table = None

    @property
    def table(self):
        if self._table is None:
            from .cache import _dynamo_table  # lazy: only the aws backend needs boto3
            self._table = _dynamo_table(self._table_name)
        return self._table

    def load(self) -> dict:
        try:
            item = self.table.get_item(Key={"cache_key": self._KEY}).get("Item")
            return json.loads(item["value"]) if item else {}
        except Exception as e:
            log.debug("Geocode store load error: %s", e)
            return {}

    def save(self, data: dict) -> None:
        try:
            self.table.put_item(Item={"cache_key": self._KEY, "value": json.dumps(data)})
        except Exception as e:
            # A geocode-cache write failure must not fail the request — the lookup
            # still succeeds, it just won't be cached for next time.
            log.warning("Geocode store save failed (continuing uncached): %s", e)


# Module-level helpers delegate to the active backend's geocode store so the
# Nominatim resolution logic below is unchanged when storage moves to the cloud.
def _load() -> dict:
    return ports.get_backend().geocode_store.load()


def _save(cache: dict):
    ports.get_backend().geocode_store.save(cache)


_US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# In-process cache: persists across warm Lambda invocations within the same container.
# Geocode entries are permanent in DynamoDB so an in-process copy is always safe.
_mem_geocode: dict[str, dict] = {}


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


def _geocode_via_nominatim(name: str, query: str) -> dict | None:
    """Forward-geocode using the public Nominatim API (local backend only)."""
    log.debug("Cache miss for '%s', geocoding via Nominatim (query: '%s')...", name, query)
    try:
        geolocator = Nominatim(user_agent=USER_AGENT)
        result = geolocator.geocode(query, timeout=10)
    except GeocoderTimedOut:
        log.error("Nominatim geocode timed out for %r", name, extra={"service": "nominatim"})
        raise RuntimeError(f"Geocoding timed out for {name!r}. Check your connection.")
    except GeocoderServiceError as e:
        log.error("Nominatim geocode service error: %s", e, extra={"service": "nominatim"})
        raise RuntimeError(f"Geocoding service error: {e}")

    if result is None:
        return None
    return {
        "lat": result.latitude,
        "lon": result.longitude,
        "display_name": result.address,
        "tz_name": _tz_name_for(result.latitude, result.longitude),
    }


def _geocode_via_aws(name: str, query: str) -> dict | None:
    """Forward-geocode using AWS Location Service (aws backend only)."""
    import boto3
    index_name = os.environ.get("PYNIGHTSKY_PLACE_INDEX", "pynightsky-place-index")
    log.debug("Cache miss for '%s', geocoding via AWS Location (query: '%s')...", name, query)
    try:
        client = boto3.client("location")
        resp = client.search_place_index_for_text(
            IndexName=index_name,
            Text=query,
            MaxResults=1,
        )
    except Exception as e:
        log.error("AWS Location geocode error for %r: %s", name, e, extra={"service": "aws-location"})
        raise RuntimeError(f"Geocoding error: {e}")

    results = resp.get("Results", [])
    if not results:
        return None
    place = results[0]["Place"]
    lon, lat = place["Geometry"]["Point"]   # AWS returns [lon, lat]
    display_name = place.get("Label", name)
    return {
        "lat": lat,
        "lon": lon,
        "display_name": display_name,
        "tz_name": _tz_name_for(lat, lon),
    }


def resolve(name: str) -> tuple:
    """
    Resolve a location name to (lat, lon, display_name, tz_name).

    Checks the local cache first. On a miss, geocodes via Nominatim and
    caches the result (including timezone) so subsequent lookups are instant
    and fully offline.
    """
    key = name.strip().lower()

    # Fast path: in-process dict avoids a DynamoDB round-trip on warm containers.
    if key in _mem_geocode:
        e = _mem_geocode[key]
        return e["lat"], e["lon"], e["display_name"], e["tz_name"]

    cache = _load()

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
        _mem_geocode[key] = entry
        return entry["lat"], entry["lon"], entry["display_name"], entry["tz_name"]

    # Cache miss — geocode via the appropriate backend
    query = _geocode_query(name)
    if ports.get_backend()._name == "aws":
        entry = _geocode_via_aws(name, query)
    else:
        entry = _geocode_via_nominatim(name, query)
    if entry is None:
        raise ValueError(f"Location not found: {name!r}")

    cache[key] = entry
    _save(cache)
    _mem_geocode[key] = entry
    log.debug("Geocoded '%s': lat=%s, lon=%s, tz=%s",
              key, entry["lat"], entry["lon"], entry["tz_name"])

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
    log.info("Saved location '%s' → lat=%.4f, lon=%.4f", name, lat, lon)


def timezone_for(lat: float, lon: float) -> ZoneInfo:
    """Return the ZoneInfo for the given coordinates (used for raw --coords input)."""
    return ZoneInfo(_tz_name_for(lat, lon))


def list_all() -> dict:
    """Return all saved/cached locations keyed by name."""
    return _load()
