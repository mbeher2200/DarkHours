#!/usr/bin/env python3
"""Profile _find_light_domes_from_array sub-steps on a real 150-mile VIIRS window.

Loads the same window find_nearby builds (dome_search_radius=150 mi) for a bright
origin, times the whole function, then attributes time to each sub-step so we know
which one variable to optimize. Local backend (uses the on-disk VIIRS TIF).
"""
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PyNightSkyPredictor import darksky  # noqa: E402

ORIGINS = [
    ("Los Angeles, CA", 34.0522, -118.2437),
    ("New York, NY", 40.7128, -74.0060),
]


def _reference_domes(arr, min_lat, max_lat, min_lon, max_lon, tier_min_bortle=8,
                     min_blob_pixels=4):
    """The ORIGINAL per-blob-loop implementation, for correctness comparison."""
    import numpy as np
    from scipy.ndimage import label as ndlabel, center_of_mass as ndcom
    rows, cols = arr.shape
    lat_vals = np.linspace(max_lat, min_lat, rows)
    lon_vals = np.linspace(min_lon, max_lon, cols)
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")
    masked = np.where(darksky._glm.is_land(lat_grid, lon_grid), arr, np.nan)
    sqm = np.where(masked > 0, 21.7 - 2.5 * np.log10(masked + 0.6), np.nan)
    bortle = darksky._sqm_to_bortle_array(sqm)
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


def _window_for(lat, lon, radius=150):
    dlat = radius / 69.0
    dlon = radius / max(69.0 * math.cos(math.radians(lat)), 0.01)
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _step_profile(arr, min_lat, max_lat, min_lon, max_lon):
    """Replicate the function's steps with per-step timing (same logic as source)."""
    import numpy as np
    from scipy.ndimage import label as ndlabel, center_of_mass as ndcom
    t = {}

    t0 = time.perf_counter()
    rows, cols = arr.shape
    lat_vals = np.linspace(max_lat, min_lat, rows)
    lon_vals = np.linspace(min_lon, max_lon, cols)
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")
    t["meshgrid"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    masked = np.where(darksky._glm.is_land(lat_grid, lon_grid), arr, np.nan)
    t["is_land mask"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    sqm = np.where(masked > 0, 21.7 - 2.5 * np.log10(masked + 0.6), np.nan)
    bortle = darksky._sqm_to_bortle_array(sqm)
    t["sqm+bortle"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    tier = (bortle >= 8) & (bortle != 0)
    labeled, n = ndlabel(tier, structure=np.ones((3, 3), dtype=np.int8))
    t["tier+label"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    nb = 0
    for i in range(1, n + 1):
        bm = labeled == i
        if bm.sum() < 4:
            continue
        rf, cf = ndcom(arr, labeled, i)
        if math.isnan(rf) or math.isnan(cf):
            continue
        nb += 1
    t["centroid loop"] = time.perf_counter() - t0
    return t, n, nb


def main():
    import numpy as np
    for label, lat, lon in ORIGINS:
        mnla, mxla, mnlo, mxlo = _window_for(lat, lon)
        arr = darksky._load_raster_window("viirs", mnla, mxla, mnlo, mxlo)
        if arr is None:
            print(f"{label}: viirs window unavailable"); continue
        print(f"\n{label}: array {arr.shape} = {arr.size:,} px")

        # whole-function timing (3 runs)
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            domes = darksky._find_light_domes_from_array(arr, mnla, mxla, mnlo, mxlo, 8)
            times.append((time.perf_counter() - t0) * 1000)
        print(f"  _find_light_domes_from_array: median {statistics.median(times):.0f} ms "
              f"(min {min(times):.0f})  -> {len(domes)} raw domes")

        # correctness: new (vectorised) vs original per-blob loop
        t0 = time.perf_counter()
        ref = _reference_domes(arr, mnla, mxla, mnlo, mxlo, 8)
        ref_ms = (time.perf_counter() - t0) * 1000
        same = sorted(domes) == sorted(ref)
        print(f"  reference (old loop): {ref_ms:.0f} ms, {len(ref)} domes  "
              f"-> identical: {same}")
        if not same:
            sd, sr = set(domes), set(ref)
            print(f"    only-new={len(sd-sr)}  only-ref={len(sr-sd)}")


if __name__ == "__main__":
    main()
