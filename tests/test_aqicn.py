"""
Tests for aqicn.py — WAQI feed parsing (PM2.5/PM10 pollutant preference, hazy
threshold, distant-station rejection), cache state machine, stale fallback,
and provider-health accounting. All hermetic: no network, no real WAQI calls
(cache and HTTP are mocked).
"""
import io
import json
import urllib.error
from unittest import mock

import pytest

from darkhours import aqicn as q

# Query point used throughout: Minneapolis, MN.
_QLAT, _QLON = 44.98, -93.27
# A station essentially at the query point (~0 km away).
_LOCAL_GEO = [44.98, -93.27]
# A station in Mayotte — genuinely ~14,700 km from Minneapolis, the real
# "nearest station" WAQI returned for an East-African query during manual
# testing. Good stand-in for "absurdly far nearest station."
_FAR_GEO = [-12.76, 45.23]


def _feed(pm25=None, pm10=None, status="ok", city="Test Station", iso="2026-07-17T02:00:00+00:00",
          geo=_LOCAL_GEO):
    iaqi = {}
    if pm25 is not None:
        iaqi["pm25"] = {"v": pm25}
    if pm10 is not None:
        iaqi["pm10"] = {"v": pm10}
    data = {
        "aqi": pm25 if pm25 is not None else (pm10 or 0),
        "iaqi": iaqi,
        "city": {"name": city},
        "time": {"iso": iso},
    }
    if geo is not None:
        data["city"]["geo"] = geo
    return json.dumps({"status": status, "data": data})


def _mock_cache(get_val=None, stale_val=None):
    c = mock.MagicMock()
    c.get.return_value = get_val
    c.get_stale.return_value = stale_val
    return c


def _http_response(text: str):
    """Context-manager response like urllib's, yielding *text* bytes."""
    resp = mock.MagicMock()
    resp.read.return_value = text.encode("utf-8")
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *exc: False
    return resp


# ---------------------------------------------------------------------------
# _haversine_km — pure geometry
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self):
        assert q._haversine_km(44.98, -93.27, 44.98, -93.27) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_minneapolis_to_mayotte(self):
        # Roughly 14,000-15,000 km — sanity check the formula isn't wildly off.
        d = q._haversine_km(_QLAT, _QLON, _FAR_GEO[0], _FAR_GEO[1])
        assert 13000 < d < 16000

    def test_short_local_distance(self):
        # ~0.5 degrees latitude apart at this latitude is roughly 55 km.
        d = q._haversine_km(44.98, -93.27, 45.48, -93.27)
        assert 50 < d < 60


# ---------------------------------------------------------------------------
# _parse — pollutant preference, hazy threshold, distance rejection
# ---------------------------------------------------------------------------

class TestParse:
    def test_pm25_preferred_when_present(self):
        result = q._parse(_feed(pm25=76, pm10=30), _QLAT, _QLON)
        assert result["pollutant"] == "pm25"
        assert result["pm_value"] == 76

    def test_falls_back_to_pm10_when_pm25_absent(self):
        result = q._parse(_feed(pm10=120), _QLAT, _QLON)
        assert result["pollutant"] == "pm10"
        assert result["pm_value"] == 120

    def test_none_when_neither_pollutant_present(self):
        assert q._parse(_feed(), _QLAT, _QLON) is None

    def test_hazy_boundary_at_100(self):
        assert q._parse(_feed(pm25=100), _QLAT, _QLON)["hazy"] is False
        assert q._parse(_feed(pm25=101), _QLAT, _QLON)["hazy"] is True

    def test_station_and_observed_at_carried_through(self):
        result = q._parse(_feed(pm25=50, city="Minneapolis", iso="2026-07-17T02:00:00+00:00"), _QLAT, _QLON)
        assert result["station"] == "Minneapolis"
        assert result["observed_at"] == "2026-07-17T02:00:00+00:00"

    def test_missing_time_degrades_to_none_observed_at(self):
        payload = json.loads(_feed(pm25=50))
        del payload["data"]["time"]
        result = q._parse(json.dumps(payload), _QLAT, _QLON)
        assert result["observed_at"] is None

    def test_non_ok_status_raises(self):
        with pytest.raises(RuntimeError):
            q._parse(_feed(pm25=50, status="error"), _QLAT, _QLON)

    def test_bad_json_raises(self):
        with pytest.raises(RuntimeError):
            q._parse("not json", _QLAT, _QLON)

    def test_far_station_rejected(self):
        # Mirrors the real Kampala/Nairobi -> Mayotte case found in manual testing.
        assert q._parse(_feed(pm25=200, geo=_FAR_GEO), _QLAT, _QLON) is None

    def test_station_within_cutoff_accepted(self):
        # ~55 km away, under the 100 km cutoff.
        nearby = [45.48, -93.27]
        result = q._parse(_feed(pm25=50, geo=nearby), _QLAT, _QLON)
        assert result is not None and result["pm_value"] == 50

    def test_missing_geo_treated_as_no_data(self):
        assert q._parse(_feed(pm25=200, geo=None), _QLAT, _QLON) is None

    def test_malformed_geo_treated_as_no_data(self):
        payload = json.loads(_feed(pm25=200))
        payload["data"]["city"]["geo"] = ["not", "numeric"]
        assert q._parse(json.dumps(payload), _QLAT, _QLON) is None


# ---------------------------------------------------------------------------
# current_haze — fetch state machine (cache -> fetch -> stale fallback -> None)
# ---------------------------------------------------------------------------

class TestCurrentHaze:
    def test_fresh_cache_hit_no_fetch(self):
        cached = {"pm_value": 76, "pollutant": "pm25", "hazy": True,
                  "station": "Minneapolis", "observed_at": "2026-07-17T02:00:00+00:00"}
        mc = _mock_cache(get_val=cached)
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url") as fetch:
            result = q.current_haze(_QLAT, _QLON)
        assert result == {**cached, "stale": False}
        fetch.assert_not_called()

    def test_miss_fetches_and_caches(self):
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url", return_value=_feed(pm25=76)):
            result = q.current_haze(_QLAT, _QLON)
        assert result["pm_value"] == 76 and result["stale"] is False
        mc.set.assert_called_once()
        assert mc.set.call_args.kwargs["ttl_seconds"] == q.AQICN_TTL

    def test_fetch_failure_falls_back_stale(self):
        stale = {"pm_value": 40, "pollutant": "pm25", "hazy": False,
                  "station": None, "observed_at": None}
        mc = _mock_cache(stale_val=stale)
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url", side_effect=RuntimeError("down")):
            result = q.current_haze(_QLAT, _QLON)
        assert result == {**stale, "stale": True}

    def test_fetch_failure_no_stale_yields_none(self):
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url", side_effect=RuntimeError("down")):
            result = q.current_haze(_QLAT, _QLON)
        assert result is None

    def test_no_pm_data_at_station_yields_none_and_does_not_cache(self):
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url", return_value=_feed()):
            result = q.current_haze(_QLAT, _QLON)
        assert result is None
        mc.set.assert_not_called()

    def test_far_station_yields_none_and_does_not_cache(self):
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q, "_fetch_url", return_value=_feed(pm25=200, geo=_FAR_GEO)):
            result = q.current_haze(_QLAT, _QLON)
        assert result is None
        mc.set.assert_not_called()

    def test_no_token_returns_none_without_touching_cache_or_health(self):
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", ""), \
             mock.patch.object(q, "_ph") as ph:
            result = q.current_haze(_QLAT, _QLON)
        assert result is None
        mc.get.assert_not_called()
        ph.record.assert_not_called()


# ---------------------------------------------------------------------------
# provider_health accounting
# ---------------------------------------------------------------------------

class TestProviderHealth:
    def test_ok_recorded_on_successful_parse(self):
        with mock.patch.object(q, "_ph") as ph:
            q._parse(_feed(pm25=50), _QLAT, _QLON)
        ph.record.assert_called_once_with("waqi", "ok")

    def test_ok_recorded_even_when_station_too_far(self):
        # The API call itself succeeded — only the reading is discarded.
        with mock.patch.object(q, "_ph") as ph:
            q._parse(_feed(pm25=200, geo=_FAR_GEO), _QLAT, _QLON)
        ph.record.assert_called_once_with("waqi", "ok")

    def test_429_records_degraded(self):
        err = urllib.error.HTTPError(q.WAQI_URL, 429, "rate limited", {}, io.BytesIO())
        with mock.patch.object(q._http, "urlopen", side_effect=err), \
             mock.patch.object(q, "_ph") as ph, \
             mock.patch.object(q, "_TOKEN", "tok"), \
             pytest.raises(RuntimeError):
            q._fetch_url(_QLAT, _QLON)
        ph.record.assert_called_once_with("waqi", "degraded", "HTTP 429")

    def test_unreachable_records_error(self):
        err = urllib.error.URLError("dns failure")
        with mock.patch.object(q._http, "urlopen", side_effect=err), \
             mock.patch.object(q, "_ph") as ph, \
             mock.patch.object(q, "_TOKEN", "tok"), \
             pytest.raises(RuntimeError):
            q._fetch_url(_QLAT, _QLON)
        assert ph.record.call_args.args[:2] == ("waqi", "error")

    def test_non_ok_status_records_error(self):
        with mock.patch.object(q, "_ph") as ph, \
             pytest.raises(RuntimeError):
            q._parse(_feed(pm25=50, status="error"), _QLAT, _QLON)
        ph.record.assert_called_once_with("waqi", "error", "status=error")


# ---------------------------------------------------------------------------
# circuit breaker integration
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def _trip(self):
        from darkhours import circuit_breaker as cb
        for _ in range(3):
            cb.on_failure("waqi")
        return cb

    def test_open_breaker_short_circuits_without_http(self):
        self._trip()
        with mock.patch.object(q._http, "urlopen") as urlopen, \
             mock.patch.object(q, "_TOKEN", "tok"), \
             pytest.raises(RuntimeError, match="circuit open"):
            q._fetch_url(_QLAT, _QLON)
        urlopen.assert_not_called()

    def test_current_haze_never_raises_when_open(self):
        """The 'never a hard dependency' contract holds for a skipped call."""
        self._trip()
        mc = _mock_cache()
        with mock.patch.object(q, "_cache", mc), \
             mock.patch.object(q, "_TOKEN", "tok"), \
             mock.patch.object(q._http, "urlopen") as urlopen:
            result = q.current_haze(_QLAT, _QLON)
        assert result is None
        urlopen.assert_not_called()

    def test_network_failures_trip_then_skip(self):
        """Three real network failures open the breaker; the fourth call makes
        no HTTP attempt."""
        err = urllib.error.URLError("dns failure")
        with mock.patch.object(q._http, "urlopen", side_effect=err) as urlopen, \
             mock.patch.object(q, "_TOKEN", "tok"):
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    q._fetch_url(_QLAT, _QLON)
            assert urlopen.call_count == 3
            with pytest.raises(RuntimeError, match="circuit open"):
                q._fetch_url(_QLAT, _QLON)
            assert urlopen.call_count == 3
