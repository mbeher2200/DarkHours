"""Tests for the vectorized ocean pre-filter in _extract_dark_sky_candidates.

The old per-point GLM filter (called in a loop over ring-grid coordinates) was
replaced by a single vectorized ``_glm.is_land()`` call applied to the full
candidate mask inside ``_extract_dark_sky_candidates``.  These tests verify:

  1. Ocean pixels are excluded when global-land-mask is available.
  2. Ocean pixels are NOT excluded when global-land-mask is unavailable.
  3. An all-water search area returns an empty candidate list.
"""
import numpy as np
from unittest.mock import MagicMock

import darkhours.darksky as ds


# ---------------------------------------------------------------------------
# Shared grid constants
#
# 2×2 grid:
#   lat_vals = linspace(36.0, 35.0, 2) = [36.0, 35.0]
#   lon_vals = linspace(-113.0, -111.0, 2) = [-113.0, -111.0]
#   indexing="ij" →
#     (0,0): lat=36.0, lon=-113.0
#     (0,1): lat=36.0, lon=-111.0
#     (1,0): lat=35.0, lon=-113.0
#     (1,1): lat=35.0, lon=-111.0  ← designated "ocean" pixel in tests 1 & 3
# ---------------------------------------------------------------------------
_MIN_LAT,  _MAX_LAT  = 35.0,   36.0
_MIN_LON,  _MAX_LON  = -113.0, -111.0
_ORIGIN_LAT, _ORIGIN_LON = 35.5, -112.0   # central; all 4 pixels within 150 mi

# Radiance that maps to Bortle 1:
#   SQM = 21.7 - 2.5*log10(L + 0.6) ≈ 22.05  (safely above the 22.0 threshold)
#   → L ≈ 0.124 nW/cm²/sr
_BORTLE1_RADIANCE = 0.124


def _all_dark_viirs() -> "np.ndarray":
    """2×2 array where every pixel maps to Bortle 1."""
    return np.full((2, 2), _BORTLE1_RADIANCE, dtype=np.float64)


class TestGlmWaterPreFilter:

    def test_water_coords_filtered_before_dark_candidates(self, monkeypatch):
        """When global-land-mask is present, pixels where is_land() returns
        False are excluded from _extract_dark_sky_candidates results.

        Replaces the old test that verified water coords were stripped before
        _bulk_bortle_lookup (now deleted); the filtering now occurs inside
        _extract_dark_sky_candidates via a vectorized land mask.
        """
        viirs = _all_dark_viirs()

        # Pixel (1, 1) → lat=35.0, lon=-111.0 is the ocean pixel
        def mock_is_land(lat_arr: np.ndarray, lon_arr: np.ndarray) -> np.ndarray:
            result = np.ones(lat_arr.shape, dtype=bool)
            result[1, 1] = False
            return result

        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", MagicMock(is_land=mock_is_land))

        results = ds._extract_dark_sky_candidates(
            viirs, None,
            _MIN_LAT, _MAX_LAT, _MIN_LON, _MAX_LON,
            _ORIGIN_LAT, _ORIGIN_LON,
            radius_miles=150,
            dark_threshold=3,
        )

        # Exactly the 3 land pixels should appear; the ocean pixel must be absent
        assert len(results) == 3
        ocean_present = any(
            abs(r["lat"] - 35.0) < 0.01 and abs(r["lon"] - (-111.0)) < 0.01
            for r in results
        )
        assert not ocean_present, "ocean pixel (35.0, -111.0) leaked into candidates"

    def test_all_coords_passed_when_glm_unavailable(self, monkeypatch):
        """When global-land-mask is absent, no land mask is applied; all dark
        pixels within the search radius appear in the candidate set regardless
        of whether they are over land or water.

        Replaces the old test that verified all coords were forwarded to
        _bulk_bortle_lookup unchanged (now deleted).
        """
        viirs = _all_dark_viirs()

        monkeypatch.setattr(ds, "_HAS_GLM", False)

        results = ds._extract_dark_sky_candidates(
            viirs, None,
            _MIN_LAT, _MAX_LAT, _MIN_LON, _MAX_LON,
            _ORIGIN_LAT, _ORIGIN_LON,
            radius_miles=150,
            dark_threshold=3,
        )

        # All 4 Bortle-1 pixels qualify — no land filter applied
        assert len(results) == 4

    def test_all_water_grid_returns_empty_results(self, monkeypatch):
        """If every pixel in the search area is classified as ocean,
        _extract_dark_sky_candidates returns an empty list rather than raising
        or returning None.
        """
        viirs = _all_dark_viirs()

        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", MagicMock(
            is_land=lambda lat_arr, lon_arr: np.zeros(lat_arr.shape, dtype=bool),
        ))

        results = ds._extract_dark_sky_candidates(
            viirs, None,
            _MIN_LAT, _MAX_LAT, _MIN_LON, _MAX_LON,
            _ORIGIN_LAT, _ORIGIN_LON,
            radius_miles=150,
            dark_threshold=3,
        )

        assert results == []
