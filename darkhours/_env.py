"""Tiny shared env-var parsing helper.

Used by both circuit_breaker.py and rate_limiter.py for their "read once at
import" boolean flags (PYNIGHTSKY_CIRCUIT_BREAKER_*, PYNIGHTSKY_RATE_LIMIT_*),
and by every module that builds a DynamoDB client for the shared pool size.
Deliberately provider-agnostic so neither of those two modules has to import
the other just to share this — they otherwise never call into each other.
"""
from __future__ import annotations

import os


def flag(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# Every DynamoDB client the engine builds is sized from here (cache.py,
# feature_flags.py, circuit_breaker.py). botocore's default of 10 is below
# find_nearby's fan-out, and a connection returned to a full pool is discarded,
# so the next caller pays a fresh TCP+TLS handshake. One knob rather than a
# literal per client, so the three sites cannot drift apart.
#
# apps/provider_health/handler.py builds its own unconfigured client. It is a
# scheduled monitor with no fan-out, and it ships in a separately deployed
# stack, so it is deliberately left alone.
DYNAMO_POOL_DEFAULT = 25


def dynamo_pool() -> int:
    """Per-client DynamoDB connection-pool size (PYNIGHTSKY_DYNAMO_POOL)."""
    try:
        return max(1, int(os.environ.get("PYNIGHTSKY_DYNAMO_POOL",
                                         str(DYNAMO_POOL_DEFAULT))))
    except ValueError:
        return DYNAMO_POOL_DEFAULT
