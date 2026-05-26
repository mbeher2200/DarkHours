"""
Tests for milky_way.py — coordinate math and geometry helpers (pure math, no dependencies).
"""

import math

import pytest

from milky_way import gal_to_radec, mw_max_visible, mw_theoretical_core_max


# ---------------------------------------------------------------------------
# gal_to_radec — IAU galactic coordinate conversion
# ---------------------------------------------------------------------------

class TestGalToRadec:
    # Reference values from the IAU (1958) galactic-pole definition:
    #   Galactic center  (l=0°,  b=0°) → RA ≈ 17.760 h, Dec ≈ -28.936°
    #   Galactic anticenter (l=180°, b=0°) → RA ≈  5.760 h, Dec ≈ +28.936°
    #   North galactic pole (l=0°, b=90°) → RA ≈ 12.817 h, Dec ≈ +27.128°

    def test_galactic_center_ra(self):
        ra, dec = gal_to_radec(0, 0)
        assert ra == pytest.approx(17.760, abs=0.05), f"Galactic center RA {ra:.3f} h"

    def test_galactic_center_dec(self):
        ra, dec = gal_to_radec(0, 0)
        assert dec == pytest.approx(-28.936, abs=0.1), f"Galactic center Dec {dec:.3f}°"

    def test_galactic_anticenter_ra(self):
        ra, dec = gal_to_radec(180, 0)
        assert ra == pytest.approx(5.760, abs=0.05), f"Galactic anticenter RA {ra:.3f} h"

    def test_galactic_anticenter_dec(self):
        ra, dec = gal_to_radec(180, 0)
        assert dec == pytest.approx(28.936, abs=0.1), f"Galactic anticenter Dec {dec:.3f}°"

    def test_galactic_north_pole_dec(self):
        """NGP (b=90°) should be at Dec ≈ +27.13°."""
        _ra, dec = gal_to_radec(0, 90)
        assert dec == pytest.approx(27.13, abs=0.5), f"NGP Dec {dec:.3f}°"

    def test_output_ra_in_range(self):
        """RA should always be in [0, 24)."""
        test_coords = [(l, b) for l in range(0, 360, 36) for b in (-60, 0, 60)]
        for l, b in test_coords:
            ra, _dec = gal_to_radec(l, b)
            assert 0.0 <= ra < 24.0, f"RA {ra} out of [0, 24) for (l={l}, b={b})"

    def test_output_dec_in_range(self):
        """Dec should always be in [-90, 90]."""
        test_coords = [(l, b) for l in range(0, 360, 36) for b in (-90, -45, 0, 45, 90)]
        for l, b in test_coords:
            _ra, dec = gal_to_radec(l, b)
            assert -90.0 <= dec <= 90.0, f"Dec {dec} out of [-90, 90] for (l={l}, b={b})"

    def test_anticenter_is_opposite_of_center_in_dec(self):
        """Anticenter (l=180°) should have the same |Dec| as center but opposite sign."""
        _ra0, dec0 = gal_to_radec(0,   0)
        _ra1, dec1 = gal_to_radec(180, 0)
        assert abs(dec0) == pytest.approx(abs(dec1), abs=0.5)
        assert dec0 * dec1 < 0, "Center and anticenter should have opposite Dec signs"

    def test_b90_and_bm90_opposite_dec(self):
        """North and south galactic poles should be at ±same |Dec|."""
        _ra_n, dec_n = gal_to_radec(0,  90)
        _ra_s, dec_s = gal_to_radec(0, -90)
        assert abs(dec_n) == pytest.approx(abs(dec_s), abs=0.1)
        assert dec_n > 0 and dec_s < 0


# ---------------------------------------------------------------------------
# mw_theoretical_core_max
# ---------------------------------------------------------------------------

class TestMwTheoreticalCoreMax:
    # _GALACTIC_CORE_DEC = -29.0

    def test_core_at_equator(self):
        # 90 - |0 - (-29)| = 61.0°
        result = mw_theoretical_core_max(0)
        assert result == pytest.approx(61.0, abs=0.5)

    def test_core_at_galactic_dec(self):
        # At lat = -29° the core is directly overhead (max 90°)
        result = mw_theoretical_core_max(-29)
        assert result == pytest.approx(90.0, abs=0.5)

    def test_core_at_high_north(self):
        # lat=60: 90 - |60 - (-29)| = 90 - 89 = 1.0°
        result = mw_theoretical_core_max(60)
        assert result == pytest.approx(1.0, abs=0.5)

    def test_core_never_negative(self):
        """Result should be ≥ 0 for all latitudes."""
        for lat in range(-90, 91, 5):
            result = mw_theoretical_core_max(lat)
            assert result >= 0.0, f"Negative core max {result} at lat {lat}"

    def test_symmetric_around_galactic_dec(self):
        """Latitudes equally distant from -29° should give the same result."""
        # -29 ± 20° → lat=-49 and lat=-9
        assert mw_theoretical_core_max(-49) == pytest.approx(mw_theoretical_core_max(-9), abs=0.1)

    @pytest.mark.parametrize("lat", [61, 70, 80, 90])
    def test_high_north_latitude_core_not_visible(self, lat):
        """Northern latitudes > 51° (> 80° from galactic Dec -29°) can't see core above 10°.

        Formula: 90 - |lat - (-29)| = 90 - (lat + 29) < 10 when lat > 51°.
        """
        result = mw_theoretical_core_max(lat)
        assert result < 10.0, f"Core max {result}° at lat {lat}N should be < 10°"


# ---------------------------------------------------------------------------
# mw_max_visible
# ---------------------------------------------------------------------------

class TestMwMaxVisible:
    def test_equator_sees_all_waypoints(self):
        """From lat=0 all 10 waypoints (|Dec| ≤ 80°) are theoretically visible."""
        assert mw_max_visible(0) == 10

    def test_high_north_sees_fewer(self):
        """lat=70 — four waypoints with southern Decs are never above 10°."""
        result = mw_max_visible(70)
        assert result < 8, f"lat=70 should see fewer than 8 waypoints, got {result}"

    def test_high_north_exact_count(self):
        """lat=70 sees exactly 6 of the 10 waypoints."""
        assert mw_max_visible(70) == 6

    def test_count_never_exceeds_10(self):
        """No latitude can see more than 10 waypoints (there are only 10)."""
        for lat in range(-90, 91, 5):
            result = mw_max_visible(lat)
            assert result <= 10, f"mw_max_visible({lat}) returned {result} > 10"

    def test_count_never_negative(self):
        for lat in range(-90, 91, 5):
            assert mw_max_visible(lat) >= 0

    def test_southern_equatorial_symmetric(self):
        """mw_max_visible is symmetric around Dec≈0 midpoint, not lat=0.
        lat=0 and lat=-58 (galactic-midpoint mirror) should both see all 10."""
        # The waypoint Decs are symmetric about 0°, so lat=0 sees all.
        # At lat=-90, only positive-Dec waypoints are seen.
        assert mw_max_visible(-90) < mw_max_visible(0)
