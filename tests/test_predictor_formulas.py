"""
Tests for inline formulas from predictor.py — moon score, Bortle conversion, crescent exemption.
These are pure math functions: no mocking, no network, no ephemeris.
"""
import pytest

from darkhours.moonlight import KS_CRESCENT_EXEMPTION_PCT, ks_moon_credit


# ---------------------------------------------------------------------------
# Moon score formula (from assemble_night):
#   score = round(10 × ((1 - moonlit_frac) + moonlit_frac × ks_moon_credit(illum)), 1)
# ---------------------------------------------------------------------------

def _moon_score(moonlit_frac: float, illumination_pct: float) -> float:
    """Replicate the moon score formula verbatim from predictor.assemble_night()."""
    return round(10 * ((1 - moonlit_frac) + moonlit_frac * ks_moon_credit(illumination_pct)), 1)


class TestMoonScoreFormula:
    def test_fully_dark_night_is_ten_regardless_of_illumination(self):
        """moonlit_frac=0 → entire night is dark → score = 10."""
        for illum in (0, 25, 50, 100):
            assert _moon_score(0.0, illum) == pytest.approx(10.0, abs=0.15)

    def test_full_moon_all_night_scores_near_zero(self):
        """100% illumination all night → K&S credit ≈ 0 → score near 0."""
        score = _moon_score(1.0, 100)
        assert score < 1.0

    def test_new_moon_all_night_scores_near_ten(self):
        """New moon (0% illum) all night → K&S credit ≈ 1 → score near 10."""
        score = _moon_score(1.0, 0)
        assert score > 9.0

    def test_half_moonlit_formula_matches_ks_credit(self):
        """Verify composition with K&S credit for a known frac/illumination pair."""
        frac  = 0.5
        illum = 50.0
        credit   = ks_moon_credit(illum)
        expected = round(10 * (frac + frac * credit), 1)
        assert _moon_score(frac, illum) == pytest.approx(expected, abs=0.05)

    def test_score_decreases_as_illumination_increases(self):
        for frac in (0.5, 1.0):
            scores = [_moon_score(frac, illum) for illum in (0, 25, 50, 75, 100)]
            assert scores == sorted(scores, reverse=True)

    def test_score_decreases_as_moonlit_fraction_increases(self):
        for illum in (50, 75):
            scores = [_moon_score(frac, illum) for frac in (0.0, 0.25, 0.5, 0.75, 1.0)]
            assert scores == sorted(scores, reverse=True)

    def test_score_always_in_valid_range(self):
        for frac in (0.0, 0.5, 1.0):
            for illum in (0, 25, 50, 75, 100):
                s = _moon_score(frac, illum)
                assert 0.0 <= s <= 10.0


# ---------------------------------------------------------------------------
# Crescent exemption — KS_CRESCENT_EXEMPTION_PCT
# ---------------------------------------------------------------------------

class TestCrescentExemption:
    def test_threshold_is_20_percent(self):
        """Crescent exemption applies when illumination ≤ 20%."""
        assert KS_CRESCENT_EXEMPTION_PCT == pytest.approx(20.0, abs=0.1)

    def test_credit_at_threshold_is_high(self):
        """At the 20% crescent exemption threshold the K&S credit is substantially high."""
        credit = ks_moon_credit(KS_CRESCENT_EXEMPTION_PCT)
        assert credit > 0.70

    def test_quarter_moon_credit_drops_substantially(self):
        """50% illumination credit is well below the crescent-exemption threshold."""
        credit_crescent = ks_moon_credit(KS_CRESCENT_EXEMPTION_PCT)
        credit_quarter  = ks_moon_credit(50)
        assert credit_quarter < credit_crescent


# ---------------------------------------------------------------------------
# Bortle score conversion (from assemble_night):
#   bortle_score = round(max(0.0, (10 - bortle_class) / 9 * 10), 1)
# ---------------------------------------------------------------------------

def _bortle_score(bortle_class: int) -> float:
    """Replicate the Bortle → score conversion verbatim from predictor.assemble_night()."""
    return round(max(0.0, (10 - bortle_class) / 9 * 10), 1)


class TestBortleScoreConversion:
    def test_bortle_1_gives_ten(self):
        assert _bortle_score(1) == pytest.approx(10.0, abs=0.05)

    def test_bortle_9_gives_lowest_nonzero_score(self):
        s = _bortle_score(9)
        assert s > 0.0        # clamped to non-negative
        assert s < 2.0        # much less than mid-range

    def test_bortle_5_is_midrange(self):
        """Bortle 5 → (10-5)/9×10 ≈ 5.6."""
        s = _bortle_score(5)
        assert 5.0 < s < 6.5

    def test_scores_strictly_decrease_with_bortle_class(self):
        """More light pollution → lower quality score."""
        scores = [_bortle_score(b) for b in range(1, 10)]
        assert scores == sorted(scores, reverse=True)

    def test_all_scores_in_valid_range(self):
        for b in range(1, 10):
            s = _bortle_score(b)
            assert 0.0 <= s <= 10.0


# ---------------------------------------------------------------------------
# Provenance re-scoping — _scope_wx_source()
# ---------------------------------------------------------------------------

from datetime import datetime, timezone

from darkhours.predictor import _scope_wx_source
from darkhours.weather import WeatherPoint


def _pt(seeing):
    return WeatherPoint(
        time=datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc),
        cloud_cover_pct=10, seeing_arcsec=seeing, transparency=None,
        humidity_pct=None, wind_speed_ms=None, wind_direction_deg=None,
        lifted_index=None, precip_type=None, temperature_c=None,
        dew_point_c=None, feels_like_c=None, precip_probability_pct=None,
        weather_code=None, aerosol_optical_depth=None, pm2_5=None,
        cloud_cover_low_pct=None, cloud_cover_mid_pct=None,
        cloud_cover_high_pct=None, visibility_m=None, wind_gust_ms=None,
    )


class TestScopeWxSource:
    """The fetch-wide label says "+ 7Timer" if ANY day of the ~16-day series got
    seeing, but 7Timer covers ~3 days — nights beyond its range must not credit it."""

    def test_strips_7timer_when_night_has_no_seeing(self):
        assert _scope_wx_source("Open-Meteo + 7Timer", [_pt(None), _pt(None)]) == "Open-Meteo"

    def test_keeps_7timer_when_night_has_seeing(self):
        assert _scope_wx_source("Open-Meteo + 7Timer", [_pt(None), _pt(1.2)]) == "Open-Meteo + 7Timer"

    def test_plain_primary_untouched(self):
        assert _scope_wx_source("Open-Meteo", [_pt(None)]) == "Open-Meteo"

    def test_7timer_full_primary_untouched(self):
        # Fallback mode: 7Timer IS the primary — its points are 7Timer data
        # regardless of seeing, so the bare label must survive.
        assert _scope_wx_source("7Timer", [_pt(None)]) == "7Timer"

    def test_none_source_passthrough(self):
        assert _scope_wx_source(None, [_pt(None)]) is None

    def test_empty_points_strips_suffix(self):
        assert _scope_wx_source("Open-Meteo + 7Timer", []) == "Open-Meteo"
