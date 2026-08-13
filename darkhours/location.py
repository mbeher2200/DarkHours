#!/usr/bin/env python3
"""Location resolution: named presets, geocoding cache, and Nominatim lookup.

Forward geocoding backend selection:
  local backend → public Nominatim (OpenStreetMap), rate-limited, cached locally.
  aws backend   → AWS Location Service (Esri place index), no rate-limit concerns.

Nominatim pacing goes through rate_limiter.py (see docs/RATE_LIMITING.md), shared
with darksky.py's own Nominatim reverse-geocode calls — one pacer, one clock, for
every call this process makes to nominatim.openstreetmap.org.
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
from geopy.exc import GeocoderTimedOut, GeocoderServiceError, GeocoderRateLimited

from . import circuit_breaker as _cb
from . import provider_health as _ph
from . import rate_limiter as _rl
from timezonefinder import TimezoneFinder

CACHE_FILE = Path.home() / ".darkhours" / "locations.json"
USER_AGENT = "darkhours/1.0"


class LocalGeocodeStore:
    """Saved/cached named locations persisted as one JSON file on local disk.

    The file keeps the whole-dict format it has always had — one process, one
    file, no size ceiling worth worrying about — but the store now exposes the
    same per-key contract as the DynamoDB one so callers never hold the whole
    dict in order to add a single entry.
    """

    def __init__(self, path: Path = CACHE_FILE):
        self.path = path

    def _read(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def get(self, key: str) -> dict | None:
        return self._read().get(key)

    def put(self, key: str, entry: dict) -> None:
        data = self._read()
        data[key] = entry
        self._write(data)

    def all(self) -> dict:
        return self._read()


class DynamoGeocodeStore:
    """Saved locations in the shared DynamoDB table, one item per location.

    Each entry is its own item under ``geocode|<key>``, written with no TTL so a
    cache flush never touches it.

    This deliberately does *not* mirror LocalGeocodeStore's file layout. It used
    to: every location lived in a single ``__geocode__`` blob that was read and
    rewritten in full on every save. That is the natural shape for a JSON file
    and a pathological one for DynamoDB — the item reached the hard 400 KB item
    limit at ~2,600 locations, after which every write failed and nothing new was
    ever cached (so every lookup re-hit the geocoding provider, at real cost).
    Before that it was merely expensive: ~50 RCU per read and ~400 WCU per write,
    for one location. It was also lossy — read-modify-write on a shared blob from
    concurrent Lambda containers silently dropped whichever write landed first.

    Per-key items cost ~1 RCU / ~1 WCU, have no collective size ceiling, and
    cannot clobber each other. Keep it that way: do not reintroduce an API that
    writes every location at once.
    """

    _PREFIX = "geocode|"

    def __init__(self, table_name: str | None = None):
        self._table_name = table_name
        self._table = None

    @property
    def table(self):
        if self._table is None:
            from .cache import _dynamo_table  # lazy: only the aws backend needs boto3
            self._table = _dynamo_table(self._table_name)
        return self._table

    def get(self, key: str) -> dict | None:
        try:
            item = self.table.get_item(Key={"cache_key": self._PREFIX + key}).get("Item")
            return json.loads(item["value"]) if item else None
        except Exception as e:
            log.debug("Geocode store get error for %r: %s", key, e)
            return None

    def put(self, key: str, entry: dict) -> None:
        value = json.dumps(entry)
        try:
            self.table.put_item(Item={"cache_key": self._PREFIX + key, "value": value})
        except Exception as e:
            # A geocode-cache write failure must not fail the request — the lookup
            # still succeeds, it just won't be cached for next time. Log the key and
            # value size so a size-limit failure is diagnosable after the fact
            # instead of just "something failed" (see DynamoGeocodeStore's docstring
            # for the historical version of this failure mode).
            log.warning(
                "Geocode store save failed (continuing uncached) for key=%r (%d bytes): %s",
                key, len(value), e,
            )

    def all(self) -> dict:
        """Every saved location. Scans the table — CLI convenience, not a request path.

        The table is hash-key-only, so there is no prefix Query to use here. This
        is why nothing on the request path calls it.
        """
        out: dict = {}
        try:
            from boto3.dynamodb.conditions import Attr
            kwargs = {"FilterExpression": Attr("cache_key").begins_with(self._PREFIX)}
            while True:
                resp = self.table.scan(**kwargs)
                for item in resp.get("Items", []):
                    out[item["cache_key"][len(self._PREFIX):]] = json.loads(item["value"])
                if "LastEvaluatedKey" not in resp:
                    return out
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        except Exception as e:
            log.debug("Geocode store scan error: %s", e)
            return out


# Module-level helpers delegate to the active backend's geocode store so the
# resolution logic below is unchanged when storage moves to the cloud. These are
# per-key on purpose: reading or writing every location to touch one of them is
# what wedged the DynamoDB store at its item-size limit (see DynamoGeocodeStore).
def _get(key: str) -> dict | None:
    return ports.get_backend().geocode_store.get(key)


def _put(key: str, entry: dict) -> None:
    ports.get_backend().geocode_store.put(key, entry)


def _all() -> dict:
    return ports.get_backend().geocode_store.all()


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
    if not _cb.allow("nominatim"):
        raise _cb.unavailable("nominatim")
    try:
        geolocator = Nominatim(user_agent=USER_AGENT)
        with _rl.acquire("nominatim"):
            result = geolocator.geocode(query, timeout=10)
        _ph.record("nominatim", "ok")
        _cb.on_success("nominatim")
    except GeocoderRateLimited as e:
        _ph.record("nominatim", "degraded", "rate limited")
        _cb.on_failure("nominatim")
        log.error("Nominatim geocode rate limited for %r", name, extra={"service": "nominatim"})
        raise RuntimeError(f"Geocoding rate limited for {name!r}. Try again later.")
    except GeocoderTimedOut:
        _ph.record("nominatim", "error", "timed out")
        _cb.on_failure("nominatim")
        log.error("Nominatim geocode timed out for %r", name, extra={"service": "nominatim"})
        raise RuntimeError(f"Geocoding timed out for {name!r}. Check your connection.")
    except GeocoderServiceError as e:
        _ph.record("nominatim", "error", str(e)[:120])
        _cb.on_failure("nominatim")
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
    from . import darksky  # reuse the process-wide pooled 'location' client
    index_name = os.environ.get("PYNIGHTSKY_PLACE_INDEX", "pynightsky-place-index")
    log.debug("Cache miss for '%s', geocoding via AWS Location (query: '%s')...", name, query)
    if not _cb.allow("aws_location"):
        raise _cb.unavailable("aws_location")
    try:
        client = darksky._location()
        resp = client.search_place_index_for_text(
            IndexName=index_name,
            Text=query,
            MaxResults=1,
        )
        _ph.record("aws_location", "ok")
        _cb.on_success("aws_location")
    except Exception as e:
        _ph.record("aws_location", "error", str(e)[:120])
        _cb.on_failure("aws_location")
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


def _suggest_via_aws(query: str, max_results: int) -> list[str]:
    """Typeahead suggestions via AWS Location SearchPlaceIndexForSuggestions."""
    from . import darksky  # reuse the process-wide pooled 'location' client
    index_name = os.environ.get("PYNIGHTSKY_PLACE_INDEX", "pynightsky-place-index")
    if not _cb.allow("aws_location"):
        raise _cb.unavailable("aws_location")
    try:
        client = darksky._location()
        resp = client.search_place_index_for_suggestions(
            IndexName=index_name, Text=query, MaxResults=max_results,
        )
        _ph.record("aws_location", "ok")
        _cb.on_success("aws_location")
    except Exception as e:
        _ph.record("aws_location", "error", str(e)[:120])
        _cb.on_failure("aws_location")
        log.error("AWS Location suggest error for %r: %s", query, e,
                  extra={"service": "aws-location"})
        raise RuntimeError(f"Suggestion error: {e}")
    out: list[str] = []
    for r in resp.get("Results", []):
        txt = r.get("Text")
        if txt and txt not in out:        # de-dup while preserving rank order
            out.append(txt)
    return out


def _suggest_via_nominatim(query: str, max_results: int) -> list[str]:
    """Typeahead suggestions via a Nominatim multi-result geocode (local backend).

    Best-effort: a timeout/rate-limit returns no suggestions rather than raising,
    so a slow geocoder never blocks typing.
    """
    if not _cb.allow("nominatim"):
        # Same best-effort contract as a failed fetch: no suggestions, no error.
        return []
    try:
        geolocator = Nominatim(user_agent=USER_AGENT)
        with _rl.acquire("nominatim"):
            results = geolocator.geocode(
                _geocode_query(query), exactly_one=False, limit=max_results, timeout=10,
            ) or []
        _ph.record("nominatim", "ok")
        _cb.on_success("nominatim")
    except (GeocoderTimedOut, GeocoderServiceError, GeocoderRateLimited) as e:
        _cb.on_failure("nominatim")
        log.debug("Nominatim suggest failed for %r: %s", query, e)
        return []
    seen: set[str] = set()
    out: list[str] = []
    for r in results:
        if r.address and r.address not in seen:
            seen.add(r.address)
            out.append(r.address)
    return out


def suggest(query: str, max_results: int = 5) -> list[str]:
    """Return typeahead place suggestions for an autocomplete box.

    aws backend   → AWS Location SearchPlaceIndexForSuggestions.
    local backend → Nominatim multi-result geocode (best-effort).

    Returns a list of human-readable suggestion strings (possibly empty). The
    chosen string is resolved to coordinates later through the normal resolve()
    path, so no PlaceId bookkeeping is needed here.
    """
    query = query.strip()
    if not query:
        return []
    if ports.get_backend()._name == "aws":
        return _suggest_via_aws(query, max_results)
    return _suggest_via_nominatim(query, max_results)


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

    entry = _get(key)

    if entry is not None:
        # Migrate older entries that predate tz_name caching
        if "tz_name" not in entry:
            log.debug("Migrating '%s': adding tz_name", key)
            entry["tz_name"] = _tz_name_for(entry["lat"], entry["lon"])
            _put(key, entry)
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

    _put(key, entry)
    _mem_geocode[key] = entry
    log.debug("Geocoded '%s': lat=%s, lon=%s, tz=%s",
              key, entry["lat"], entry["lon"], entry["tz_name"])

    return entry["lat"], entry["lon"], entry["display_name"], entry["tz_name"]


def save(name: str, lat: float, lon: float, display_name: str = None):
    """Explicitly save a named location (e.g. 'home', 'dark site')."""
    _put(name.strip().lower(), {
        "lat": lat,
        "lon": lon,
        "display_name": display_name or name,
        "tz_name": _tz_name_for(lat, lon),
    })
    log.info("Saved location '%s' → lat=%.4f, lon=%.4f", name, lat, lon)


def timezone_for(lat: float, lon: float) -> ZoneInfo:
    """Return the ZoneInfo for the given coordinates (used for raw --coords input)."""
    return ZoneInfo(_tz_name_for(lat, lon))


def reverse_geocode(lat: float, lon: float) -> str | None:
    """Reverse-geocode (lat, lon) to a human-readable name.

    Prefers a verified named POI from the local OSM/PAD-US index (see
    darksky._poi_reverse_name) over the network settlement lookup — it's more specific
    ("Nineteenmile Campground, Wenatchee National Forest" beats "Chelan, WA"), needs no
    network call, and — unlike a client-supplied name — can't be spoofed by a crafted
    URL since it's derived only from the coordinate against a trusted, offline index.
    Falls back to "City, ST" for US locations, "City" elsewhere, or None if the point
    is over water or the geocoder is unavailable.
    """
    from . import darksky
    poi_name = darksky._poi_reverse_name(lat, lon)
    if poi_name:
        return poi_name
    return darksky._settlement(lat, lon)


def list_all() -> dict:
    """Return all saved/cached locations keyed by name.

    CLI-only (``darkhours.py locations``). On the aws backend this scans the
    cache table, so keep it off any request path.
    """
    return _all()
