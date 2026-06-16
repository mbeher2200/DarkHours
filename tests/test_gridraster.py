"""Contract tests for the pure-Python tiled-grid reader (gridraster.GridArray).

Hermetic: builds an in-memory grid from a synthetic numpy array (tiled exactly as
gridbuild writes it) and backs GridArray with an in-memory read_elems — no files,
no rasterio, no S3. Covers the contract that used to live in the rasterio path:
nodata/negative clamp, north-up orientation, boundless 0.0 fill, float64 output,
single-pixel exactness, multi-tile assembly, out_shape bilinear, and None-on-error.
"""
import numpy as np
import pytest

from PyNightSkyPredictor import gridraster


def make_grid(arr, *, nodata=None, tile=4,
              west=-180.0, north=90.0, x_res=1.0, y_res=1.0, fail=False):
    """Build an in-memory GridArray over `arr` (H×W) tiled into `tile`×`tile` blocks,
    row-major, edge-padded with 0.0 — the same layout gridbuild produces."""
    arr = np.asarray(arr)
    H, W = arr.shape
    dtype = arr.dtype
    tiles_x = (W + tile - 1) // tile
    tiles_y = (H + tile - 1) // tile
    padded = np.zeros((tiles_y * tile, tiles_x * tile), dtype=dtype)
    padded[:H, :W] = arr
    buf = np.empty(tiles_x * tiles_y * tile * tile, dtype=dtype)
    k = 0
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            block = padded[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
            buf[k:k + tile * tile] = block.ravel()
            k += tile * tile
    meta = dict(dataset="t", width=W, height=H, tile_size=tile, tiles_x=tiles_x,
                tiles_y=tiles_y, tile_bytes=tile * tile * dtype.itemsize,
                dtype=dtype.name, nodata=nodata, fill=0.0,
                west=west, north=north, x_res=x_res, y_res=y_res, bin_bytes=buf.nbytes)

    def read_elems(off, n):
        if fail:
            raise RuntimeError("simulated read error")
        return buf[off:off + n]

    return gridraster.GridArray(meta, read_elems)


# value = row*100 + col, north-up grid spanning lon[-180,..], lat[90,..]
def _ramp(H, W, dtype=np.float32):
    return np.fromfunction(lambda r, c: r * 100 + c, (H, W)).astype(dtype)


def _coord(g, row, col):
    """Pixel-center lon/lat for (row, col)."""
    return g.north - (row + 0.5) * g.y_res, g.west + (col + 0.5) * g.x_res


# ── sample ────────────────────────────────────────────────────────────────────

def test_sample_exact_every_pixel_across_tiles():
    g = make_grid(_ramp(5, 6), tile=4)          # 2×2 tiles, exercises seams
    for r in range(5):
        for c in range(6):
            lat, lon = _coord(g, r, c)
            assert g.sample(lat, lon) == pytest.approx(r * 100 + c)


def test_sample_out_of_bounds_returns_zero():
    g = make_grid(_ramp(4, 4))
    assert g.sample(89.9, 179.9) == 0.0          # far outside the grid extent


def test_sample_nodata_and_negative_clamped():
    arr = np.array([[255.0, -7.0, 9.0]], dtype=np.float32)
    g = make_grid(arr, nodata=255.0)
    lat, lon = _coord(g, 0, 0); assert g.sample(lat, lon) == 0.0   # nodata → 0
    lat, lon = _coord(g, 0, 1); assert g.sample(lat, lon) == 0.0   # negative → 0
    lat, lon = _coord(g, 0, 2); assert g.sample(lat, lon) == pytest.approx(9.0)


def test_sample_none_on_read_error():
    g = make_grid(_ramp(4, 4), fail=True)
    lat, lon = _coord(g, 1, 1)        # in-bounds → triggers a read → error → None
    assert g.sample(lat, lon) is None


# ── read_window ─────────────────────────────────────────────────────────────--

def test_window_orientation_and_dtype():
    arr = _ramp(5, 6)
    g = make_grid(arr, tile=4)
    out = g.read_window(g.north - 5 * g.y_res, g.north, g.west, g.west + 6 * g.x_res)
    assert out.dtype == np.float64
    assert out.shape == (5, 6)
    assert out[0, 0] == arr[0, 0]                 # row 0 = north
    assert np.allclose(out, arr)


def test_window_boundless_fill_zero_beyond_edges():
    g = make_grid(_ramp(4, 4), tile=4)
    # request a window that extends north/west of the grid → padded with 0.0
    out = g.read_window(g.north - 2 * g.y_res, g.north + 2 * g.y_res,
                        g.west - 2 * g.x_res, g.west + 2 * g.x_res)
    assert out.shape == (4, 4)
    assert out[0, 0] == 0.0 and out[0, 1] == 0.0  # north/west overhang filled 0


def test_window_clamps_nodata_and_negative():
    arr = np.array([[255.0, 10.0], [-3.0, 4.0]], dtype=np.float32)
    g = make_grid(arr, nodata=255.0, tile=4)
    out = g.read_window(g.north - 2 * g.y_res, g.north, g.west, g.west + 2 * g.x_res)
    assert out[0, 0] == 0.0 and out[1, 0] == 0.0
    assert out[0, 1] == pytest.approx(10.0) and out[1, 1] == pytest.approx(4.0)


def test_window_none_on_read_error():
    g = make_grid(_ramp(8, 8), tile=4, fail=True)
    assert g.read_window(g.north - 8 * g.y_res, g.north, g.west, g.west + 8 * g.x_res) is None


def test_window_out_shape_resamples():
    g = make_grid(_ramp(8, 8), tile=4)
    out = g.read_window(g.north - 8 * g.y_res, g.north, g.west, g.west + 8 * g.x_res,
                        out_shape=(4, 4))
    assert out.shape == (4, 4)
