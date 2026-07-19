"""
Build the DarkHours light-dome H3 index (cache/lightdome_h3.npz).

Light dome is a pure function of static VIIRS radiance + location, so it is precomputed
once here and served as an O(log n) lookup on the initial page-load path — no 150-mile
raster read at request time. Mirrors the PAD-US / OSM POI index pattern.

The build runs LOCALLY against the on-disk VIIRS grid (PYNIGHTSKY_BACKEND=local, memmap,
no S3). For each H3 res-6 cell (~36 km^2) covering CONUS it computes the per-direction
light-dome scores, dome heights, and glow-weighted mean distances, and stores them raw —
``summarize_horizons`` runs at *lookup* time, so a threshold recalibration needs no rebuild.

--- USAGE ---

    # full CONUS index (committed; the Docker images copy it)
    python scripts/build_lightdome_index.py

    # a small regional index for verification (e.g. AZ/UT)
    python scripts/build_lightdome_index.py --min-lat 33 --max-lat 42 \
        --min-lon -117 --max-lon -110 --out /tmp/ld_az.npz

--- RUNTIME LOOKUP ---

    Loaded once per process and queried via light_dome.load_lightdome_index /
    light_dome.lightdome_lookup (columnar np.searchsorted).
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import h3
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYNIGHTSKY_BACKEND", "local")
from darkhours.light_dome import DIRS_8, LightDomeAnalyzer  # noqa: E402

RESOLUTION = 6                              # ~36.1 km^2/cell — matches the runtime lookup
CONUS = (24.4, 49.4, -125.0, -66.9)         # min_lat, max_lat, min_lon, max_lon
_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "cache" / "lightdome_h3.npz"

_analyzer: LightDomeAnalyzer | None = None


def _init(resolution_deg: float) -> None:
    """Pool worker init: build one analyzer per process (kernels amortise across cells)."""
    global _analyzer
    _analyzer = LightDomeAnalyzer(resolution_deg=resolution_deg) if resolution_deg else LightDomeAnalyzer()


def _compute(cell_int: int):
    """Return (cell, scores[8], heights[8], distances[8]) or None for ocean/no-data cells."""
    lat, lon = h3.cell_to_latlng(h3.int_to_str(cell_int))
    det = _analyzer.analyze_detailed(lat, lon)
    scores = np.array([det[d]["score"] for d in DIRS_8], dtype=np.float32)
    if float(scores.max()) <= 0.0:
        return None                          # all-zero window → ocean / outside data
    heights = np.array([det[d]["dome_height_deg"] for d in DIRS_8], dtype=np.float32)
    dists = np.array(
        [-1.0 if det[d]["mean_distance_mi"] is None else det[d]["mean_distance_mi"] for d in DIRS_8],
        dtype=np.float32,
    )
    return cell_int, scores, heights, dists


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the light-dome H3 index.")
    ap.add_argument("--min-lat", type=float, default=CONUS[0])
    ap.add_argument("--max-lat", type=float, default=CONUS[1])
    ap.add_argument("--min-lon", type=float, default=CONUS[2])
    ap.add_argument("--max-lon", type=float, default=CONUS[3])
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--resolution-deg", type=float, default=0.0,
                    help="analyzer pixel size (0 = VIIRS native; coarser = faster, scores are "
                         "resolution-independent). Keep 0 to match the live analyzer exactly.")
    args = ap.parse_args()

    poly = box(args.min_lon, args.min_lat, args.max_lon, args.max_lat)
    cell_ints = sorted(h3.str_to_int(c) for c in h3.geo_to_cells(mapping(poly), RESOLUTION))
    print(f"[build] {len(cell_ints)} H3 res-{RESOLUTION} cells over "
          f"lat[{args.min_lat},{args.max_lat}] lon[{args.min_lon},{args.max_lon}]; "
          f"{args.workers} workers")

    t0 = time.time()
    with Pool(args.workers, initializer=_init, initargs=(args.resolution_deg,)) as pool:
        results = pool.map(_compute, cell_ints, chunksize=64)
    rows = [r for r in results if r is not None]
    rows.sort(key=lambda r: r[0])
    dt = time.time() - t0

    cells = np.array([r[0] for r in rows], dtype=np.uint64)
    scores = np.array([r[1] for r in rows], dtype=np.float16)
    heights = np.array([r[2] for r in rows], dtype=np.float16)
    dists = np.array([r[3] for r in rows], dtype=np.float16)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, cells=cells, scores=scores,
                        dome_heights=heights, mean_distances=dists)
    size_mb = args.out.stat().st_size / 1e6
    print(f"[build] kept {len(rows)}/{len(cell_ints)} land cells "
          f"({len(cell_ints) - len(rows)} ocean/no-data skipped) in {dt:.0f}s")
    print(f"[build] wrote {args.out} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
