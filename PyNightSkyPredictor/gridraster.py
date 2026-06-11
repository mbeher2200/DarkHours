#!/usr/bin/env python3
"""Pure-Python reader for the tiled raw-binary grids built by
``scripts/build_raster_grid.py`` — the runtime half of removing rasterio/GDAL.

A grid is a ``<prefix>.json`` (affine + tiling params) plus a ``<prefix>.bin``
(fixed TILE×TILE tiles, row-major tile order, edge tiles zero-padded).  Because
every tile is exactly ``tile_size**2 * itemsize`` bytes, any pixel's byte offset
is pure arithmetic — no offset index is stored or needed::

    tile_id  = ty * tiles_x + tx
    elem_off = tile_id * tile_size**2 + row_in_tile * tile_size + col_in_tile

Two access patterns, both served by one ``_read_elems(elem_off, n)`` primitive:

  * ``sample(lat, lon)``  — one pixel.  Locally a memmap slice; on S3 an
    ``itemsize``-byte ranged GET.  Mirrors ``darksky._sample_tif``:
    nodata → 0.0, negatives → 0, out-of-raster → 0.0, ``None`` only on error.
  * ``read_window(...)`` — a bbox sub-array (row 0 = max_lat, col 0 = min_lon),
    boundless-filled with 0.0, nodata/neg clamped, optional bilinear ``out_shape``
    resample.  Mirrors ``darksky._load_raster_window``.  Tiles within a tile-row
    are contiguous in the file, so each tile-row is one ranged read (fetched in
    parallel on S3).

Dependencies: ``numpy`` always; ``boto3`` only for the S3 backend.  No GDAL,
no rasterio, no tifffile.
"""

from __future__ import annotations

import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Tile-row span fan-out for S3 window reads.  A 150-mile window spans at most a
# handful of tile-rows; keep the pool small so concurrent worker Lambdas don't
# stampede S3.
_WINDOW_MAX_WORKERS = int(os.environ.get("PYNIGHTSKY_GRID_WORKERS", "8"))


class GridArray:
    """A tiled raw-binary raster.  Construct via :func:`open_local` / :func:`open_s3`."""

    def __init__(self, meta: dict, read_elems):
        self.meta = meta
        self._read_elems = read_elems          # (elem_off:int, n:int) -> np.ndarray (flat, native dtype)
        self.W = int(meta["width"])
        self.H = int(meta["height"])
        self.tile = int(meta["tile_size"])
        self.tiles_x = int(meta["tiles_x"])
        self.tiles_y = int(meta["tiles_y"])
        self.dtype = np.dtype(meta["dtype"])
        self.nodata = meta.get("nodata")
        self.west = float(meta["west"])
        self.north = float(meta["north"])
        self.x_res = float(meta["x_res"])
        self.y_res = float(meta["y_res"])
        self._tile_elems = self.tile * self.tile

    # ── coordinate ↔ pixel ────────────────────────────────────────────────────
    def _colrow(self, lat: float, lon: float) -> tuple[int, int]:
        """Floor pixel index of the pixel containing (lat, lon); matches rasterio's
        ``ds.index`` (north-up).  Not clamped — caller decides on out-of-bounds."""
        col = math.floor((lon - self.west) / self.x_res)
        row = math.floor((self.north - lat) / self.y_res)
        return row, col

    # ── tile fetch ────────────────────────────────────────────────────────────
    def _read_tile_row_span(self, ty: int, tx0: int, tx1: int) -> np.ndarray:
        """Return rows [ty*tile, ty*tile+tile) × cols [tx0*tile, (tx1+1)*tile) as a
        (tile, n_tiles*tile) float64 array.  One contiguous read (tiles tx0..tx1 in
        row ty are adjacent in the file)."""
        n_tiles = tx1 - tx0 + 1
        first = ty * self.tiles_x + tx0
        flat = self._read_elems(first * self._tile_elems, n_tiles * self._tile_elems)
        tiles = flat.reshape(n_tiles, self.tile, self.tile)        # (n, tile, tile)
        # lay tiles side by side -> (tile, n*tile)
        return np.concatenate(list(tiles), axis=1).astype(np.float64)

    def _read_block(self, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        """Read the in-bounds raster sub-array [r0:r1, c0:c1] (all within the grid)."""
        ty0, ty1 = r0 // self.tile, (r1 - 1) // self.tile
        tx0, tx1 = c0 // self.tile, (c1 - 1) // self.tile
        rows = range(ty0, ty1 + 1)
        if len(rows) > 1 and _WINDOW_MAX_WORKERS > 1:
            with ThreadPoolExecutor(max_workers=min(_WINDOW_MAX_WORKERS, len(rows))) as ex:
                spans = list(ex.map(lambda ty: self._read_tile_row_span(ty, tx0, tx1), rows))
        else:
            spans = [self._read_tile_row_span(ty, tx0, tx1) for ty in rows]
        big = np.vstack(spans)                                     # ((ty1-ty0+1)*tile, n*tile)
        rr0, cc0 = r0 - ty0 * self.tile, c0 - tx0 * self.tile
        return big[rr0:rr0 + (r1 - r0), cc0:cc0 + (c1 - c0)]

    def _clamp(self, arr: np.ndarray) -> np.ndarray:
        if self.nodata is not None:
            arr = np.where(np.abs(arr - self.nodata) < 1.0, 0.0, arr)
        return np.where(arr < 0.0, 0.0, arr)

    # ── public: single pixel ──────────────────────────────────────────────────
    def sample(self, lat: float, lon: float) -> float | None:
        """Value at (lat, lon).  nodata/out-of-bounds → 0.0, negatives → 0,
        ``None`` only on read error.  Equivalent to ``darksky._sample_tif``."""
        try:
            row, col = self._colrow(lat, lon)
            if not (0 <= row < self.H and 0 <= col < self.W):
                return 0.0                                         # rasterio: outside → nodata → 0
            tx, ty = col // self.tile, row // self.tile
            rit, cit = row - ty * self.tile, col - tx * self.tile
            tile_id = ty * self.tiles_x + tx
            elem_off = tile_id * self._tile_elems + rit * self.tile + cit
            value = float(self._read_elems(elem_off, 1)[0])
            if self.nodata is not None and abs(value - self.nodata) < 1.0:
                return 0.0
            return max(value, 0.0)
        except Exception as e:
            log.warning("grid sample failed (%s): %s", self.meta.get("dataset"), e)
            return None

    # ── public: bbox window ───────────────────────────────────────────────────
    def read_window(self, min_lat: float, max_lat: float, min_lon: float, max_lon: float,
                    out_shape: tuple[int, int] | None = None) -> np.ndarray | None:
        """Bbox sub-window as float64.  Row 0 = max_lat (north), col 0 = min_lon
        (west); out-of-raster pixels filled 0.0; nodata/neg clamped; optional
        bilinear resample to ``out_shape``.  Equivalent to
        ``darksky._load_raster_window`` (both datasets are 4326 — no reproject)."""
        try:
            # Match rasterio's `windows.from_bounds` + boundless read: round the
            # window origin and extent to nearest (not floor/ceil), so the returned
            # array is pixel-identical to the old rasterio path. Origin: north/west
            # corner (row 0 = max_lat, col 0 = min_lon).
            col0 = round((min_lon - self.west) / self.x_res)
            row0 = round((self.north - max_lat) / self.y_res)
            out_w = round((max_lon - min_lon) / self.x_res)
            out_h = round((max_lat - min_lat) / self.y_res)
            col1, row1 = col0 + out_w, row0 + out_h
            if out_h <= 0 or out_w <= 0:
                return np.zeros((max(out_h, 0), max(out_w, 0)), dtype=np.float64)

            out = np.zeros((out_h, out_w), dtype=np.float64)        # boundless fill 0.0
            vr0, vr1 = max(row0, 0), min(row1, self.H)
            vc0, vc1 = max(col0, 0), min(col1, self.W)
            if vr1 > vr0 and vc1 > vc0:
                block = self._read_block(vr0, vr1, vc0, vc1)
                out[vr0 - row0:vr1 - row0, vc0 - col0:vc1 - col0] = block

            out = self._clamp(out)
            if out_shape is not None and tuple(out_shape) != out.shape:
                out = _resample_bilinear(out, out_shape)
            return out
        except Exception as e:
            log.warning("grid read_window failed (%s): %s", self.meta.get("dataset"), e)
            return None


def _resample_bilinear(arr: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    """Bilinear resample ``arr`` to ``out_shape`` (numpy-only; half-pixel aligned).
    Approximates GDAL ``Resampling.bilinear`` to within bilinear tolerance — used to
    align Falchi onto the VIIRS pixel grid before the composite fill."""
    src_h, src_w = arr.shape
    dst_h, dst_w = out_shape
    ys = (np.arange(dst_h) + 0.5) * (src_h / dst_h) - 0.5
    xs = (np.arange(dst_w) + 0.5) * (src_w / dst_w) - 0.5
    y0 = np.floor(ys).astype(int); x0 = np.floor(xs).astype(int)
    wy = (ys - y0)[:, None]; wx = (xs - x0)[None, :]
    y0c, y1c = np.clip(y0, 0, src_h - 1), np.clip(y0 + 1, 0, src_h - 1)
    x0c, x1c = np.clip(x0, 0, src_w - 1), np.clip(x0 + 1, 0, src_w - 1)
    Ia, Ib = arr[np.ix_(y0c, x0c)], arr[np.ix_(y0c, x1c)]
    Ic, Id = arr[np.ix_(y1c, x0c)], arr[np.ix_(y1c, x1c)]
    top = Ia * (1 - wx) + Ib * wx
    bot = Ic * (1 - wx) + Id * wx
    return top * (1 - wy) + bot * wy


# ── factories ─────────────────────────────────────────────────────────────────
def open_local(prefix: str | Path) -> GridArray:
    """Open a ``<prefix>.json`` / ``<prefix>.bin`` pair from local disk (memmap)."""
    prefix = Path(prefix)
    meta = json.loads(prefix.with_suffix(".json").read_text())
    mm = np.memmap(prefix.with_suffix(".bin"), dtype=np.dtype(meta["dtype"]), mode="r")

    def read_elems(elem_off: int, n: int) -> np.ndarray:
        return np.asarray(mm[elem_off:elem_off + n])

    return GridArray(meta, read_elems)


def open_s3(bucket: str, key_prefix: str, client=None) -> GridArray:
    """Open a grid stored on S3 as ``{key_prefix}.json`` / ``{key_prefix}.bin``.
    Reads the tiny JSON once; the .bin is range-read on demand (never downloaded)."""
    import boto3

    client = client or boto3.client("s3")
    meta = json.loads(
        client.get_object(Bucket=bucket, Key=f"{key_prefix}.json")["Body"].read()
    )
    dtype = np.dtype(meta["dtype"])
    bin_key = f"{key_prefix}.bin"

    def read_elems(elem_off: int, n: int) -> np.ndarray:
        start = elem_off * dtype.itemsize
        end = start + n * dtype.itemsize - 1                       # HTTP Range is inclusive
        body = client.get_object(Bucket=bucket, Key=bin_key, Range=f"bytes={start}-{end}")["Body"].read()
        return np.frombuffer(body, dtype=dtype)

    return GridArray(meta, read_elems)
