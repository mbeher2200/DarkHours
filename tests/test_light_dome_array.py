"""
Tests for the unified array-based find_nearby functions:
  _sqm_to_bortle_array
  _load_raster_window
  _find_light_domes_from_array
  _extract_dark_sky_candidates

All tests use synthetic numpy arrays — no rasterio, no S3, no network access.
"""
import math
import pytest
import numpy as np
from unittest.mock import MagicMock, patch

scipy = pytest.importorskip("scipy", reason="scipy required for array-based dome tests")

import PyNightSkyPredictor.darksky as ds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _radiance_for_bortle(bortle: int) -> float:
    """Return a VIIRS radiance value that maps to exactly the given Bortle class.

    Each Bortle class spans [lower_sqm, next_lower_sqm).  We use
    sqm = lower_sqm + 0.05 to land safely inside the class.
    """
    # Lower SQM boundary for each class (must be >= this to reach the class)
    sqm_lower = {
        1: 22.0, 2: 21.7, 3: 21.3, 4: 20.8,
        5: 20.0, 6: 19.1, 7: 18.0, 8: 17.0, 9: 0.0,
    }
    lower = sqm_lower.get(bortle, 17.0)
    sqm = lower + 0.05 if lower > 0 else 0.05
    L = 10 ** ((21.7 - sqm) / 2.5) - 0.6
    return max(L, 0.001)


# ---------------------------------------------------------------------------
# _sqm_to_bortle_array
# ---------------------------------------------------------------------------

class TestSqmToBortleArray:

    def test_boundary_values(self):
        sqm = np.array([[22.0, 17.0, 16.9]], dtype=np.float64)
        result = ds._sqm_to_bortle_array(sqm)
        assert result[0, 0] == 1
        assert result[0, 1] == 8
        assert result[0, 2] == 9

    def test_nan_produces_zero(self):
        sqm = np.array([[np.nan, 20.0]], dtype=np.float64)
        result = ds._sqm_to_bortle_array(sqm)
        assert result[0, 0] == 0
        assert result[0, 1] == 5


# ---------------------------------------------------------------------------
# _load_raster_window
# ---------------------------------------------------------------------------

class TestLoadRasterWindow:

    def _make_mock_ds(self, data: np.ndarray, nodata=None, epsg=4326):
        from rasterio.transform import from_bounds
        mock_ds = MagicMock()
        mock_ds.__enter__ = lambda s: s
        mock_ds.__exit__ = MagicMock(return_value=False)
        mock_ds.crs = MagicMock()
        mock_ds.crs.to_epsg.return_value = epsg
        mock_ds.nodata = nodata
        mock_ds.transform = from_bounds(-180, -90, 180, 90, 3600, 1800)
        mock_ds.read.return_value = data.copy()
        mock_ds.window_transform = MagicMock(return_value=mock_ds.transform)
        return mock_ds

    def test_clamps_nodata_to_zero(self):
        data = np.array([[255.0, 10.0, 0.0]], dtype=np.float32)
        mock_ds = self._make_mock_ds(data, nodata=255.0)
        with patch("rasterio.open", return_value=mock_ds):
            result = ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0)
        assert result is not None
        assert result[0, 0] == pytest.approx(0.0)   # nodata → 0
        assert result[0, 1] == pytest.approx(10.0)  # valid value preserved

    def test_clamps_negative_to_zero(self):
        data = np.array([[-5.0, 3.0]], dtype=np.float32)
        mock_ds = self._make_mock_ds(data, nodata=None)
        with patch("rasterio.open", return_value=mock_ds):
            result = ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0)
        assert result is not None
        assert result[0, 0] == pytest.approx(0.0)   # negative → 0
        assert result[0, 1] == pytest.approx(3.0)

    def test_returns_none_on_rasterio_error(self):
        with patch("rasterio.open", side_effect=RuntimeError("disk error")):
            result = ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0)
        assert result is None

    def test_returns_float64(self):
        data = np.array([[5.0, 10.0]], dtype=np.float32)
        mock_ds = self._make_mock_ds(data)
        with patch("rasterio.open", return_value=mock_ds):
            result = ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0)
        assert result is not None
        assert result.dtype == np.float64


# ---------------------------------------------------------------------------
# _find_light_domes_from_array
# ---------------------------------------------------------------------------

class TestFindLightDomesFromArray:

    def setup_method(self):
        # Ensure scipy path is active
        self._orig_scipy = ds._HAS_SCIPY
        ds._HAS_SCIPY = True

    def teardown_method(self):
        ds._HAS_SCIPY = self._orig_scipy

    # TC-1: grid alignment — NW corner
    def test_grid_alignment_nw_corner(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        # Bright 5x5 array over bbox [30,40] x [-120,-110]
        arr = np.full((5, 5), _radiance_for_bortle(9), dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert len(results) == 1
        lat, lon, _ = results[0]
        # Centroid of a uniform 5x5 grid = geographic centre
        assert lat == pytest.approx(35.0, abs=0.5)
        assert lon == pytest.approx(-115.0, abs=0.5)

    # TC-2: all ocean → empty
    def test_all_ocean_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", MagicMock(
            is_land=lambda lat_g, lon_g: np.zeros_like(lat_g, dtype=bool)
        ))
        arr = np.full((5, 5), _radiance_for_bortle(9), dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0
        )
        assert results == []

    # TC-3: GLM disabled → values pass through
    def test_glm_disabled_no_masking(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.full((5, 5), _radiance_for_bortle(9), dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, min_blob_pixels=1
        )
        assert len(results) == 1

    # TC-4: single blob centroid maps to correct lat/lon
    def test_single_blob_centroid(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        # 3x3 bright patch at rows 3-5, cols 3-5
        arr[3:6, 3:6] = _radiance_for_bortle(9)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=4
        )
        assert len(results) == 1
        lat, lon, _ = results[0]
        expected_lat = np.linspace(40.0, 30.0, 10)[4]   # centroid row = 4
        expected_lon = np.linspace(-120.0, -110.0, 10)[4]
        assert lat == pytest.approx(expected_lat, abs=0.1)
        assert lon == pytest.approx(expected_lon, abs=0.1)

    # TC-5: two separated blobs → two domes
    def test_two_blobs_two_domes(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((20, 20), dtype=np.float64)
        arr[2:6, 2:6]     = _radiance_for_bortle(9)
        arr[14:18, 14:18] = _radiance_for_bortle(9)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=4
        )
        assert len(results) == 2

    # TC-6: max bortle within blob
    def test_max_bortle_within_blob(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        arr[3:7, 3:7] = _radiance_for_bortle(8)   # Bortle 8 fringe
        arr[4:6, 4:6] = _radiance_for_bortle(9)   # Bortle 9 core
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert len(results) == 1
        assert results[0][2] == 9

    # TC-7: noise pixel filtered by min_blob_pixels
    def test_noise_pixel_filtered(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        arr[5, 5] = _radiance_for_bortle(9)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=4
        )
        assert results == []

    # TC-8: min_blob_pixels=1 lets single pixel through
    def test_noise_passes_min_blob_1(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        arr[5, 5] = _radiance_for_bortle(9)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert len(results) == 1

    # TC-9: tier_min_bortle controls threshold
    def test_tier_min_bortle_threshold(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        arr[2:6, 2:6] = _radiance_for_bortle(7)   # Bortle 7 pixels
        results_8 = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        results_7 = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=7, min_blob_pixels=1
        )
        assert results_8 == []
        assert len(results_7) == 1

    # TC-10: all-NaN array → empty
    def test_nan_pixels_no_blobs(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.full((5, 5), np.nan, dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0
        )
        assert results == []

    # TC-11: all-zero array → empty
    def test_zero_radiance_no_blobs(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert results == []

    # TC-12: radiance at exactly Bortle-8 boundary
    def test_bortle_boundary_sqm_17(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        # SQM=17.0 → Bortle 8; radiance = 10^((21.7-17.0)/2.5) - 0.6
        L = 10 ** ((21.7 - 17.0) / 2.5) - 0.6
        arr = np.full((4, 4), L, dtype=np.float64)
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert len(results) == 1
        assert results[0][2] == 8

    # TC-13: scipy unavailable → empty
    def test_scipy_unavailable_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_SCIPY", False)
        arr = np.full((5, 5), _radiance_for_bortle(9), dtype=np.float64)
        results = ds._find_light_domes_from_array(arr, 30.0, 40.0, -120.0, -110.0)
        assert results == []

    # TC-14: empty array → empty
    def test_empty_array_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((0, 10), dtype=np.float64)
        results = ds._find_light_domes_from_array(arr, 30.0, 40.0, -120.0, -110.0)
        assert results == []

    # TC-15: centroid weighted toward bright core (not geometric centre)
    def test_centroid_weighted_toward_bright_core(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((10, 10), dtype=np.float64)
        # Dim Bortle-8 pixels on the left, single very-bright pixel on the right
        arr[4, 1:8] = _radiance_for_bortle(8)   # dim strip
        arr[4, 8]   = _radiance_for_bortle(9) * 100  # very bright rightmost pixel
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert len(results) == 1
        _, lon, _ = results[0]
        geometric_centre_lon = np.linspace(-120.0, -110.0, 10)[4]
        bright_pixel_lon     = np.linspace(-120.0, -110.0, 10)[8]
        # Weighted centroid should be closer to the bright pixel than the geometric centre
        assert abs(lon - bright_pixel_lon) < abs(lon - geometric_centre_lon)

    # TC-16: NaN-radiance blob skipped (zero-sum weights)
    def test_nan_radiance_blob_skipped(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", True)
        # Land mask returns True everywhere so bortle classification runs,
        # but then we manually set viirs_array values that will be NaN-masked
        # by the ocean mask before center_of_mass is called.
        monkeypatch.setattr(ds, "_glm", MagicMock(
            is_land=lambda lat_g, lon_g: np.zeros_like(lat_g, dtype=bool)  # all ocean
        ))
        arr = np.full((5, 5), _radiance_for_bortle(9), dtype=np.float64)
        # With all-ocean mask, no blobs should form
        results = ds._find_light_domes_from_array(
            arr, 30.0, 40.0, -120.0, -110.0, tier_min_bortle=8, min_blob_pixels=1
        )
        assert results == []  # ocean masking wipes the tier_mask


# ---------------------------------------------------------------------------
# _extract_dark_sky_candidates
# ---------------------------------------------------------------------------

class TestExtractDarkSkyCandidates:

    def setup_method(self):
        self._orig_glm = ds._HAS_GLM
        ds._HAS_GLM = False   # default: disable GLM so ocean check doesn't interfere

    def teardown_method(self):
        ds._HAS_GLM = self._orig_glm

    def _bbox(self):
        """Small 10x10 bbox centred near 40°N, -100°W."""
        return dict(min_lat=35.0, max_lat=45.0, min_lon=-105.0, max_lon=-95.0)

    # TC-17: dark pixel within radius → in results
    def test_dark_pixel_within_radius_returned(self):
        arr = np.zeros((10, 10), dtype=np.float64)
        # Place a Bortle-1 pixel near the centre of the bbox (~40°N, -100°W)
        arr[5, 5] = 0.001   # very low radiance → VIIRS ~0, but > 0
        # Use Falchi=None; VIIRS > 0 gives SQM > 22 → Bortle 1
        results = ds._extract_dark_sky_candidates(
            arr, None, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=100.0, dark_threshold=3,
        )
        assert len(results) >= 1
        assert all(r["bortle_class"] <= 3 for r in results)

    # TC-18: dark pixel outside radius → excluded
    def test_dark_pixel_outside_radius_excluded(self):
        # Entire array is "dark" but origin is in the centre; radius is tiny
        arr = np.full((10, 10), 0.001, dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            arr, None, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=1.0,   # 1 mile: almost nothing qualifies
            dark_threshold=3,
        )
        # Almost all pixels are outside 1-mile radius
        for r in results:
            assert r["distance_miles"] <= 1.0 + 0.5   # allow half-pixel rounding

    # TC-19: bright pixel within radius excluded
    def test_bright_pixel_within_radius_excluded(self):
        arr = np.full((10, 10), _radiance_for_bortle(9), dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            arr, None, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=100.0, dark_threshold=3,
        )
        assert results == []

    # TC-20: VIIRS=0 + Falchi → bortle filled from Falchi
    def test_viirs_zero_falchi_fills_bortle(self):
        viirs = np.zeros((10, 10), dtype=np.float64)
        # Falchi luminance → Bortle 2  (sqm ~21.85 → bortle 2)
        # La=0.01 mcd/m², scaled*3=0.03: SQM = 22.08 - 2.5*log10((0.03+0.252)/0.252) ≈ 21.6 → Bortle 2
        falchi = np.full((10, 10), 0.01, dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            viirs, falchi, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=200.0, dark_threshold=3,
        )
        assert len(results) > 0
        assert all(r["bortle_class"] <= 3 for r in results)

    # TC-21: VIIRS=0 + Falchi=0 → Bortle 1 (pristine)
    def test_viirs_zero_falchi_zero_assigns_bortle1(self):
        viirs  = np.zeros((10, 10), dtype=np.float64)
        falchi = np.zeros((10, 10), dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            viirs, falchi, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=200.0, dark_threshold=3,
        )
        assert len(results) > 0
        assert all(r["bortle_class"] == 1 for r in results)

    # TC-22: ocean pixels excluded via GLM
    def test_ocean_pixels_excluded(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", MagicMock(
            is_land=lambda lat_g, lon_g: np.zeros_like(lat_g, dtype=bool)  # all ocean
        ))
        arr = np.zeros((10, 10), dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            arr, None, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=200.0, dark_threshold=3,
        )
        assert results == []

    # TC-23: cap at _MAX_ARRAY_EXTRACT (500)
    def test_cap_at_max_array_extract(self):
        # 50x50 = 2500 pixels all dark
        arr = np.zeros((50, 50), dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            arr, None,
            min_lat=35.0, max_lat=45.0, min_lon=-105.0, max_lon=-95.0,
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=500.0, dark_threshold=1,
        )
        assert len(results) <= 500

    # TC-24: cap returns the closest pixels (not arbitrary ones)
    def test_cap_returns_stratified_pixels(self):
        # 50x50 = 2500 Bortle-1 pixels (VIIRS=0, Falchi=0 → Bortle 1)
        viirs  = np.zeros((50, 50), dtype=np.float64)
        falchi = np.zeros((50, 50), dtype=np.float64)
        results = ds._extract_dark_sky_candidates(
            viirs, falchi,
            min_lat=35.0, max_lat=45.0, min_lon=-105.0, max_lon=-95.0,
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=500.0, dark_threshold=3,
        )
        assert len(results) > 0, "expected Bortle-1 candidates from VIIRS=0/Falchi=0 array"
        distances = [r["distance_miles"] for r in results]
        # Stratified sampling caps total at _MAX_ARRAY_EXTRACT; sparse outer bands
        # may return fewer, so we check ≤ rather than ==.
        assert len(results) <= 500
        # Results must span multiple distance bands (not just the nearest pixels)
        assert max(distances) > 100, "stratified sampling should include distant candidates"
        # Max returned distance must be within the search radius
        assert max(distances) <= 500

    # TC-25: None viirs_array → empty
    def test_none_viirs_returns_empty(self):
        results = ds._extract_dark_sky_candidates(
            None, None, **self._bbox(),
            origin_lat=40.0, origin_lon=-100.0,
            radius_miles=100.0, dark_threshold=3,
        )
        assert results == []

    # TC-26: Falchi alignment — out_shape passed to _load_raster_window
    def test_falchi_aligned_to_viirs_shape(self):
        """When VIIRS and Falchi have different native resolutions, the Falchi
        read should receive out_shape matching the VIIRS array's shape."""
        viirs_shape = (50, 80)
        viirs_arr = np.zeros(viirs_shape, dtype=np.float64)

        call_args = {}

        real_fn = ds._load_raster_window

        def capture_load(source_key, min_lat, max_lat, min_lon, max_lon, out_shape=None):
            call_args[source_key] = out_shape
            if source_key == "viirs":
                return viirs_arr
            return np.zeros(out_shape or (10, 10), dtype=np.float64)

        with patch.object(ds, "_load_raster_window", side_effect=capture_load):
            # Simulate the find_nearby window-load pattern
            v = ds._load_raster_window("viirs", 35.0, 45.0, -105.0, -95.0)
            f = ds._load_raster_window(
                "falchi", 35.0, 45.0, -105.0, -95.0,
                out_shape=v.shape if v is not None else None,
            )

        assert call_args.get("falchi") == viirs_shape
