#!/usr/bin/env python3
"""Build-time half of the rasterio-free raster path: convert an EPSG:4326 GeoTIFF
into the tiled raw-binary grid (``<prefix>.bin`` + ``<prefix>.json``) that
``gridraster`` reads at runtime with only numpy + boto3.

``rasterio`` is imported **lazily inside** :func:`build` so importing this module
costs nothing and never pulls GDAL into a runtime that only reads grids.  This
module is used in two places, both build-time / local-only:
  * ``scripts/build_raster_grid.py`` — the offline CLI.
  * ``darksky.LocalRasterSource`` — builds a grid on first CLI use from the
    downloaded raw GeoTIFF (the local backend keeps rasterio as a build dep; the
    Lambda image does not).

Layout — fixed TILE×TILE tiles, row-major tile order, edge tiles zero-padded so
every tile is exactly ``tile_size**2 * itemsize`` bytes.  That makes a pixel's
byte offset pure arithmetic at read time, with no offset index::

    tile_id  = ty * tiles_x + tx
    byte_off = tile_id * TILE_BYTES + (row_in_tile * TILE + col_in_tile) * itemsize

Values are written unmodified (nodata/negative clamping is a runtime policy, so the
.bin stays a faithful copy of the source band); out-of-raster padding is 0.0 to
match the legacy rasterio read path's ``boundless=True, fill_value=0.0``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

TILE_DEFAULT = 512


def build(src_path: str | Path, out_prefix: str | Path, dataset: str,
          tile: int = TILE_DEFAULT, *, show_progress: bool = True) -> dict:
    """Build ``<out_prefix>.bin`` + ``.json`` from ``src_path`` (EPSG:4326, north-up).

    Returns the metadata dict.  Raises ``ValueError`` for a non-4326 or rotated
    source (reproject to 4326 at build time — the runtime has no warp engine)."""
    import rasterio
    from rasterio.windows import Window

    src_path, out_prefix = Path(src_path), Path(out_prefix)

    with rasterio.open(src_path) as ds:
        epsg = ds.crs.to_epsg() if ds.crs else None
        if epsg != 4326:
            raise ValueError(
                f"{src_path.name}: CRS is EPSG:{epsg}, not 4326. Reproject to 4326 "
                f"at build time before tiling — the runtime has no warp engine."
            )
        t = ds.transform
        if t.b != 0 or t.d != 0:
            raise ValueError(f"{src_path.name}: raster is rotated/sheared; only axis-aligned grids supported.")

        W, H = ds.width, ds.height
        west, north = float(t.c), float(t.f)          # outer-edge origin of pixel (0,0)
        x_res, y_res = float(t.a), float(-t.e)
        if y_res <= 0:
            raise ValueError(f"{src_path.name}: expected north-up raster (transform.e < 0).")

        dtype = np.dtype(ds.dtypes[0])
        nodata = None if ds.nodata is None else float(ds.nodata)
        tiles_x = (W + tile - 1) // tile
        tiles_y = (H + tile - 1) // tile
        tile_bytes = tile * tile * dtype.itemsize

        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        bin_path = out_prefix.with_suffix(".bin")
        if show_progress:
            print(f"{src_path.name}: {W}x{H} {dtype} EPSG:4326  ->  "
                  f"{tiles_x}x{tiles_y} tiles of {tile}^2 ({tile_bytes} B each)")

        t0 = time.time()
        # One full-width strip of `tile` rows per read; pad to (tile, tiles_x*tile)
        # with 0.0, slice each column block. Touches each source row once.
        with open(bin_path, "wb") as f:
            for ty in range(tiles_y):
                row0 = ty * tile
                h = min(tile, H - row0)
                strip = ds.read(1, window=Window(0, row0, W, h))
                padded = np.zeros((tile, tiles_x * tile), dtype=dtype)
                padded[:h, :W] = strip
                for tx in range(tiles_x):
                    block = padded[:, tx * tile:(tx + 1) * tile]
                    f.write(np.ascontiguousarray(block).tobytes())
                if show_progress and (ty % 10 == 0 or ty == tiles_y - 1):
                    print(f"\r  tile-row {ty + 1}/{tiles_y}  ({(ty + 1) / tiles_y * 100:.0f}%)  "
                          f"{bin_path.stat().st_size >> 20} MB", end="", flush=True)
        if show_progress:
            print()

    meta = {
        "dataset": dataset, "width": W, "height": H,
        "tile_size": tile, "tiles_x": tiles_x, "tiles_y": tiles_y,
        "tile_bytes": tile_bytes, "dtype": dtype.name, "nodata": nodata, "fill": 0.0,
        "west": west, "north": north, "x_res": x_res, "y_res": y_res,
        "bin_bytes": tiles_x * tiles_y * tile_bytes,
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    if show_progress:
        print(f"  wrote {bin_path} ({bin_path.stat().st_size >> 20} MB) + "
              f"{out_prefix.with_suffix('.json').name}  in {time.time() - t0:.0f}s")
    return meta
