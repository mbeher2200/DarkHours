"""
Tests for weather.py — rate_conditions(), _parse_open_meteo_hourly(), _merge_7timer().
All pure logic: no network, no ephemeris.
"""
from datetime import datetime, timedelta, timezone

import pytest

from PyNightSkyPredictor.weather import (
    WeatherPoint,
    _merge_7timer,
    _parse_open_meteo_hourly,
    rate_conditions,
)


def _wp(**kwargs) -> WeatherPoint:
    """Build a WeatherPoint with all fields None by default."""
    defaults = dict(
        time=datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc),
        cloud_cover_pct=None,
        seeing_arcsec=None,
        transparency=None,
        humidity_pct=None,
        wind_speed_ms=None,
        lifted_index=None,
        precip_type=None,
        temperature_c=None,
        feels_like_c=None,
        dew_point_c=None,
        wind_direction_deg=None,
    )
    defaults.update(kwargs)
    return WeatherPoint(**defaults)


# ---------------------------------------------------------------------------
# rate_conditions
# ---------------------------------------------------------------------------

class TestRateConditions:
    def test_no_data_returns_five(self):
        """All fields None → fallback 'no data' sentinel = 5."""
        assert rate_conditions(_wp()) == 5

    def test_result_is_int(self):
        assert isinstance(rate_conditions(_wp(cloud_cover_pct=50)), int)

    def test_score_always_in_1_to_10(self):
        for cloud in (0, 25, 50, 75, 100):
            s = rate_conditions(_wp(cloud_cover_pct=cloud))
            assert 1 <= s <= 10

    # --- Precipitation cap ---

    def test_rain_caps_score_at_one(self):
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type="rain")) == 1

    def test_snow_caps_score_at_one(self):
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type="snow")) == 1

    def test_freezing_rain_caps_score_at_one(self):
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type="frzr")) == 1

    def test_icep_caps_score_at_one(self):
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type="icep")) == 1

    def test_precip_none_is_not_capped(self):
        """precip_type=None is treated as no precipitation."""
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type=None)) > 1

    def test_precip_string_none_is_not_capped(self):
        """precip_type='none' is treated as clear — not precipitation."""
        assert rate_conditions(_wp(cloud_cover_pct=0, precip_type="none")) > 1

    # --- Cloud cover: max(0, 1 - (cloud/100)^0.7) ---

    def test_clear_sky_beats_overcast(self):
        clear = rate_conditions(_wp(cloud_cover_pct=0))
        overcast = rate_conditions(_wp(cloud_cover_pct=100))
        assert clear > overcast

    def test_overcast_yields_minimum_score(self):
        """100% cloud cover produces score at the minimum (1)."""
        assert rate_conditions(_wp(cloud_cover_pct=100)) == 1

    def test_cloud_score_monotone_decreasing(self):
        scores = [rate_conditions(_wp(cloud_cover_pct=c)) for c in (0, 25, 50, 75, 100)]
        assert scores == sorted(scores, reverse=True)

    def test_only_cloud_cover_yields_ten(self):
        """Perfect cloud cover with no other data → score = 10."""
        p = _wp(cloud_cover_pct=0)
        assert rate_conditions(p) == 10

    # --- Seeing: max(0, (3.0 - arcsec) / 2.6) ---

    def test_best_seeing_and_average_seeing_give_same_score(self):
        """Both 0.4" and 0.1" saturate the formula — same score."""
        s_best = rate_conditions(_wp(seeing_arcsec=0.4))
        s_better = rate_conditions(_wp(seeing_arcsec=0.1))
        assert s_best == s_better

    def test_good_seeing_beats_poor_seeing(self):
        good = rate_conditions(_wp(seeing_arcsec=0.5, cloud_cover_pct=0))
        poor = rate_conditions(_wp(seeing_arcsec=2.8, cloud_cover_pct=0))
        assert good > poor

    # --- Wind: max(0, 1 - ms/12) ---

    def test_calm_wind_beats_strong_wind(self):
        calm = rate_conditions(_wp(wind_speed_ms=0.0))
        strong = rate_conditions(_wp(wind_speed_ms=10.0))
        assert calm > strong

    def test_wind_above_12ms_clamped_to_same_as_12ms(self):
        """Wind component saturates at 0 for ≥ 12 m/s."""
        s12 = rate_conditions(_wp(wind_speed_ms=12.0))
        s20 = rate_conditions(_wp(wind_speed_ms=20.0))
        assert s12 == s20

    # --- Humidity: max(0, 1 - max(0, RH-50)/40) ---

    def test_humidity_below_50_has_no_penalty(self):
        """≤ 50% RH → no dew risk, full humidity score."""
        s30 = rate_conditions(_wp(humidity_pct=30))
        s50 = rate_conditions(_wp(humidity_pct=50))
        assert s30 == s50

    def test_humidity_90_reduces_score(self):
        """90% RH → component = max(0, 1 - 40/40) = 0."""
        low_rh = rate_conditions(_wp(humidity_pct=50, cloud_cover_pct=0))
        high_rh = rate_conditions(_wp(humidity_pct=90, cloud_cover_pct=0))
        assert low_rh > high_rh

    # --- Transparency ---

    def test_transparency_ranking(self):
        scores = [
            rate_conditions(_wp(transparency=t))
            for t in ("Excellent", "Good", "Fair", "Poor")
        ]
        assert scores == sorted(scores, reverse=True)

    # --- Weight redistribution ---

    def test_missing_seeing_does_not_crash(self):
        p = _wp(cloud_cover_pct=0, seeing_arcsec=None)
        assert rate_conditions(p) is not None

    def test_all_optimal_fields_give_ten(self):
        p = _wp(
            cloud_cover_pct=0,
            seeing_arcsec=0.4,
            transparency="Excellent",
            wind_speed_ms=0.0,
            humidity_pct=30,
        )
        assert rate_conditions(p) == 10

    def test_all_worst_fields_give_one(self):
        p = _wp(
            cloud_cover_pct=100,
            seeing_arcsec=3.0,
            transparency="Poor",
            wind_speed_ms=12.0,
            humidity_pct=90,
        )
        assert rate_conditions(p) == 1


# ---------------------------------------------------------------------------
# _parse_open_meteo_hourly — JSON parsing and precip_type derivation
# ---------------------------------------------------------------------------

def _make_hourly(**overrides) -> dict:
    """Return a minimal Open-Meteo hourly dict for a single time step."""
    h = {
        "time":                 ["2026-06-15T02:00"],
        "cloud_cover":          [0],
        "rain":                 [0.0],
        "snowfall":             [0.0],
        "relative_humidity_2m": [50],
        "wind_speed_10m":       [2.0],
        "temperature_2m":       [15.0],
    }
    h.update(overrides)
    return h


class TestParseOpenMeteoHourly:
    def test_no_precip_gives_none_string(self):
        points = _parse_open_meteo_hourly(_make_hourly(rain=[0.0], snowfall=[0.0]))
        assert points[0].precip_type == "none"

    def test_rain_positive_gives_rain(self):
        points = _parse_open_meteo_hourly(_make_hourly(rain=[0.5], snowfall=[0.0]))
        assert points[0].precip_type == "rain"

    def test_snowfall_positive_gives_snow(self):
        points = _parse_open_meteo_hourly(_make_hourly(rain=[0.0], snowfall=[0.3]))
        assert points[0].precip_type == "snow"

    def test_snowfall_wins_over_rain(self):
        """When both rain and snowfall are non-zero, snowfall takes priority."""
        points = _parse_open_meteo_hourly(_make_hourly(rain=[0.1], snowfall=[0.1]))
        assert points[0].precip_type == "snow"

    def test_time_is_utc_aware(self):
        points = _parse_open_meteo_hourly(_make_hourly())
        assert points[0].time.tzinfo is not None

    def test_cloud_cover_parsed(self):
        points = _parse_open_meteo_hourly(_make_hourly(cloud_cover=[75]))
        assert points[0].cloud_cover_pct == 75

    def test_seeing_is_none(self):
        """Open-Meteo does not supply seeing — field stays None."""
        points = _parse_open_meteo_hourly(_make_hourly())
        assert points[0].seeing_arcsec is None

    def test_multi_point_produces_correct_count(self):
        h = _make_hourly(
            time=["2026-06-15T02:00", "2026-06-15T03:00"],
            cloud_cover=[0, 100],
            rain=[0.0, 0.0],
            snowfall=[0.0, 0.0],
            relative_humidity_2m=[50, 60],
            wind_speed_10m=[2.0, 3.0],
            temperature_2m=[15.0, 14.0],
        )
        points = _parse_open_meteo_hourly(h)
        assert len(points) == 2
        assert points[0].cloud_cover_pct == 0
        assert points[1].cloud_cover_pct == 100


# ---------------------------------------------------------------------------
# _merge_7timer — seeing/transparency blend with 90-minute tolerance
# ---------------------------------------------------------------------------

def _t(hour: float) -> datetime:
    base = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=hour)


class TestMerge7Timer:
    def test_empty_seven_returns_original_points(self):
        p = _wp(time=_t(2))
        result = _merge_7timer([p], [])
        assert result[0] is p or result[0] == p

    def test_within_90_min_merges_seeing(self):
        p = _wp(time=_t(2), seeing_arcsec=None)
        s = _wp(time=_t(2.5), seeing_arcsec=0.8)  # 30 min away
        merged = _merge_7timer([p], [s])
        assert merged[0].seeing_arcsec == pytest.approx(0.8)

    def test_within_90_min_exact_boundary_is_merged(self):
        """Exactly 5400 s away is still within tolerance (≤ 5400)."""
        p = _wp(time=_t(2), seeing_arcsec=None)
        s = _wp(time=_t(3.5), seeing_arcsec=1.2)  # 90 min exactly
        merged = _merge_7timer([p], [s])
        assert merged[0].seeing_arcsec == pytest.approx(1.2)

    def test_beyond_90_min_is_not_merged(self):
        p = _wp(time=_t(2), seeing_arcsec=None)
        s = _wp(time=_t(3.51), seeing_arcsec=1.2)  # 90 min 36 s away
        merged = _merge_7timer([p], [s])
        assert merged[0].seeing_arcsec is None

    def test_transparency_is_merged(self):
        p = _wp(time=_t(2), transparency=None)
        s = _wp(time=_t(2), transparency="Excellent")
        merged = _merge_7timer([p], [s])
        assert merged[0].transparency == "Excellent"

    def test_cloud_cover_not_overwritten(self):
        """cloud_cover_pct comes from the primary provider; 7Timer doesn't supply it."""
        p = _wp(time=_t(2), cloud_cover_pct=30)
        s = _wp(time=_t(2), cloud_cover_pct=90, seeing_arcsec=0.5)
        merged = _merge_7timer([p], [s])
        assert merged[0].cloud_cover_pct == 30

    def test_nearest_7timer_point_is_chosen(self):
        p = _wp(time=_t(3), seeing_arcsec=None)
        s_far  = _wp(time=_t(1),   seeing_arcsec=2.0)
        s_near = _wp(time=_t(3.2), seeing_arcsec=0.6)
        merged = _merge_7timer([p], [s_far, s_near])
        assert merged[0].seeing_arcsec == pytest.approx(0.6)

    def test_lifted_index_is_merged(self):
        p = _wp(time=_t(2), lifted_index=None)
        s = _wp(time=_t(2), lifted_index=3)
        merged = _merge_7timer([p], [s])
        assert merged[0].lifted_index == 3
