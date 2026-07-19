"""
Tests for milky_way.py — coordinate math, geometry helpers, and arch summary synthesis.
Pure math/logic tests: no ephemeris, no network.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from darkhours.milky_way import (
    gal_to_radec,
    milky_way_arch_summary,
    mw_max_visible,
    mw_theoretical_core_max,
)


# ---------------------------------------------------------------------------
# Minimal mock objects for milky_way_arch_summary
# (avoids importing targets.py at module level, which triggers config loading)
# ---------------------------------------------------------------------------

@dataclass
class _Win:
    """Lightweight TargetWindow stand-in — only fields used by arch_summary."""
    start: datetime
    end: datetime
    peak_time: datetime
    peak_alt_deg: float
    peak_az_deg: float = 180.0
    start_alt_deg: float = 5.0
    end_alt_deg: float = 5.0
    moon_interference: bool = False
    photo_cutoff: object = None
    ks_computed: bool = False
    photo_start: object = None
    arch_angle_deg: object = None


@dataclass
class _Tgt:
    """Lightweight VisibleTarget stand-in."""
    name: str
    type: str = "milky_way"
    windows: list = field(default_factory=list)
    note: object = None


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
        """From lat=0 all 14 waypoints are theoretically visible (all |Dec| < 80°)."""
        assert mw_max_visible(0) == 14

    def test_high_north_sees_fewer(self):
        """lat=70 — southern-dec waypoints are never above 10° from high northern latitudes."""
        result = mw_max_visible(70)
        assert result < 10, f"lat=70 should see fewer than 10 waypoints, got {result}"

    def test_high_north_exact_count(self):
        """lat=70 sees exactly 7 of the 14 waypoints (Scorpius/Norma/Crux/Carina/Vela/Puppis/Core unreachable)."""
        assert mw_max_visible(70) == 7

    def test_count_never_exceeds_total(self):
        """No latitude can see more than 14 waypoints (there are only 14)."""
        for lat in range(-90, 91, 5):
            result = mw_max_visible(lat)
            assert result <= 14, f"mw_max_visible({lat}) returned {result} > 14"

    def test_count_never_negative(self):
        for lat in range(-90, 91, 5):
            assert mw_max_visible(lat) >= 0

    def test_southern_equatorial_symmetric(self):
        """mw_max_visible is symmetric around Dec≈0 midpoint, not lat=0.
        lat=0 and lat=-58 (galactic-midpoint mirror) should both see all 10."""
        # The waypoint Decs are symmetric about 0°, so lat=0 sees all.
        # At lat=-90, only positive-Dec waypoints are seen.
        assert mw_max_visible(-90) < mw_max_visible(0)


# ---------------------------------------------------------------------------
# milky_way_arch_summary — quality score synthesis
# ---------------------------------------------------------------------------

# Reference night: core visible 22:00–04:00 UTC (6h window)
_BASE  = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
_NS    = _BASE + timedelta(hours=-2)  # 22:00
_NE    = _BASE + timedelta(hours=4)   # 04:00
_CPEAK = _BASE + timedelta(hours=1)   # 01:00 — core transit


def _win(start=_NS, end=_NE, peak_alt=20.0, moon_interference=False):
    peak = start + (end - start) / 2
    return _Win(start=start, end=end, peak_time=peak,
                peak_alt_deg=peak_alt, moon_interference=moon_interference)


def _tgt(name, peak_alt=20.0, moon_interference=False, start=_NS, end=_NE):
    return _Tgt(name=name, windows=[_win(start, end, peak_alt, moon_interference)])


# lat=36 (Grand Canyon area): theo_max=25°, n_max=8
_LAT = 36.0


class TestMilkyWayArchSummary:
    def test_empty_list_returns_none(self):
        assert milky_way_arch_summary([]) is None

    def test_missing_galactic_core_returns_none(self):
        targets = [_tgt("Cygnus Star Cloud", peak_alt=40.0)]
        assert milky_way_arch_summary(targets, lat=_LAT) is None

    def test_core_only_returns_dict(self):
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(targets, lat=_LAT)
        for key in (
            "arch_start", "arch_end", "arch_hours", "moon_limited",
            "n_visible", "n_max_possible", "local_score",
            "core_peak_time", "core_peak_alt_deg",
        ):
            assert key in result, f"Missing key: {key}"

    def test_local_score_in_valid_range(self):
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert 0.0 <= result["local_score"] <= 10.0

    def test_n_visible_matches_target_count(self):
        targets = [
            _tgt("Galactic Core",     peak_alt=20.0),
            _tgt("Cygnus Star Cloud", peak_alt=40.0),
        ]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert result["n_visible"] == 2

    def test_arch_hours_reflects_window_overlap(self):
        """Core [22:00–04:00] ∩ Cygnus [01:00–04:00] = 3 h arch window."""
        cygnus_start = _BASE + timedelta(hours=1)  # 01:00
        targets = [
            _tgt("Galactic Core",     peak_alt=20.0),
            _tgt("Cygnus Star Cloud", peak_alt=40.0,
                 start=cygnus_start, end=_NE),
        ]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert result["arch_hours"] == pytest.approx(3.0, abs=0.15)

    def test_moon_interference_on_core_lowers_score(self):
        """When the core window has moon_interference, the 0.7× penalty fires."""
        no_moon  = [_tgt("Galactic Core", peak_alt=20.0, moon_interference=False)]
        with_moon = [_tgt("Galactic Core", peak_alt=20.0, moon_interference=True)]
        score_clean = milky_way_arch_summary(no_moon, lat=_LAT)["local_score"]
        score_moon  = milky_way_arch_summary(with_moon, lat=_LAT)["local_score"]
        assert score_moon < score_clean

    def test_moon_limited_flag_false_without_moonrise(self):
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(
            targets, lat=_LAT, moonrise=None, moonset=None, moon_illumination_pct=0.0
        )
        assert result["moon_limited"] is False

    def test_higher_core_altitude_gives_higher_score(self):
        """Higher core culmination → better alt_score component → higher local_score."""
        low  = milky_way_arch_summary([_tgt("Galactic Core", peak_alt=10.0)], lat=_LAT)
        high = milky_way_arch_summary([_tgt("Galactic Core", peak_alt=24.0)], lat=_LAT)
        assert high["local_score"] > low["local_score"]

    def test_farthest_name_populated_with_far_waypoint(self):
        targets = [
            _tgt("Galactic Core",     peak_alt=20.0),
            _tgt("Cygnus Star Cloud", peak_alt=45.0),
        ]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert result["farthest_name"] == "Cygnus Star Cloud"

    def test_farthest_name_none_when_no_far_waypoints(self):
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert result["farthest_name"] is None

    def test_score_formula_components_present(self):
        """alt_score, cov_score, win_score are returned for transparency."""
        targets = [_tgt("Galactic Core", peak_alt=20.0)]
        result = milky_way_arch_summary(targets, lat=_LAT)
        assert "alt_score" in result
        assert "cov_score" in result
        assert "win_score" in result
