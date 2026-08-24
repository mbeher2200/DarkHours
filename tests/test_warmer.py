"""TLE warmer handler (M6.2) — hermetic (tle_provider mocked, no network/AWS).

The warmer is the *only* thing that populates the TLE cache keys: the request path
reads them and never fetches. So these tests are less about "did the fetch work" and
more about "can this job ever report success while the cache stays empty" — which is
exactly what it did in production for months.
"""
import json
import time

import pytest

from apps.warmer import handler as h
from darkhours import tle_provider as tle


class _FakeCache:
    """In-memory stand-in for the cache port, with real TTL semantics."""

    def __init__(self, accept_writes=True):
        self.d = {}
        self.accept_writes = accept_writes
        self.writes = []

    def get(self, key):
        entry = self.d.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and time.time() > expires:
            return None
        return value

    def get_stale(self, key):
        entry = self.d.get(key)
        return None if entry is None else entry[0]

    def set(self, key, value, ttl_seconds=None):
        self.writes.append((key, ttl_seconds))
        if not self.accept_writes:
            return False
        self.d[key] = (value, time.time() + ttl_seconds if ttl_seconds else None)
        return True


@pytest.fixture
def fake_cache(monkeypatch):
    c = _FakeCache()
    monkeypatch.setattr(tle, "_cache", c)
    return c


def _ok_refreshers(monkeypatch, cache, trains=1):
    """Wire refresh_* to succeed and actually populate *cache*, as the real ones do."""
    seen = {}

    def fake_refresh_tle(norad, timeout=None):
        seen["tle"] = timeout
        cache.set(tle._tle_key(norad), "NAME\nline1\nline2", ttl_seconds=tle.TLE_TTL)
        return tle.TLEResult(lines=("NAME", "line1", "line2"), stale=False, error=None)

    def fake_refresh_group(timeout=None):
        seen["group"] = timeout
        value = [["S", "1 x", "2 x"]] * trains
        cache.set(tle._STARLINK_TRAINS_CACHE_KEY, value, ttl_seconds=tle.TLE_TTL)
        return value, False, None

    monkeypatch.setattr(tle, "refresh_tle", fake_refresh_tle)
    monkeypatch.setattr(tle, "refresh_starlink_trains", fake_refresh_group)
    return seen


def test_warm_all_ok(monkeypatch, fake_cache):
    _ok_refreshers(monkeypatch, fake_cache)
    out = h.handler({}, None)
    assert out["ok"] is True
    assert out["results"]["ISS"]["status"] == "ok"
    assert out["results"]["ISS"]["verified"] is True
    assert out["results"]["ISS"]["bytes"] > 0
    assert "1 trains" in out["results"]["starlink"]["status"]


def test_warm_reports_failures(monkeypatch, fake_cache):
    monkeypatch.setattr(tle, "refresh_tle",
                        lambda n, timeout=None: tle.TLEResult(lines=None, stale=False, error="HTTP 503"))
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None: ([], True, "timed out"))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "FAIL" in out["results"]["ISS"]["status"]


def test_warm_stale_is_not_ok(monkeypatch, fake_cache):
    # stale data served (fetch failed but cache had an old entry) → ok=False
    monkeypatch.setattr(
        tle, "refresh_tle",
        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=True, error="HTTP 500"))
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "stale" in out["results"]["ISS"]["status"]


def test_warm_is_not_ok_when_the_write_is_silently_dropped(monkeypatch):
    """The production failure, in one test.

    Every fetch succeeds. Every refresh reports success. The cache accepts nothing —
    which is exactly what DynamoDB did with the 1.8 MB Starlink blob for months. The
    old handler derived ok from (stale, error) and reported ok=True through all of
    it, 58 runs over 14 days, while the row did not exist. Reading the value back is
    what makes that impossible to report as success.
    """
    dead = _FakeCache(accept_writes=False)
    monkeypatch.setattr(tle, "_cache", dead)
    monkeypatch.setattr(
        tle, "refresh_tle",
        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None: ([("a", "b", "c")], False, None))

    out = h.handler({}, None)

    assert out["ok"] is False
    for label, result in out["results"].items():
        assert result["verified"] is False, f"{label} claimed a verified write"
        assert "NOT CACHED" in result["status"]


def test_warmer_forces_a_refresh_even_when_the_cache_is_fresh(monkeypatch, fake_cache):
    """The warmer must refresh unconditionally, not only repair an expired entry.

    It previously called a function that returned early on a fresh cache hit, making
    it a no-op whenever the cache was healthy. It could then only act *after* an
    entry had already expired — so the refresh always landed on whichever user asked
    first. Observed live: three tle| rows written 22.1h ago against a 24h TTL, with
    four warmer runs in between that touched none of them.
    """
    # Pre-populate every key with a fresh entry.
    for norad, _ in tle.TRACKED_SATELLITES:
        fake_cache.set(tle._tle_key(norad), "NAME\nl1\nl2", ttl_seconds=tle.TLE_TTL)
    fake_cache.set(tle._STARLINK_TRAINS_CACHE_KEY, [], ttl_seconds=tle.TLE_TTL)

    calls = []
    monkeypatch.setattr(tle, "refresh_tle", lambda n, timeout=None: (
        calls.append(n),
        tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))[1])
    monkeypatch.setattr(tle, "refresh_starlink_trains", lambda timeout=None: (
        calls.append("group"), ([], False, None))[1])

    h.handler({}, None)

    assert [n for n, _ in tle.TRACKED_SATELLITES] + ["group"] == calls, \
        "a fresh cache entry must not skip the refresh — that is what lets it expire"


def test_cli_warm_skips_refresh_when_already_fresh(monkeypatch, fake_cache):
    """force=False is the CLI's mode: fill a cold cache, leave a warm one alone.

    The CLI has no warmer behind it, so it must fetch on a cold cache — but a warm
    local cache should not cost the operator Celestrak's 2s pacing per call.
    """
    for norad, _ in tle.TRACKED_SATELLITES:
        fake_cache.set(tle._tle_key(norad), "NAME\nl1\nl2", ttl_seconds=tle.TLE_TTL)
    fake_cache.set(tle._STARLINK_TRAINS_CACHE_KEY, [], ttl_seconds=tle.TLE_TTL)

    calls = []
    monkeypatch.setattr(tle, "refresh_tle", lambda n, timeout=None: calls.append(n))
    monkeypatch.setattr(tle, "refresh_starlink_trains", lambda timeout=None: calls.append("g"))

    summary = tle.warm_cache(force=False)

    assert calls == []
    assert summary.ok is True


def test_warmer_uses_the_long_timeout_not_the_request_path_one(monkeypatch, fake_cache):
    """The warmer must opt into the patient timeout.

    _FETCH_TIMEOUT is the interactive budget (the CLI, where a person is waiting).
    The warmer has nobody waiting and its success is what stops anyone else from
    needing to fetch at all, so it gets the longer budget. If it silently inherited
    the short default, the job most likely to succeed would be the one most likely
    to give up.
    """
    seen = _ok_refreshers(monkeypatch, fake_cache)
    h.handler({}, None)

    assert seen["tle"] == tle._WARM_FETCH_TIMEOUT
    assert seen["group"] == tle._WARM_FETCH_TIMEOUT
    assert seen["tle"] > tle._FETCH_TIMEOUT


def test_emits_one_failure_metric_and_a_per_key_success_metric(monkeypatch, fake_cache, capsys):
    """EMF is how this becomes an alarm rather than a log nobody reads."""
    _ok_refreshers(monkeypatch, fake_cache)
    h.handler({}, None)

    emitted = [json.loads(line) for line in capsys.readouterr().out.splitlines()
               if line.startswith("{")]
    per_key = [e for e in emitted if "TleWarmSuccess" in e]
    failures = [e for e in emitted if "TleWarmFailure" in e]

    assert {e["Key"] for e in per_key} == {"ISS", "Hubble Telescope", "Tiangong", "starlink"}
    assert all(e["TleWarmSuccess"] == 1 for e in per_key)
    assert all(e["TleCachedBytes"] > 0 for e in per_key)
    assert len(failures) == 1 and failures[0]["TleWarmFailure"] == 0
