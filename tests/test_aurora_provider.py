"""
Tests for aurora.py fetchers — SWPC JSON/text parsing, cache state machine,
stale fallback, and provider-health accounting.
All hermetic: no network, no real SWPC calls (cache and HTTP are mocked).
"""
import io
import urllib.error
from unittest import mock

import pytest

from PyNightSkyPredictor import aurora as a

# ---------------------------------------------------------------------------
# Fixtures — realistic but not live
# ---------------------------------------------------------------------------

_KP_JSON = """[
  {"time_tag":"2026-07-10T21:00:00","kp":3.67,"observed":"observed","noaa_scale":null},
  {"time_tag":"2026-07-11T00:00:00","kp":5.00,"observed":"estimated","noaa_scale":"G1"},
  {"time_tag":"2026-07-11T03:00:00","kp":6.33,"observed":"predicted","noaa_scale":"G2"},
  {"time_tag":null,"kp":"garbage","observed":"predicted","noaa_scale":null},
  {"kp":4.0}
]"""

_OUTLOOK_TEXT = """:Product: 27-day Space Weather Outlook Table 27DO.txt
:Issued: 2026 Jul 06 0315 UTC
# Prepared by the US Dept. of Commerce, NOAA, Space Weather Prediction Center
#
#      UTC      Radio Flux   Planetary   Largest
#      Date     10.7 cm      A Index     Kp Index
2026 Jul 06     130          14          4
2026 Jul 07     135           8          3
2026 Jul 15     140          25          6
"""

# Column drift / garbage the strict regex must reject without raising.
_OUTLOOK_GARBLED = """:Product: 27-day Space Weather Outlook Table
2026 Jul 06     130          14
2026 July 07    135           8          3
26 Jul 08       135           8          3
2026 Jul 09     135           8          3   extra
not a data line at all
2026 Jul 10     135           8          3
"""


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
# Parsers
# ---------------------------------------------------------------------------

class TestParseKpJson:
    def test_valid_rows_kept_malformed_skipped(self):
        rows = a._parse_kp_json(_KP_JSON)
        assert len(rows) == 3
        assert rows[0]["kp"] == pytest.approx(3.67)
        assert rows[2] == {"time_tag": "2026-07-11T03:00:00", "kp": 6.33,
                           "observed": "predicted", "noaa_scale": "G2"}

    def test_non_list_payload_yields_empty(self):
        assert a._parse_kp_json('{"unexpected": "shape"}') == []


class TestParse27DayText:
    def test_valid_product(self):
        out = a._parse_27day_text(_OUTLOOK_TEXT)
        assert out == {"2026-07-06": 4.0, "2026-07-07": 3.0, "2026-07-15": 6.0}

    def test_garbled_rows_skipped_gracefully(self):
        # Only the one well-formed row survives; nothing raises.
        out = a._parse_27day_text(_OUTLOOK_GARBLED)
        assert out == {"2026-07-10": 3.0}

    def test_total_garbage_yields_empty(self):
        assert a._parse_27day_text("NOAA changed everything\n1 2 3") == {}


# ---------------------------------------------------------------------------
# fetch state machine (cache → fetch → stale fallback → empty)
# ---------------------------------------------------------------------------

class TestFetchKpForecast:
    def test_fresh_cache_hit_no_fetch(self):
        cached = [{"time_tag": "2026-07-11T00:00:00", "kp": 5.0,
                   "observed": "predicted", "noaa_scale": None}]
        mc = _mock_cache(get_val=cached)
        with mock.patch.object(a, "_cache", mc), \
             mock.patch.object(a, "_fetch_url") as fetch:
            rows, stale = a.fetch_kp_forecast()
        assert rows == cached and stale is False
        fetch.assert_not_called()

    def test_miss_fetches_and_caches(self):
        mc = _mock_cache()
        with mock.patch.object(a, "_cache", mc), \
             mock.patch.object(a, "_fetch_url", return_value=_KP_JSON):
            rows, stale = a.fetch_kp_forecast()
        assert len(rows) == 3 and stale is False
        mc.set.assert_called_once()
        assert mc.set.call_args.kwargs["ttl_seconds"] == a.KP_TTL

    def test_fetch_failure_falls_back_stale(self):
        stale_rows = [{"time_tag": "2026-07-10T00:00:00", "kp": 4.0,
                       "observed": "predicted", "noaa_scale": None}]
        mc = _mock_cache(stale_val=stale_rows)
        with mock.patch.object(a, "_cache", mc), \
             mock.patch.object(a, "_fetch_url", side_effect=RuntimeError("down")):
            rows, stale = a.fetch_kp_forecast()
        assert rows == stale_rows and stale is True

    def test_total_failure_yields_empty(self):
        mc = _mock_cache()
        with mock.patch.object(a, "_cache", mc), \
             mock.patch.object(a, "_fetch_url", side_effect=RuntimeError("down")):
            rows, stale = a.fetch_kp_forecast()
        assert rows == [] and stale is False

    def test_outlook_total_failure_yields_empty_dict(self):
        mc = _mock_cache()
        with mock.patch.object(a, "_cache", mc), \
             mock.patch.object(a, "_fetch_url", side_effect=RuntimeError("down")):
            out, stale = a.fetch_27day_outlook()
        assert out == {} and stale is False


# ---------------------------------------------------------------------------
# provider_health accounting
# ---------------------------------------------------------------------------

class TestProviderHealth:
    def test_ok_recorded(self):
        with mock.patch.object(a._http, "urlopen", return_value=_http_response(_KP_JSON)), \
             mock.patch.object(a, "_ph") as ph:
            a._fetch_url(a.KP_URL)
        ph.record.assert_called_once_with("swpc", "ok")

    def test_429_records_degraded(self):
        err = urllib.error.HTTPError(a.KP_URL, 429, "rate limited", {}, io.BytesIO())
        with mock.patch.object(a._http, "urlopen", side_effect=err), \
             mock.patch.object(a, "_ph") as ph, \
             pytest.raises(RuntimeError):
            a._fetch_url(a.KP_URL)
        ph.record.assert_called_once_with("swpc", "degraded", "HTTP 429")

    def test_unreachable_records_error(self):
        err = urllib.error.URLError("dns failure")
        with mock.patch.object(a._http, "urlopen", side_effect=err), \
             mock.patch.object(a, "_ph") as ph, \
             pytest.raises(RuntimeError):
            a._fetch_url(a.KP_URL)
        assert ph.record.call_args.args[:2] == ("swpc", "error")
