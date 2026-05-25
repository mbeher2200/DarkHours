#!/usr/bin/env python3
"""
Milky Way geometry regression test — five representative latitudes.

Verifies that milky_way_arch_summary() returns physically correct results
across the full range of observable latitudes:

  1. Whitehorse, YT       (~60.7°N)  — galactic core below horizon, northern arm only
  2. Denver, CO           (~39.7°N)  — core barely clears 10° floor, Cygnus overhead
  3. Quito, Ecuador       (~  0.0°)  — equatorial; core high, both arms visible
  4. Buenos Aires, AR     (~34.6°S)  — core near zenith in north, southern arm prominent
  5. Ushuaia, AR          (~54.8°S)  — deep south; Norma near zenith, Cygnus absent

Expected maximum altitudes (formula: 90° − |lat − dec|, using nominal decs):
  Galactic Core  dec ≈ −29°:  Whitehorse  0°  Denver 21°  Quito 61°  BA 84°  Ushuaia 64°
  Cygnus         dec ≈ +34°:  Whitehorse 63°  Denver 84°  Quito 56°  BA 21°  Ushuaia <10°
  Norma          dec ≈ −56°:  Whitehorse <0°  Denver  <0° Quito 34°  BA 69°  Ushuaia 89°

Run with:
  python test_mw_geometry.py          # pass / fail summary
  python test_mw_geometry.py -v       # verbose — print full MW table per city

The test uses cached ephemeris and location data; no network access required
after the first run.
"""

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Allow running from any working directory — add project root to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------

CASES = [
    {
        "label":       "Whitehorse, YT (60.7°N)  — northern Canada",
        "lat":          60.7216,
        "lon":        -135.0549,
        "date":        date(2026, 8, 1),   # August: dark enough, Cygnus/Perseus high
        # ---- geometry expectations ----
        "core_visible": False,             # core max alt ~0.3° — never clears 10° floor
        "summary_none": True,              # no summary (no core → returns None)
        "expect_visible": {                # waypoints that MUST appear in the table
            "Cygnus Star Cloud",           # dec +34° → max 63° from here
            "Perseus/Cassiopeia",          # dec +56° → up to 85° possible
        },
        "expect_absent": {                 # waypoints that must NOT appear
            "Galactic Core",
            "Puppis Star Cloud",
            "Carina Arm",
            "Norma Star Cloud",
        },
    },
    {
        "label":       "Denver, CO (39.7°N)  — mid-latitude North America",
        "lat":          39.7392,
        "lon":        -104.9849,
        "date":        date(2026, 6, 13),
        "core_visible": True,
        "summary_none": False,
        # Core barely clears 10°; expected peak 18–23°
        "core_peak_alt_range": (15, 25),
        # Core transits due south from NH
        "core_peak_az_range":  (170, 195),   # ±10° around due south
        # Sweep goes toward Cygnus (highest far-side from NH)
        "sweep_far_name":      "Cygnus Star Cloud",
        # Cygnus peaks very high (near zenith) — ~80–89°
        "sweep_far_alt_range": (75, 90),
        "min_visible":         5,
        "expect_absent": {
            "Carina Arm",
            "Norma Star Cloud",
        },
    },
    {
        "label":       "Quito, Ecuador (0.0°)  — equatorial",
        "lat":         -0.2202,
        "lon":        -78.5123,
        "date":        date(2026, 6, 13),
        "core_visible": True,
        "summary_none": False,
        # Core peaks high in the south ~55–65°
        "core_peak_alt_range": (55, 68),
        # Core transits to south from equator (dec < lat)
        "core_peak_az_range":  (165, 195),
        # Sweep can go to Cygnus (N) or Aquila (N) — both visible
        # Key: far-end must be north (az 0±30° or 330–360°)
        "sweep_far_az_range":  (330, 390),   # wraps around north; 390 ≡ 30°
        "min_visible":         7,            # both arms present
        "expect_visible": {
            "Cygnus Star Cloud",
            "Puppis Star Cloud",
        },
    },
    {
        "label":       "Buenos Aires, AR (34.6°S)  — mid-latitude South America",
        "lat":        -34.6096,
        "lon":        -58.3888,
        "date":        date(2026, 6, 13),
        "core_visible": True,
        "summary_none": False,
        # Core near zenith in north — peak ~80–87°
        "core_peak_alt_range": (79, 88),
        # Transits just north of zenith from -34.6° (core dec -29° > lat)
        "core_peak_az_range":  (350, 15),    # wraps around north
        # Sweep toward southern arm — far-end in south
        "sweep_far_az_range":  (160, 210),   # due south ±25°
        "min_visible":         7,
        "expect_visible": {
            "Norma Star Cloud",
            "Puppis Star Cloud",
        },
        "expect_absent": {"Perseus/Cassiopeia"},    # dec +56°: max alt = 90-90.7° ≈ 0
    },
    {
        "label":       "Ushuaia, AR (54.8°S)  — deep southern hemisphere",
        "lat":        -54.8073,
        "lon":        -68.3084,
        "date":        date(2026, 6, 13),
        "core_visible": True,
        "summary_none": False,
        # Core peaks ~60–68° — clearly visible but not at zenith
        "core_peak_alt_range": (58, 70),
        # Core transits to north (dec -29° > lat -54.8°)
        "core_peak_az_range":  (350, 15),
        # Sweep toward southern arm — Norma near zenith
        "sweep_far_alt_range": (82, 92),     # Norma peaks ~89°
        "min_visible":         6,
        "expect_absent": {
            "Cygnus Star Cloud",             # dec +34°: max alt 1° — never clears 10° floor
            "Perseus/Cassiopeia",            # dec +56°: even lower — never rises
        },
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _az_in_range(az: float, lo: float, hi: float) -> bool:
    """True if az is within [lo, hi], handling wrap-around at 0/360°.

    Specify ranges that cross north as lo > hi, e.g. (350, 15) for ±15° of N.
    Both az and the bounds are normalised to [0, 360) before comparison.
    """
    az = az % 360
    lo = lo % 360
    hi = hi % 360
    if lo <= hi:
        return lo <= az <= hi
    # Wraps around north: az >= lo  OR  az <= hi
    return az >= lo or az <= hi


def _run_case(case: dict, verbose: bool) -> list[str]:
    """
    Run one test case.  Returns a list of failure strings (empty = pass).
    """
    from zoneinfo import ZoneInfo

    from skyfield.api import load, wgs84
    from targets import visible_targets, milky_way_arch_summary

    lat, lon     = case["lat"], case["lon"]
    target_date  = case["date"]

    tz = ZoneInfo("UTC")

    # Compute night bounds
    from sky_events import sky_events, moon_phase_info, find_event
    events = sky_events(lat, lon, target_date)

    sunset   = find_event(events, "Sunset",   after=datetime(target_date.year,  target_date.month,  target_date.day,  tzinfo=timezone.utc))
    sunrise  = find_event(events, "Sunrise",  after=sunset)
    night_start = find_event(events, "Astronomical night begins", after=sunset,      before=sunrise)
    night_end   = find_event(events, "Astronomical night ends",   after=night_start or sunset, before=sunrise)

    if not sunset or not sunrise:
        return [f"  SKIP: no sunset/sunrise found for {case['label']}"]

    _, illum = moon_phase_info(sunset)

    tgts = visible_targets(lat, lon, sunset, sunrise, illum,
                           night_start=night_start, night_end=night_end)

    mw_tgts  = [t for t in tgts if t.type == "milky_way"]
    mw_names = {t.name for t in mw_tgts}
    summary  = milky_way_arch_summary(mw_tgts)

    failures = []

    # ── verbose print ────────────────────────────────────────────────────────
    if verbose:
        print(f"\n  {'─'*60}")
        print(f"  {case['label']}")
        if summary is None:
            print("  Summary: None (core below horizon)")
        else:
            def _fmt(dt):
                return dt.astimezone(tz).strftime("%H:%M UTC")
            print(f"  Summary: arch {_fmt(summary['arch_start'])} – {_fmt(summary['arch_end'])}"
                  f"  |  {summary['n_visible']} of {summary['n_total']} waypoints")
            print(f"           core peaks {_fmt(summary['core_peak_time'])}"
                  f" @ {summary['core_peak_alt_deg']}°  az {summary['core_peak_az_deg']}°")
            if summary["farthest_name"]:
                print(f"           far-end: {summary['farthest_name']}"
                      f" @ {summary['farthest_peak_alt_deg']}°  az {summary['farthest_peak_az_deg']}°")
        print(f"  Visible MW waypoints ({len(mw_names)}):")
        for t in sorted(mw_tgts, key=lambda x: max(w.peak_alt_deg for w in x.windows), reverse=True):
            w = max(t.windows, key=lambda x: x.peak_alt_deg)
            print(f"    {t.name:<28}  peak {w.peak_alt_deg:5.1f}°  az {w.peak_az_deg:6.1f}°")

    # ── assertions ───────────────────────────────────────────────────────────

    if case.get("summary_none"):
        if summary is not None:
            failures.append(f"  summary should be None but got {summary}")
    else:
        if summary is None:
            failures.append("  summary is None but core should be visible")
            return failures  # remaining checks require summary

    if not case.get("summary_none") and summary is not None:
        # Core peak altitude
        if lo_hi := case.get("core_peak_alt_range"):
            lo, hi = lo_hi
            alt = summary["core_peak_alt_deg"]
            if not (lo <= alt <= hi):
                failures.append(f"  core_peak_alt {alt}° outside expected [{lo}°, {hi}°]")

        # Core peak azimuth
        if lo_hi := case.get("core_peak_az_range"):
            lo, hi = lo_hi
            az = summary["core_peak_az_deg"]
            if not _az_in_range(az, lo, hi):
                failures.append(f"  core_peak_az {az}° outside expected [{lo}°, {hi}°]")

        # Sweep far-end name
        if expected_far := case.get("sweep_far_name"):
            got = summary.get("farthest_name")
            if got != expected_far:
                failures.append(f"  sweep far-end: expected {expected_far!r}, got {got!r}")

        # Sweep far-end altitude
        if lo_hi := case.get("sweep_far_alt_range"):
            lo, hi = lo_hi
            alt = summary.get("farthest_peak_alt_deg")
            if alt is None or not (lo <= alt <= hi):
                failures.append(f"  farthest_peak_alt {alt}° outside [{lo}°, {hi}°]")

        # Sweep far-end azimuth
        if lo_hi := case.get("sweep_far_az_range"):
            lo, hi = lo_hi
            az = summary.get("farthest_peak_az_deg")
            if az is None or not _az_in_range(az, lo, hi):
                failures.append(f"  farthest_peak_az {az}° outside [{lo}°, {hi}°]")

        # Minimum visible count
        if min_v := case.get("min_visible"):
            n = summary["n_visible"]
            if n < min_v:
                failures.append(f"  n_visible {n} < expected minimum {min_v}")

    # Must-be-visible waypoints
    for name in case.get("expect_visible", set()):
        if name not in mw_names:
            failures.append(f"  expected visible but absent: {name!r}")

    # Must-be-absent waypoints
    for name in case.get("expect_absent", set()):
        if name in mw_names:
            failures.append(f"  expected absent but visible: {name!r}")

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Milky Way geometry regression tests")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print detailed MW table for each city")
    args = parser.parse_args()

    print("\nMilky Way geometry tests\n")
    total = passed = 0
    all_failures = []

    for case in CASES:
        total += 1
        failures = _run_case(case, verbose=args.verbose)
        if failures:
            print(f"  FAIL  {case['label']}")
            for f in failures:
                print(f"       {f}")
            all_failures.extend(failures)
        else:
            passed += 1
            print(f"  PASS  {case['label']}")

    print(f"\n{'─'*60}")
    print(f"  {passed}/{total} passed")
    if all_failures:
        print()
        sys.exit(1)
    print()


if __name__ == "__main__":
    main()
