"""Scheduled TLE cache warmer (M6.2).

Runs on a schedule (EventBridge → Lambda) to keep the satellite TLEs fresh in the
shared DynamoDB cache. This is not an optimization — it is the *only* thing that
populates those keys. ``/night?satellites=true`` reads the cache and never fetches
(see ``tle_provider``'s module docstring), so if this job stops working, satellite
data silently disappears from every request. That is why it verifies its own writes
and alarms on failure rather than reporting a status derived from the fetch alone.

TLE is GLOBAL (one dataset for every user and every location), so there is nothing
per-region to warm — this refreshes the handful of tracked-satellite TLEs, the
recent-launch dates the train filter needs, and the filtered Starlink train list,
under the same keys the request path reads.

Imports stay light on purpose: ``tle_provider`` only touches the cache port
(DynamoDB), never the raster adapter (which would pull in rasterio/GDAL — 335 MB).
That's why this Lambda can be a tiny rasterio-free zip. Env it expects:
``PYNIGHTSKY_BACKEND=aws``, ``PYNIGHTSKY_CACHE_TABLE``, ``AWS_REGION``.
"""
import json
import logging
import time

from darkhours import tle_provider as _tle

log = logging.getLogger()
log.setLevel(logging.INFO)

METRIC_NAMESPACE = "PyNightSky/Tle"


def _emit_key_metrics(key_result) -> None:
    """CloudWatch embedded metric format, per cache key.

    TleWarmSuccess is the signal that matters: it is 1 only when the value was read
    back out of the cache after the write, so it cannot report success for a write
    that was silently rejected. TleCachedBytes exists so an item creeping toward
    DynamoDB's 400 KB ceiling is visible on a graph before it crosses it.

    TleWarmItems is how many entries the value holds. A verified write of an empty
    list is a success by every other measure here, and for the Starlink train key an
    empty list is also the normal state between launches — which is exactly how a
    filter that could never match anything stayed invisible. The count separates
    "nothing to show tonight" from "nothing, for weeks".
    """
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["Key"]],
                "Metrics": [
                    {"Name": "TleWarmSuccess", "Unit": "Count"},
                    {"Name": "TleCachedBytes", "Unit": "Bytes"},
                    {"Name": "TleWarmItems", "Unit": "Count"},
                ],
            }],
        },
        "Key": key_result.label,
        "TleWarmSuccess": 1 if key_result.verified else 0,
        "TleCachedBytes": key_result.bytes,
        "TleWarmItems": key_result.count,
    }
    print(json.dumps(emf))


def _emit_warm_failure(count: int) -> None:
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [[]],
                "Metrics": [{"Name": "TleWarmFailure", "Unit": "Count"}],
            }],
        },
        "TleWarmFailure": count,
    }
    print(json.dumps(emf))


def handler(event=None, context=None):
    """EventBridge target: refresh every tracked TLE into the shared cache.

    ``force=True``: refresh unconditionally rather than only when the entry has
    already expired. A warmer that returns early on a fresh cache hit can only
    repair expiry, never prevent it — the refresh then lands on whichever user asks
    first after the entry dies. Refreshing every run keeps the row's age below one
    schedule interval, so it never reaches its TTL.

    The longer per-socket timeout is deliberate: nobody is waiting on this, and
    every fetch that lands here is one nobody else has to make.
    """
    summary = _tle.warm_cache(timeout=_tle._WARM_FETCH_TIMEOUT, force=True)

    failures = 0
    for key_result in summary.keys:
        _emit_key_metrics(key_result)
        if not key_result.verified or key_result.error is not None:
            failures += 1
    _emit_warm_failure(failures)

    out = summary.as_dict()
    (log.info if summary.ok else log.warning)("TLE warm: %s", out)
    return out
