#!/usr/bin/env python3
"""Correctness gate: compare the pure-Python GridArray reader against rasterio for
both operations, over the source GeoTIFF, before any runtime wiring.

  sample()      vs rasterio single-pixel read  — must match exactly (same pixel).
  read_window() vs rasterio windowed read       — compared over the overlapping
                pixel region (a ±1px window-edge difference vs from_bounds rounding
                is expected; values inside must match).

Usage:
    python scripts/validate_grid_vs_rasterio.py world_atlas_2016.tif out/world_atlas_2016 -n 2000
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np

from darkhours import gridraster


def _rasterio_sample(ds, lat, lon):
    value = float(list(ds.sample([(lon, lat)]))[0][0])
    if ds.nodata is not None and abs(value - ds.nodata) < 1.0:
        return 0.0
    return max(value, 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path, help="source GeoTIFF (EPSG:4326)")
    ap.add_argument("prefix", type=Path, help="built grid prefix (.json/.bin)")
    ap.add_argument("-n", type=int, default=2000, help="random sample points")
    ap.add_argument("-w", type=int, default=50, help="random windows")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import rasterio
    random.seed(args.seed)

    grid = gridraster.open_local(args.prefix)
    with rasterio.open(args.src) as ds:
        b = ds.bounds
        # ── sample() ──────────────────────────────────────────────────────────
        worst = 0.0
        mism = 0
        for _ in range(args.n):
            lat = random.uniform(b.bottom + 1e-4, b.top - 1e-4)
            lon = random.uniform(b.left + 1e-4, b.right - 1e-4)
            got = grid.sample(lat, lon)
            exp = _rasterio_sample(ds, lat, lon)
            d = abs(got - exp)
            worst = max(worst, d)
            if d > 1e-3:
                mism += 1
                if mism <= 5:
                    print(f"  MISMATCH ({lat:.4f},{lon:.4f}): grid={got} rasterio={exp}")
        print(f"sample(): {args.n} pts, {mism} mismatches, max|Δ|={worst:.6g}")

        # ── read_window() over the overlap ────────────────────────────────────
        from rasterio.windows import from_bounds as _fb
        wworst = 0.0
        for _ in range(args.w):
            span = random.uniform(0.2, 2.0)
            min_lat = random.uniform(b.bottom, b.top - span)
            min_lon = random.uniform(b.left, b.right - span)
            max_lat, max_lon = min_lat + span, min_lon + span
            g = grid.read_window(min_lat, max_lat, min_lon, max_lon)

            win = _fb(min_lon, min_lat, max_lon, max_lat, transform=ds.transform)
            r = ds.read(1, window=win, boundless=True, fill_value=0.0).astype(np.float64)
            if ds.nodata is not None:
                r = np.where(np.abs(r - ds.nodata) < 1.0, 0.0, r)
            r = np.where(r < 0.0, 0.0, r)

            h = min(g.shape[0], r.shape[0]); w = min(g.shape[1], r.shape[1])
            d = np.abs(g[:h, :w] - r[:h, :w]).max() if h and w else 0.0
            wworst = max(wworst, float(d))
        print(f"read_window(): {args.w} windows, max|Δ| over overlap = {wworst:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
