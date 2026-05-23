#!/usr/bin/env python3
"""Lunar cycle calculator for a given date."""

import math
from datetime import date, datetime


SYNODIC_PERIOD = 29.53059  # days per lunar cycle
KNOWN_NEW_MOON = date(2000, 1, 6)  # reference new moon

PHASE_NAMES = [
    (0.0,   "New Moon"),
    (0.125, "Waxing Crescent"),
    (0.25,  "First Quarter"),
    (0.375, "Waxing Gibbous"),
    (0.5,   "Full Moon"),
    (0.625, "Waning Gibbous"),
    (0.75,  "Third Quarter"),
    (0.875, "Waning Crescent"),
    (1.0,   "New Moon"),
]


def lunar_phase(target_date: date) -> dict:
    """
    Calculate the lunar phase for a given date.

    Returns a dict with:
      - phase_name: human-readable name (e.g. "Waxing Gibbous")
      - cycle_fraction: 0.0 (new moon) → 1.0 (next new moon)
      - illumination_pct: approximate percentage of the moon that is illuminated
      - days_into_cycle: days elapsed since last new moon
      - days_until_next_new_moon: days remaining until the next new moon
    """
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    days_since_ref = (target_date - KNOWN_NEW_MOON).days
    cycle_fraction = (days_since_ref % SYNODIC_PERIOD) / SYNODIC_PERIOD
    days_into_cycle = cycle_fraction * SYNODIC_PERIOD

    # Illumination: 0% at new moon, 100% at full moon, back to 0%
    illumination_pct = (1 - math.cos(2 * math.pi * cycle_fraction)) / 2 * 100

    # Find phase name by picking the closest boundary
    phase_name = PHASE_NAMES[0][1]
    for threshold, name in PHASE_NAMES:
        if cycle_fraction >= threshold:
            phase_name = name

    days_until_next_new_moon = SYNODIC_PERIOD - days_into_cycle

    return {
        "phase_name": phase_name,
        "cycle_fraction": round(cycle_fraction, 4),
        "illumination_pct": round(illumination_pct, 1),
        "days_into_cycle": round(days_into_cycle, 1),
        "days_until_next_new_moon": round(days_until_next_new_moon, 1),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Calculate the lunar phase for a date.")
    parser.add_argument(
        "date",
        nargs="?",
        default=date.today().isoformat(),
        help="Date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Error: '{args.date}' is not a valid date (expected YYYY-MM-DD).")
        raise SystemExit(1)

    result = lunar_phase(target)

    print(f"Date:                    {target}")
    print(f"Phase:                   {result['phase_name']}")
    print(f"Illumination:            {result['illumination_pct']}%")
    print(f"Days into cycle:         {result['days_into_cycle']} / {SYNODIC_PERIOD}")
    print(f"Days until new moon:     {result['days_until_next_new_moon']}")


if __name__ == "__main__":
    main()
