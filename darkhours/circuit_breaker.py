"""Circuit breaker for 3rd-party provider calls.

Sits alongside provider_health.py (the passive "what happened last" registry
that feeds /healthz); this module is the "should I even try" decision point.
Provider modules call allow() before an outbound call and on_success()/
on_failure() after it, as siblings to their existing provider_health.record()
calls. When a provider has failed FAILURE_THRESHOLD times in a row the breaker
opens and calls are skipped instantly (ProviderUnavailableError, no network
I/O) instead of making every user wait through the provider's timeout.

Recovery — how an OPEN breaker closes again — has two modes, chosen per call:

* Monitor-driven (open_meteo, seven_timer, swpc, waqi): defer to the synthetic
  health-check Lambda (apps/provider_health/handler.py), which probes those
  providers every 5 minutes and writes UP/DOWN to a DynamoDB table named by
  PYNIGHTSKY_PROVIDER_HEALTH_TABLE. A fresh DOWN blocks without spending any
  user request on the dead provider; a fresh UP grants a single probe call —
  the breaker only actually closes when a real call succeeds (on_success is
  the sole OPEN->CLOSED transition), so a monitor false-UP can't silently
  neuter the breaker. Probes are rate-limited by _PROBE_GUARD_SECONDS.

* Self-timed (every other provider, and the fallback whenever no fresh
  monitor signal exists — env var unset, read failure, stale entry): after
  the provider's cooldown, grant a single probe; success closes, failure
  re-arms a fresh cooldown. This is classic half-open behavior folded into
  the OPEN state check rather than tracked as a third state.

"No signal" always degrades to self-timed, never to "blocked forever" — with
PYNIGHTSKY_PROVIDER_HEALTH_TABLE unset (the default), every provider self-times
and this module needs no AWS access at all. Probe grants are atomic: granting
re-arms the clock inside the state lock, so concurrent threads (predictor's
I/O fan-out) can't all probe at once.

Breaker keys are per-host, because reachability is a host property — e.g.
open_meteo (api.open-meteo.com) vs open_meteo_archive
(archive-api.open-meteo.com), which the codebase documents as failing
independently. Keys match provider_health.record() names.

State is in-process (per warm Lambda container), like provider_health's
registry. Call sites only ever touch allow/on_success/on_failure — that
indirection is what would let this state move to DynamoDB later (the deferred
storm-throttling work) without touching any provider module, mirroring the
local/aws adapter swaps in ports.py. A future call-metrics layer (latency,
volume) hooks into the same three functions.

Flags (read once at import, same idiom as PYNIGHTSKY_NO_CACHE):
  PYNIGHTSKY_CIRCUIT_BREAKER_ENABLED             kill switch, default enabled
  PYNIGHTSKY_CIRCUIT_BREAKER_<PROVIDER>_DISABLE  per-provider opt-out
  PYNIGHTSKY_PROVIDER_HEALTH_TABLE               monitor table (optional)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from . import _env

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

FAILURE_THRESHOLD = 3     # consecutive failures before the breaker opens
COOLDOWN_SECONDS = 60.0   # self-timed wait before granting a probe

# Bound on how often a monitor false-UP can cost a user request: while OPEN
# with the monitor reporting UP, at most one probe is granted per this many
# seconds (per container).
_PROBE_GUARD_SECONDS = 15.0

# Per-provider (threshold, cooldown) overrides. Celestrak trips on the first
# failure and waits 5 minutes: its TLE cache is global (one success serves all
# users for 6h), a failed fetch never refreshes it, and Celestrak's anti-abuse
# policy punishes exactly the concentrated-retry pattern that emerges at cache
# expiry — while get_tle()'s stale-cache fallback makes patience free.
_OVERRIDES: dict[str, tuple[int, float]] = {
    "celestrak": (1, 300.0),
}

_ALL_PROVIDERS = (
    "open_meteo",
    "open_meteo_archive",
    "open_meteo_air_quality",
    "seven_timer",
    "celestrak",
    "waqi",
    "swpc",
    "nominatim",
    "aws_location",
    "aws_georoutes",
)

# Breaker key -> provider_id in the synthetic monitor's table. Only these four
# are ever probed by apps/provider_health/handler.py; everything else (and any
# read miss/staleness/error) falls back to self-timed recovery.
_SYNTHETIC_MONITOR_ID = {
    "open_meteo": "open-meteo",
    "seven_timer": "7timer",
    "swpc": "swpc",
    "waqi": "waqi",
}

_MONITOR_STALE_AFTER = 20 * 60   # ignore monitor entries older than this (4x its schedule)
_MONITOR_CACHE_TTL = 60.0        # in-process cache of monitor reads


_flag = _env.flag   # kept as a module-local name: existing call sites/tests use _flag(...)

_ENABLED = _flag("PYNIGHTSKY_CIRCUIT_BREAKER_ENABLED", "1")
_DISABLED_PROVIDERS = frozenset(
    p for p in _ALL_PROVIDERS if _flag(f"PYNIGHTSKY_CIRCUIT_BREAKER_{p.upper()}_DISABLE")
)
_HEALTH_TABLE = os.environ.get("PYNIGHTSKY_PROVIDER_HEALTH_TABLE", "").strip()


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

@dataclass
class _State:
    consecutive_failures: int = 0
    # Monotonic timestamp of the last open/probe-grant/failed-probe event;
    # None = CLOSED. Re-armed on every probe grant so concurrent callers block.
    opened_at: float | None = None


_lock = threading.Lock()
_states: dict[str, _State] = {}

# monitor_id -> (status_or_None, fetched_at_monotonic); None results are cached
# too, so a broken table read costs its timeout once per TTL, not per call.
_monitor_cache: dict[str, tuple[str | None, float]] = {}
_ddb_table = None
_ddb_lock = threading.Lock()


class ProviderUnavailableError(RuntimeError):
    """Raised instead of calling a provider whose breaker is open.

    Subclasses RuntimeError so every existing `except RuntimeError` up the
    stack (predictor, weather fallback chain, TLE stale-cache fallback)
    handles a skipped call exactly like a failed one.
    """

    def __init__(self, provider: str, retry_after_seconds: float):
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"{provider} unavailable (circuit open, retry in {retry_after_seconds:.0f}s)"
        )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def allow(provider: str) -> bool:
    """True if a call to *provider* should be attempted now.

    CLOSED (or breaker disabled) -> True. OPEN -> False, except when a probe
    is granted: monitor-driven (fresh UP, probe-guard elapsed) or self-timed
    (cooldown elapsed). Granting atomically re-arms the clock so exactly one
    concurrent caller wins the probe.
    """
    if not _ENABLED or provider in _DISABLED_PROVIDERS:
        return True
    with _lock:
        st = _states.get(provider)
        if st is None or st.opened_at is None:
            return True
    # OPEN. Consult the monitor outside the lock — the (rare, cached) table
    # read may block ~1-2s and must not stall other providers' allow() calls.
    monitor = _synthetic_status(provider) if provider in _SYNTHETIC_MONITOR_ID else None
    now = time.monotonic()
    _, cooldown = _limits(provider)
    with _lock:
        st = _states.get(provider)
        if st is None or st.opened_at is None:   # closed while we were reading
            return True
        if monitor == "DOWN":
            return False
        wait = _PROBE_GUARD_SECONDS if monitor == "UP" else cooldown
        if now - st.opened_at >= wait:
            st.opened_at = now   # claim the probe; concurrent callers stay blocked
            return True
        return False


def on_success(provider: str) -> None:
    """Record a successful call: reset the failure streak, close if open."""
    with _lock:
        st = _states.get(provider)
        if st is not None:
            st.consecutive_failures = 0
            st.opened_at = None


def on_failure(provider: str) -> None:
    """Record a failed call: count it, open at the threshold, re-arm if open."""
    threshold, _ = _limits(provider)
    with _lock:
        st = _states.setdefault(provider, _State())
        st.consecutive_failures += 1
        if st.consecutive_failures >= threshold:
            st.opened_at = time.monotonic()


def is_open(provider: str) -> bool:
    """Read-only state check for diagnostics — never mutates."""
    with _lock:
        st = _states.get(provider)
        return st is not None and st.opened_at is not None


def retry_after(provider: str) -> float:
    """Seconds until the next self-timed probe would be granted (0 if closed)."""
    _, cooldown = _limits(provider)
    with _lock:
        st = _states.get(provider)
        if st is None or st.opened_at is None:
            return 0.0
        return max(0.0, cooldown - (time.monotonic() - st.opened_at))


def unavailable(provider: str) -> ProviderUnavailableError:
    """Build the skip exception with the current retry-after estimate."""
    return ProviderUnavailableError(provider, retry_after(provider))


def reset() -> None:
    """Clear all breaker state (test isolation; mirrors ports.reset_backend)."""
    global _ddb_table
    with _lock:
        _states.clear()
        _monitor_cache.clear()
    _ddb_table = None


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

def _limits(provider: str) -> tuple[int, float]:
    return _OVERRIDES.get(provider, (FAILURE_THRESHOLD, COOLDOWN_SECONDS))


def _synthetic_status(provider: str) -> str | None:
    """'UP'/'DOWN' from the synthetic monitor table, or None for "no fresh signal".

    None — env var unset, provider not monitored, read error of any kind, or
    entry older than _MONITOR_STALE_AFTER — always means "fall back to
    self-timed recovery", never "stay blocked". Never raises.
    """
    if not _HEALTH_TABLE:
        return None
    monitor_id = _SYNTHETIC_MONITOR_ID.get(provider)
    if monitor_id is None:
        return None
    now = time.monotonic()
    with _lock:
        cached = _monitor_cache.get(monitor_id)
        if cached is not None and now - cached[1] < _MONITOR_CACHE_TTL:
            return cached[0]
    status: str | None = None
    try:
        item = _table().get_item(Key={"provider_id": monitor_id}).get("Item")
        if item:
            checked = float(item.get("last_checked", 0))
            if time.time() - checked <= _MONITOR_STALE_AFTER:
                raw = str(item.get("status", "")).upper()
                if raw in ("UP", "DOWN"):
                    status = raw
    except Exception as e:  # any failure degrades to self-timed, never breaks a request
        log.debug("provider-health table read failed for %s: %s", monitor_id, e)
        status = None
    with _lock:
        _monitor_cache[monitor_id] = (status, now)
    return status


def _table():
    """Lazy DynamoDB table handle with tight fail-fast bounds.

    The 1s connect/read timeouts + single attempt are load-bearing: a
    misconfigured IAM grant or unreachable endpoint must cost ~1-2s once per
    cache TTL and then degrade to self-timed mode — not hang requests on
    botocore's default 60s timeouts on exactly the path this module exists to
    keep fast.
    """
    global _ddb_table
    if _ddb_table is None:
        with _ddb_lock:
            if _ddb_table is None:
                import boto3
                from botocore.config import Config
                # total_max_attempts (not max_attempts, which botocore reads
                # as a RETRY count) — exactly one attempt, no retry storm.
                _ddb_table = boto3.resource(
                    "dynamodb",
                    config=Config(
                        connect_timeout=1.0,
                        read_timeout=1.0,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                    ),
                ).Table(_HEALTH_TABLE)
    return _ddb_table
