"""
Tests for tle_provider.py — TLE parsing helpers, Starlink filter, and get_tle() state machine.
All hermetic: no network, no ephemeris, no real Celestrak calls.
"""
from datetime import date, timedelta
from unittest import mock

import pytest

from darkhours import tle_provider as tle_mod
from darkhours.tle_provider import (
    _cospar_designator,
    _filter_train_tles,
    _parse_mean_motion,
    get_tle,
)

# ---------------------------------------------------------------------------
# Sample TLE data (realistic but not live — for parsing tests)
# ---------------------------------------------------------------------------

# ISS TLE line 2 — mean motion at fixed columns 52-62 = "15.49162413"
_ISS_L2 = (
    "2 25544  51.6416  55.2282 0002997  69.2059  25.5940 15.49162413448760"
)

# ISS complete 3-line TLE text (raw cache format)
_ISS_L1 = (
    "1 25544U 98067A   24191.72490000  .00022888  00000+0  40424-3 0  9998"
)
_ISS_RAW = "ISS (ZARYA)\n" + _ISS_L1 + "\n" + _ISS_L2


# ---------------------------------------------------------------------------
# _parse_mean_motion — fixed-column extraction
# ---------------------------------------------------------------------------

class TestParseMeanMotion:
    def test_iss_mean_motion(self):
        mm = _parse_mean_motion(_ISS_L2)
        assert mm == pytest.approx(15.49162413, rel=1e-6)

    def test_high_mm_raising_phase(self):
        # Prefix is 52 chars; mean motion = 15.60000000 at cols 52-62
        prefix = "2 00001  51.6000 180.0000 0001000  90.0000 270.0000 "
        l2 = prefix + "15.6000000000001 0"
        mm = _parse_mean_motion(l2)
        assert mm == pytest.approx(15.6, rel=1e-5)

    def test_too_short_returns_none(self):
        assert _parse_mean_motion("2 25544") is None

    def test_empty_returns_none(self):
        assert _parse_mean_motion("") is None

    def test_above_threshold_is_raising_phase(self):
        """Satellite with MM ≥ 15.5 is in raising phase (below operational altitude)."""
        assert _parse_mean_motion(_ISS_L2) < 15.5  # ISS is operational


# ---------------------------------------------------------------------------
# _cospar_designator — International Designator parsing
#
# These previously asserted the day-of-year reading as ground truth ("98067A" →
# day 67), which is what made the Starlink train filter match nothing: NNN is the
# launch's sequence number within its year, not a day of it.
# ---------------------------------------------------------------------------

class TestCosparDesignator:
    def test_iss_is_the_67th_launch_of_1998_not_day_67(self):
        """1998-067A is the ISS. It launched on 20 November 1998; day 67 is 8 March."""
        assert _cospar_designator(_ISS_L1) == "1998-067"

    def test_year_below_57_maps_to_2000s(self):
        l1 = "1 00001U 20001A   24191.50000000  .00000000  00000-0  00000-0 0  9999"
        assert _cospar_designator(l1) == "2020-001"

    def test_year_57_maps_to_1957(self):
        l1 = "1 00002U 57001A   24191.50000000  .00000000  00000-0  00000-0 0  9999"
        assert _cospar_designator(l1) == "1957-001"

    def test_blank_intl_designator_returns_none(self):
        l1 = "1 00001U         24191.50000000  .00000000  00000-0  00000-0 0  9999"
        assert _cospar_designator(l1) is None

    def test_too_short_line_returns_none(self):
        assert _cospar_designator("1 00001U") is None


# ---------------------------------------------------------------------------
# _filter_train_tles — Starlink raising-phase filter
# ---------------------------------------------------------------------------

def _omm_csv(*sats) -> str:
    """Build a GP CSV response (OMM) from (name, designator, mean_motion) triples.

    The real feed is FORMAT=CSV: FORMAT=TLE cannot carry the 6-digit catalog numbers
    Celestrak assigns to anything catalogued since 2026-07-11, so a TLE request
    silently omits every recent launch — which is exactly what made this filter
    report zero trains.
    """
    cols = ["OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY",
            "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY",
            "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID",
            "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT",
            "MEAN_MOTION_DDOT"]
    rows = [",".join(cols)]
    for i, (name, designator, mm) in enumerate(sats):
        rows.append(",".join([
            name, designator, "2026-08-25T12:00:00.000000", f"{mm:.8f}",
            ".0001000", "53.0000", "100.0000", "90.0000", "270.0000",
            "0", "U", str(50000 + i), "999", "100", ".0001000", ".00000000", "0",
        ]))
    return "\n".join(rows)


def _make_train_block() -> str:
    """
    Four synthetic Starlinks:
      RECENT-HIGH   — launch 2026-040, 5 days ago,  MM=15.60 → INCLUDE
      RECENT-LOW    — launch 2026-040, 5 days ago,  MM=15.06 → EXCLUDE (on station)
      OLD-HIGH      — launch 2026-030, 30 days ago, MM=15.70 → EXCLUDE (stale batch)
      UNKNOWN-DATE  — blank designator,             MM=15.55 → EXCLUDE (undatable)

    UNKNOWN-DATE used to be included on the theory that an unknown launch date was
    safest treated as recent. It is the opposite: an undatable satellite at high mean
    motion is far more likely to be an old one on its way down.
    """
    return _omm_csv(
        ("STARLINK-RECENT-HIGH",   "2026-040A", 15.60),
        ("STARLINK-RECENT-LOW",    "2026-040B", 15.06),
        ("STARLINK-OLD-HIGH",      "2026-030A", 15.70),
        ("STARLINK-UNKNOWN-DATE",  "",          15.55),
    )


_TODAY        = date.today()
_LAUNCH_DATES = {
    "2026-040": _TODAY - timedelta(days=5),
    "2026-030": _TODAY - timedelta(days=30),
}


class TestFilterTrainTles:
    def setup_method(self):
        self.block   = _make_train_block()
        self.results = _filter_train_tles(self.block, _LAUNCH_DATES)
        self.names   = {r[0] for r in self.results}

    def test_recent_high_mm_included(self):
        assert "STARLINK-RECENT-HIGH" in self.names

    def test_operational_mean_motion_excluded(self):
        assert "STARLINK-RECENT-LOW" not in self.names

    def test_old_high_mm_excluded(self):
        assert "STARLINK-OLD-HIGH" not in self.names

    def test_unknown_date_excluded(self):
        assert "STARLINK-UNKNOWN-DATE" not in self.names

    def test_each_result_carries_its_launch_date(self):
        for entry in self.results:
            assert len(entry) == 4          # (name, line1, line2, launch_date)
            assert entry[3] == (_TODAY - timedelta(days=5)).isoformat()

    def test_empty_body_raises_rather_than_reporting_no_trains(self):
        """An empty body is a broken fetch, not a quiet night. Returning [] here is
        indistinguishable from a real result and is how a 45-day outage stayed
        invisible — the warmer reports `ok (0 trains)` either way."""
        with pytest.raises(ValueError, match="not OMM CSV"):
            _filter_train_tles("", _LAUNCH_DATES)

    def test_non_csv_body_raises(self):
        """Celestrak answers some queries with a one-line 'No GP data found' under
        HTTP 200. That must reach the caller's error path, not the filter's."""
        with pytest.raises(ValueError, match="not OMM CSV"):
            _filter_train_tles("No GP data found", _LAUNCH_DATES)

    def test_no_launch_dates_matches_nothing(self):
        assert _filter_train_tles(self.block, {}) == []


# ---------------------------------------------------------------------------
# get_tle — four-state acquisition machine
# ---------------------------------------------------------------------------

class TestGetTle:
    def _mock_cache(self, get_val=None, stale_val=None):
        c = mock.MagicMock()
        c.get.return_value = get_val
        c.get_stale.return_value = stale_val
        return c

    def test_fresh_cache_hit_returns_not_stale(self):
        mc = self._mock_cache(get_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc):
            result = get_tle(25544)
        assert result.stale is False
        assert result.error is None
        assert result.lines is not None
        mc.get.assert_called_once()
        # No fetch on cache hit
        mc.set.assert_not_called()

    def test_cache_miss_fetch_succeeds_stores_result(self):
        mc = self._mock_cache(get_val=None, stale_val=None)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod, '_fetch_tle_raw', return_value=_ISS_RAW):
            result = get_tle(25544)
        assert result.stale is False
        assert result.lines is not None
        mc.set.assert_called_once()

    def test_fetch_failure_uses_stale_fallback(self):
        """When Celestrak is unreachable, expired cache entry is served as stale."""
        mc = self._mock_cache(get_val=None, stale_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod, '_fetch_tle_raw',
                               side_effect=RuntimeError("connection timeout")):
            result = get_tle(25544)
        assert result.stale is True
        assert result.error is not None
        assert result.lines is not None

    def test_fetch_failure_no_cache_returns_none_lines(self):
        """No cached data at all → complete failure: lines=None."""
        mc = self._mock_cache(get_val=None, stale_val=None)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod, '_fetch_tle_raw',
                               side_effect=RuntimeError("DNS failure")):
            result = get_tle(25544)
        assert result.lines is None
        assert result.error is not None
        assert result.stale is False

    def test_403_revalidates_and_extends_ttl(self):
        """403 means 'your copy is current' → re-set it with a fresh TTL, serve fresh.

        The entry must never be allowed to expire while Celestrak keeps confirming
        it, which is what previously stranded the cache with nothing to fall back on.
        """
        mc = self._mock_cache(get_val=None, stale_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod, '_fetch_tle_raw',
                               side_effect=tle_mod._NotModified("unchanged")):
            result = get_tle(25544)
        assert result.lines is not None
        assert result.stale is False, "revalidated data is current, not stale"
        assert result.error is None
        mc.set.assert_called_once()
        assert mc.set.call_args.kwargs["ttl_seconds"] == tle_mod.TLE_TTL

    def test_403_with_empty_cache_counts_as_failure(self):
        """403 with nothing cached is unrecoverable by retrying — must not spin.

        Re-asking returns the same 403, so it has to trip the breaker rather than
        being reported as success on every request.
        """
        mc = self._mock_cache(get_val=None, stale_val=None)
        fail = mock.MagicMock()
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._cb, 'on_failure', fail), \
             mock.patch.object(tle_mod, '_fetch_tle_raw',
                               side_effect=tle_mod._NotModified("unchanged")):
            result = get_tle(25544)
        assert result.lines is None
        assert result.error is not None
        fail.assert_called_once_with("celestrak")
        mc.set.assert_not_called()

    def test_request_path_timeout_is_short(self):
        """A hung Celestrak fetch must not hold a user request for 30s.

        Satellites are optional; the rest of the forecast is already computed by
        the time this is awaited. The 30s default previously produced ~30s p99
        spikes whenever a fetch stalled.
        """
        assert tle_mod._FETCH_TIMEOUT <= 10

    def test_warmer_gets_a_longer_timeout_than_the_request_path(self):
        """The background warmer should be the patient one.

        Its success is what stops the request path from ever needing to fetch, so
        it is the wrong place to fail fast.
        """
        assert tle_mod._WARM_FETCH_TIMEOUT > tle_mod._FETCH_TIMEOUT

    def test_fetch_uses_the_supplied_timeout(self):
        """The timeout argument must actually reach urlopen, not just exist."""
        mc = self._mock_cache(get_val=None, stale_val=None)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = \
                _ISS_RAW.encode()
            get_tle(25544, timeout=7)
        assert urlopen.call_args.kwargs["timeout"] == 7

    def test_refresh_tle_fetches_even_on_a_fresh_cache_hit(self):
        """refresh_tle is unconditional — that is the whole reason it exists.

        The warmer used to call a function that returned early on a fresh hit, so it
        was a no-op whenever the cache was healthy and could only repair an entry
        that had already expired. The refresh then landed on whichever user asked
        first. Observed live: three tle| rows written 22.1h ago against a 24h TTL,
        across four warmer runs that touched none of them.
        """
        mc = self._mock_cache(get_val=_ISS_RAW, stale_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod, '_fetch_tle_raw', return_value=_ISS_RAW) as fetch:
            result = tle_mod.refresh_tle(25544)
        fetch.assert_called_once()
        mc.set.assert_called_once()
        assert mc.set.call_args.kwargs["ttl_seconds"] == tle_mod.TLE_TTL
        assert result.stale is False

    def test_cached_tle_never_fetches_and_falls_back_to_stale(self):
        """The request-path reader: cache only, degrading through stale to nothing."""
        mc = self._mock_cache(get_val=None, stale_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen') as urlopen:
            result = tle_mod.cached_tle(25544)
        urlopen.assert_not_called()
        mc.set.assert_not_called()
        assert result.stale is True and result.lines is not None

    def test_ttl_is_wider_than_the_warmer_interval(self):
        """TLE_TTL must stay several refresh cycles wide.

        The warmer revalidates every 6h; if TLE_TTL were also 6h a single missed
        run would delete the only copy. Guards against someone tightening it back.
        """
        assert tle_mod.TLE_TTL >= 4 * 6 * 3600


# ---------------------------------------------------------------------------
# circuit breaker integration
# ---------------------------------------------------------------------------

_GROUP_TEST_LAUNCH_DATES = {"2026-040": date.today() - timedelta(days=3)}


def _with_launch_dates():
    """Stub the SATCAT leg so the group fetch below is what is under test."""
    return mock.patch.object(tle_mod, "_fetch_starlink_launch_dates",
                             return_value=dict(_GROUP_TEST_LAUNCH_DATES))


class TestCircuitBreaker:
    @staticmethod
    def _mock_cache(get_val=None, stale_val=None):
        c = mock.MagicMock()
        c.get.return_value = get_val
        c.get_stale.return_value = stale_val
        return c

    def test_single_failure_trips_then_skips_http(self):
        """Celestrak's (1, 300) override: one real failure opens the breaker;
        the next fetch makes no HTTP attempt and still serves stale cache."""
        import urllib.error
        from darkhours import circuit_breaker as cb

        mc = self._mock_cache(get_val=None, stale_val=_ISS_RAW)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen',
                               side_effect=urllib.error.URLError("dns failure")):
            r1 = get_tle(25544)
        assert r1.stale is True
        assert cb.is_open("celestrak")

        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen') as urlopen:
            r2 = get_tle(25544)
        urlopen.assert_not_called()
        assert r2.stale is True and r2.lines is not None
        assert "circuit open" in r2.error

    def test_starlink_group_skipped_when_open(self):
        from darkhours import circuit_breaker as cb

        cb.on_failure("celestrak")           # threshold 1: open
        mc = self._mock_cache(get_val=None, stale_val=None)
        with mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen') as urlopen:
            tles, stale, error = tle_mod.get_starlink_train_tles()
        urlopen.assert_not_called()
        assert tles == [] and error is None   # silent-skip contract preserved

    def test_starlink_group_403_revalidates_and_extends_ttl(self):
        """403 on the group fetch → re-set the cached copy with a fresh TTL.

        This is the production failure: the group entry expired, DynamoDB deleted
        it, Celestrak answered 403 ("unchanged since your last download"), there
        was nothing to fall back on, and because 403 was recorded as success the
        breaker never opened — so every request re-asked, ~3/min for hours.
        """
        import urllib.error

        # The cached value is the *filtered train list*, not the raw group block —
        # see _STARLINK_TRAINS_CACHE_KEY for why the raw block could never be stored.
        recent = (date.today() - timedelta(days=3)).isoformat()
        cached = [["STARLINK-1", "1 aaa", "2 bbb", recent]]
        mc = self._mock_cache(get_val=None, stale_val=cached)
        err = urllib.error.HTTPError(url="x", code=403, msg="Forbidden",
                                     hdrs=None, fp=None)
        with _with_launch_dates(), mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen', side_effect=err):
            tles, stale, error = tle_mod.get_starlink_train_tles()
        assert error is None
        assert stale is False, "revalidated data is current, not stale"
        assert tles == [("STARLINK-1", "1 aaa", "2 bbb", recent)]
        mc.set.assert_called_once()
        assert mc.set.call_args.kwargs["ttl_seconds"] == tle_mod.TLE_TTL

    def test_starlink_group_403_with_empty_cache_opens_breaker(self):
        """403 with nothing cached must trip the breaker, not report success.

        Retrying cannot help — Celestrak returns the same 403 until its data
        changes — so this is the guard against re-entering the spin loop.
        """
        import urllib.error
        from darkhours import circuit_breaker as cb

        mc = self._mock_cache(get_val=None, stale_val=None)
        err = urllib.error.HTTPError(url="x", code=403, msg="Forbidden",
                                     hdrs=None, fp=None)
        with _with_launch_dates(), mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen', side_effect=err):
            tles, stale, error = tle_mod.get_starlink_train_tles()
        assert tles == []
        assert error is not None and "no cached copy" in error
        assert cb.is_open("celestrak"), "breaker must open so we stop re-asking"
        mc.set.assert_not_called()

    def test_starlink_group_fetch_failure_no_cache_surfaces_error(self):
        """Breaker closed, fetch fails, no stale fallback → error must be
        threaded through rather than dropped (get_tle()'s equivalent complete-
        failure path already does this; the Starlink-group path used to
        hardcode error=None here regardless of the real failure reason)."""
        import urllib.error

        mc = self._mock_cache(get_val=None, stale_val=None)
        with _with_launch_dates(), mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen',
                               side_effect=urllib.error.URLError("dns failure")):
            tles, stale, error = tle_mod.get_starlink_train_tles()
        assert tles == []
        assert stale is False
        assert error is not None
        assert "unreachable" in error.lower()

    def test_no_launch_dates_skips_the_group_fetch_entirely(self):
        """SATCAT unreachable → no launch dates → the group cannot be filtered into
        trains, so downloading 1.8 MB of it buys nothing. Surface the reason instead."""
        mc = self._mock_cache(get_val=None, stale_val=None)
        with mock.patch.object(tle_mod, "_fetch_starlink_launch_dates",
                               return_value={}), \
             mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen') as urlopen:
            tles, stale, error = tle_mod.get_starlink_train_tles()
        urlopen.assert_not_called()
        assert tles == []
        assert error is not None and "launch dates" in error

    def test_starlink_group_read_timeout_does_not_raise(self):
        """A read-phase timeout (e.g. ssl.SSLSocket.read() mid-response) raises a raw
        TimeoutError, not urllib.error.URLError — urllib only wraps connect-phase
        failures. Confirmed live in production: this previously propagated uncaught
        out of get_starlink_train_tles() and crashed the entire /night response."""
        mc = self._mock_cache(get_val=None, stale_val=None)
        with _with_launch_dates(), mock.patch.object(tle_mod, '_cache', mc), \
             mock.patch.object(tle_mod._http, 'urlopen',
                               side_effect=TimeoutError("The read operation timed out")):
            tles, stale, error = tle_mod.get_starlink_train_tles()
        assert tles == []
        assert stale is False
        assert error is not None and "timed out" in error.lower()
