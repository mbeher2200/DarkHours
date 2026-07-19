"""
Tests for weather.py — rate_conditions(), _parse_open_meteo_hourly(), _merge_7timer(),
_merge_air_quality(). All pure logic: no network, no ephemeris.
"""
from datetime import datetime, timedelta, timezone

import pytest

from darkhours.weather import (
    WeatherPoint,
    _merge_7timer,
    _merge_air_quality,
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
        aerosol_optical_depth=None,
        pm2_5=None,
        cloud_cover_low_pct=None,
        cloud_cover_mid_pct=None,
        cloud_cover_high_pct=None,
        visibility_m=None,
        wind_gust_ms=None,
    )
    defaults.update(kwargs)
    return WeatherPoint(**defaults)


# ---------------------------------------------------------------------------
# rate_conditions
# ---------------------------------------------------------------------------

class TestRateConditions:
    def test_no_data_returns_ten(self):
        """All fields None → assumes base score 1.0 with no limiters = 10."""
        assert rate_conditions(_wp()) == 10

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

    # --- Cloud cover (Limiter): max(0.0, 1.0 - (cloud/100.0)^1.5) ---

    def test_clear_sky_beats_overcast(self):
        clear = rate_conditions(_wp(cloud_cover_pct=0))
        overcast = rate_conditions(_wp(cloud_cover_pct=100))
        assert clear > overcast

    def test_overcast_yields_minimum_score(self):
        """100% cloud cover produces a 0.0 multiplier, leading to the minimum score (1)."""
        assert rate_conditions(_wp(cloud_cover_pct=100)) == 1

    def test_cloud_score_monotone_decreasing(self):
        scores = [rate_conditions(_wp(cloud_cover_pct=c)) for c in (0, 25, 50, 75, 100)]
        assert scores == sorted(scores, reverse=True)

    def test_only_cloud_cover_yields_ten(self):
        """Perfect cloud cover with no other data → multiplier 1.0 * base 1.0 = 10."""
        p = _wp(cloud_cover_pct=0)
        assert rate_conditions(p) == 10

    # --- Seeing (Base Quality): max(0.0, min(1.0, (4.0 - arcsec) / 3.0)) ---

    def test_best_seeing_and_average_seeing_give_same_score(self):
        """Both 0.4" and 0.1" saturate the top of the formula — same score."""
        s_best = rate_conditions(_wp(seeing_arcsec=0.4))
        s_better = rate_conditions(_wp(seeing_arcsec=0.1))
        assert s_best == s_better

    def test_good_seeing_beats_poor_seeing(self):
        good = rate_conditions(_wp(seeing_arcsec=0.5, cloud_cover_pct=0))
        poor = rate_conditions(_wp(seeing_arcsec=3.8, cloud_cover_pct=0))
        assert good > poor

    # --- Wind (Limiter): max(0.0, 1.0 - (ms/17.0)^2) ---

    def test_calm_wind_beats_strong_wind(self):
        calm = rate_conditions(_wp(wind_speed_ms=0.0))
        strong = rate_conditions(_wp(wind_speed_ms=10.0))
        assert calm > strong

    def test_wind_above_17ms_clamped_to_same_as_17ms(self):
        """Wind limiter drops to 0 at 17 m/s and doesn't go below."""
        s17 = rate_conditions(_wp(wind_speed_ms=17.0))
        s25 = rate_conditions(_wp(wind_speed_ms=25.0))
        assert s17 == s25

    def test_gust_used_when_sustained_wind_missing(self):
        assert rate_conditions(_wp(wind_gust_ms=25.0)) == 1

    def test_worse_of_sustained_and_gust_is_used(self):
        """Calm sustained wind (2 m/s) with a severe gust (20 m/s) must still gate —
        a calm average can mask short gusts strong enough to shake a tripod."""
        assert rate_conditions(_wp(wind_speed_ms=2.0, wind_gust_ms=20.0)) == 1

    def test_gust_lower_than_sustained_does_not_help(self):
        """Severe sustained wind isn't masked by a merely-mild gust value."""
        assert rate_conditions(_wp(wind_speed_ms=20.0, wind_gust_ms=2.0)) == 1

    # --- Humidity (Base Quality): max(0.0, 1.0 - max(0.0, RH-50)/50) ---

    def test_humidity_below_50_has_no_penalty(self):
        """≤ 50% RH → no dew risk, full humidity base quality."""
        s30 = rate_conditions(_wp(humidity_pct=30))
        s50 = rate_conditions(_wp(humidity_pct=50))
        assert s30 == s50

    def test_humidity_90_reduces_score(self):
        """90% RH drops the base quality significantly (1.0 - 40/50 = 0.2)."""
        low_rh = rate_conditions(_wp(humidity_pct=50, cloud_cover_pct=0))
        high_rh = rate_conditions(_wp(humidity_pct=90, cloud_cover_pct=0))
        assert low_rh > high_rh

    # --- Transparency (Limiter) ---

    def test_transparency_ranking(self):
        scores = [
            rate_conditions(_wp(transparency=t))
            for t in ("Excellent", "Good", "Fair", "Poor")
        ]
        assert scores == sorted(scores, reverse=True)

    # --- Overall Limiter & Quality integration ---

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
            seeing_arcsec=4.0,
            transparency="Poor",
            wind_speed_ms=17.0,
            humidity_pct=100,
        )
        assert rate_conditions(p) == 1

    # --- Cloud tiers (Limiter): random overlap of low/mid + 0.6*high, 1.5 power curve ---

    def test_cloud_tiers_random_overlap_worse_than_either_layer_alone(self):
        """Two independent 50% layers should score worse than either alone (50%) — random
        overlap (1-(1-low)(1-mid)) gives 75% effective opacity, not max(low,mid)=50%."""
        both      = rate_conditions(_wp(cloud_cover_low_pct=50, cloud_cover_mid_pct=50, cloud_cover_high_pct=0))
        low_only  = rate_conditions(_wp(cloud_cover_low_pct=50, cloud_cover_mid_pct=0,  cloud_cover_high_pct=0))
        mid_only  = rate_conditions(_wp(cloud_cover_low_pct=0,  cloud_cover_mid_pct=50, cloud_cover_high_pct=0))
        assert both < low_only
        assert both < mid_only

    def test_cloud_tiers_high_cirrus_penalized_less_than_low_mid(self):
        """Same magnitude split between high-only vs low-only cloud cover — high/cirrus
        gets the lighter 0.6 weight, so it should score strictly higher."""
        high_only = rate_conditions(_wp(cloud_cover_low_pct=0, cloud_cover_mid_pct=0, cloud_cover_high_pct=80))
        low_only  = rate_conditions(_wp(cloud_cover_low_pct=80, cloud_cover_mid_pct=0, cloud_cover_high_pct=0))
        assert high_only > low_only

    def test_cloud_tiers_fall_back_to_total_when_tiers_absent(self):
        """No tier fields set — reproduces the pre-upgrade cloud_cover_pct-only score."""
        assert rate_conditions(_wp(cloud_cover_pct=50)) == rate_conditions(
            _wp(cloud_cover_pct=50, cloud_cover_low_pct=None, cloud_cover_mid_pct=None, cloud_cover_high_pct=None)
        )

    def test_cloud_tiers_take_priority_over_total_when_both_present(self):
        """Tier fields set alongside cloud_cover_pct — tiers drive the score, not the total."""
        tiered = rate_conditions(_wp(cloud_cover_pct=100, cloud_cover_low_pct=0, cloud_cover_mid_pct=0, cloud_cover_high_pct=0))
        assert tiered == 10

    # --- AOD (Limiter): piecewise 1.0 / linear taper / power curve / 0.0 ---

    def test_aod_below_0_1_no_penalty(self):
        assert rate_conditions(_wp(aerosol_optical_depth=0.05)) == 10

    def test_aod_above_0_8_zeroes_score(self):
        assert rate_conditions(_wp(aerosol_optical_depth=0.9)) == 1

    def test_aod_monotone_decreasing(self):
        scores = [rate_conditions(_wp(aerosol_optical_depth=a)) for a in (0.0, 0.2, 0.5, 0.9)]
        assert scores == sorted(scores, reverse=True)

    def test_pm25_fallback_used_when_aod_missing(self):
        assert rate_conditions(_wp(pm2_5=200, aerosol_optical_depth=None)) == 1

    def test_pm25_still_penalizes_when_aod_present_and_clean(self):
        """Real wildfire-smoke case: satellite column AOD 'clean' (0.05) but a shallow,
        trapped surface smoke layer reads hazardous on ground-level PM2.5 (200 ug/m3).
        The worse (PM2.5) must still zero the score — AOD must not mask it."""
        assert rate_conditions(_wp(aerosol_optical_depth=0.05, pm2_5=200)) == 1

    def test_aod_still_penalizes_when_pm25_present_and_clean(self):
        """Inverse case: AOD hazardous (0.9) but PM2.5 clean (5) — AOD must still win."""
        assert rate_conditions(_wp(aerosol_optical_depth=0.9, pm2_5=5)) == 1

    def test_worse_of_aod_and_pm25_is_used(self):
        """Both present, both mildly elevated but at different severities — score should
        match whichever curve is worse, not just AOD."""
        aod_only = rate_conditions(_wp(aerosol_optical_depth=0.2))
        both = rate_conditions(_wp(aerosol_optical_depth=0.2, pm2_5=200))
        assert both < aod_only

    # --- Visibility (Limiter + hard gate) ---

    def test_visibility_above_20000_no_penalty(self):
        assert rate_conditions(_wp(visibility_m=25000)) == 10

    def test_visibility_below_10000_log_dropoff_monotonic(self):
        scores = [rate_conditions(_wp(visibility_m=v)) for v in (9000, 5000, 2000)]
        assert scores == sorted(scores, reverse=True)

    def test_visibility_below_1000_hard_gates(self):
        assert rate_conditions(_wp(visibility_m=500, precip_type=None)) == 1

    def test_fog_precip_type_hard_gates(self):
        assert rate_conditions(_wp(precip_type="fog", cloud_cover_pct=0)) == 1

    def test_tstorm_precip_type_hard_gates(self):
        assert rate_conditions(_wp(precip_type="tstorm", cloud_cover_pct=0)) == 1


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
        "weather_code":         [0],
        "visibility":           [24000],
    }
    h.update(overrides)
    return h


class TestParseOpenMeteoHourly:
    def test_unmapped_code_gives_none(self):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[2]))
        assert points[0].precip_type == "none"

    @pytest.mark.parametrize("code", [45, 48])
    def test_fog_codes_give_fog(self, code):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "fog"

    @pytest.mark.parametrize("code", [56, 57, 66, 67])
    def test_frzr_codes_give_frzr(self, code):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "frzr"

    @pytest.mark.parametrize("code", [77, 87, 88])
    def test_icep_codes_give_icep(self, code):
        """87/88 are not standard WMO codes; included per spec even though Open-Meteo
        has never been observed to emit them."""
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "icep"

    @pytest.mark.parametrize("code", [71, 73, 75, 85, 86])
    def test_snow_codes_give_snow(self, code):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "snow"

    @pytest.mark.parametrize("code", [51, 53, 55, 61, 63, 65, 80, 81, 82])
    def test_rain_codes_give_rain(self, code):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "rain"

    @pytest.mark.parametrize("code", [95, 96, 99])
    def test_tstorm_codes_give_tstorm(self, code):
        points = _parse_open_meteo_hourly(_make_hourly(weather_code=[code]))
        assert points[0].precip_type == "tstorm"

    def test_visibility_parsed(self):
        points = _parse_open_meteo_hourly(_make_hourly(visibility=[15000]))
        assert points[0].visibility_m == 15000

    def test_wind_gust_parsed(self):
        points = _parse_open_meteo_hourly(_make_hourly(wind_gusts_10m=[12.5]))
        assert points[0].wind_gust_ms == 12.5

    def test_cloud_tiers_parsed(self):
        points = _parse_open_meteo_hourly(_make_hourly(
            cloud_cover_low=[10], cloud_cover_mid=[20], cloud_cover_high=[30],
        ))
        assert points[0].cloud_cover_low_pct == 10
        assert points[0].cloud_cover_mid_pct == 20
        assert points[0].cloud_cover_high_pct == 30

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
            weather_code=[0, 0],
            visibility=[24000, 24000],
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


# ---------------------------------------------------------------------------
# _merge_air_quality — pm2_5/aerosol_optical_depth blend, 90-minute tolerance
# ---------------------------------------------------------------------------

class TestMergeAirQuality:
    def test_empty_aq_returns_original_points(self):
        p = _wp(time=_t(2))
        result = _merge_air_quality([p], [])
        assert result[0] is p or result[0] == p

    def test_within_tolerance_merges_pm25_and_aod(self):
        p = _wp(time=_t(2), pm2_5=None, aerosol_optical_depth=None)
        aq = [(_t(2.5), 12.0, 0.2)]  # 30 min away
        merged = _merge_air_quality([p], aq)
        assert merged[0].pm2_5 == pytest.approx(12.0)
        assert merged[0].aerosol_optical_depth == pytest.approx(0.2)

    def test_outside_tolerance_leaves_fields_none(self):
        p = _wp(time=_t(2), pm2_5=None, aerosol_optical_depth=None)
        aq = [(_t(3.51), 12.0, 0.2)]  # 90 min 36 s away
        merged = _merge_air_quality([p], aq)
        assert merged[0].pm2_5 is None
        assert merged[0].aerosol_optical_depth is None
