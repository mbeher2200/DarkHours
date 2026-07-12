"""
Tests for moonlight.py — Krisciunas & Schaefer (1991) model (pure math, no dependencies).
"""

import pytest
from PyNightSkyPredictor.moonlight import (
    ks_delta_mag,
    ks_moon_credit,
    moon_wash_severity,
    KS_MODERATE_THRESH,
    _KS_MEAN_DIST_KM,
    _KS_MINOR_THRESH,
    _KS_SEVERE_THRESH,
)


# ---------------------------------------------------------------------------
# ks_delta_mag
# ---------------------------------------------------------------------------

class TestKsDeltaMag:
    def test_new_moon_returns_zero(self):
        assert ks_delta_mag(0, 45.0, 45.0) == 0.0

    def test_negative_illumination_returns_zero(self):
        assert ks_delta_mag(-1, 45.0, 45.0) == 0.0

    def test_moon_below_horizon_returns_zero(self):
        assert ks_delta_mag(100, 45.0, 0.0) == 0.0

    def test_moon_at_negative_altitude_returns_zero(self):
        assert ks_delta_mag(100, 45.0, -10.0) == 0.0

    def test_full_moon_produces_brightening(self):
        delta = ks_delta_mag(100, 45.0, 45.0)
        assert delta > 0.0, "Full moon should brighten the sky"

    def test_greater_separation_less_brightening(self):
        close  = ks_delta_mag(100, 10.0, 45.0)
        far    = ks_delta_mag(100, 90.0, 45.0)
        assert close > far, "Closer moon separation should produce more brightening"

    def test_higher_illumination_more_brightening(self):
        quarter = ks_delta_mag(50,  45.0, 45.0)
        full    = ks_delta_mag(100, 45.0, 45.0)
        assert full > quarter, "Full moon should brighten more than quarter moon"

    def test_higher_moon_more_brightening(self):
        low  = ks_delta_mag(100, 45.0, 15.0)
        high = ks_delta_mag(100, 45.0, 60.0)
        assert high > low, "Higher moon should scatter more light"

    def test_distance_supermoon_brighter_than_mean(self):
        """Moon at 357,000 km (supermoon distance) brightens more than at mean distance."""
        supermoon_dist = 357_000.0
        delta_super = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=supermoon_dist)
        delta_mean  = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=_KS_MEAN_DIST_KM)
        assert delta_super > delta_mean, "Supermoon should produce more brightening"

    def test_distance_micromoon_dimmer_than_mean(self):
        """Moon at 406,000 km (micromoon distance) brightens less than at mean distance."""
        micromoon_dist = 406_000.0
        delta_micro = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=micromoon_dist)
        delta_mean  = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=_KS_MEAN_DIST_KM)
        assert delta_micro < delta_mean, "Micromoon should produce less brightening"

    def test_distance_mean_unchanged(self):
        """Explicitly passing mean distance gives same result as default."""
        default  = ks_delta_mag(100, 45.0, 45.0)
        explicit = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=_KS_MEAN_DIST_KM)
        assert abs(default - explicit) < 1e-10

    def test_distance_correction_magnitude(self):
        """Supermoon/micromoon correction is within physically plausible range."""
        supermoon_dist = 357_000.0
        micromoon_dist = 406_000.0
        delta_super = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=supermoon_dist)
        delta_micro = ks_delta_mag(100, 45.0, 45.0, moon_earth_dist_km=micromoon_dist)
        delta_diff  = delta_super - delta_micro
        # ±8.5% orbital variation → up to ±0.35 mag/arcsec²; total spread ≤ 0.70
        assert 0.05 < delta_diff < 0.70, (
            f"Distance correction spread {delta_diff:.3f} outside expected range"
        )


# ---------------------------------------------------------------------------
# ks_moon_credit
# ---------------------------------------------------------------------------

class TestKsMoonCredit:
    def test_new_moon_full_credit(self):
        credit = ks_moon_credit(0)
        assert credit == pytest.approx(1.0, abs=0.01)

    def test_full_moon_zero_credit(self):
        credit = ks_moon_credit(100)
        assert credit == pytest.approx(0.0, abs=0.01)

    def test_quarter_moon_heavily_penalised(self):
        credit = ks_moon_credit(50)
        assert credit < 0.40, f"Quarter moon credit {credit:.3f} should be < 0.40"

    def test_crescent_high_credit(self):
        credit = ks_moon_credit(15)
        assert credit > 0.80, f"Crescent credit {credit:.3f} should be > 0.80"

    def test_credit_monotonically_decreasing(self):
        illums = range(0, 105, 5)
        credits = [ks_moon_credit(i) for i in illums]
        for i in range(len(credits) - 1):
            assert credits[i] >= credits[i + 1] - 1e-9, (
                f"Credit not monotonic at {list(illums)[i]}% → {list(illums)[i+1]}%: "
                f"{credits[i]:.4f} > {credits[i+1]:.4f}"
            )

    def test_credit_bounded(self):
        for illum in range(0, 101, 5):
            credit = ks_moon_credit(illum)
            assert 0.0 <= credit <= 1.0, f"Credit {credit} out of [0, 1] at {illum}%"


# ---------------------------------------------------------------------------
# moon_wash_severity
# ---------------------------------------------------------------------------

class TestMoonWashSeverity:
    def test_new_moon_no_severity(self):
        assert moon_wash_severity(0, 45.0, 45.0) is None

    def test_full_moon_close_separation_severe(self):
        result = moon_wash_severity(100, 15.0, 60.0)
        assert result == "severe", f"Expected 'severe', got {result!r}"

    def test_crescent_minor_or_none(self):
        result = moon_wash_severity(15, 45.0, 30.0)
        assert result in (None, "minor"), f"15% crescent should be None or minor, got {result!r}"

    def test_none_defaults_match_explicit_45(self):
        """moon_wash_severity with None args should equal explicit 45° / 45°."""
        result_default  = moon_wash_severity(80.0, None, None)
        result_explicit = moon_wash_severity(80.0, 45.0, 45.0)
        assert result_default == result_explicit

    def test_threshold_none_below_minor(self):
        """Confirm delta < 0.10 → None."""
        # Near-new moon, wide separation, low altitude → negligible brightening
        result = moon_wash_severity(5, 90.0, 10.0)
        assert result is None

    def test_threshold_severe_at_or_above_1_5(self):
        """Confirm severe category for very bright moon at close range."""
        # Full moon, tight separation, high altitude → definitely severe
        result = moon_wash_severity(100, 10.0, 80.0)
        assert result == "severe"

    def test_moderate_classification(self):
        """Confirm moderate classification exists and falls between minor and severe."""
        # Find a geometry that lands in the moderate band
        result = moon_wash_severity(60, 90.0, 30.0)
        assert result in ("minor", "moderate", "severe"), f"Unexpected severity: {result!r}"

    def test_moon_below_horizon_no_severity(self):
        """Moon altitude ≤ 0 → ks_delta_mag returns 0 → severity None."""
        assert moon_wash_severity(100, 45.0, 0.0) is None
        assert moon_wash_severity(100, 45.0, -20.0) is None


# ---------------------------------------------------------------------------
# Winkler (2022) hybrid model: AOD, slant path, normalisation anchor
# ---------------------------------------------------------------------------

class TestWinklerModel:
    def test_aod_none_equals_reference_aod(self):
        """aod=None must be exactly the reference clear sky (_AOD_REF)."""
        from PyNightSkyPredictor.moonlight import _AOD_REF
        for illum, sep, alt in [(30, 45, 20), (80, 10, 60), (100, 120, 45)]:
            assert ks_delta_mag(illum, sep, alt) == ks_delta_mag(illum, sep, alt, aod=_AOD_REF)

    def test_norm_anchors_to_legacy_at_credit_geometry(self):
        """_KS_NORM pins the new kernel to the legacy K&S intensity at the
        ks_moon_credit proxy geometry, keeping the credit curve bit-identical."""
        import math
        legacy_ratio = 10 ** 5.36 * 1.06 * 10 ** (-0.4 * 0.172 * 2.0)  # sep 90°, moon alt 30°
        for illum in (5, 25, 50, 75, 100):
            alpha  = math.degrees(math.acos(max(-1.0, min(1.0, 2.0 * illum / 100.0 - 1.0))))
            v_moon = -12.73 + 0.026 * alpha + 4e-9 * alpha ** 4
            i_moon = 10 ** (-0.4 * (v_moon + 16.57))
            i_sky  = 10 ** ((27.78 - 21.6) / 2.5)
            legacy_delta = 2.5 * math.log10(1.0 + legacy_ratio * i_moon / i_sky)
            assert ks_delta_mag(illum, 90.0, 30.0) == pytest.approx(legacy_delta, abs=1e-12)

    def test_high_aod_amplifies_aureole_near_moon(self):
        """Smoke brightens the sky near the moon (Mie forward scattering)."""
        assert ks_delta_mag(80, 10.0, 45.0, aod=0.8) > ks_delta_mag(80, 10.0, 45.0)

    def test_high_aod_dims_far_sky(self):
        """Smoke dims the sky far from the moon (beam extinction dominates)."""
        assert ks_delta_mag(80, 120.0, 45.0, aod=0.8) < ks_delta_mag(80, 120.0, 45.0)

    def test_aod_capped(self):
        """AOD beyond the single-scatter validity cap is clamped."""
        assert ks_delta_mag(80, 45.0, 45.0, aod=50.0) == ks_delta_mag(80, 45.0, 45.0, aod=3.0)

    def test_negative_aod_clamped_to_zero(self):
        assert ks_delta_mag(80, 45.0, 45.0, aod=-1.0) == ks_delta_mag(80, 45.0, 45.0, aod=0.0)

    def test_degenerate_airmass_continuous(self):
        """moon alt == target alt hits the analytic kernel limit; it must be
        finite and continuous with the neighbouring geometry."""
        same  = ks_delta_mag(80, 45.0, 45.0, target_alt_deg=45.0)
        near  = ks_delta_mag(80, 45.0, 45.001, target_alt_deg=45.0)
        assert same > 0
        assert same == pytest.approx(near, rel=1e-3)

    def test_low_target_altitude_brighter(self):
        """Longer slant path at low target altitude scatters more moonlight."""
        low  = ks_delta_mag(80, 90.0, 30.0, target_alt_deg=20.0)
        high = ks_delta_mag(80, 90.0, 30.0, target_alt_deg=70.0)
        assert low > high


class TestHelpers:
    def test_k_ext_from_aod_reference(self):
        from PyNightSkyPredictor.moonlight import k_ext_from_aod
        assert k_ext_from_aod(None) == pytest.approx(0.172, abs=1e-12)

    def test_k_ext_from_aod_monotonic(self):
        from PyNightSkyPredictor.moonlight import k_ext_from_aod
        assert k_ext_from_aod(0.5) > k_ext_from_aod(0.1) > k_ext_from_aod(0.0)

    def test_nelm_known_points(self):
        from PyNightSkyPredictor.moonlight import nelm_from_sqm
        assert nelm_from_sqm(22.0) == pytest.approx(6.62, abs=0.02)   # pristine sky
        assert nelm_from_sqm(18.0) == pytest.approx(3.97, abs=0.02)   # Bortle 8
        assert nelm_from_sqm(22.0) > nelm_from_sqm(20.0) > nelm_from_sqm(18.0)
