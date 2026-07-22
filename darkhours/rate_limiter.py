"""Preventive outbound rate limiting for 3rd-party provider calls.

Sits alongside circuit_breaker.py (which is REACTIVE: it stops calling a provider
once it's already failing) as the PREVENTIVE half of outbound call safety — it caps
DarkHours' own call rate/concurrency to a provider regardless of whether that
provider is healthy, so the app's own fan-out (trip.plan_trip()'s up to 20 concurrent
nights x predictor.assemble_night()'s up to 9 concurrent provider calls per night)
can never look like a burst/DoS to a healthy public provider. The two modules never
call into each other; call sites compose them explicitly — circuit_breaker.allow()
first, rate_limiter.acquire() second, so a breaker-open skip never pays a pacing
cost.

Two mechanisms, generalizing patterns that already existed ad hoc in darksky.py
(sleep-based min-interval pacing for Nominatim/Overpass, each with its own lock) and
darksky.py's _GEOCODE_MAX_WORKERS (a concurrency cap for AWS Location fan-out):

* pace(provider) — min-interval serialization, one lock per provider (never a single
  shared lock across providers — a provider that has to sleep must not block an
  unrelated provider's pacing decision). Blocks until `interval` seconds have passed
  since the last call *start* for this provider, across every caller/module sharing
  the key — this is what lets location.py and darksky.py's independent Nominatim
  call sites coordinate through one shared clock instead of two uncoordinated ones.
  The wait gates when a call is allowed to start; it is not held for the call's
  duration, matching the darksky.py behavior this replaces.
* limit(provider) — concurrency-cap serialization via a semaphore. A permit is held
  for the call's full duration and released on exit (success or exception).

acquire(provider) is the one call sites need to know: it dispatches to pace() or
limit() per the provider's configuration, and is a no-op for any provider with no
entry (e.g. swpc, aws_location, aws_georoutes — already protected by other means,
see docs/RATE_LIMITING.md for why those are not gaps).

Every provider's lock/semaphore is built once at import — the provider set is a
fixed set of constants, not dynamic, so there is no need for the double-checked
lazy-init locking circuit_breaker.py uses for its DynamoDB table handle.

Flags (read once at import, same idiom as circuit_breaker.py / PYNIGHTSKY_NO_CACHE):
  PYNIGHTSKY_RATE_LIMIT_ENABLED                       kill switch, default enabled
  PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_DISABLE            per-provider opt-out
  PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_INTERVAL           override for a pace() provider (seconds)
  PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_MAX_CONCURRENT     override for a limit() provider
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

from . import _env

log = logging.getLogger(__name__)

_flag = _env.flag   # kept as a module-local name: existing call sites/tests use _flag(...)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


_ENABLED = _flag("PYNIGHTSKY_RATE_LIMIT_ENABLED", "1")


# --------------------------------------------------------------------------
# Per-provider configuration
# --------------------------------------------------------------------------

# Policy-grounded min-interval providers. Defaults match the constants they
# replace (darksky.py's former _NOMINATIM_SLEEP/_OVERPASS_SLEEP) or pair with
# circuit_breaker.py's own extra-cautious Celestrak override (threshold 1,
# 300s cooldown) — same "be extra careful with this one" posture.
_PACE_DEFAULTS: dict[str, float] = {
    "nominatim": 1.1,
    "celestrak": 2.0,
    "overpass":  1.0,
}

# Bulk-tolerant providers: no documented strict per-IP throttle, but still capped
# so a 20-way trip.py fan-out never bursts all at once against one host.
_LIMIT_DEFAULTS: dict[str, int] = {
    "open_meteo":             10,
    "open_meteo_archive":     10,
    "open_meteo_air_quality": 10,
    "seven_timer":            10,
    "waqi":                   10,
}

_PACE_INTERVAL: dict[str, float] = {
    p: _float_env(f"PYNIGHTSKY_RATE_LIMIT_{p.upper()}_INTERVAL", default)
    for p, default in _PACE_DEFAULTS.items()
}
_LIMIT_MAX_CONCURRENT: dict[str, int] = {
    p: max(1, _int_env(f"PYNIGHTSKY_RATE_LIMIT_{p.upper()}_MAX_CONCURRENT", default))
    for p, default in _LIMIT_DEFAULTS.items()
}

_ALL_PROVIDERS = tuple(_PACE_DEFAULTS) + tuple(_LIMIT_DEFAULTS)
_DISABLED_PROVIDERS = frozenset(
    p for p in _ALL_PROVIDERS if _flag(f"PYNIGHTSKY_RATE_LIMIT_{p.upper()}_DISABLE")
)


# --------------------------------------------------------------------------
# State — one lock per pace() provider, one semaphore per limit() provider,
# all built eagerly since the provider set is fixed at import.
# --------------------------------------------------------------------------

_PACE_LOCKS: dict[str, threading.Lock] = {p: threading.Lock() for p in _PACE_DEFAULTS}
_last_call: dict[str, float] = {p: 0.0 for p in _PACE_DEFAULTS}

_SEMAPHORES: dict[str, threading.Semaphore] = {
    p: threading.Semaphore(n) for p, n in _LIMIT_MAX_CONCURRENT.items()
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

@contextlib.contextmanager
def pace(provider: str):
    """Block __enter__ until *provider*'s min-interval has elapsed since the last
    call start (across every caller sharing this key); __exit__ is a no-op — this
    gates call starts, not in-flight duration."""
    lock = None
    if _ENABLED and provider not in _DISABLED_PROVIDERS:
        lock = _PACE_LOCKS.get(provider)
    if lock is None:
        yield
        return
    interval = _PACE_INTERVAL[provider]
    with lock:
        wait = interval - (time.monotonic() - _last_call[provider])
        if wait > 0:
            time.sleep(wait)
        _last_call[provider] = time.monotonic()
    yield


@contextlib.contextmanager
def limit(provider: str):
    """Block __enter__ until a concurrency permit for *provider* is free; hold it
    for the whole `with` block, release on exit (success or exception)."""
    sem = None
    if _ENABLED and provider not in _DISABLED_PROVIDERS:
        sem = _SEMAPHORES.get(provider)
    if sem is None:
        yield
        return
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


def acquire(provider: str):
    """The one call-site API: `with rate_limiter.acquire(provider): <do the call>`.

    Dispatches to pace() or limit() per the provider's configuration; a no-op
    (nullcontext) for any provider with no configured entry.
    """
    if provider in _PACE_DEFAULTS:
        return pace(provider)
    if provider in _LIMIT_DEFAULTS:
        return limit(provider)
    return contextlib.nullcontext()


def reset() -> None:
    """Clear all rate-limiter state (test isolation; mirrors circuit_breaker.reset()).

    Resets every pace provider's last-call clock to 0 (next acquire() never waits)
    and rebuilds every semaphore fresh (defends against a test that acquired
    without releasing leaking a permit into the next test).

    Rebuilding replaces the semaphore object outright rather than draining/
    refilling it in place, so this assumes the limiter is idle when called (true
    for its only caller today, the autouse test fixture, as long as tests join
    their threads before returning). A caller still mid-`limit()` across this
    call keeps using the old object for the rest of its own call — logged below
    so that scenario is visible instead of silently under-enforcing the cap.
    """
    for p, lock in _PACE_LOCKS.items():
        with lock:
            _last_call[p] = 0.0
    for p, n in _LIMIT_MAX_CONCURRENT.items():
        outstanding = n - _SEMAPHORES[p]._value
        if outstanding > 0:
            log.warning(
                "rate_limiter.reset(): %d permit(s) for %r still held by an "
                "in-flight caller — that caller will keep using the semaphore "
                "being replaced here, so the concurrency cap won't cover it",
                outstanding, p,
            )
        _SEMAPHORES[p] = threading.Semaphore(n)
