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

    def test_result_rounded_to_one_decimal(self):
        sqm = radiance_to_sqm(2.5)
        assert sqm == round(sqm, 1)

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

    def test_result_rounded_to_one_decimal(self):
        sqm = luminance_to_sqm(0.5)
        assert sqm == round(sqm, 1)


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
