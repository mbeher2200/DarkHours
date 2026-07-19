#!/usr/bin/env python3
"""
Run find_nearby() across 10 cities and capture per-phase [profile] timings.
Usage: PYNIGHTSKY_PROFILE=1 PYNIGHTSKY_BACKEND=aws ... python scripts/bench_states.py [--runs N] [--label LABEL]
"""
import argparse, logging, os, re, statistics, sys, time
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

CITIES = [
    ("Los Angeles",  34.05,  -118.24),
    ("New York",     40.71,   -74.01),
    ("Chicago",      41.88,   -87.63),
    ("Phoenix",      33.45,  -112.07),
    ("Houston",      29.76,   -95.37),
    ("Denver",       39.74,  -104.98),
    ("Seattle",      47.61,  -122.33),
    ("Miami",        25.77,   -80.19),
    ("Atlanta",      33.75,   -84.39),
    ("Minneapolis",  44.98,   -93.27),
]

# Columns to display. For the "raster" bucket we accept both the new combined
# phase name (post-S2) and the two legacy names (pre-S2) and sum them.
PHASES = [
    ("raster window reads",       "raster"),
    ("extract dark candidates",   "extract"),
    ("light dome detection",      "dome det"),
    ("dome naming (geocode)",     "dome name"),
    ("jit geocode candidates",    "jit geo"),
    ("drive times (aws)",         "drive"),
]

# Legacy phase names that map to a canonical column key
_ALIASES = {
    "viirs window read":  "raster window reads",
    "falchi window read": "raster window reads",
}

def run_one(darksky, cache_mod, lat, lon):
    buf = StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(h)
    try:
        cache_mod.stats.reset()
        t0 = time.perf_counter()
        darksky.find_nearby(lat, lon, 60)
        wall = (time.perf_counter() - t0) * 1000
    finally:
        logging.getLogger().removeHandler(h)
    phases = {}
    for line in buf.getvalue().splitlines():
        if "[profile]" not in line:
            continue
        # Format: [profile] <name>    XX.X ms  (cache ...)
        m = re.search(r'\[profile\]\s+(.*?)\s{2,}([\d.]+)\s*ms', line)
        if m:
            name = m.group(1).strip()
            val  = float(m.group(2))
            name = _ALIASES.get(name, name)          # normalise legacy names
            phases[name] = phases.get(name, 0) + val  # sum aliased phases
        m2 = re.search(r'TOTAL.*?([\d.]+)\s*ms', line)
        if m2:
            phases["_total"] = float(m2.group(1))
    phases["_wall"] = wall
    return phases

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG, handlers=[logging.StreamHandler(sys.stderr)], format="%(message)s")

    import darkhours.darksky as ds
    from darkhours import cache as cm, ports
    ports.get_backend()

    # Phase table header
    col_w = [14] + [8] * (len(PHASES) + 1)
    sep = " | "
    hdr = ["City"] + [lbl for _, lbl in PHASES] + ["TOTAL"]
    print(f"\n=== {args.label} ===")
    print(sep.join(f"{h:<{w}}" for h, w in zip(hdr, col_w)))
    print(sep.join("-" * w for w in col_w))

    all_totals = []
    for city, lat, lon in CITIES:
        sys.stderr.write(f"  {city}…\n"); sys.stderr.flush()
        run_data = [run_one(ds, cm, lat, lon) for _ in range(args.runs)]
        def med(key):
            vs = [r[key] for r in run_data if key in r]
            return statistics.median(vs) if vs else None
        row = [city]
        for key, _ in PHASES:
            v = med(key)
            row.append(f"{v:>6.0f}" if v is not None else "   —")
        total = med("_total")
        row.append(f"{total:>6.0f}" if total is not None else "  —")
        if total:
            all_totals.append(total)
        print(sep.join(f"{str(v):<{w}}" for v, w in zip(row, col_w)))

    print(sep.join("-" * w for w in col_w))
    if all_totals:
        print(f"{'median':<14} | " + " | ".join(" " * (w - 1) for _, w in zip(PHASES, col_w[1:-1])) + f" | {statistics.median(all_totals):>6.0f}")
    print()

if __name__ == "__main__":
    main()
