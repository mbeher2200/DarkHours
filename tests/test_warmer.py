"""TLE warmer handler (M6.2) — hermetic (tle_provider mocked, no network/AWS)."""
from apps.warmer import handler as h
from darkhours import tle_provider as tle


def test_warm_all_ok(monkeypatch):
    monkeypatch.setattr(tle, "get_tle",
                        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda timeout=None: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is True
    assert out["results"]["ISS"] == "ok"
    assert out["results"]["Hubble"] == "ok"
    assert "1 trains" in out["results"]["starlink"]


def test_warm_reports_failures(monkeypatch):
    monkeypatch.setattr(tle, "get_tle",
                        lambda n, timeout=None: tle.TLEResult(lines=None, stale=False, error="HTTP 503"))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda timeout=None: ([], True, "timed out"))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "FAIL" in out["results"]["ISS"]
    assert "stale" in out["results"]["starlink"]


def test_warm_stale_is_not_ok(monkeypatch):
    # stale data served (fetch failed but cache had an old entry) → ok=False
    monkeypatch.setattr(tle, "get_tle",
                        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=True, error="HTTP 500"))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda timeout=None: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "stale" in out["results"]["ISS"]


def test_warmer_uses_the_long_timeout_not_the_request_path_one(monkeypatch):
    """The warmer must opt into the patient timeout.

    The request path fails fast (5s) so a hung Celestrak fetch never holds a user
    request. The warmer has nobody waiting and its success is what stops the
    request path from needing to fetch at all, so it gets the longer budget. If
    it silently inherited the short default, the job most likely to succeed would
    be the one most likely to give up.
    """
    seen: dict[str, float] = {}

    def fake_get_tle(norad, timeout=None):
        seen["tle"] = timeout
        return tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None)

    def fake_group(timeout=None):
        seen["group"] = timeout
        return [("a", "b", "c")], False, None

    monkeypatch.setattr(tle, "get_tle", fake_get_tle)
    monkeypatch.setattr(tle, "get_starlink_train_tles", fake_group)
    h.handler({}, None)

    assert seen["tle"] == tle._WARM_FETCH_TIMEOUT
    assert seen["group"] == tle._WARM_FETCH_TIMEOUT
    assert seen["tle"] > tle._FETCH_TIMEOUT
