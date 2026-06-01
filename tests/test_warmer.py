"""TLE warmer handler (M6.2) — hermetic (tle_provider mocked, no network/AWS)."""
from apps.warmer import handler as h
from PyNightSkyPredictor import tle_provider as tle


def test_warm_all_ok(monkeypatch):
    monkeypatch.setattr(tle, "get_tle",
                        lambda n: tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is True
    assert out["results"]["ISS"] == "ok"
    assert out["results"]["Hubble"] == "ok"
    assert "1 trains" in out["results"]["starlink"]


def test_warm_reports_failures(monkeypatch):
    monkeypatch.setattr(tle, "get_tle",
                        lambda n: tle.TLEResult(lines=None, stale=False, error="HTTP 503"))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda: ([], True, "timed out"))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "FAIL" in out["results"]["ISS"]
    assert "stale" in out["results"]["starlink"]


def test_warm_stale_is_not_ok(monkeypatch):
    # stale data served (fetch failed but cache had an old entry) → ok=False
    monkeypatch.setattr(tle, "get_tle",
                        lambda n: tle.TLEResult(lines=("a", "b", "c"), stale=True, error="HTTP 500"))
    monkeypatch.setattr(tle, "get_starlink_train_tles",
                        lambda: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "stale" in out["results"]["ISS"]
