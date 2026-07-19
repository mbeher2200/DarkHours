#!/usr/bin/env python3
"""Benchmark the PAD-US H3 index load + lookup (the cold-start cost in find_nearby).

Times darksky._load_padus_h3_index() over several cold iterations (resetting the
module cache each time) and times a batch of lookups. Run it before and after the
columnar rewrite to compare the SAME entry points.

  --verify-against <string-parquet>  cross-check every lookup against a reference
                                     dict built from the original string-keyed
                                     parquet (correctness gate for the migration).
"""
import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from darkhours import darksky  # noqa: E402

# Mix of public land (expect hits), cities, and ocean (expect None).
SAMPLE_PTS = [
    (36.0544, -112.1401, "Grand Canyon NP"),
    (44.4280, -110.5885, "Yellowstone NP"),
    (37.8651, -119.5383, "Yosemite NP"),
    (40.7128, -74.0060, "New York City"),
    (33.4484, -112.0740, "Phoenix"),
    (25.0, -90.0, "Gulf of Mexico (ocean)"),
]


def bench_load(iters: int):
    times = []
    idx = None
    for _ in range(iters):
        darksky._padus_h3_cache = None          # force a cold load
        t0 = time.perf_counter()
        idx = darksky._load_padus_h3_index()
        times.append((time.perf_counter() - t0) * 1000.0)
    return idx, times


def bench_lookup(idx, n_random=200_000):
    # Sample points first (and print), then a large random-coordinate batch for timing.
    for lat, lon, label in SAMPLE_PTS:
        print(f"    {label:28s} -> {darksky._padus_h3_lookup(lat, lon, idx)}")
    pts = [(random.uniform(25, 49), random.uniform(-124, -67)) for _ in range(n_random)]
    t0 = time.perf_counter()
    hits = 0
    for lat, lon in pts:
        if darksky._padus_h3_lookup(lat, lon, idx) is not None:
            hits += 1
    dt = time.perf_counter() - t0
    return n_random, hits, dt


def verify_against(string_parquet: str, idx) -> int:
    """Compare new-index lookups to a reference dict from the original string parquet."""
    import pyarrow.parquet as pq
    import h3
    print(f"\n[verify] building reference dict from {string_parquet} ...")
    tbl = pq.read_table(string_parquet, columns=["h3_cell", "Unit_Nm", "is_blacklisted"])
    cells = tbl.column("h3_cell").to_pylist()
    names = tbl.column("Unit_Nm").to_pylist()
    bls = tbl.column("is_blacklisted").to_pylist()
    ref = dict(zip(cells, zip(names, bls)))
    # Compare on a large random sample of known cells (cell-center -> lookup).
    sample = random.sample(cells, min(50_000, len(cells)))
    mismatches = 0
    for cell in sample:
        lat, lon = h3.cell_to_latlng(cell)
        want = ref.get(cell)
        got = darksky._padus_h3_lookup(lat, lon, idx)
        if got != want:
            mismatches += 1
            if mismatches <= 5:
                print(f"  MISMATCH cell={cell} want={want} got={got}")
    # Also the explicit sample points.
    for lat, lon, label in SAMPLE_PTS:
        cell = h3.latlng_to_cell(lat, lon, 7)
        if darksky._padus_h3_lookup(lat, lon, idx) != ref.get(cell):
            mismatches += 1
            print(f"  MISMATCH {label}")
    print(f"[verify] {len(sample)} sampled cells + {len(SAMPLE_PTS)} points: "
          f"{mismatches} mismatches")
    return mismatches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--verify-against", type=str, default=None)
    args = ap.parse_args()

    random.seed(42)
    idx, load_times = bench_load(args.iters)
    if idx is None:
        print("PAD-US index unavailable (None) — check the parquet path.")
        return
    print(f"PAD-US index load ({args.iters} cold iterations):")
    print(f"  min={min(load_times):8.1f} ms  median={statistics.median(load_times):8.1f} ms  "
          f"max={max(load_times):8.1f} ms")
    print("  sample lookups:")
    n, hits, dt = bench_lookup(idx)
    print(f"  lookup: {n} random pts in {dt*1000:.1f} ms "
          f"({dt/n*1e6:.2f} us/pt, {hits} hits)")

    if args.verify_against:
        m = verify_against(args.verify_against, idx)
        print("VERIFY:", "PASS ✅" if m == 0 else f"FAIL ❌ ({m} mismatches)")
        sys.exit(0 if m == 0 else 1)


if __name__ == "__main__":
    main()
