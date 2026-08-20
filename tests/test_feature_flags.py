"""Tests for feature_flags.py — check ordering (global switch, per-flag env override,
table read), TTL caching, fail-open defaults, and the DynamoDB client's fail-fast bounds.
All hermetic: no network, no real AWS.
"""
import types
from unittest import mock

import pytest

from darkhours import feature_flags as ff


class FakeClock:
    """Controllable stand-in for time.time() — wall-clock, not monotonic (see
    _cached_value's docstring: monotonic doesn't reliably advance across a
    Lambda container's freeze/thaw, so the cache deliberately uses time.time())."""

    def __init__(self, start=1_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(ff, "time", types.SimpleNamespace(time=c))
    ff.reset()
    yield c
    ff.reset()


@pytest.fixture(autouse=True)
def _deterministic_flags(monkeypatch):
    """Tests must not depend on the developer's environment."""
    monkeypatch.setattr(ff, "_ENABLED", True)
    monkeypatch.setattr(ff, "_TABLE", "")


def _table_returning(item):
    table = mock.MagicMock()
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


# ---------------------------------------------------------------------------
# Fail-open defaults (no table configured)
# ---------------------------------------------------------------------------

def test_no_table_returns_default_true(clock):
    assert ff.enabled("satellites") is True


def test_no_table_returns_provided_default(clock):
    assert ff.enabled("satellites", default=False) is False


def test_no_table_never_touches_aws(clock, monkeypatch):
    table_factory = mock.MagicMock()
    monkeypatch.setattr(ff, "_table", table_factory)
    ff.enabled("satellites")
    table_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Global kill switch
# ---------------------------------------------------------------------------

def test_global_kill_switch_bypasses_table(clock, monkeypatch):
    monkeypatch.setattr(ff, "_ENABLED", False)
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    table = _table_returning({"flag_id": "satellites", "enabled": False})
    table_factory = mock.MagicMock(return_value=table)
    monkeypatch.setattr(ff, "_table", table_factory)
    assert ff.enabled("satellites") is True   # default, not the stored False
    table_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Per-flag env override
# ---------------------------------------------------------------------------

def test_per_flag_env_disable_short_circuits(clock, monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_SATELLITES_DISABLE", "1")
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    table_factory = mock.MagicMock()
    monkeypatch.setattr(ff, "_table", table_factory)
    assert ff.enabled("satellites") is False
    table_factory.assert_not_called()


def test_per_flag_env_disable_is_per_name(clock, monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_SATELLITES_DISABLE", "1")
    assert ff.enabled("satellites") is False
    assert ff.enabled("aurora") is True       # unaffected


# ---------------------------------------------------------------------------
# Table read semantics
# ---------------------------------------------------------------------------

def test_table_read_returns_stored_false(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    item = {"flag_id": "satellites", "enabled": False}
    monkeypatch.setattr(ff, "_table", lambda: _table_returning(item))
    assert ff.enabled("satellites") is False


def test_table_read_returns_stored_true(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    item = {"flag_id": "satellites", "enabled": True}
    monkeypatch.setattr(ff, "_table", lambda: _table_returning(item))
    assert ff.enabled("satellites", default=False) is True


def test_missing_item_falls_back_to_default(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    monkeypatch.setattr(ff, "_table", lambda: _table_returning(None))
    assert ff.enabled("never_set", default=True) is True
    assert ff.enabled("never_set", default=False) is False


def test_read_error_falls_back_to_default_and_is_cached(clock, monkeypatch):
    """A broken read (missing IAM, network) degrades to the default and is cached —
    the cost is paid once per cache TTL, not per request."""
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    table = mock.MagicMock()
    table.get_item.side_effect = RuntimeError("simulated AccessDenied/conn failure")
    monkeypatch.setattr(ff, "_table", lambda: table)
    assert ff.enabled("satellites") is True
    assert ff.enabled("satellites") is True
    assert table.get_item.call_count == 1  # second call served from cache


def test_cache_expires_after_ttl(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    table = _table_returning({"flag_id": "satellites", "enabled": False})
    monkeypatch.setattr(ff, "_table", lambda: table)
    assert ff.enabled("satellites") is False
    assert table.get_item.call_count == 1
    clock.advance(ff._CACHE_TTL_SECONDS + 1)
    assert ff.enabled("satellites") is False
    assert table.get_item.call_count == 2


def test_flags_are_cached_independently(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    items = {"satellites": {"flag_id": "satellites", "enabled": False}}   # "aurora": no item

    table = mock.MagicMock()
    table.get_item.side_effect = lambda Key: {"Item": items[Key["flag_id"]]} if Key["flag_id"] in items else {}
    monkeypatch.setattr(ff, "_table", lambda: table)

    assert ff.enabled("satellites") is False
    assert ff.enabled("aurora") is True   # separate cache slot, separate read, no item -> default
    assert table.get_item.call_count == 2


# ---------------------------------------------------------------------------
# snapshot() / reset()
# ---------------------------------------------------------------------------

def test_snapshot_reflects_cached_values_without_touching_table(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    monkeypatch.setattr(ff, "_table", lambda: _table_returning({"flag_id": "satellites", "enabled": False}))
    ff.enabled("satellites")
    table_factory = mock.MagicMock()
    monkeypatch.setattr(ff, "_table", table_factory)
    assert ff.snapshot() == {"satellites": False}
    table_factory.assert_not_called()


def test_snapshot_empty_before_any_read(clock):
    assert ff.snapshot() == {}


def test_reset_clears_cache(clock, monkeypatch):
    monkeypatch.setattr(ff, "_TABLE", "tbl")
    monkeypatch.setattr(ff, "_table", lambda: _table_returning({"flag_id": "satellites", "enabled": False}))
    ff.enabled("satellites")
    assert ff.snapshot() != {}
    ff.reset()
    assert ff.snapshot() == {}


# ---------------------------------------------------------------------------
# DynamoDB client fail-fast bounds
# ---------------------------------------------------------------------------

def test_ddb_client_has_fail_fast_bounds():
    """The lazy DynamoDB handle must be built with tight timeouts — botocore's 60s
    defaults would reintroduce exactly the request-blocking behavior this module
    exists to avoid."""
    boto3 = pytest.importorskip("boto3")
    with mock.patch.object(ff, "_TABLE", "tbl"), \
         mock.patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"}):
        ff._ddb_table = None
        try:
            table = ff._table()
            config = table.meta.client.meta.config
            assert config.connect_timeout == 1.0
            assert config.read_timeout == 1.0
            assert config.retries["total_max_attempts"] == 1
            assert config.retries["mode"] == "standard"
        finally:
            ff._ddb_table = None
