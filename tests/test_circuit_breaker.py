"""
Tests for circuit_breaker.py — state machine (trip/reset/probe cycle), flag
handling, monitor-driven vs self-timed recovery, probe atomicity, fail-fast
monitor reads, and the AWS client latency bounds the breaker's detection
speed depends on. All hermetic: no network, no real AWS.
"""
import threading
import types
import time as real_time
from unittest import mock

import pytest

from darkhours import circuit_breaker as cb


class FakeClock:
    """Controllable stand-in for time.monotonic / time.time."""

    def __init__(self, start=1_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    """Fresh breaker state + a controllable clock scoped to the module."""
    c = FakeClock()
    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=c, time=c))
    cb.reset()
    yield c
    cb.reset()


@pytest.fixture(autouse=True)
def _deterministic_flags(monkeypatch):
    """Tests must not depend on the developer's environment."""
    monkeypatch.setattr(cb, "_ENABLED", True)
    monkeypatch.setattr(cb, "_DISABLED_PROVIDERS", frozenset())
    monkeypatch.setattr(cb, "_HEALTH_TABLE", "")


def _trip(provider, clock=None):
    threshold, _ = cb._limits(provider)
    for _ in range(threshold):
        cb.on_failure(provider)


# ---------------------------------------------------------------------------
# Core state machine
# ---------------------------------------------------------------------------

def test_closed_by_default(clock):
    assert cb.allow("open_meteo")
    assert not cb.is_open("open_meteo")


def test_opens_after_threshold_consecutive_failures(clock):
    cb.on_failure("open_meteo")
    cb.on_failure("open_meteo")
    assert cb.allow("open_meteo")          # 2 < threshold of 3
    cb.on_failure("open_meteo")
    assert cb.is_open("open_meteo")
    assert not cb.allow("open_meteo")


def test_success_resets_consecutive_count(clock):
    cb.on_failure("open_meteo")
    cb.on_failure("open_meteo")
    cb.on_success("open_meteo")
    cb.on_failure("open_meteo")
    cb.on_failure("open_meteo")
    assert not cb.is_open("open_meteo")    # never 3 in a row


def test_cooldown_grants_exactly_one_probe(clock):
    _trip("open_meteo")
    assert not cb.allow("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow("open_meteo")          # the probe
    assert not cb.allow("open_meteo")      # grant re-armed the clock


def test_probe_failure_rearms_fresh_cooldown(clock):
    _trip("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow("open_meteo")
    cb.on_failure("open_meteo")            # probe failed
    clock.advance(cb.COOLDOWN_SECONDS - 1)
    assert not cb.allow("open_meteo")      # still inside the fresh cooldown
    clock.advance(2)
    assert cb.allow("open_meteo")


def test_probe_success_closes(clock):
    _trip("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow("open_meteo")
    cb.on_success("open_meteo")
    assert not cb.is_open("open_meteo")
    assert cb.allow("open_meteo")
    assert cb.allow("open_meteo")          # fully closed, not probe-limited


def test_kill_switch_bypasses_gating(clock, monkeypatch):
    _trip("open_meteo")
    monkeypatch.setattr(cb, "_ENABLED", False)
    assert cb.allow("open_meteo")


def test_per_provider_disable(clock, monkeypatch):
    monkeypatch.setattr(cb, "_DISABLED_PROVIDERS", frozenset({"open_meteo"}))
    _trip("open_meteo")
    _trip("waqi")
    assert cb.allow("open_meteo")          # disabled: never gated
    assert not cb.allow("waqi")            # others still trip normally


def test_keys_are_independent(clock):
    _trip("open_meteo_archive")
    assert not cb.allow("open_meteo_archive")
    assert cb.allow("open_meteo")
    assert not cb.is_open("open_meteo")


def test_celestrak_override_trips_on_first_failure(clock):
    cb.on_failure("celestrak")
    assert cb.is_open("celestrak")
    clock.advance(60 + 1)
    assert not cb.allow("celestrak")       # 60s default doesn't apply
    clock.advance(300)
    assert cb.allow("celestrak")


def test_celestrak_never_consults_monitor(clock, monkeypatch):
    monitor = mock.MagicMock(return_value="UP")
    monkeypatch.setattr(cb, "_synthetic_status", monitor)
    cb.on_failure("celestrak")
    cb.allow("celestrak")
    monitor.assert_not_called()


def test_provider_unavailable_error_attributes(clock):
    _trip("open_meteo")
    err = cb.unavailable("open_meteo")
    assert isinstance(err, RuntimeError)
    assert err.provider == "open_meteo"
    assert 0 < err.retry_after_seconds <= cb.COOLDOWN_SECONDS
    assert "open_meteo" in str(err)


# ---------------------------------------------------------------------------
# Monitor-driven recovery
# ---------------------------------------------------------------------------

def test_deadlock_regression_no_signal_self_times(clock):
    """Env var unset (shipped default): a tripped monitor-eligible provider
    must recover via self-timed cooldown, never stay blocked forever."""
    _trip("open_meteo")
    assert not cb.allow("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS + 1)
    assert cb.allow("open_meteo")


def test_monitor_down_blocks_past_cooldown(clock, monkeypatch):
    monkeypatch.setattr(cb, "_synthetic_status", lambda p: "DOWN")
    _trip("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS * 10)
    assert not cb.allow("open_meteo")      # fresh DOWN outranks the local timer


def test_monitor_up_grants_probe_but_only_success_closes(clock, monkeypatch):
    monkeypatch.setattr(cb, "_synthetic_status", lambda p: "UP")
    _trip("open_meteo")
    clock.advance(cb._PROBE_GUARD_SECONDS + 1)
    assert cb.allow("open_meteo")          # probe granted well before cooldown
    assert cb.is_open("open_meteo")        # UP alone must not close the breaker
    cb.on_success("open_meteo")
    assert not cb.is_open("open_meteo")


def test_monitor_stuck_up_bounded_by_probe_guard(clock, monkeypatch):
    """Monitor says UP but the provider keeps failing for us: at most one
    probe per guard interval, not one per request."""
    monkeypatch.setattr(cb, "_synthetic_status", lambda p: "UP")
    _trip("open_meteo")
    clock.advance(cb._PROBE_GUARD_SECONDS + 1)
    assert cb.allow("open_meteo")
    cb.on_failure("open_meteo")
    for _ in range(20):
        assert not cb.allow("open_meteo")  # within the guard window
    clock.advance(cb._PROBE_GUARD_SECONDS + 1)
    assert cb.allow("open_meteo")


def test_monitor_none_falls_back_to_self_timed(clock, monkeypatch):
    monkeypatch.setattr(cb, "_synthetic_status", lambda p: None)
    _trip("open_meteo")
    clock.advance(cb._PROBE_GUARD_SECONDS + 1)
    assert not cb.allow("open_meteo")      # guard interval is a monitor-UP privilege
    clock.advance(cb.COOLDOWN_SECONDS)
    assert cb.allow("open_meteo")


# ---------------------------------------------------------------------------
# Probe atomicity
# ---------------------------------------------------------------------------

def test_concurrent_allow_grants_exactly_one_probe(clock):
    _trip("open_meteo")
    clock.advance(cb.COOLDOWN_SECONDS + 1)

    n = 16
    barrier = threading.Barrier(n)
    results = []

    def worker():
        barrier.wait()
        results.append(cb.allow("open_meteo"))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


# ---------------------------------------------------------------------------
# _synthetic_status: read semantics + fail-fast
# ---------------------------------------------------------------------------

def _table_returning(item):
    table = mock.MagicMock()
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def test_synthetic_status_unset_table_reads_nothing(clock, monkeypatch):
    table_factory = mock.MagicMock()
    monkeypatch.setattr(cb, "_table", table_factory)
    assert cb._synthetic_status("open_meteo") is None
    table_factory.assert_not_called()      # env unset: no AWS touch at all


def test_synthetic_status_fresh_entry(clock, monkeypatch):
    monkeypatch.setattr(cb, "_HEALTH_TABLE", "tbl")
    item = {"provider_id": "open-meteo", "status": "UP", "last_checked": int(clock.t)}
    monkeypatch.setattr(cb, "_table", lambda: _table_returning(item))
    assert cb._synthetic_status("open_meteo") == "UP"


def test_synthetic_status_stale_entry_is_none(clock, monkeypatch):
    monkeypatch.setattr(cb, "_HEALTH_TABLE", "tbl")
    stale = int(clock.t) - cb._MONITOR_STALE_AFTER - 60
    item = {"provider_id": "open-meteo", "status": "UP", "last_checked": stale}
    monkeypatch.setattr(cb, "_table", lambda: _table_returning(item))
    assert cb._synthetic_status("open_meteo") is None


def test_synthetic_status_read_error_returns_none_and_caches(clock, monkeypatch):
    """A broken read (missing IAM, network) degrades to None and is cached —
    the cost is paid once per cache TTL, not per request."""
    monkeypatch.setattr(cb, "_HEALTH_TABLE", "tbl")
    table = mock.MagicMock()
    table.get_item.side_effect = RuntimeError("simulated AccessDenied/conn failure")
    monkeypatch.setattr(cb, "_table", lambda: table)
    assert cb._synthetic_status("open_meteo") is None
    assert cb._synthetic_status("open_meteo") is None
    assert table.get_item.call_count == 1  # second call served from cache


def test_synthetic_status_client_error_returns_none(clock, monkeypatch):
    botocore_exc = pytest.importorskip("botocore.exceptions")
    monkeypatch.setattr(cb, "_HEALTH_TABLE", "tbl")
    table = mock.MagicMock()
    table.get_item.side_effect = botocore_exc.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "GetItem"
    )
    monkeypatch.setattr(cb, "_table", lambda: table)
    assert cb._synthetic_status("open_meteo") is None


def test_ddb_client_has_fail_fast_bounds():
    """The lazy DynamoDB handle must be built with tight timeouts — botocore's
    60s defaults would reintroduce the hang this module exists to remove."""
    boto3 = pytest.importorskip("boto3")
    with mock.patch.object(cb, "_HEALTH_TABLE", "tbl"), \
         mock.patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"}):
        cb._ddb_table = None
        try:
            table = cb._table()
            config = table.meta.client.meta.config
            assert config.connect_timeout == 1.0
            assert config.read_timeout == 1.0
            # botocore normalizes retries to total_max_attempts; one total
            # attempt is the whole point (fail fast, no retry storm).
            assert config.retries["total_max_attempts"] == 1
            assert config.retries["mode"] == "standard"
        finally:
            cb._ddb_table = None


# ---------------------------------------------------------------------------
# AWS Location / GeoRoutes client latency bounds (detection-latency budget)
# ---------------------------------------------------------------------------

def test_location_clients_have_bounded_latency():
    """Breaker detection speed = threshold x worst-case call latency; the AWS
    clients must be configured to fail fast (2s/5s, max 2 attempts) or the
    breaker is minutes-slow exactly when it matters."""
    pytest.importorskip("boto3")
    from darkhours import darksky

    with mock.patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"}):
        darksky._reset_location_client()
        try:
            for client in (darksky._location(), darksky._georoutes()):
                config = client.meta.config
                assert config.connect_timeout == 2.0
                assert config.read_timeout == 5.0
                # adaptive mode: max_attempts is the total (2 = 1 retry).
                assert config.retries["total_max_attempts"] == 2
                assert config.retries["mode"] == "adaptive"
        finally:
            darksky._reset_location_client()


# ---------------------------------------------------------------------------
# Cross-module integration
# ---------------------------------------------------------------------------

def test_air_quality_skipped_when_open(clock):
    """_fetch_air_quality honors its 'never a hard dependency' contract when
    skipped: empty list, no HTTP attempt, no exception."""
    from darkhours import weather as wx

    for _ in range(3):
        cb.on_failure("open_meteo_air_quality")
    with mock.patch.object(wx._http, "urlopen") as urlopen:
        assert wx._fetch_air_quality(40.0, -105.0) == []
    urlopen.assert_not_called()


def test_nominatim_key_shared_across_call_sites(clock):
    """A failure streak recorded via one Nominatim access path (geopy forward
    geocode) blocks the other (raw-HTTP reverse geocode in darksky) — same
    upstream, same breaker key, regardless of HTTP mechanism."""
    from darkhours import darksky, location as loc

    for _ in range(3):
        cb.on_failure("nominatim")

    # geopy path: raises without ever constructing a geocoder
    with mock.patch.object(loc, "Nominatim") as geocoder, \
         pytest.raises(RuntimeError, match="circuit open"):
        loc._geocode_via_nominatim("Denver", "Denver")
    geocoder.assert_not_called()

    # raw-HTTP path: returns None (its degrade contract) without HTTP
    with mock.patch.object(darksky.cache, "get", return_value=None), \
         mock.patch.object(darksky._http, "urlopen") as urlopen:
        assert darksky._nominatim_settlement(39.7392, -104.9903) is None
    urlopen.assert_not_called()


def test_aws_location_gate_skips_client_entirely(clock):
    from darkhours import darksky, location as loc

    for _ in range(3):
        cb.on_failure("aws_location")
    with mock.patch.object(darksky, "_location") as client_factory, \
         pytest.raises(RuntimeError, match="circuit open"):
        loc._geocode_via_aws("Denver", "Denver")
    client_factory.assert_not_called()


def test_trip_summary_serializes_wx_error(clock):
    """NightSummary round-trips wx_error, and tolerates pre-deploy cache
    entries that lack the field."""
    from datetime import date
    from darkhours import trip

    s = trip.NightSummary(
        date=date(2026, 7, 21), display_name="Test", lat=40.0, lon=-105.0,
        score=5.0, score_components={}, phase_name="New Moon",
        illumination_pct=1.0, moon_distance_km=384_400, moon_special=None,
        moon_eclipses=[], dark_hours=6.0, bortle_score=3.0,
        weather_score=None, weather_informed=False,
        wx_pending=False, wx_no_data=False,
        wx_error="open_meteo unavailable (circuit open, retry in 60s)",
    )
    d = trip._to_dict(s)
    assert d["wx_error"] == s.wx_error
    assert trip._from_dict(d).wx_error == s.wx_error

    d.pop("wx_error")            # pre-deploy cached blob
    assert trip._from_dict(d).wx_error is None
