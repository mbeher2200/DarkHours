#!/usr/bin/env python3
"""Offline CLI: convert an EPSG:4326 GeoTIFF into a tiled raw-binary grid
(``<prefix>.bin`` + ``<prefix>.json``) for the rasterio-free runtime reader.

Thin wrapper over ``darkhours.gridbuild.build`` (which holds the logic
and is also called by the local backend to build on first use).  See that module
for the on-disk layout.

Usage:
    python scripts/build_raster_grid.py viirs_2025_cog.tif  out/viirs_2025  --dataset viirs
    python scripts/build_raster_grid.py world_atlas_2016_cog.tif out/world_atlas_2016 --dataset falchi
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from darkhours import gridbuild


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="input GeoTIFF (must be EPSG:4326, north-up)")
    ap.add_argument("out_prefix", type=Path, help="output prefix (writes <prefix>.bin + <prefix>.json)")
    ap.add_argument("--dataset", required=True, choices=["viirs", "falchi"], help="logical dataset name")
    ap.add_argument("--tile", type=int, default=gridbuild.TILE_DEFAULT, help=f"tile size (default {gridbuild.TILE_DEFAULT})")
    args = ap.parse_args(argv)

    if not args.src.exists():
        print(f"error: {args.src} not found", file=sys.stderr)
        return 1
    gridbuild.build(args.src, args.out_prefix, args.dataset, args.tile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
