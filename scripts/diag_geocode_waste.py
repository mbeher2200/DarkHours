#!/usr/bin/env python3
"""Classify why _jit_geocode_candidates burns Nominatim calls for a bright origin.

Wraps _settlement to tag every Tier-3 probe as water / none / duplicate / kept,
then runs find_nearby against a fresh cache so every probe is a live call.
"""
import os, sys, tempfile
from collections import Counter
from pathlib import Path

os.environ["PYNIGHTSKY_PROFILE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PyNightSkyPredictor import cache, darksky, ports  # noqa: E402

LAT, LON, RADIUS = 33.4484, -112.0740, 60   # Phoenix, AZ

tally = Counter()
seen_names: dict[str, tuple[float, float]] = {}   # name -> first (lat, lon) kept
dup_dists: list[float] = []                        # duplicate's dist to its kept twin
_orig = darksky._settlement


def _traced(lat, lon):
    r = _orig(lat, lon)
    if r == darksky._OVER_WATER:
        tally["water"] += 1
    elif not r:
        tally["none(coord-fallback)"] += 1
    elif r in seen_names:
        tally["duplicate"] += 1
        klat, klon = seen_names[r]
        dup_dists.append(darksky._haversine_miles(lat, lon, klat, klon))
    else:
        tally["kept"] += 1
        seen_names[r] = (lat, lon)
    return r


darksky._settlement = _traced

backend = ports.get_backend()
with tempfile.TemporaryDirectory() as tmp:
    backend._cache = cache.LocalFileCache(Path(tmp))
    darksky._bortle_mem_cache.clear()
    cache.stats.reset()
    res = darksky.find_nearby(LAT, LON, RADIUS)
    h, m = cache.stats.snapshot()

print(f"Phoenix radius={RADIUS}  results={len(res['results'])}  domes={len(res['light_domes'])}")
print(f"cache: {h} hits / {m} misses")
print("Tier-3 _settlement probe classification:")
for k, v in tally.most_common():
    print(f"  {k:24s} {v}")
print(f"  {'TOTAL probes':24s} {sum(tally.values())}")
if dup_dists:
    dup_dists.sort()
    import statistics
    print(f"duplicate→kept distance (mi): min={dup_dists[0]:.1f} "
          f"median={statistics.median(dup_dists):.1f} max={dup_dists[-1]:.1f}")
    for thr in (2, 5, 8, 12, 20):
        n = sum(1 for d in dup_dists if d <= thr)
        print(f"  dups within {thr:2d} mi of kept twin: {n}/{len(dup_dists)}")
