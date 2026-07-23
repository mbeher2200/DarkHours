#!/usr/bin/env python3
"""
Profile find_nearby() across 10 geographically diverse US cities against the
real AWS backend (PYNIGHTSKY_BACKEND=aws).

Run via scripts/bench_10cities.sh (sets env vars).  Each city is invoked
RUNS_PER_CITY times; the first run may be cold (cache miss) — the script
reports median of all runs for each phase.

Output: a Markdown table of per-phase wall-clock times across all 10 cities,
suitable for pasting directly into docs/PERF_FINDNEARBY.md as a benchmark log.

Usage:
    scripts/bench_10cities.sh                    # median of 3 runs per city
    scripts/bench_10cities.sh --runs 5           # more samples
    scripts/bench_10cities.sh --no-cache-reset   # keep existing cache (warm scenario)
"""
import argparse
import logging
import os
import statistics
import sys
import time

# Monkey-patch _Profiler output so we can capture phase timings per run
import io
import contextlib

CITIES = [
    ("Los Angeles, CA",    34.05,  -118.24),
    ("New York, NY",       40.71,   -74.01),
    ("Chicago, IL",        41.88,   -87.63),
    ("Phoenix, AZ",        33.45,  -112.07),
    ("Houston, TX",        29.76,   -95.37),
    ("Denver, CO",         39.74,  -104.98),
    ("Seattle, WA",        47.61,  -122.33),
    ("Miami, FL",          25.77,   -80.19),
    ("Atlanta, GA",        33.75,   -84.39),
    ("Minneapolis, MN",    44.98,   -93.27),
]

RADIUS = 60   # miles — standard search radius


def _parse_profile_lines(log_output: str) -> dict[str, float]:
    """Extract phase timings from _Profiler output lines.

    Format (darksky._Profiler): '[profile] <name, %-26s> <ms, %8.1f> ms  (cache ...)'
    e.g. '[profile] origin lookup                  123.4 ms  (cache +0h/+1m)'.
    Phase names contain spaces; the timing is the last token before ' ms'.
    """
    phases = {}
    for line in log_output.splitlines():
        if "[profile]" not in line or " ms" not in line:
            continue
        try:
            head = line.split("[profile]", 1)[1].split(" ms", 1)[0]
            name, ms_str = head.rsplit(None, 1)
            name = name.strip()
            ms = float(ms_str)
            if name.startswith("TOTAL"):
                phases["_total"] = ms
            else:
                phases[name] = ms
        except (ValueError, IndexError):
            pass
    return phases


def run_city(darksky, cache_mod, lat: float, lon: float, runs: int, no_cache_reset: bool) -> list[dict]:
    results = []
    for i in range(runs):
        if not no_cache_reset and i == 0:
            # First run: reset in-process caches so we get a "first real call" profile
            darksky._bortle_mem_cache.clear()
            if darksky._padus_h3_cache is not None:
                darksky._padus_h3_cache = None
            if darksky._poi_h3_cache is not None:
                darksky._poi_h3_cache = None

        # Capture log output to extract [profile] lines
        buf = io.StringIO()
        log_handler = logging.StreamHandler(buf)
        log_handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        cache_mod.stats.reset()
        t0 = time.perf_counter()
        res = darksky.find_nearby(lat, lon, RADIUS)
        wall_ms = (time.perf_counter() - t0) * 1000.0
        hits, misses = cache_mod.stats.snapshot()

        root_logger.removeHandler(log_handler)
        phases = _parse_profile_lines(buf.getvalue())
        phases["_total_wall"] = wall_ms
        phases["_cache_hits"] = hits
        phases["_cache_misses"] = misses
        if res is not None:
            phases["_origin_bortle"] = res.get("origin_bortle", 0)
            phases["_n_results"] = len(res.get("results", []))
            phases["_n_domes"] = len(res.get("light_domes", []))
        results.append(phases)
    return results


def median_phases(runs: list[dict]) -> dict:
    all_keys = set()
    for r in runs:
        all_keys.update(r.keys())
    out = {}
    for k in all_keys:
        vals = [r[k] for r in runs if k in r]
        if vals:
            out[k] = statistics.median(vals)
    return out


def fmt_ms(v) -> str:
    if v is None:
        return "  —  "
    return f"{v:>6.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=3, help="Runs per city (default 3; report median)")
    ap.add_argument("--no-cache-reset", action="store_true",
                    help="Don't reset in-process caches between cities (fully warm path)")
    args = ap.parse_args()

    backend_name = os.environ.get("PYNIGHTSKY_BACKEND", "local")
    if backend_name != "aws":
        print("ERROR: set PYNIGHTSKY_BACKEND=aws  (use scripts/bench_10cities.sh)")
        sys.exit(1)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],  # progress to stderr
    )

    from darkhours import cache as cache_mod, darksky, ports
    _ = ports.get_backend()  # force backend init

    os.environ["PYNIGHTSKY_PROFILE"] = "1"
    # Re-import darksky so _PROFILE is picked up (it's module-level at import time)
    import importlib
    import darkhours.darksky as ds_module
    importlib.reload(ds_module)
    darksky = ds_module

    print(f"\n# find_nearby() 10-city benchmark  backend={backend_name}  runs={args.runs}  radius={RADIUS}mi")
    print(f"# {time.strftime('%Y-%m-%d %H:%M %Z')}\n")

    # Phase columns to display (subset of all phases)
    PHASE_COLS = [
        ("raster window reads", "raster rdW"),
        ("extract dark candidates", "extract"),
        ("cluster + band select", "cluster"),
        ("light dome detection", "dome det"),
        ("dome naming", "dome name"),
        ("jit geocode candidates", "jit geo"),
        ("drive times", "drive"),
        ("_total_wall", "TOTAL ms"),
    ]
    col_labels = ["City", "B", "res", "domes"] + [label for _, label in PHASE_COLS]
    sep = " | "
    widths = [22, 1, 3, 5] + [max(9, len(lbl)) for lbl in [label for _, label in PHASE_COLS]]

    header = sep.join(f"{col:<{w}}" for col, w in zip(col_labels, widths))
    ruler  = sep.join("-" * w for w in widths)
    print(header)
    print(ruler)

    all_data = {}
    for city, lat, lon in CITIES:
        sys.stderr.write(f"  profiling {city} ({runs} runs)...\n")
        runs_data = run_city(darksky, cache_mod, lat, lon, args.runs, args.no_cache_reset)
        med = median_phases(runs_data)
        all_data[city] = med

        bortle = int(med.get("_origin_bortle", 0)) or "?"
        n_res  = int(med.get("_n_results", 0))
        n_dom  = int(med.get("_n_domes", 0))
        vals   = [city, bortle, n_res, n_dom]
        for phase_key, _ in PHASE_COLS:
            vals.append(fmt_ms(med.get(phase_key)))
        print(sep.join(f"{str(v):<{w}}" for v, w in zip(vals, widths)))
        sys.stdout.flush()

    # Summary row
    print(ruler)
    totals = [med.get("_total_wall") for med in all_data.values() if "_total_wall" in med]
    if totals:
        print(f"median total across {len(CITIES)} cities: {statistics.median(totals):.1f} ms")
    print()


if __name__ == "__main__":
    main()
