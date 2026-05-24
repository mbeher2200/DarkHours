#!/usr/bin/env python3
"""
Benchmark the trip-planning engine across 3 groups of 5 cities.

Each group is timed end-to-end (location resolution + night computation).
Runs twice per group — cold first (cache cleared before each cold run)
then warm (immediate re-run hitting the cache) — to show the cache speedup.

Results are printed to stdout AND appended to benchmark_results.log.
"""

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import cache as _cache
import location as loc
from trip import plan_trip

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESULTS_FILE = Path("benchmark_results.log")

# 3 groups of 5 geographically diverse cities — different climates and
# light-pollution profiles so caching doesn't trivially mask differences.
CITY_GROUPS = [
    [
        "Death Valley, CA",
        "New York, NY",
        "London, United Kingdom",
        "Tokyo, Japan",
        "Sydney, Australia",
    ],
    [
        "Mauna Kea, HI",
        "Chicago, IL",
        "Paris, France",
        "Mumbai, India",
        "Cape Town, South Africa",
    ],
    [
        "Bryce Canyon, UT",
        "Toronto, Canada",
        "Berlin, Germany",
        "Buenos Aires, Argentina",
        "Seoul, South Korea",
    ],
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_group(city_names: list[str]) -> tuple[list[dict], float]:
    """Geocode all cities in the group; return (locations, elapsed_seconds)."""
    t0 = time.perf_counter()
    locations = []
    for name in city_names:
        lat, lon, display_name, tz_name = loc.resolve(name)
        locations.append(
            {"lat": lat, "lon": lon, "display_name": display_name, "tz_name": tz_name}
        )
    return locations, time.perf_counter() - t0


def _run_group(locations: list[dict], start: date, end: date) -> tuple[object, float]:
    """Call plan_trip() and return (report, elapsed_seconds)."""
    t0 = time.perf_counter()
    report = plan_trip(locations, start, end, fetch_weather=True)
    return report, time.perf_counter() - t0


def _score_summary(report) -> str:
    """One-liner showing per-city average scores."""
    parts = []
    for l in report.locations:
        nights = [
            n for n in report.nights
            if n.lat == l["lat"] and n.lon == l["lon"] and n.score is not None
        ]
        if nights:
            avg = sum(n.score for n in nights) / len(nights)
            name = l["display_name"].split(",")[0]
            parts.append(f"{name}: {avg:.1f}")
        else:
            parts.append(f"{l['display_name'].split(',')[0]}: —")
    return "  |  ".join(parts)


def _divider(char="─", width=72) -> str:
    return char * width


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(message)s")

    # Date window: start 2 days from now, run for 3 nights
    start_date = date.today() + timedelta(days=2)
    end_date   = start_date + timedelta(days=2)

    header_lines = [
        "",
        _divider("═"),
        f"  PyNightSky Benchmark",
        f"  Date range : {start_date}  →  {end_date}  (3 nights)",
        f"  Groups     : {len(CITY_GROUPS)} × 5 cities  ({len(CITY_GROUPS) * 5} total)",
        f"  Run started: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        _divider("═"),
        "",
    ]

    lines = list(header_lines)

    def emit(text=""):
        """Print and buffer for the log file."""
        print(text)
        lines.append(text)

    for text in header_lines:
        print(text)

    # -----------------------------------------------------------------------
    # Resolve all locations upfront so geocoding cost is isolated
    # -----------------------------------------------------------------------
    emit("Resolving locations …")
    emit()
    all_groups: list[tuple[list[dict], float]] = []
    for i, city_names in enumerate(CITY_GROUPS, 1):
        resolved, geo_time = _resolve_group(city_names)
        all_groups.append((resolved, geo_time))
        city_col = "  ".join(l["display_name"].split(",")[0] for l in resolved)
        emit(f"  Group {i}  ({geo_time:.2f}s geocoding)  →  {city_col}")
    emit()

    # -----------------------------------------------------------------------
    # Benchmark: cold then warm per group
    # -----------------------------------------------------------------------
    grand_cold = 0.0
    grand_warm = 0.0

    for i, (locations, geo_time) in enumerate(all_groups, 1):
        emit(_divider())
        emit(f"  Group {i} — {len(locations)} cities × 3 nights")
        emit(_divider())

        # --- Cold run (clear relevant cache entries first) ---
        _cache.clear_all()
        emit("  [COLD] Cache cleared.")
        report_cold, cold_time = _run_group(locations, start_date, end_date)
        grand_cold += cold_time
        emit(f"  [COLD] Elapsed : {cold_time:.2f}s")
        emit(f"  [COLD] Nights  : {len(report_cold.nights)} computed")
        emit(f"  [COLD] Scores  : {_score_summary(report_cold)}")

        # Per-night detail
        emit()
        emit("    Night detail (cold):")
        for n in sorted(report_cold.nights, key=lambda x: (x.date, x.display_name)):
            wx_tag = "w/ wx" if n.weather_informed else "     "
            score  = f"{n.score:.1f}" if n.score is not None else "—"
            name   = n.display_name.split(",")[0][:20]
            emit(f"      {n.date}  {name:<21} {score:>4}/10  {wx_tag}  {n.phase_name}")

        # --- Warm run (cache is now warm) ---
        emit()
        report_warm, warm_time = _run_group(locations, start_date, end_date)
        grand_warm += warm_time
        speedup = cold_time / warm_time if warm_time > 0 else float("inf")
        emit(f"  [WARM] Elapsed : {warm_time:.2f}s  ({speedup:.1f}× speedup from cache)")
        emit(f"  [WARM] Nights  : {len(report_warm.nights)} returned")
        emit()

    # -----------------------------------------------------------------------
    # Grand totals
    # -----------------------------------------------------------------------
    emit(_divider("═"))
    emit("  Summary")
    emit(_divider("═"))
    emit(f"  Total cold time  : {grand_cold:.2f}s  across {len(CITY_GROUPS)} groups")
    emit(f"  Total warm time  : {grand_warm:.2f}s  across {len(CITY_GROUPS)} groups")
    overall_speedup = grand_cold / grand_warm if grand_warm > 0 else float("inf")
    emit(f"  Overall speedup  : {overall_speedup:.1f}×  (cold → warm)")
    emit(f"  Per night (cold) : {grand_cold / (len(CITY_GROUPS) * 5 * 3):.2f}s avg")
    emit(f"  Per night (warm) : {grand_warm / (len(CITY_GROUPS) * 5 * 3):.2f}s avg")
    emit()

    # -----------------------------------------------------------------------
    # Write log file
    # -----------------------------------------------------------------------
    with RESULTS_FILE.open("a") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Results appended to: {RESULTS_FILE.resolve()}")


if __name__ == "__main__":
    main()
