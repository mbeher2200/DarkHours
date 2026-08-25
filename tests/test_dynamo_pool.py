"""Every DynamoDB client the engine builds must size its connection pool.

botocore defaults to 10 connections. find_nearby fans out wider than that, and a
connection handed back to a full pool is discarded rather than parked — so the
next caller pays a fresh TCP+TLS handshake and the worker logs "Connection pool
is full, discarding connection". cache.py had always set this; feature_flags.py
and circuit_breaker.py set every other bound on their Config but not this one,
which is the shape of bug these tests exist to catch.
"""
import os

import pytest

from darkhours import _env


BOTOCORE_DEFAULT_POOL = 10


# --------------------------------------------------------------------------
# the shared knob
# --------------------------------------------------------------------------

def test_default_is_above_the_botocore_default():
    assert _env.dynamo_pool() > BOTOCORE_DEFAULT_POOL


def test_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_DYNAMO_POOL", "40")
    assert _env.dynamo_pool() == 40


def test_garbage_override_falls_back_to_the_default(monkeypatch):
    """An unparseable value must not take the process down on first cache read."""
    monkeypatch.setenv("PYNIGHTSKY_DYNAMO_POOL", "not-a-number")
    assert _env.dynamo_pool() == _env.DYNAMO_POOL_DEFAULT


def test_pool_is_never_zero(monkeypatch):
    """botocore raises on a pool of 0; clamp rather than propagate."""
    monkeypatch.setenv("PYNIGHTSKY_DYNAMO_POOL", "0")
    assert _env.dynamo_pool() >= 1


# --------------------------------------------------------------------------
# each client actually applies it
# --------------------------------------------------------------------------

def _captured_config(monkeypatch, module, builder, table_env=None):
    """Build a table handle with boto3 stubbed, returning the botocore Config."""
    boto3 = pytest.importorskip("boto3")
    seen = {}

    class _FakeResource:
        def Table(self, name):
            return object()

    def _fake_resource(service, **kwargs):
        seen["service"] = service
        seen["config"] = kwargs.get("config")
        return _FakeResource()

    monkeypatch.setattr(boto3, "resource", _fake_resource)
    builder()
    return seen["config"]


def test_feature_flags_client_sizes_its_pool(monkeypatch):
    from darkhours import feature_flags as ff
    monkeypatch.setattr(ff, "_ddb_table", None)
    cfg = _captured_config(monkeypatch, ff, ff._table)
    assert cfg.max_pool_connections == _env.dynamo_pool()
    assert cfg.max_pool_connections > BOTOCORE_DEFAULT_POOL
    # The fail-fast bounds this module depends on must survive the change.
    assert cfg.connect_timeout == 1.0
    assert cfg.read_timeout == 1.0
    monkeypatch.setattr(ff, "_ddb_table", None)


def test_circuit_breaker_client_sizes_its_pool(monkeypatch):
    from darkhours import circuit_breaker as cb
    monkeypatch.setattr(cb, "_ddb_table", None)
    cfg = _captured_config(monkeypatch, cb, cb._table)
    assert cfg.max_pool_connections == _env.dynamo_pool()
    assert cfg.max_pool_connections > BOTOCORE_DEFAULT_POOL
    assert cfg.connect_timeout == 1.0
    assert cfg.read_timeout == 1.0
    monkeypatch.setattr(cb, "_ddb_table", None)


def test_cache_client_sizes_its_pool(monkeypatch):
    from darkhours import cache as _cache
    monkeypatch.setenv("PYNIGHTSKY_CACHE_TABLE", "unit-test-table")
    cfg = _captured_config(monkeypatch, _cache, _cache._dynamo_table)
    assert cfg.max_pool_connections == _env.dynamo_pool()
    assert cfg.max_pool_connections > BOTOCORE_DEFAULT_POOL
