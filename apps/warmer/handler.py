"""Scheduled TLE cache warmer (M6.2).

Runs on a schedule (EventBridge → Lambda) to keep the satellite TLEs fresh in the
shared DynamoDB cache, so user ``/night?satellites=true`` requests are cache hits
and the app is decoupled from Celestrak's availability/rate limits. TLE is GLOBAL
(one dataset for every user and every location), so there is nothing per-region to
warm — this just refreshes the handful of tracked-satellite TLEs plus the Starlink
group, which ``get_tle`` / ``get_starlink_train_tles`` fetch-and-cache under the
same keys the request path reads.

Imports stay light on purpose: ``tle_provider`` only touches the cache port
(DynamoDB), never the raster adapter (which would pull in rasterio/GDAL — 335 MB).
That's why this Lambda can be a tiny rasterio-free zip. Env it expects:
``PYNIGHTSKY_BACKEND=aws``, ``PYNIGHTSKY_CACHE_TABLE``, ``AWS_REGION``.
"""
import logging

from darkhours import tle_provider as _tle

log = logging.getLogger()
log.setLevel(logging.INFO)

# (NORAD id, label) for the individually-tracked satellites.
_TRACKED = [
    (_tle.ISS_NORAD_ID,      "ISS"),
    (_tle.HUBBLE_NORAD_ID,   "Hubble"),
    (_tle.TIANGONG_NORAD_ID, "Tiangong"),
]


def _status(stale: bool, error) -> str:
    if error is None and not stale:
        return "ok"
    return f"{'stale' if stale else 'FAIL'} ({error})"


def handler(event=None, context=None):
    """EventBridge target: refresh every tracked TLE into the shared cache."""
    results: dict[str, str] = {}
    ok = True

    for norad, label in _TRACKED:
        r = _tle.get_tle(norad)          # fetch + cache under "tle|<norad>"
        results[label] = _status(r.stale, r.error)
        ok = ok and (r.error is None and not r.stale)

    trains, stale, err = _tle.get_starlink_train_tles()   # caches "tle|group|starlink"
    results["starlink"] = (f"ok ({len(trains)} trains)"
                           if (err is None and not stale) else _status(stale, err))
    ok = ok and (err is None and not stale)

    summary = {"ok": ok, "results": results}
    (log.info if ok else log.warning)("TLE warm: %s", summary)
    return summary
