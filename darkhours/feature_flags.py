"""Operator-controlled runtime feature flags.

Lets an operator turn off one optional feature (satellites, aurora, live haze,
drive-time routing, an entire async job type, ...) without a code change or
redeploy, while everything else keeps working. Sits alongside circuit_breaker.py
and rate_limiter.py as this codebase's third hand-rolled resilience mechanism,
and borrows the same shape: an optional DynamoDB table, a short in-process TTL
cache so a request never blocks on it, and a fail-open default so an unset or
unreachable table changes nothing.

These flags are administrative, not user-facing: nothing reachable over the
public API can ever write one. The Lambda execution roles are granted read-only
access (GetItem/Scan) to the backing table; the only writer is an operator's own
AWS credentials, used locally (see scripts/local-flags/ — not checked in).

Flags (read once at import, same idiom as PYNIGHTSKY_NO_CACHE):
  PYNIGHTSKY_FEATURE_FLAGS_ENABLED       global kill switch, default enabled
  PYNIGHTSKY_FEATURE_<NAME>_DISABLE      per-flag opt-out, no AWS call
  PYNIGHTSKY_FEATURE_FLAGS_TABLE         flag table name (optional)
"""
from __future__ import annotations

import logging
import os
import threading
import time

from . import _env

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0

_flag = _env.flag

_ENABLED = _flag("PYNIGHTSKY_FEATURE_FLAGS_ENABLED", "1")
_TABLE = os.environ.get("PYNIGHTSKY_FEATURE_FLAGS_TABLE", "").strip()

_lock = threading.Lock()
# flag name -> (enabled_or_None, fetched_at_wall_clock). None results (miss/error)
# are cached too, so a broken table read costs its timeout once per TTL.
_cache: dict[str, tuple[bool | None, float]] = {}
_ddb_table = None
_ddb_lock = threading.Lock()


def enabled(name: str, default: bool = True) -> bool:
    """True if the feature named *name* should run.

    Order of checks, each a hard override of the next:
      1. PYNIGHTSKY_FEATURE_FLAGS_ENABLED=0 -> default, no AWS call at all.
      2. PYNIGHTSKY_FEATURE_<NAME>_DISABLE  -> False, no AWS call.
      3. No table configured                -> default.
      4. Table read (TTL-cached)             -> the stored value, or default
         if the flag has never been explicitly set or the read failed.
    """
    if not _ENABLED:
        return default
    if _flag(f"PYNIGHTSKY_FEATURE_{name.upper()}_DISABLE"):
        return False
    if not _TABLE:
        return default
    value = _cached_value(name)
    return default if value is None else value


def snapshot() -> dict[str, bool | None]:
    """Whatever's currently cached, for /healthz visibility. Never reads DynamoDB."""
    with _lock:
        return {name: value for name, (value, _) in _cache.items()}


def reset() -> None:
    """Clear all cached state (test isolation; mirrors circuit_breaker.reset())."""
    global _ddb_table
    with _lock:
        _cache.clear()
    _ddb_table = None


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

def _cached_value(name: str) -> bool | None:
    # time.time(), not time.monotonic(): this cache must stay correct across a
    # Lambda container's freeze/thaw between invocations, where the monotonic
    # clock doesn't reliably advance while frozen — a container idle between
    # sparse requests could otherwise never perceive the TTL as expired and get
    # stuck serving a stale value indefinitely. Wall-clock time doesn't have
    # that problem; it isn't used here to measure a duration within one
    # continuous execution (where monotonic would be the right tool), only to
    # answer "how long ago, in the real world, was this fetched."
    now = time.time()
    with _lock:
        cached = _cache.get(name)
        if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
    value: bool | None = None
    try:
        item = _table().get_item(Key={"flag_id": name}).get("Item")
        if item is not None:
            value = bool(item.get("enabled", True))
    except Exception as e:  # any failure degrades to "no signal", never breaks a request
        log.debug("feature-flags table read failed for %s: %s", name, e)
        value = None
    with _lock:
        _cache[name] = (value, now)
    return value


def _table():
    """Lazy DynamoDB table handle with tight fail-fast bounds — see
    circuit_breaker._table() for why these timeouts are load-bearing."""
    global _ddb_table
    if _ddb_table is None:
        with _ddb_lock:
            if _ddb_table is None:
                import boto3
                from botocore.config import Config
                _ddb_table = boto3.resource(
                    "dynamodb",
                    config=Config(
                        connect_timeout=1.0,
                        read_timeout=1.0,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                        # This Config set every bound except the pool, so the
                        # client silently kept botocore's default of 10 while
                        # find_nearby checked flags from a wider fan-out.
                        max_pool_connections=_env.dynamo_pool(),
                    ),
                ).Table(_TABLE)
    return _ddb_table
