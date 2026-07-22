"""Tiny shared env-var parsing helper.

Used by both circuit_breaker.py and rate_limiter.py for their "read once at
import" boolean flags (PYNIGHTSKY_CIRCUIT_BREAKER_*, PYNIGHTSKY_RATE_LIMIT_*).
Deliberately provider-agnostic so neither of those two modules has to import
the other just to share this — they otherwise never call into each other.
"""
from __future__ import annotations

import os


def flag(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")
