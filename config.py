#!/usr/bin/env python3
"""Load user configuration with built-in defaults."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.json"

_DEFAULTS = {
    "targets": {
        "min_elevation_deg":        20,
        "moon_min_separation_deg":  30,
        "moon_max_illumination_pct": 50,
    },
    "prime_targets": {
        "min_peak_altitude_deg": 40,
        "min_window_hours":       1.0,
    },
}


def load() -> dict:
    """Return config merged over defaults. Missing keys fall back to defaults."""
    if not _CONFIG_PATH.exists():
        return _DEFAULTS

    try:
        data = json.loads(_CONFIG_PATH.read_text())
    except Exception as e:
        log.warning("Could not read config.json: %s — using defaults", e)
        return _DEFAULTS

    result = {}
    for section, defaults in _DEFAULTS.items():
        result[section] = {**defaults, **data.get(section, {})}
    return result
