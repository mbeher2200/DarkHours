#!/usr/bin/env python3
"""Run a single find_nearby against the REAL aws backend, with profiling.

Intended to be launched via scripts/profile_aws.sh (which sets the resource env
vars). Makes live AWS calls: S3 range-reads of the COGs, DynamoDB cache get/set,
AWS Location reverse-geocode + route matrix. All small/within free tier; the only
writes are geocode entries into the shared prod cache (intended, beneficial).
"""
import argparse
import logging
import time

from darkhours import cache, darksky, ports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=33.4484)      # Phoenix, AZ (worst case earlier)
    ap.add_argument("--lon", type=float, default=-112.0740)
    ap.add_argument("--radius", type=int, default=60)
    ap.add_argument("--workers", type=int, help="override PYNIGHTSKY_GEOCODE_WORKERS")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.workers:
        darksky._GEOCODE_MAX_WORKERS = args.workers

    backend = ports.get_backend()._name
    print(f"backend={backend}  geocode_workers={darksky._GEOCODE_MAX_WORKERS}  "
          f"({args.lat}, {args.lon}) r={args.radius}mi")
    if backend != "aws":
        print("!! backend is not 'aws' — set PYNIGHTSKY_BACKEND=aws (use scripts/profile_aws.sh)")
        return

    cache.stats.reset()
    t0 = time.perf_counter()
    res = darksky.find_nearby(args.lat, args.lon, args.radius)
    wall = (time.perf_counter() - t0) * 1000.0
    h, m = cache.stats.snapshot()

    if res is None:
        print("find_nearby returned None (raster/data unavailable)")
        return
    print(f"\nwall={wall:.1f} ms  origin Bortle {res['origin_bortle']}  "
          f"results={len(res['results'])}  domes={len(res['light_domes'])}  "
          f"cache {h}h/{m}m")
    print("results:")
    for r in res["results"]:
        dm = r.get("drive_minutes")
        print(f"  B{r['bortle_class']}  {r['distance_miles']:>5} mi  "
              f"{(str(dm) + ' min') if dm is not None else '   -   '}  {r['name']}")


if __name__ == "__main__":
    main()
