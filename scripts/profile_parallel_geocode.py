#!/usr/bin/env python3
"""Offline profile of the aws-backend parallel reverse-geocode path (A) + client (B).

Real AWS isn't available here, so _settlement is stubbed with a fixed latency that
simulates an AWS Location call (no 1.1s Nominatim throttle — AWS has no per-sec policy).
We run the SAME candidate set through:
  * local backend  → serial path (one stubbed call at a time)
  * aws backend    → _parallel_prefetch_settlements fan-out, loop reads from memory
and compare wall time, call count, and — critically — that the RESULTS are identical.
Then we check B: _location() returns a cached singleton with the pooled Config.
"""
import os
import sys
import time
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("PYNIGHTSKY_PLACE_INDEX", "test-index")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darkhours import darksky, ports  # noqa: E402

SETTLE_LATENCY_S = 0.10   # simulated AWS Location round-trip
_calls = {"n": 0}


def _stub_settlement(lat, lon):
    """Deterministic name by 0.5° cell so within-cell points share a name (duplicates)."""
    _calls["n"] += 1
    time.sleep(SETTLE_LATENCY_S)
    cell_lat, cell_lon = round(lat * 2), round(lon * 2)
    return f"Town {cell_lat}_{cell_lon}, ST"


def _make_candidates(n_areas=16, per_area=4):
    """n_areas distinct 0.5°-separated areas, each with per_area points <8mi apart
    (so spatial dedup collapses each area to one rep). Sorted nearest-first."""
    cands = []
    for a in range(n_areas):
        base_lat = 39.0 + a * 0.6          # 0.6° apart  → distinct cells & >8mi
        base_lon = -118.0 + a * 0.05
        for p in range(per_area):
            cands.append({
                "lat": base_lat + p * 0.01,    # ~0.7mi steps → same area
                "lon": base_lon + p * 0.01,
                "bortle_class": 2,
                "sqm": 21.8,
                "distance_miles": round(10 + a * 5 + p * 0.1, 1),
                "direction": "N",
                "name": None,
            })
    return cands


def _run(backend_name, candidates, max_results):
    os.environ["PYNIGHTSKY_BACKEND"] = backend_name
    ports.reset_backend()
    _calls["n"] = 0
    t0 = time.perf_counter()
    out = darksky._jit_geocode_candidates(
        deepcopy(candidates), max_results, natural_areas=None, padus_index=None,
    )
    wall = (time.perf_counter() - t0) * 1000.0
    keys = [(r["name"], round(r["lat"], 4), round(r["lon"], 4)) for r in out]
    return wall, _calls["n"], keys


def main():
    darksky._settlement = _stub_settlement     # patch the network call
    candidates = _make_candidates()
    max_results = 10

    print(f"candidates={len(candidates)}  max_results={max_results}  "
          f"workers={darksky._GEOCODE_MAX_WORKERS}  stub_latency={SETTLE_LATENCY_S*1000:.0f}ms\n")

    s_wall, s_calls, s_keys = _run("local", candidates, max_results)
    p_wall, p_calls, p_keys = _run("aws", candidates, max_results)

    print(f"{'serial (local/Nominatim path)':32s} wall={s_wall:8.1f} ms  geocode_calls={s_calls}")
    print(f"{'parallel (aws/AWS Location path)':32s} wall={p_wall:8.1f} ms  geocode_calls={p_calls}")
    print(f"\nspeedup: {s_wall / p_wall:.1f}x")
    print(f"results identical: {s_keys == p_keys}  (serial={len(s_keys)}, parallel={len(p_keys)} results)")
    if s_keys != p_keys:
        print("  serial  :", s_keys)
        print("  parallel:", p_keys)

    # ── B: client singleton + pooled config ──────────────────────────────────
    darksky._reset_location_client()
    c1 = darksky._location()
    c2 = darksky._location()
    cfg = c1.meta.config
    print(f"\n[B] _location() singleton: {c1 is c2}")
    print(f"[B] max_pool_connections={cfg.max_pool_connections}  "
          f"retries={cfg.retries}")


if __name__ == "__main__":
    main()
