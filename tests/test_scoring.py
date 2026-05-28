"""
Tests for scoring.py — night quality score and weather weighting (pure math, no dependencies).
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pytest

from PyNightSkyPredictor.scoring import rate_night, weighted_weather_score


# ---------------------------------------------------------------------------
# Minimal WeatherPoint stand-in for scoring tests
# ---------------------------------------------------------------------------

@dataclass
class _MockPoint:
    """Minimal stand-in for weather.WeatherPoint — only fields scoring uses."""
    time: datetime
    cloud_cover_pct: Optional[int] = 0


def _dt(hour: int, day: int = 14) -> datetime:
    """Return a UTC datetime on 2026-06-{day} at {hour}:00."""
    return datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc)


# Reference night window: 02:00–08:00 UTC
_NIGHT_START = _dt(2)
_NIGHT_END   = _dt(8)


# ---------------------------------------------------------------------------
# rate_night
# ---------------------------------------------------------------------------

class TestRateNight:
    def test_all_tens_gives_ten(self):
        result = rate_night(10, 10, 10, 10)
        assert result["score"] == pytest.approx(10.0, abs=0.05)

    def test_zero_moon_zeros_output(self):
        """Moon score = 0 → geometric mean collapses to 0."""
        result = rate_night(0, 10, 10, 10)
        assert result["score"] == pytest.approx(0.0, abs=0.05)

    def test_zero_weather_zeros_output(self):
        result = rate_night(10, 10, 0, 10)
        assert result["score"] == pytest.approx(0.0, abs=0.05)

    def test_missing_weather_redistributes_weights(self):
        result = rate_night(8, 8, None, 8)
        assert result["score"] is not None
        assert "weather" not in result["components"]
        assert "moon" in result["components"]
        assert "dark" in result["components"]

    def test_missing_bortle_redistributes_weights(self):
        result = rate_night(8, 8, 8, None)
        assert result["score"] is not None
        assert "bortle" not in result["components"]

    def test_all_optional_missing(self):
        """Both weather and bortle absent → valid score from moon + dark alone."""
        result = rate_night(8, 8, None, None)
        assert result["score"] is not None
        assert set(result["components"].keys()) == {"moon", "dark"}

    def test_all_none_returns_none(self):
        result = rate_night(None, None, None, None)
        assert result["score"] is None
        assert result["components"] == {}

    def test_score_clamped_within_0_10(self):
        """Any valid inputs should produce a score in [0, 10]."""
        for s in [0, 5, 10]:
            result = rate_night(s, s, s, s)
            score = result["score"]
            assert score is not None
            assert 0.0 <= score <= 10.0

    def test_components_dict_present(self):
        result = rate_night(7, 6, 8, 5)
        assert "moon" in result["components"]
        assert "dark" in result["components"]
        assert "weather" in result["components"]
        assert "bortle" in result["components"]

    def test_higher_inputs_produce_higher_score(self):
        """Monotonicity: better inputs → better score."""
        low  = rate_night(3, 3, 3, 3)["score"]
        mid  = rate_night(6, 6, 6, 6)["score"]
        high = rate_night(9, 9, 9, 9)["score"]
        assert low < mid < high

    def test_geometric_mean_formula(self):
        """Verify weighted geometric mean calculation matches expected formula."""
        moon, dark, wx, bortle = 8.0, 6.0, 7.0, 5.0
        weights = {"weather": 0.40, "moon": 0.25, "dark": 0.25, "bortle": 0.10}
        expected_wgm = (
            (wx     / 10) ** weights["weather"] *
            (moon   / 10) ** weights["moon"]    *
            (dark   / 10) ** weights["dark"]    *
            (bortle / 10) ** weights["bortle"]
        )
        expected_score = round(10 * expected_wgm, 1)
        result = rate_night(moon, dark, wx, bortle)
        assert result["score"] == pytest.approx(expected_score, abs=0.05)


# ---------------------------------------------------------------------------
# weighted_weather_score
# ---------------------------------------------------------------------------

def _fixed_rate(score: float):
    """Return a rate_fn that always returns `score`."""
    return lambda _p: score


class TestWeightedWeatherScore:
    def test_no_points_returns_none(self):
        result = weighted_weather_score([], _NIGHT_START, _NIGHT_END, _fixed_rate(10))
        assert result is None

    def test_all_clear_returns_high_score(self):
        points = [_MockPoint(time=_dt(h)) for h in (3, 4, 5, 6)]
        score = weighted_weather_score(points, _NIGHT_START, _NIGHT_END, _fixed_rate(10))
        assert score == pytest.approx(10.0, abs=0.1)

    def test_all_overcast_returns_zero(self):
        points = [_MockPoint(time=_dt(h)) for h in (3, 4, 5, 6)]
        score = weighted_weather_score(points, _NIGHT_START, _NIGHT_END, _fixed_rate(0))
        assert score == pytest.approx(0.0, abs=0.1)

    def test_points_in_darkness_weighted_3x(self):
        """
        Clear point inside darkness (score=10, 3× weight) + overcast outside (score=0, 1× weight).
        Expected = (10×3 + 0×1) / (3+1) = 7.5
        Reversed: (0×3 + 10×1) / (3+1) = 2.5
        """
        p_dark    = _MockPoint(time=_dt(4))   # inside [02:00–08:00]
        p_twilight = _MockPoint(time=_dt(10)) # outside window

        # Clear inside, overcast outside
        rates = {id(p_dark): 10.0, id(p_twilight): 0.0}
        rate_fn = lambda p: rates[id(p)]
        score_clear_inside = weighted_weather_score(
            [p_dark, p_twilight], _NIGHT_START, _NIGHT_END, rate_fn
        )
        assert score_clear_inside == pytest.approx(7.5, abs=0.1)

        # Overcast inside, clear outside (reversed)
        rates2 = {id(p_dark): 0.0, id(p_twilight): 10.0}
        rate_fn2 = lambda p: rates2[id(p)]
        score_overcast_inside = weighted_weather_score(
            [p_dark, p_twilight], _NIGHT_START, _NIGHT_END, rate_fn2
        )
        assert score_overcast_inside == pytest.approx(2.5, abs=0.1)

        assert score_clear_inside > score_overcast_inside

    def test_no_dark_window_equal_weights(self):
        """When night_start/night_end are None all points are weighted equally."""
        p1 = _MockPoint(time=_dt(3))
        p2 = _MockPoint(time=_dt(10))
        rates = {id(p1): 8.0, id(p2): 4.0}
        rate_fn = lambda p: rates[id(p)]
        score = weighted_weather_score([p1, p2], None, None, rate_fn)
        assert score == pytest.approx(6.0, abs=0.1)  # simple average (8+4)/2

    def test_single_point_returns_its_score(self):
        p = _MockPoint(time=_dt(4))
        score = weighted_weather_score([p], _NIGHT_START, _NIGHT_END, _fixed_rate(7))
        assert score == pytest.approx(7.0, abs=0.1)

    def test_score_rounded_to_one_decimal(self):
        """weighted_weather_score result should be rounded to 1 decimal."""
        p1 = _MockPoint(time=_dt(3))   # inside darkness
        p2 = _MockPoint(time=_dt(10))  # outside
        rates = {id(p1): 7.0, id(p2): 3.0}
        rate_fn = lambda p: rates[id(p)]
        score = weighted_weather_score([p1, p2], _NIGHT_START, _NIGHT_END, rate_fn)
        # (7×3 + 3×1) / 4 = 6.0 — verify it's a float, not None
        assert isinstance(score, float)
        assert score == round(score, 1)
