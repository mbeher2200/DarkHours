"""
Tests for targets.py pure helper functions — coordinate parsing and window detection.
No ephemeris, no network, no Skyfield calls.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from darkhours.targets import _find_windows, _parse_dec, _parse_ra


# ---------------------------------------------------------------------------
# _parse_ra — RA string → decimal hours
# ---------------------------------------------------------------------------

class TestParseRa:
    def test_full_hms(self):
        # 05h 35m 17s = 5 + 35/60 + 17/3600 ≈ 5.58806 h
        result = _parse_ra("05h 35m 17s")
        assert result == pytest.approx(5 + 35 / 60 + 17 / 3600, rel=1e-5)

    def test_hours_only(self):
        assert _parse_ra("12h") == pytest.approx(12.0, abs=1e-9)

    def test_hours_and_minutes(self):
        assert _parse_ra("0h 30m") == pytest.approx(0.5, abs=1e-9)

    def test_zero_ra(self):
        assert _parse_ra("00h 00m 00s") == pytest.approx(0.0, abs=1e-9)

    def test_galactic_center_approx(self):
        # Galactic center RA ≈ 17h 45m 40s
        result = _parse_ra("17h 45m 40s")
        assert result == pytest.approx(17 + 45 / 60 + 40 / 3600, rel=1e-5)

    def test_ra_24h(self):
        result = _parse_ra("24h 00m 00s")
        assert result == pytest.approx(24.0, abs=1e-9)


# ---------------------------------------------------------------------------
# _parse_dec — Dec string → signed decimal degrees
# ---------------------------------------------------------------------------

class TestParseDec:
    def test_positive_dms(self):
        # +45° 30' 00" = 45.5°
        result = _parse_dec("+45° 30' 00\"")
        assert result == pytest.approx(45.5, rel=1e-5)

    def test_negative_dms(self):
        result = _parse_dec("-29° 00' 00\"")
        assert result == pytest.approx(-29.0, abs=1e-9)

    def test_negative_with_arcmin_and_arcsec(self):
        # -(5 + 23/60 + 28/3600)
        result = _parse_dec("-05° 23' 28\"")
        assert result == pytest.approx(-(5 + 23 / 60 + 28 / 3600), rel=1e-5)

    def test_zero_dec(self):
        result = _parse_dec("+00° 00' 00\"")
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_near_south_pole(self):
        result = _parse_dec("-89° 00' 00\"")
        assert result == pytest.approx(-89.0, abs=1e-9)

    def test_sign_symmetry(self):
        """Positive and negative versions of the same angle are equal in magnitude."""
        pos = _parse_dec("+20° 30' 00\"")
        neg = _parse_dec("-20° 30' 00\"")
        assert pos > 0
        assert neg < 0
        assert pos == pytest.approx(-neg, abs=1e-9)

    def test_degrees_only(self):
        result = _parse_dec("-45 0 0")
        assert result == pytest.approx(-45.0, abs=1e-9)


# ---------------------------------------------------------------------------
# _find_windows — contiguous above-threshold segment detection
# ---------------------------------------------------------------------------

_MIN_ELEV = 10.0


def _dts(n: int) -> list:
    """Generate n datetimes at 10-minute intervals starting at midnight UTC."""
    base = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
    return [base + timedelta(minutes=10 * i) for i in range(n)]


class TestFindWindows:
    def test_all_below_threshold_returns_empty(self):
        alts = np.array([5.0, 5.0, 5.0, 5.0])
        result = _find_windows(alts, np.zeros(4), _dts(4), _MIN_ELEV)
        assert result == []

    def test_all_above_threshold_is_single_window(self):
        alts = np.array([15.0, 20.0, 25.0, 20.0, 15.0])
        result = _find_windows(alts, np.zeros(5), _dts(5), _MIN_ELEV)
        assert len(result) == 1

    def test_single_window_peak_is_maximum_altitude(self):
        alts = np.array([12.0, 30.0, 50.0, 35.0, 20.0])
        result = _find_windows(alts, np.zeros(5), _dts(5), _MIN_ELEV)
        win, _ = result[0]
        assert win.peak_alt_deg == pytest.approx(50.0)

    def test_dip_below_threshold_creates_two_windows(self):
        alts = np.array([15.0, 20.0, 5.0, 20.0, 15.0])  # dips at index 2
        result = _find_windows(alts, np.zeros(5), _dts(5), _MIN_ELEV)
        assert len(result) == 2

    def test_window_includes_correct_indices(self):
        alts = np.array([5.0, 15.0, 20.0, 15.0, 5.0])
        dts  = _dts(5)
        result = _find_windows(alts, np.zeros(5), dts, _MIN_ELEV)
        assert len(result) == 1
        _, indices = result[0]
        assert indices == [1, 2, 3]

    def test_window_start_and_end_times(self):
        alts = np.array([5.0, 12.0, 18.0, 12.0, 5.0])
        dts  = _dts(5)
        result = _find_windows(alts, np.zeros(5), dts, _MIN_ELEV)
        win, _ = result[0]
        assert win.start == dts[1]
        assert win.end   == dts[3]

    def test_window_still_open_at_last_sample(self):
        """A window not closed by the end of the sample list is included."""
        alts = np.array([5.0, 15.0, 20.0, 25.0])
        dts  = _dts(4)
        result = _find_windows(alts, np.zeros(4), dts, _MIN_ELEV)
        assert len(result) == 1
        _, indices = result[0]
        assert indices[-1] == 3   # extends to last sample

    def test_exactly_at_threshold_counts_as_above(self):
        """alt == min_elev (not strictly greater) is treated as visible."""
        alts = np.array([5.0, 10.0, 5.0])
        dts  = _dts(3)
        result = _find_windows(alts, np.zeros(3), dts, _MIN_ELEV)
        assert len(result) == 1

    def test_single_above_threshold_point_is_a_window(self):
        alts = np.array([5.0, 15.0, 5.0])
        dts  = _dts(3)
        result = _find_windows(alts, np.zeros(3), dts, _MIN_ELEV)
        assert len(result) == 1
        _, indices = result[0]
        assert indices == [1]

    def test_azimuth_stored_at_peak(self):
        alts = np.array([5.0, 20.0, 30.0, 20.0, 5.0])
        azs  = np.array([0.0, 90.0, 180.0, 270.0, 0.0])
        dts  = _dts(5)
        result = _find_windows(alts, azs, dts, _MIN_ELEV)
        win, _ = result[0]
        assert win.peak_az_deg == pytest.approx(180.0)  # peak is at index 2, az=180


# ---------------------------------------------------------------------------
# VisibleTarget RA/Dec catalog passthrough (sky-dome renderer wire format)
# ---------------------------------------------------------------------------

class TestRaDecPassthrough:
    def test_new_fields_default_to_none(self):
        """Defaulted fields keep every existing constructor call-site valid."""
        from darkhours.targets import VisibleTarget
        vt = VisibleTarget(name="x", type="galaxy", windows=[], note=None)
        assert vt.ra_deg is None
        assert vt.dec_deg is None
        assert vt.magnitude is None
        assert vt.galactic_l is None
        assert vt.galactic_b is None

    def test_catalog_entry_decimal_conversion(self):
        """Orion Nebula catalog strings → the decimal degrees sent on the wire."""
        ra_deg  = _parse_ra("05h 35m 17s") * 15.0
        dec_deg = _parse_dec("-05° 23' 28\"")
        assert ra_deg  == pytest.approx(83.8208, abs=0.005)
        assert dec_deg == pytest.approx(-5.3911, abs=0.005)
