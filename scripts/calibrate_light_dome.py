#!/usr/bin/env python3
"""Calibrate the LightDomeAnalyzer Major/Minor thresholds against a reference basket.

Runs the analyzer at the default 150-mile radius for a set of sites whose darkness class
is known a priori, and prints the worst-direction score distribution per class. At 150 mi
darkness is a continuum (distant-metro glow reaches almost everywhere), so the MINOR/MAJOR
constants in ``darkhours/light_dome.py`` are not separating two clean clusters:
MAJOR is set below the close-metro floor (sky-degrading), MINOR just above premier
dark-sky-park glow so a real-but-low distant metro dome still flags.

Local backend only — uses the on-disk VIIRS grid (no network). Re-run this if the default
``radius_miles`` geometry changes, since the score (an area-weighted glow index) scales
with it.

Usage:
    PYNIGHTSKY_BACKEND=local python scripts/calibrate_light_dome.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from darkhours.light_dome import LightDomeAnalyzer  # noqa: E402

# class: 'pristine' (genuinely remote even within 150 mi — should stay near zero),
#        'domed'    (a metro within ~120 mi — should flag a dome in the city direction).
# At 150 mi there is no "rural" middle cluster: distant glow makes darkness a continuum.
SITES = [
    ("pristine", "Big Bend NP, TX",             29.27, -103.30),
    ("pristine", "Cosmic Campground, NM",       33.48, -108.91),
    ("pristine", "Great Basin NP, NV",          38.98, -114.30),
    ("pristine", "Natural Bridges, UT",         37.60, -110.01),
    ("pristine", "Cherry Springs SP, PA",       41.66,  -77.82),  # premier park; distant-town ceiling
    ("domed",    "Sedona/Munds Park (Phoenix)", 34.8697, -111.4106),  # low Phoenix dome 90 mi S
    ("domed",    "Joshua Tree NP (LA/Palm Spr)", 33.87, -115.90),
    ("domed",    "25mi W of Las Vegas",          36.17, -115.60),
    ("domed",    "30mi N of Phoenix",            33.88, -112.07),
    ("domed",    "30mi S of Denver",             39.30, -104.99),
    ("domed",    "40mi NW of Dallas",            33.30,  -97.30),
    ("domed",    "Pine Barrens NJ (Phila/NYC)",  39.80,  -74.50),
]


def main() -> None:
    analyzer = LightDomeAnalyzer()
    by_class: dict[str, list[float]] = {}

    for cls, label, lat, lon in SITES:
        scores = list(analyzer.analyze(lat, lon).values())
        worst = max(scores)
        by_class.setdefault(cls, []).append(worst)
        ordered = sorted(scores, reverse=True)
        print(f"{cls:8s} {label:28s} worst={worst:8.2f}  2nd={ordered[1]:8.2f}  sum={sum(scores):9.2f}")

    print()
    for cls in ("pristine", "domed"):
        v = by_class.get(cls, [])
        if not v:
            continue
        print(f"{cls:8s}: worst-direction score  min={min(v):.3f}  "
              f"median={np.median(v):.3f}  max={max(v):.3f}  (n={len(v)})")

    pristine = by_class.get("pristine", [])
    domed = by_class.get("domed", [])
    if pristine and domed:
        print()
        print(f"pristine ceiling={max(pristine):.3f}  ->  domed floor={min(domed):.3f} "
              "(continuum at 150 mi — these may overlap)")
        from darkhours.light_dome import MINOR_DOME_THRESHOLD, MAJOR_DOME_THRESHOLD
        print(f"current constants: MINOR_DOME_THRESHOLD={MINOR_DOME_THRESHOLD}, "
              f"MAJOR_DOME_THRESHOLD={MAJOR_DOME_THRESHOLD}")


if __name__ == "__main__":
    main()
