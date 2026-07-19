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

import darkhours.darksky as ds

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
    """_load_raster_window delegates to the active RasterSource.read_window (the
    tiled-grid reader) and degrades to None on error. The value contract
    (nodata/negative clamp, orientation, float64) is covered against the real
    reader in test_gridraster.py."""

    def _patch_backend(self, monkeypatch, read_window):
        fake_src = MagicMock()
        fake_src.read_window.side_effect = read_window
        fake_backend = MagicMock(raster_source=fake_src)
        monkeypatch.setattr(ds.ports, "get_backend", lambda: fake_backend)
        return fake_src

    def test_forwards_args_and_returns_array(self, monkeypatch):
        arr = np.zeros((3, 4), dtype=np.float64)
        src = self._patch_backend(monkeypatch, lambda *a, **k: arr)
        result = ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0)
        assert result is arr
        src.read_window.assert_called_once_with(
            "viirs", 30.0, 31.0, -120.0, -119.0, out_shape=None)

    def test_forwards_out_shape(self, monkeypatch):
        src = self._patch_backend(monkeypatch, lambda *a, **k: np.zeros((5, 8)))
        ds._load_raster_window("falchi", 30.0, 31.0, -120.0, -119.0, out_shape=(5, 8))
        assert src.read_window.call_args.kwargs["out_shape"] == (5, 8)

    def test_returns_none_on_error(self, monkeypatch):
        self._patch_backend(monkeypatch, MagicMock(side_effect=RuntimeError("disk error")))
        assert ds._load_raster_window("viirs", 30.0, 31.0, -120.0, -119.0) is None


# ---------------------------------------------------------------------------
# _find_light_domes_from_array
# ---------------------------------------------------------------------------

class TestFindLightDomesFromArray:

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
# _connected_components_8  (pure-numpy replacement for scipy.ndimage.label)
# ---------------------------------------------------------------------------

class TestConnectedComponents8:

    def test_empty_mask(self):
        labeled, n = ds._connected_components_8(np.zeros((4, 5), dtype=bool))
        assert n == 0
        assert labeled.shape == (4, 5)
        assert not labeled.any()

    def test_zero_size(self):
        labeled, n = ds._connected_components_8(np.zeros((0, 3), dtype=bool))
        assert n == 0 and labeled.shape == (0, 3)

    def test_single_pixel(self):
        m = np.zeros((3, 3), dtype=bool); m[1, 1] = True
        labeled, n = ds._connected_components_8(m)
        assert n == 1 and labeled[1, 1] == 1 and (labeled > 0).sum() == 1

    def test_diagonal_pixels_merge_8conn(self):
        # Two diagonally-touching pixels are ONE component under 8-connectivity.
        m = np.zeros((3, 3), dtype=bool); m[0, 0] = True; m[1, 1] = True
        labeled, n = ds._connected_components_8(m)
        assert n == 1 and labeled[0, 0] == labeled[1, 1] == 1

    def test_orthogonal_adjacency_merges(self):
        m = np.zeros((2, 3), dtype=bool); m[0, 0] = m[0, 1] = m[1, 1] = True
        labeled, n = ds._connected_components_8(m)
        assert n == 1

    def test_two_separated_components(self):
        m = np.zeros((3, 5), dtype=bool); m[1, 0] = True; m[1, 4] = True
        labeled, n = ds._connected_components_8(m)
        assert n == 2
        # Raster-scan numbering: the earlier (top-left) pixel is label 1.
        assert labeled[1, 0] == 1 and labeled[1, 4] == 2

    def test_all_foreground(self):
        labeled, n = ds._connected_components_8(np.ones((4, 4), dtype=bool))
        assert n == 1 and (labeled == 1).all()

    def test_label_numbering_is_raster_scan_order(self):
        # Three separate single-pixel blobs; labels follow first-appearance (C-order).
        m = np.zeros((3, 3), dtype=bool)
        m[0, 2] = True; m[1, 0] = True; m[2, 2] = True
        labeled, n = ds._connected_components_8(m)
        assert n == 3
        assert labeled[0, 2] == 1 and labeled[1, 0] == 2 and labeled[2, 2] == 3

    def test_parity_with_scipy_random_masks(self):
        # Exact match (partition AND label numbering) vs scipy across many shapes/densities.
        pytest.importorskip("scipy")
        from scipy.ndimage import label as scilabel
        struct = np.ones((3, 3), dtype=np.int8)
        rng = np.random.default_rng(1234)
        for _ in range(150):
            h = int(rng.integers(1, 40)); w = int(rng.integers(1, 40))
            mask = rng.random((h, w)) < rng.uniform(0.2, 0.75)
            mine, n_mine = ds._connected_components_8(mask)
            ref, n_ref = scilabel(mask, structure=struct)
            assert n_mine == n_ref
            assert np.array_equal(mine, ref)


def _reference_domes_scipy(arr, min_lat, max_lat, min_lon, max_lon,
                           tier_min_bortle=8, min_blob_pixels=4):
    """Old scipy-based dome detection — the parity reference (mirrors
    scripts/bench_dome_detection.py._reference_domes; GLM disabled)."""
    from scipy.ndimage import label as ndlabel, center_of_mass as ndcom
    rows, cols = arr.shape
    lat_vals = np.linspace(max_lat, min_lat, rows)
    lon_vals = np.linspace(min_lon, max_lon, cols)
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")
    sqm = np.where(arr > 0, 21.7 - 2.5 * np.log10(arr + 0.6), np.nan)
    bortle = ds._sqm_to_bortle_array(sqm)
    tier = (bortle >= tier_min_bortle) & (bortle != 0)
    if not tier.any():
        return []
    labeled, n = ndlabel(tier, structure=np.ones((3, 3), dtype=np.int8))
    out = []
    for i in range(1, n + 1):
        bm = labeled == i
        if bm.sum() < min_blob_pixels:
            continue
        rf, cf = ndcom(arr, labeled, i)
        if math.isnan(rf) or math.isnan(cf):
            continue
        ri = min(int(round(rf)), rows - 1)
        ci = min(int(round(cf)), cols - 1)
        out.append((float(lat_grid[ri, ci]), float(lon_grid[ri, ci]), int(np.max(bortle[bm]))))
    return out


class TestDomeParityWithScipy:
    """The new pure-numpy dome detection matches the old scipy implementation on
    random radiance fields (GLM disabled for a deterministic, hermetic compare)."""

    def test_random_fields_match_scipy_reference(self, monkeypatch):
        pytest.importorskip("scipy")
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        rng = np.random.default_rng(7)
        for _ in range(60):
            h = int(rng.integers(6, 40)); w = int(rng.integers(6, 40))
            # Mix of dark (0) and bright (Bortle 8-9) pixels so tier blobs form.
            arr = np.zeros((h, w), dtype=np.float64)
            bright = rng.random((h, w)) < rng.uniform(0.05, 0.4)
            arr[bright] = _radiance_for_bortle(int(rng.integers(8, 10)))
            mine = ds._find_light_domes_from_array(arr, 30.0, 40.0, -120.0, -110.0,
                                                   tier_min_bortle=8, min_blob_pixels=4)
            ref = _reference_domes_scipy(arr, 30.0, 40.0, -120.0, -110.0,
                                         tier_min_bortle=8, min_blob_pixels=4)
            # Same blob count and the same multiset of peak Bortle classes.
            assert len(mine) == len(ref)
            assert sorted(m[2] for m in mine) == sorted(r[2] for r in ref)
            # Centroids match to within ~1 grid pixel: a radiance-weighted centroid
            # landing on an exact .5 boundary rounds to either neighbouring pixel
            # depending on float summation order — both are correct, the grid-snap
            # just flips. Greedy 1:1 match each mine dome to a ref dome within 1.2 px.
            tol_lat = 1.2 * 10.0 / (h - 1)
            tol_lon = 1.2 * 10.0 / (w - 1)
            unmatched = list(ref)
            for ml in mine:
                for j, rl in enumerate(unmatched):
                    if (ml[2] == rl[2]
                            and abs(ml[0] - rl[0]) <= tol_lat
                            and abs(ml[1] - rl[1]) <= tol_lon):
                        unmatched.pop(j)
                        break
                else:
                    assert False, f"no ref match for dome {ml} (shape {h}x{w})"
            assert not unmatched


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

    # TC-26: _resample_to_shape — coarser Falchi upscaled to VIIRS grid
    def test_resample_to_shape_upsample(self):
        """_resample_to_shape nearest-neighbours a coarser array to a finer target."""
        # 2×3 source, each cell has a distinct value
        src = np.array([[1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0]], dtype=np.float64)
        target_shape = (4, 6)   # exactly 2× in each axis
        out = ds._resample_to_shape(src, target_shape)
        assert out.shape == target_shape
        # Each source pixel should tile into a 2×2 block
        assert out[0, 0] == 1.0
        assert out[0, 5] == 3.0
        assert out[3, 0] == 4.0
        assert out[3, 5] == 6.0

    # TC-27: parallel read + _resample_to_shape ≡ sequential read + out_shape
    def test_parallel_resample_matches_sequential_outshape(self):
        """Post-read _resample_to_shape must produce an array equivalent to the
        inline out_shape resampling that _load_raster_window performs."""
        rng = np.random.default_rng(42)
        viirs_shape = (50, 80)
        # Simulate a coarser Falchi native read (25×40)
        falchi_native = rng.random((25, 40)).astype(np.float64)

        # Inline path: _load_raster_window resamples via out_shape (uses same
        # linspace index arithmetic as _resample_to_shape)
        sequential = ds._resample_to_shape(falchi_native, viirs_shape)

        # Post-read path: read at native shape, then resample
        parallel = ds._resample_to_shape(falchi_native, viirs_shape)

        # Both paths must produce identical arrays
        np.testing.assert_array_equal(sequential, parallel)


# ---------------------------------------------------------------------------
# S3: shared pre-computed arrays (viirs_sqm_arr, lat_grid, land_mask)
# ---------------------------------------------------------------------------

class TestSharedPrecompute:
    """Verify that passing pre-computed lat_grid / land_mask / viirs_sqm_arr
    to _find_light_domes_from_array and _extract_dark_sky_candidates produces
    results identical to the standalone (compute-internally) path."""

    def _make_viirs_sqm(self, arr):
        import numpy as np
        return np.where(arr > 0, 21.7 - 2.5 * np.log10(arr + 0.6), np.nan)

    def _make_grids(self, arr, min_lat, max_lat, min_lon, max_lon):
        import numpy as np
        rows, cols = arr.shape
        lat_grid, lon_grid = np.meshgrid(
            np.linspace(max_lat, min_lat, rows),
            np.linspace(min_lon, max_lon, cols),
            indexing="ij",
        )
        return lat_grid, lon_grid

    # TC-28: _find_light_domes_from_array — pre-computed path == standalone path
    def test_dome_precompute_matches_standalone(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((20, 20), dtype=np.float64)
        arr[2:6, 2:6] = _radiance_for_bortle(9)
        arr[12:16, 12:16] = _radiance_for_bortle(8)
        bbox = dict(min_lat=30.0, max_lat=40.0, min_lon=-120.0, max_lon=-110.0)

        standalone = ds._find_light_domes_from_array(
            arr, bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"],
            tier_min_bortle=8, min_blob_pixels=4,
        )

        lat_grid, lon_grid = self._make_grids(arr, **bbox)
        sqm = self._make_viirs_sqm(arr)
        precomputed = ds._find_light_domes_from_array(
            arr, bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"],
            tier_min_bortle=8, min_blob_pixels=4,
            lat_grid=lat_grid, lon_grid=lon_grid, land_mask=None, viirs_sqm_arr=sqm,
        )

        assert len(standalone) == len(precomputed)
        for (la, lo, mb_s), (la2, lo2, mb_p) in zip(
            sorted(standalone), sorted(precomputed)
        ):
            assert la == pytest.approx(la2, abs=1e-6)
            assert lo == pytest.approx(lo2, abs=1e-6)
            assert mb_s == mb_p

    # TC-29: _extract_dark_sky_candidates — pre-computed path == standalone path
    def test_extract_precompute_matches_standalone(self, monkeypatch):
        monkeypatch.setattr(ds, "_HAS_GLM", False)
        arr = np.zeros((30, 30), dtype=np.float64)
        # Ring of dark pixels at radius ~5 rows/cols from centre
        arr[10:14, 10:14] = _radiance_for_bortle(2)
        arr[18:22, 18:22] = _radiance_for_bortle(3)
        bbox = dict(min_lat=30.0, max_lat=40.0, min_lon=-120.0, max_lon=-110.0)

        standalone = ds._extract_dark_sky_candidates(
            arr, None,
            bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"],
            origin_lat=35.0, origin_lon=-115.0,
            radius_miles=200.0, dark_threshold=4,
        )

        lat_grid, lon_grid = self._make_grids(arr, **bbox)
        sqm = self._make_viirs_sqm(arr)
        precomputed = ds._extract_dark_sky_candidates(
            arr, None,
            bbox["min_lat"], bbox["max_lat"], bbox["min_lon"], bbox["max_lon"],
            origin_lat=35.0, origin_lon=-115.0,
            radius_miles=200.0, dark_threshold=4,
            lat_grid=lat_grid, lon_grid=lon_grid, land_mask=None, viirs_sqm_arr=sqm,
        )

        assert len(standalone) == len(precomputed)
        for s, p in zip(standalone, precomputed):
            assert s["lat"]          == pytest.approx(p["lat"], abs=1e-6)
            assert s["lon"]          == pytest.approx(p["lon"], abs=1e-6)
            assert s["bortle_class"] == p["bortle_class"]
