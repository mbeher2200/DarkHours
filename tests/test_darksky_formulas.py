"""
Tests for darksky.py conversion formulas — SQM from radiance/luminance and Bortle classification.
All pure math: no rasterio, no S3, no network.
"""
import math

import pytest

from darkhours.darksky import (
    luminance_to_sqm,
    radiance_to_sqm,
    sqm_to_bortle,
    sqm_to_zone,
)


# ---------------------------------------------------------------------------
# radiance_to_sqm — VIIRS empirical regression
# SQM ≈ 21.7 − 2.5 × log10(L + 0.6)    (L in nW/cm²/sr)
# ---------------------------------------------------------------------------

class TestRadianceToSqm:
    def test_zero_radiance_gives_dark_sky(self):
        """Zero VIIRS radiance dominated by 0.6 airglow offset → SQM well above 21."""
        sqm = radiance_to_sqm(0.0)
        assert sqm > 21.0

    def test_high_radiance_gives_bright_sky(self):
        assert radiance_to_sqm(100.0) < radiance_to_sqm(0.0)

    def test_known_formula_value(self):
        L = 1.0
        expected = round(21.7 - 2.5 * math.log10(L + 0.6), 1)
        assert radiance_to_sqm(L) == pytest.approx(expected, abs=0.05)

    def test_returns_the_unrounded_regression_value(self):
        """The Bortle thresholds are themselves fixed-decimal, so the conversion must
        not pre-quantise onto them. Display rounding belongs in lookup()."""
        sqm = radiance_to_sqm(2.5)
        assert sqm == pytest.approx(21.7 - 2.5 * math.log10(2.5 + 0.6), abs=1e-12)
        assert sqm != round(sqm, 1)

    def test_monotone_decreasing(self):
        """Higher radiance (more light) → lower SQM (brighter sky)."""
        sqms = [radiance_to_sqm(L) for L in (0.0, 1.0, 5.0, 20.0, 100.0)]
        assert sqms == sorted(sqms, reverse=True)


# ---------------------------------------------------------------------------
# luminance_to_sqm — Falchi physical model
# SQM = 22.08 − 2.5 × log10((La + 0.252) / 0.252)   (La in mcd/m²)
# ---------------------------------------------------------------------------

class TestLuminanceToSqm:
    def test_zero_luminance_is_natural_sky(self):
        """Zero artificial luminance → natural-sky SQM reference = 22.08."""
        sqm = luminance_to_sqm(0.0)
        assert sqm == pytest.approx(22.08, abs=0.05)

    def test_high_luminance_gives_bright_sky(self):
        assert luminance_to_sqm(10.0) < luminance_to_sqm(0.0)

    def test_known_formula_value(self):
        La = 1.0
        L_NAT = 0.252
        SQM_NAT = 22.08
        expected = round(SQM_NAT - 2.5 * math.log10((La + L_NAT) / L_NAT), 1)
        assert luminance_to_sqm(La) == pytest.approx(expected, abs=0.05)

    def test_monotone_decreasing(self):
        sqms = [luminance_to_sqm(L) for L in (0.0, 0.1, 1.0, 5.0, 20.0)]
        assert sqms == sorted(sqms, reverse=True)

    def test_negative_luminance_treated_as_zero(self):
        """Negative (below-detection) luminance falls back to natural-sky value."""
        sqm = luminance_to_sqm(-1.0)
        assert sqm == pytest.approx(22.08, abs=0.05)

    def test_returns_the_unrounded_model_value(self):
        sqm = luminance_to_sqm(0.5)
        expected = 22.08 - 2.5 * math.log10((0.5 + 0.252) / 0.252)
        assert sqm == pytest.approx(expected, abs=1e-12)
        assert sqm != round(sqm, 1)

    def test_both_branches_carry_the_same_precision(self):
        """The La <= 0 branch always returned the full-precision constant; the model
        branch now matches it, so the two exits agree."""
        assert luminance_to_sqm(0.0) == 22.08
        assert luminance_to_sqm(0.0) == pytest.approx(22.08, abs=1e-12)


# ---------------------------------------------------------------------------
# sqm_to_bortle — classification against documented thresholds
# ---------------------------------------------------------------------------

class TestSqmToBortle:
    # (SQM at the lower boundary of each class, expected class)
    _BOUNDARIES = [
        (22.0,  1),   # ≥ 22.0 → Exceptional dark sky
        (21.7,  2),   # ≥ 21.7 → Truly dark sky
        (21.3,  3),   # ≥ 21.3 → Rural sky
        (20.8,  4),   # ≥ 20.8 → Rural/suburban transition
        (20.0,  5),   # ≥ 20.0 → Suburban sky
        (19.1,  6),   # ≥ 19.1 → Bright suburban
        (18.0,  7),   # ≥ 18.0 → Suburban/urban transition
        (17.0,  8),   # ≥ 17.0 → City sky
        ( 0.0,  9),   # ≥  0.0 → Inner city sky
    ]

    def test_exact_boundary_values(self):
        for sqm, expected_class in self._BOUNDARIES:
            cls, desc = sqm_to_bortle(sqm)
            assert cls == expected_class, (
                f"SQM {sqm} → expected Bortle {expected_class}, got {cls}"
            )

    def test_class_is_int(self):
        cls, _ = sqm_to_bortle(21.5)
        assert isinstance(cls, int)

    def test_description_is_non_empty_string(self):
        _, desc = sqm_to_bortle(21.5)
        assert isinstance(desc, str) and len(desc) > 0

    def test_just_below_bortle1_threshold_is_bortle2(self):
        cls, _ = sqm_to_bortle(21.99)
        assert cls == 2

    def test_negative_sqm_is_bortle9(self):
        cls, _ = sqm_to_bortle(-5.0)
        assert cls == 9

    def test_darker_sky_lower_class_number(self):
        """Brighter sky (lower SQM) → higher Bortle class number."""
        dark_class  = sqm_to_bortle(22.0)[0]
        light_class = sqm_to_bortle(17.0)[0]
        assert dark_class < light_class


# ---------------------------------------------------------------------------
# sqm_to_zone — djlorenz light pollution index zones
# ---------------------------------------------------------------------------

class TestSqmToZone:
    def test_very_dark_is_zone_0(self):
        assert sqm_to_zone(22.0) == "0"

    def test_boundary_21_99_is_zone_1a(self):
        # Exactly 21.99 is the zone-0/1a boundary: zone 0 requires SQM > 21.99
        assert sqm_to_zone(21.99) == "1a"

    def test_near_dark_threshold_is_zone_1b(self):
        # sqm < 21.99 (zone 0 gate), ≥ 21.93 (zone 1b threshold) → zone 1b
        assert sqm_to_zone(21.95) == "1b"

    def test_mid_dark_is_zone_3b(self):
        # 21.55 ≥ 21.51 (3b threshold) and < 21.69 (3a threshold) → zone 3b
        zone = sqm_to_zone(21.55)
        assert zone == "3b"

    def test_bright_sky_is_zone_7b(self):
        assert sqm_to_zone(10.0) == "7b"

    def test_returns_string(self):
        assert isinstance(sqm_to_zone(20.0), str)


# ---------------------------------------------------------------------------
# Classifier parity — scalar sqm_to_bortle vs the vectorized _sqm_to_bortle_array
# ---------------------------------------------------------------------------

class TestClassifierParity:
    """A standing invariant, not a regression guard.

    These two helpers have always agreed on identical input; what differed was the
    SQM each was handed (see tests/test_darksky_classification.py, which covers the
    composition). Pinned so a future edit to either table or either lookup keeps them
    in step.
    """

    def test_agree_across_the_stored_value_range(self):
        """Domain starts well below 17.0: radiance_to_sqm is unbounded below and real
        readings reach ~14."""
        import numpy as np

        from darkhours.darksky import _sqm_to_bortle_array

        xs = np.arange(13.0, 22.4, 0.001)
        scalar = np.array([sqm_to_bortle(float(x))[0] for x in xs], dtype=np.int8)
        assert np.array_equal(scalar, _sqm_to_bortle_array(xs))

    def test_agree_on_exact_threshold_values(self):
        """`>=` against `searchsorted(side="right")` — equality is the only place the
        two implementations could diverge, and arange never lands on a threshold."""
        import numpy as np

        from darkhours.darksky import _BORTLE, _sqm_to_bortle_array

        xs = np.array([t for t, _, _ in _BORTLE], dtype=np.float64)
        scalar = np.array([sqm_to_bortle(float(x))[0] for x in xs], dtype=np.int8)
        assert np.array_equal(scalar, _sqm_to_bortle_array(xs))

    def test_nan_is_the_one_documented_asymmetry(self):
        """The array path reserves 0 for ocean/nodata; the scalar path has no such
        sentinel and falls through to 9. Callers must not feed it NaN."""
        import numpy as np

        from darkhours.darksky import _sqm_to_bortle_array

        assert sqm_to_bortle(float("nan"))[0] == 9
        assert int(_sqm_to_bortle_array(np.array([np.nan]))[0]) == 0
