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
import time
from pathlib import Path

from . import ports

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".pynightsky-predictor" / "cache"


class LocalFileCache:
    """Cache backed by one JSON file per key under a local directory."""

    def __init__(self, cache_dir: Path = _CACHE_DIR):
        self.cache_dir = cache_dir

    def _key_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    def get(self, key: str):
        """Return cached value or None if missing or expired."""
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
            if entry["expires"] is not None and time.time() > entry["expires"]:
                path.unlink(missing_ok=True)
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
            path.write_text(json.dumps({"expires": expires, "value": value}))
            log.debug("Cache set: %s (ttl=%s)", key, ttl_seconds)
        except Exception as e:
            log.debug("Cache write error for %s: %s", key, e)

    def invalidate(self, key: str) -> None:
        """Remove a single cache entry."""
        self._key_path(key).unlink(missing_ok=True)

    def clear_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        if not self.cache_dir.exists():
            return 0
        now = time.time()
        count = 0
        for path in self.cache_dir.glob("*.json"):
            try:
                entry = json.loads(path.read_text())
                if entry["expires"] is not None and now > entry["expires"]:
                    path.unlink(missing_ok=True)
                    count += 1
            except Exception:
                pass
        return count

    def clear_all(self) -> int:
        """Remove all cache entries. Returns count removed."""
        if not self.cache_dir.exists():
            return 0
        count = 0
        for path in self.cache_dir.glob("*.json"):
            path.unlink(missing_ok=True)
            count += 1
        return count


# ── Module-level API (delegates to the active backend) ──────────────────────
# Callers use cache.get(...) / cache.set(...) etc.; these thin wrappers keep that
# surface stable while the underlying store is backend-selected via ports.

def get(key: str):
    return ports.get_backend().cache.get(key)


def get_stale(key: str):
    return ports.get_backend().cache.get_stale(key)


def set(key: str, value, ttl_seconds: int | None = None) -> None:
    ports.get_backend().cache.set(key, value, ttl_seconds)


def invalidate(key: str) -> None:
    ports.get_backend().cache.invalidate(key)


def clear_expired() -> int:
    return ports.get_backend().cache.clear_expired()


def clear_all() -> int:
    return ports.get_backend().cache.clear_all()
