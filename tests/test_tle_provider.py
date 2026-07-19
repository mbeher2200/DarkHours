"""
Tests for tle_provider.py — TLE parsing helpers, Starlink filter, and get_tle() state machine.
All hermetic: no network, no ephemeris, no real Celestrak calls.
"""
from datetime import date, timedelta
from unittest import mock

import pytest

from darkhours import tle_provider as tle_mod
from darkhours.tle_provider import (
    _filter_train_tles,
    _parse_launch_date,
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
# _parse_launch_date — COSPAR International Designator parsing
# ---------------------------------------------------------------------------

class TestParseLaunchDate:
    def test_iss_launch_1998(self):
        """ISS intl designator '98067A' → year=1998, DOY=67 (March 8)."""
        d = _parse_launch_date(_ISS_L1)
        assert d is not None
        assert d.year == 1998
        assert d.timetuple().tm_yday == 67

    def test_year_below_57_maps_to_2000s(self):
        # year_2d=20 < 57 → 2000+20=2020, DOY=001
        l1 = "1 00001U 20001A   24191.50000000  .00000000  00000-0  00000-0 0  9999"
        d = _parse_launch_date(l1)
        assert d is not None
        assert d.year == 2020
        assert d.timetuple().tm_yday == 1

    def test_year_57_maps_to_1957(self):
        # year_2d=57 ≥ 57 → 1900+57=1957 (Sputnik era)
        l1 = "1 00002U 57001A   24191.50000000  .00000000  00000-0  00000-0 0  9999"
        d = _parse_launch_date(l1)
        assert d is not None
        assert d.year == 1957
        assert d.timetuple().tm_yday == 1

    def test_blank_intl_designator_returns_none(self):
        # All spaces in cols 9-16 → stripped to "" → len < 5 → None
        l1 = "1 00001U         24191.50000000  .00000000  00000-0  00000-0 0  9999"
        assert _parse_launch_date(l1) is None

    def test_too_short_line_returns_none(self):
        assert _parse_launch_date("1 00001U") is None


# ---------------------------------------------------------------------------
# _filter_train_tles — Starlink raising-phase filter
# ---------------------------------------------------------------------------

def _make_train_block() -> str:
    """
    Build a multi-TLE block with four synthetic Starlink satellites:
      RECENT-HIGH   — launched 5 days ago, MM=15.60 → INCLUDE
      RECENT-LOW    — launched 5 days ago, MM=15.30 → EXCLUDE (operational altitude)
      OLD-HIGH      — launched 30 days ago, MM=15.70 → EXCLUDE (stale batch)
      UNKNOWN-DATE  — empty intl designator, MM=15.55 → INCLUDE (no date = conservative)
    """
    today  = date.today()
    recent = today - timedelta(days=5)
    old    = today - timedelta(days=30)

    def _doy(d: date) -> int:
        return (d - date(d.year, 1, 1)).days + 1

    def _l1(n: int, d: date | None, piece: str = "A") -> str:
        if d is None:
            intl = "        "   # blank = unknown launch
        else:
            yy  = d.year % 100
            doy = _doy(d)
            intl = f"{yy:02d}{doy:03d}{piece}  "[:8]
        return f"1 {n:05d}U {intl}24001.50000000  .00000000  00000-0  00000-0 0  9999"

    def _l2(n: int, mm: float) -> str:
        prefix = f"2 {n:05d}  51.6000 180.0000 0001000  90.0000 270.0000 "
        return prefix + f"{mm:.8f}00001 0"

    return "\n".join([
        "STARLINK-RECENT-HIGH",
        _l1(10001, recent),
        _l2(10001, 15.60),

        "STARLINK-RECENT-LOW",
        _l1(10002, recent),
        _l2(10002, 15.30),

        "STARLINK-OLD-HIGH",
        _l1(10003, old),
        _l2(10003, 15.70),

        "STARLINK-UNKNOWN-DATE",
        _l1(10004, None),
        _l2(10004, 15.55),
    ])


class TestFilterTrainTles:
    def setup_method(self):
        self.block = _make_train_block()
        self.results = _filter_train_tles(self.block)
        self.names = {r[0] for r in self.results}

    def test_recent_high_mm_included(self):
        assert "STARLINK-RECENT-HIGH" in self.names

    def test_recent_low_mm_excluded(self):
        assert "STARLINK-RECENT-LOW" not in self.names

    def test_old_high_mm_excluded(self):
        assert "STARLINK-OLD-HIGH" not in self.names

    def test_unknown_date_high_mm_included(self):
        assert "STARLINK-UNKNOWN-DATE" in self.names

    def test_each_result_is_three_tuple(self):
        for entry in self.results:
            assert len(entry) == 3   # (name, line1, line2)

    def test_empty_block_returns_empty(self):
        assert _filter_train_tles("") == []

    def test_malformed_block_skipped_gracefully(self):
        block = "JUNK LINE\nNOT A TLE\nALSO JUNK"
        assert _filter_train_tles(block) == []


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
