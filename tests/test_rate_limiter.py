"""
Tests for rate_limiter.py — pace()/limit() mechanics, kill switch/per-provider
disable, reset() isolation, env-var parsing, and the cross-module Nominatim
sharing regression test (the concrete proof that location.py and darksky.py now
pace through one shared clock instead of two independent ones). All hermetic:
no network, no real AWS, no real sleeping (pace() tests use a fake clock; limit()
tests use small real sleeps since no interval math is involved there).
"""
import threading
import time as real_time
import types
from unittest import mock

import pytest

from darkhours import rate_limiter as rl


class FakeClock:
    """Controllable stand-in for time.monotonic / time.time / time.sleep.

    Unlike circuit_breaker.py's FakeClock (which is never asked to sleep — every
    breaker decision is instant), pace() genuinely calls time.sleep(), so this
    clock's sleep() advances the same shared, lock-protected clock rather than
    blocking for real.
    """

    def __init__(self, start=1_000.0):
        self.t = start
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.t

    def advance(self, seconds):
        with self._lock:
            self.t += seconds

    def sleep(self, seconds):
        self.advance(seconds)


@pytest.fixture
def clock(monkeypatch):
    """Fresh rate-limiter state + a controllable clock scoped to the module."""
    c = FakeClock()
    monkeypatch.setattr(rl, "time", types.SimpleNamespace(monotonic=c, time=c, sleep=c.sleep))
    rl.reset()
    yield c
    rl.reset()


@pytest.fixture(autouse=True)
def _deterministic_flags(monkeypatch):
    """Tests must not depend on the developer's environment."""
    monkeypatch.setattr(rl, "_ENABLED", True)
    monkeypatch.setattr(rl, "_DISABLED_PROVIDERS", frozenset())


# ---------------------------------------------------------------------------
# pace() — min-interval serialization
# ---------------------------------------------------------------------------

def test_pace_first_call_never_sleeps(clock):
    with rl.pace("nominatim"):
        pass
    assert clock.t == 1_000.0


def test_pace_second_call_sleeps_for_remaining_gap(clock):
    with rl.pace("nominatim"):
        pass
    t_after_first = clock.t
    with rl.pace("nominatim"):
        pass
    assert clock.t - t_after_first == pytest.approx(rl._PACE_INTERVAL["nominatim"])


def test_pace_call_after_interval_elapsed_does_not_sleep(clock):
    with rl.pace("celestrak"):
        pass
    clock.advance(rl._PACE_INTERVAL["celestrak"] + 0.5)
    t_before = clock.t
    with rl.pace("celestrak"):
        pass
    assert clock.t == t_before


def test_pace_providers_have_independent_clocks(clock):
    """Pacing one provider must not perturb another's clock."""
    with rl.pace("nominatim"):
        pass
    with rl.pace("celestrak"):
        pass
    # celestrak's first-ever call shouldn't have waited for nominatim's interval
    assert clock.t == 1_000.0


def test_pace_concurrent_calls_serialize_with_min_interval(clock):
    """N threads racing pace() for one provider must come out spaced >= interval
    apart on the (fake, but shared) clock — proves the per-provider lock genuinely
    serializes the wait+stamp step rather than just being eventually consistent."""
    n = 8
    barrier = threading.Barrier(n)
    call_times: list[float] = []
    call_times_lock = threading.Lock()

    def worker():
        barrier.wait()
        with rl.pace("nominatim"):
            with call_times_lock:
                call_times.append(clock.t)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(call_times) == n
    call_times.sort()
    gaps = [b - a for a, b in zip(call_times, call_times[1:])]
    interval = rl._PACE_INTERVAL["nominatim"]
    assert all(gap >= interval - 1e-9 for gap in gaps)


# ---------------------------------------------------------------------------
# limit() — concurrency-cap serialization
# ---------------------------------------------------------------------------

def test_limit_never_exceeds_max_concurrent(monkeypatch):
    monkeypatch.setitem(rl._LIMIT_MAX_CONCURRENT, "open_meteo", 3)
    rl.reset()

    n = 10
    barrier = threading.Barrier(n)
    state_lock = threading.Lock()
    concurrent = 0
    max_concurrent = 0

    def worker():
        nonlocal concurrent, max_concurrent
        barrier.wait()
        with rl.limit("open_meteo"):
            with state_lock:
                concurrent += 1
                max_concurrent = max(max_concurrent, concurrent)
            real_time.sleep(0.05)
            with state_lock:
                concurrent -= 1

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_concurrent == 3


def test_limit_releases_permit_on_exception(monkeypatch):
    monkeypatch.setitem(rl._LIMIT_MAX_CONCURRENT, "waqi", 1)
    rl.reset()

    with pytest.raises(RuntimeError):
        with rl.limit("waqi"):
            raise RuntimeError("boom")

    # a fresh acquire must not block now that the permit was released
    acquired = []

    def worker():
        with rl.limit("waqi"):
            acquired.append(True)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=1.0)
    assert acquired == [True]


# ---------------------------------------------------------------------------
# acquire() dispatch
# ---------------------------------------------------------------------------

def test_acquire_dispatches_pace_type(clock):
    with rl.acquire("nominatim"):
        pass
    t_after_first = clock.t
    with rl.acquire("nominatim"):
        pass
    assert clock.t - t_after_first == pytest.approx(rl._PACE_INTERVAL["nominatim"])


def test_acquire_dispatches_limit_type(monkeypatch):
    monkeypatch.setitem(rl._LIMIT_MAX_CONCURRENT, "waqi", 1)
    rl.reset()
    sem = rl._SEMAPHORES["waqi"]
    with rl.acquire("waqi"):
        assert sem._value == 0   # permit held for the duration of the block
    assert sem._value == 1


def test_acquire_is_noop_for_unconfigured_provider(clock):
    """swpc/aws_location/aws_georoutes are deliberately unconfigured — already
    protected by other means (global fetch lock, quota-capped max-workers)."""
    with rl.acquire("swpc"):
        pass
    with rl.acquire("swpc"):
        pass
    assert clock.t == 1_000.0   # no pacing state exists for this key at all


# ---------------------------------------------------------------------------
# Kill switch / per-provider disable
# ---------------------------------------------------------------------------

def test_globally_disabled_never_paces(clock, monkeypatch):
    monkeypatch.setattr(rl, "_ENABLED", False)
    with rl.pace("nominatim"):
        pass
    with rl.pace("nominatim"):
        pass
    assert clock.t == 1_000.0


def test_per_provider_disable_skips_pacing(clock, monkeypatch):
    monkeypatch.setattr(rl, "_DISABLED_PROVIDERS", frozenset({"nominatim"}))
    with rl.pace("nominatim"):
        pass
    with rl.pace("nominatim"):
        pass
    assert clock.t == 1_000.0


def test_globally_disabled_never_limits(monkeypatch):
    monkeypatch.setattr(rl, "_ENABLED", False)
    monkeypatch.setitem(rl._LIMIT_MAX_CONCURRENT, "waqi", 1)
    rl.reset()
    # two nested acquisitions would deadlock a real semaphore of size 1
    with rl.limit("waqi"):
        with rl.limit("waqi"):
            pass


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

def test_reset_clears_pace_state(clock):
    with rl.pace("nominatim"):
        pass
    clock.advance(0.1)   # well under the 1.1s interval
    rl.reset()
    t_before = clock.t
    with rl.pace("nominatim"):
        pass
    assert clock.t == t_before   # looks like a fresh provider after reset


def test_reset_rebuilds_leaked_semaphore(monkeypatch):
    monkeypatch.setitem(rl._LIMIT_MAX_CONCURRENT, "waqi", 1)
    rl.reset()
    rl._SEMAPHORES["waqi"].acquire()   # simulate a leaked permit, never released
    rl.reset()

    acquired = []

    def worker():
        with rl.limit("waqi"):
            acquired.append(True)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=1.0)
    assert acquired == [True]


# ---------------------------------------------------------------------------
# Provider coverage
# ---------------------------------------------------------------------------

def test_provider_coverage():
    """Hardcode the expected rate-limited key set so a future call site added
    without rate-limiter wiring shows up as a failing assertion here, not a
    silent gap."""
    expected = {
        "nominatim", "celestrak", "overpass",
        "open_meteo", "open_meteo_archive", "open_meteo_air_quality",
        "seven_timer", "waqi",
    }
    assert expected == set(rl._ALL_PROVIDERS)


def test_provider_registry_asymmetry_against_circuit_breaker_is_the_known_one():
    """rate_limiter.py and circuit_breaker.py each keep their own independent
    provider registry — nothing structurally forces a provider added to one to
    also land in the other. Pin the *documented* asymmetry (docs/RATE_LIMITING.md
    "Provider configuration") so any *other* drift — a new provider that quietly
    gets one protection but not the other — fails here instead of going
    unnoticed:

    - "overpass" is rate-limited but has no circuit-breaker gate at all.
    - "swpc"/"aws_location"/"aws_georoutes" are breaker-gated but deliberately
      not rate-limited (already protected by other means — see
      docs/RATE_LIMITING.md's "confirmed non-gaps" section).
    """
    from darkhours import circuit_breaker as cb

    rl_only = set(rl._ALL_PROVIDERS) - set(cb._ALL_PROVIDERS)
    cb_only = set(cb._ALL_PROVIDERS) - set(rl._ALL_PROVIDERS)

    assert rl_only == {"overpass"}
    assert cb_only == {"swpc", "aws_location", "aws_georoutes"}


# ---------------------------------------------------------------------------
# Env-var parsing
# ---------------------------------------------------------------------------

def test_float_env_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", "3.5")
    assert rl._float_env("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", 1.0) == 3.5
    monkeypatch.delenv("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", raising=False)
    assert rl._float_env("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", 1.0) == 1.0
    monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", "not-a-number")
    assert rl._float_env("PYNIGHTSKY_RATE_LIMIT_TEST_INTERVAL", 1.0) == 1.0


def test_int_env_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", "7")
    assert rl._int_env("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", 5) == 7
    monkeypatch.delenv("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", raising=False)
    assert rl._int_env("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", 5) == 5
    monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", "not-a-number")
    assert rl._int_env("PYNIGHTSKY_RATE_LIMIT_TEST_MAX", 5) == 5


def test_flag_parses_common_truthy_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_FLAG", v)
        assert rl._flag("PYNIGHTSKY_RATE_LIMIT_TEST_FLAG") is True
    monkeypatch.setenv("PYNIGHTSKY_RATE_LIMIT_TEST_FLAG", "0")
    assert rl._flag("PYNIGHTSKY_RATE_LIMIT_TEST_FLAG") is False


# ---------------------------------------------------------------------------
# Cross-module integration — the concrete regression test for the gap this
# module closes: location.py's geopy-based Nominatim call and darksky.py's
# raw-HTTP Nominatim call must share one pacer key, not two independent ones.
# ---------------------------------------------------------------------------

def test_nominatim_pacing_shared_across_location_and_darksky(clock):
    from darkhours import darksky, location as loc

    with mock.patch.object(loc, "Nominatim") as geocoder_cls:
        geocoder_cls.return_value.geocode.return_value = None
        loc._geocode_via_nominatim("Denver", "Denver")

    t_after_first = clock.t

    with mock.patch.object(darksky.cache, "get", return_value=None), \
         mock.patch.object(darksky.cache, "set"), \
         mock.patch.object(darksky._http, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"address": {}}'
        darksky._nominatim_settlement(39.7392, -104.9903)

    urlopen.assert_called_once()   # the second call was NOT skipped by the breaker
    assert clock.t - t_after_first == pytest.approx(rl._PACE_INTERVAL["nominatim"])
