#!/usr/bin/env python3
"""Simple disk-backed JSON cache with per-entry TTL."""

import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".pynightsky-predictor" / "cache"


def _key_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()
    return _CACHE_DIR / f"{h}.json"


def get(key: str):
    """Return cached value or None if missing or expired."""
    path = _key_path(key)
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


def get_stale(key: str):
    """Return cached value even if expired; None only if missing or unreadable.

    Used for stale-while-revalidate: if a fresh fetch fails, callers can fall
    back to the most recently cached value rather than returning nothing.
    Unlike get(), this does NOT delete the entry when it is expired.
    """
    path = _key_path(key)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
        log.debug("Cache stale-read: %s", key)
        return entry["value"]
    except Exception as e:
        log.debug("Cache stale-read error for %s: %s", key, e)
        return None


def set(key: str, value, ttl_seconds: int | None = None) -> None:
    """Store value under key with optional TTL in seconds. None = no expiry."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    expires = time.time() + ttl_seconds if ttl_seconds is not None else None
    path = _key_path(key)
    try:
        path.write_text(json.dumps({"expires": expires, "value": value}))
        log.debug("Cache set: %s (ttl=%s)", key, ttl_seconds)
    except Exception as e:
        log.debug("Cache write error for %s: %s", key, e)


def invalidate(key: str) -> None:
    """Remove a single cache entry."""
    _key_path(key).unlink(missing_ok=True)


def clear_expired() -> int:
    """Remove all expired entries. Returns count removed."""
    if not _CACHE_DIR.exists():
        return 0
    now = time.time()
    count = 0
    for path in _CACHE_DIR.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
            if entry["expires"] is not None and now > entry["expires"]:
                path.unlink(missing_ok=True)
                count += 1
        except Exception:
            pass
    return count


def clear_all() -> int:
    """Remove all cache entries. Returns count removed."""
    if not _CACHE_DIR.exists():
        return 0
    count = 0
    for path in _CACHE_DIR.glob("*.json"):
        path.unlink(missing_ok=True)
        count += 1
    return count
