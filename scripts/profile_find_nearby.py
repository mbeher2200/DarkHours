#!/usr/bin/env python3
"""Profiling harness for darksky.find_nearby.

Runs find_nearby against a set of central (city-centre) origins and prints the
per-phase wall-clock breakdown emitted by the in-code profiler, alongside the
cache hit/miss delta for each call.

  WARM pass  — uses the existing on-disk geocode cache (the typical steady state).
  COLD pass  — redirects the cache to a throwaway temp dir and drops in-process
               caches, so every reverse-geocode is a miss and real network calls
               are made. This exposes the worst-case naming cost.

Usage:
    .venv/bin/python scripts/profile_find_nearby.py            # warm, all cities
    .venv/bin/python scripts/profile_find_nearby.py --cold     # + one cold run
    .venv/bin/python scripts/profile_find_nearby.py --radius 100
"""

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PYNIGHTSKY_PROFILE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyNightSkyPredictor import cache, darksky, ports  # noqa: E402

darksky._PROFILE = True  # force on even if env was set late

# City-centre origins — deliberately bright (Bortle 7-9) so the full naming /
# dome-detection path runs (a rural origin short-circuits much of it).
LOCATIONS = [
    ("Los Angeles, CA", 34.0522, -118.2437),
    ("New York, NY",    40.7128,  -74.0060),
    ("Chicago, IL",     41.8781,  -87.6298),
    ("Denver, CO",      39.7392, -104.9903),
    ("Phoenix, AZ",     33.4484, -112.0740),
    ("Atlanta, GA",     33.7490,  -84.3880),
]


def _run(label, lat, lon, radius):
    print(f"\n{'=' * 72}\n{label}  ({lat}, {lon})  radius={radius} mi\n{'=' * 72}")
    cache.stats.reset()
    t0 = time.perf_counter()
    res = darksky.find_nearby(lat, lon, radius)
    wall = (time.perf_counter() - t0) * 1000.0
    h, m = cache.stats.snapshot()
    if res is None:
        print("  -> find_nearby returned None (rasterio unavailable?)")
        return
    print(f"  wall={wall:8.1f} ms   origin Bortle {res['origin_bortle']}   "
          f"results={len(res['results'])}  domes={len(res['light_domes'])}  "
          f"cache {h}h/{m}m  hit-rate={h / max(h + m, 1) * 100:4.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=60)
    ap.add_argument("--cold", action="store_true",
                    help="also run one origin against a fresh (empty) cache")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Quiet the noisy per-pixel/per-call debug logs; we only want [profile] lines.
    logging.getLogger("PyNightSkyPredictor.darksky").setLevel(logging.INFO)

    print(f"Backend: {ports.get_backend()._name}   "
          f"PROFILE={darksky._PROFILE}   radius={args.radius} mi")

    # ── WARM pass (existing disk cache) ──────────────────────────────────────
    print("\n########## WARM PASS (existing on-disk cache) ##########")
    for label, lat, lon in LOCATIONS:
        _run(label, lat, lon, args.radius)

    # ── WARM repeat of first city (in-process caches now hot) ────────────────
    print("\n########## WARM REPEAT (in-process caches hot) ##########")
    label, lat, lon = LOCATIONS[0]
    _run(label, lat, lon, args.radius)

    # ── COLD pass (fresh empty cache, real network calls) ────────────────────
    if args.cold:
        print("\n########## COLD PASS (empty cache, live network) ##########")
        backend = ports.get_backend()
        saved_cache = backend._cache
        with tempfile.TemporaryDirectory() as tmp:
            backend._cache = cache.LocalFileCache(Path(tmp))
            darksky._bortle_mem_cache.clear()
            label, lat, lon = LOCATIONS[0]
            _run(label, lat, lon, args.radius)
        backend._cache = saved_cache


if __name__ == "__main__":
    main()
