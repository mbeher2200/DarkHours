#!/usr/bin/env python3
"""Disk-backed JSON cache with per-entry TTL.

``LocalFileCache`` is the default (local) adapter — one JSON file per key under
``~/.pynightsky-predictor/cache``. The module-level ``get/set/...`` functions
delegate to whichever ``Cache`` the active backend selects, so callers
(``weather``, ``tle_provider``, ``darksky``) need no changes when the backend
swaps to a cloud store (DynamoDB in M3). See ``ports.py``.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from . import ports

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".pynightsky-predictor" / "cache"

# Items whose key starts with this prefix are non-cache "system" records that
# share the table (the geocode store, the dark-cycle blob). clear_all() and
# clear_expired() skip them so a cache flush never wipes saved data.
_SYSTEM_KEY_PREFIX = "__"


class LocalFileCache:
    """Cache backed by one JSON file per key under a local directory."""

    def __init__(self, cache_dir: Path = _CACHE_DIR):
        self.cache_dir = cache_dir

    def _key_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    def get(self, key: str):
        """Return cached value or None if missing or expired.

        Expired entries are NOT deleted here, so a subsequent get_stale() can still
        serve them for stale-while-revalidate (see tle_provider's fallback). Cleanup
        happens via clear_expired() or when the key is next overwritten — matching
        DynamoCache, which leaves expiry cleanup to DynamoDB TTL.
        """
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
            if entry["expires"] is not None and time.time() > entry["expires"]:
                log.debug("Cache expired: %s", key)
                return None
            log.debug("Cache hit: %s", key)
            return entry["value"]
        except Exception as e:
            log.debug("Cache read error for %s: %s", key, e)
            return None

    def get_stale(self, key: str):
        """Return cached value even if expired; None only if missing or unreadable.

        Used for stale-while-revalidate: if a fresh fetch fails, callers can fall
        back to the most recently cached value rather than returning nothing.
        Unlike get(), this does NOT delete the entry when it is expired.
        """
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
            log.debug("Cache stale-read: %s", key)
            return entry["value"]
        except Exception as e:
            log.debug("Cache stale-read error for %s: %s", key, e)
            return None

    def set(self, key: str, value, ttl_seconds: int | None = None) -> None:
        """Store value under key with optional TTL in seconds. None = no expiry."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        expires = time.time() + ttl_seconds if ttl_seconds is not None else None
        path = self._key_path(key)
        try:
            # The key is stored alongside value/expires so clear_*() can recognise
            # __-prefixed system records (filenames are hashes and can't reveal it).
            path.write_text(json.dumps({"key": key, "expires": expires, "value": value}))
            log.debug("Cache set: %s (ttl=%s)", key, ttl_seconds)
        except Exception as e:
            log.debug("Cache write error for %s: %s", key, e)

    def invalidate(self, key: str) -> None:
        """Remove a single cache entry."""
        self._key_path(key).unlink(missing_ok=True)

    def clear_expired(self) -> int:
        """Remove all expired entries (preserving __-prefixed system records)."""
        if not self.cache_dir.exists():
            return 0
        now = time.time()
        count = 0
        for path in self.cache_dir.glob("*.json"):
            try:
                entry = json.loads(path.read_text())
                if str(entry.get("key", "")).startswith(_SYSTEM_KEY_PREFIX):
                    continue
                if entry["expires"] is not None and now > entry["expires"]:
                    path.unlink(missing_ok=True)
                    count += 1
            except Exception:
                pass
        return count

    def clear_all(self) -> int:
        """Remove all cache entries, preserving __-prefixed system records."""
        if not self.cache_dir.exists():
            return 0
        count = 0
        for path in self.cache_dir.glob("*.json"):
            try:
                if str(json.loads(path.read_text()).get("key", "")).startswith(_SYSTEM_KEY_PREFIX):
                    continue
            except Exception:
                pass
            path.unlink(missing_ok=True)
            count += 1
        return count


# ── DynamoDB-backed cache (the 'aws' backend) ───────────────────────────────

def _dynamo_table(table_name: str | None = None):
    """Return a boto3 DynamoDB Table for the shared cache table.

    Lazy-imports boto3 so the local backend never requires it. The table name
    comes from PYNIGHTSKY_CACHE_TABLE; credentials/region resolve from the
    standard AWS environment (task role in the cloud, AWS_PROFILE locally).
    """
    import boto3  # lazy: only needed for the aws backend
    name = table_name or os.environ.get("PYNIGHTSKY_CACHE_TABLE")
    if not name:
        raise RuntimeError(
            "PYNIGHTSKY_CACHE_TABLE is not set — required for the 'aws' cache backend."
        )
    # Pass region explicitly: in a container/App Runner there's no ~/.aws/config to
    # fall back on, so boto3 must get the region from the environment. Falls back to
    # boto3's own resolution (profile/config) when neither env var is set (host/CLI).
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    return boto3.resource("dynamodb", region_name=region).Table(name)


class DynamoCache:
    """Cache backed by a DynamoDB table with native TTL.

    Each item is ``{cache_key, value (JSON string), expires (epoch secs, optional)}``.
    Values are stored as JSON text — like LocalFileCache — so dicts containing
    floats round-trip exactly without DynamoDB's Decimal coercion.

    DynamoDB TTL deletes expired items lazily (up to ~48h later), so ``get()``
    still checks ``expires`` itself and treats an expired item as a miss —
    matching the file cache's observable behavior. TTL only does the eventual
    physical cleanup (and storage reclaim).
    """

    def __init__(self, table_name: str | None = None):
        self._table_name = table_name
        self._table = None  # built lazily on first use

    @property
    def table(self):
        if self._table is None:
            self._table = _dynamo_table(self._table_name)
        return self._table

    def get(self, key: str):
        try:
            item = self.table.get_item(Key={"cache_key": key}).get("Item")
            if not item:
                return None
            expires = item.get("expires")
            if expires is not None and time.time() > float(expires):
                log.debug("Cache expired: %s", key)
                return None
            log.debug("Cache hit: %s", key)
            return json.loads(item["value"])
        except Exception as e:
            log.debug("Cache read error for %s: %s", key, e)
            return None

    def get_stale(self, key: str):
        try:
            item = self.table.get_item(Key={"cache_key": key}).get("Item")
            if not item:
                return None
            log.debug("Cache stale-read: %s", key)
            return json.loads(item["value"])
        except Exception as e:
            log.debug("Cache stale-read error for %s: %s", key, e)
            return None

    def set(self, key: str, value, ttl_seconds: int | None = None) -> None:
        item = {"cache_key": key, "value": json.dumps(value)}
        if ttl_seconds is not None:
            item["expires"] = int(time.time()) + ttl_seconds
        try:
            self.table.put_item(Item=item)
            log.debug("Cache set: %s (ttl=%s)", key, ttl_seconds)
        except Exception as e:
            log.debug("Cache write error for %s: %s", key, e)

    def invalidate(self, key: str) -> None:
        try:
            self.table.delete_item(Key={"cache_key": key})
        except Exception as e:
            log.debug("Cache invalidate error for %s: %s", key, e)

    def _scan_keys(self):
        """Yield (cache_key, expires) for every item, paginating the scan.
        (`expires` is aliased to dodge any reserved-word collision.)"""
        kwargs = {
            "ProjectionExpression": "cache_key, #e",
            "ExpressionAttributeNames": {"#e": "expires"},
        }
        while True:
            resp = self.table.scan(**kwargs)
            for it in resp.get("Items", []):
                yield it["cache_key"], it.get("expires")
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

    def clear_expired(self) -> int:
        """Delete expired non-system items. (DynamoDB TTL also does this lazily.)"""
        now = time.time()
        count = 0
        for key, expires in list(self._scan_keys()):
            if key.startswith(_SYSTEM_KEY_PREFIX):
                continue
            if expires is not None and now > float(expires):
                self.table.delete_item(Key={"cache_key": key})
                count += 1
        return count

    def clear_all(self) -> int:
        """Delete all cache items, preserving __-prefixed system records."""
        count = 0
        for key, _ in list(self._scan_keys()):
            if key.startswith(_SYSTEM_KEY_PREFIX):
                continue
            self.table.delete_item(Key={"cache_key": key})
            count += 1
        return count


# ── Hit/miss instrumentation ────────────────────────────────────────────────
# A single global counter on the module-level get() chokepoint. Two int adds per
# lookup — negligible always-on cost — so profilers (see darksky.find_nearby) can
# attribute time to cache misses without threading a stats object through callers.
# A non-None return counts as a hit; None (missing OR expired) counts as a miss.

class _CacheStats:
    __slots__ = ("hits", "misses")

    def __init__(self):
        self.hits = 0
        self.misses = 0

    def reset(self) -> None:
        self.hits = 0
        self.misses = 0

    def snapshot(self) -> tuple[int, int]:
        return self.hits, self.misses


stats = _CacheStats()

# Diagnostic bypass: when PYNIGHTSKY_NO_CACHE is set, every get() misses and every
# set() is a no-op — the engine runs as if the cache were permanently empty (and
# touches no backing store). Used for uncached profiling runs; off in normal operation.
_NO_CACHE = os.environ.get("PYNIGHTSKY_NO_CACHE", "").strip().lower() in ("1", "true", "yes", "on")


# ── Module-level API (delegates to the active backend) ──────────────────────
# Callers use cache.get(...) / cache.set(...) etc.; these thin wrappers keep that
# surface stable while the underlying store is backend-selected via ports.

def get(key: str):
    if _NO_CACHE:
        stats.misses += 1
        return None
    value = ports.get_backend().cache.get(key)
    if value is None:
        stats.misses += 1
    else:
        stats.hits += 1
    return value


def get_stale(key: str):
    return ports.get_backend().cache.get_stale(key)


def set(key: str, value, ttl_seconds: int | None = None) -> None:
    if _NO_CACHE:
        return
    ports.get_backend().cache.set(key, value, ttl_seconds)


def invalidate(key: str) -> None:
    ports.get_backend().cache.invalidate(key)


def clear_expired() -> int:
    return ports.get_backend().cache.clear_expired()


def clear_all() -> int:
    return ports.get_backend().cache.clear_all()
