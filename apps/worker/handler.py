"""SQS-triggered async job worker (M6.3).

A container Lambda (needs rasterio → can't be the zip warmer) subscribed to the jobs
queue. For each message it runs the calendar/trip compute off the request path and
writes the result into the shared cache, where the API's /jobs/{id} poll reads it.

The compute + result-storage live in ``apps.jobs.process`` (shared with the inline
path), so this handler is just the SQS plumbing. ``process`` records failures rather
than raising, so a bad job won't wedge the queue with infinite redeliveries.
Env: PYNIGHTSKY_BACKEND=aws, PYNIGHTSKY_CACHE_TABLE, PYNIGHTSKY_RASTER_BUCKET, AWS_REGION.
"""
import json
import logging
import os

from apps.logging_config import configure as _configure_logging
_configure_logging()

from apps import jobs

log = logging.getLogger()

_warmed = False


# Pre-warm the first-job cost centres (mirrors the API's lifespan prewarm in
# apps/api/main.py). Idempotent + cheap to re-run; guarded by `_warmed` so the
# recurring warmup ping only does the real work once per container. Each step is
# independently guarded so one failure can't wedge the rest.
def _prewarm() -> None:
    global _warmed
    if _warmed:
        return
    # S3 grids: open (fetch the tiny .json) + sample one pixel (a few-byte ranged
    # GET) to prime each dataset's GridArray, cutting cold raster I/O on the first job.
    try:
        from darkhours import ports as _p
        src = _p.get_backend().raster_source
        for dataset in ("viirs", "falchi"):
            src.sample(dataset, 0.0, 0.0)
    except Exception as _e:
        log.debug("Raster pre-warm failed: %s", _e)
    # PAD-US H3 index (columnar load) used by find_nearby Tier 1.
    try:
        from darkhours import darksky as _ds
        _ds._load_padus_h3_index()
    except Exception as _e:
        log.debug("PAD-US pre-warm failed: %s", _e)
    # Routable OSM POI index (columnar load) used by find_nearby's POI-first extraction.
    try:
        from darkhours import darksky as _ds
        _ds._load_poi_h3_index()
    except Exception as _e:
        log.debug("OSM POI pre-warm failed: %s", _e)
    # DynamoDB connection pool (same warmup call the API uses).
    try:
        from darkhours import ports as _p
        _p.get_backend().cache.get("__warmup__")
    except Exception as _e:
        log.debug("Cache pre-warm failed: %s", _e)
    # Ephemeris (mmap de421.bsp) — needed by trip/calendar jobs.
    try:
        from darkhours import sky_events as _se
        _se._ephemeris()
    except Exception as _e:
        log.debug("Ephemeris pre-warm failed: %s", _e)
    _warmed = True


if "LAMBDA_TASK_ROOT" in os.environ:
    try:
        from aws_xray_sdk.core import patch_all as _xray_patch_all
        logging.getLogger("aws_xray_sdk.core.patcher").setLevel(logging.WARNING)
        logging.getLogger("aws_xray_sdk.core.lambda_launcher").setLevel(logging.ERROR)
        _xray_patch_all()
    except ImportError:
        pass

    # Best-effort prewarm in a BACKGROUND DAEMON THREAD at module init: doing it
    # synchronously here blew Lambda's init budget (INIT_REPORT timeout). This covers
    # the case where the first event on a cold container is a real job. The scheduled
    # warmup ping (see handler) is the primary path and finishes the prewarm off the
    # user path; this thread is suspended when the handler returns, so it may not.
    import threading
    threading.Thread(target=_prewarm, daemon=True).start()


def handler(event, context=None):
    records = event.get("Records", []) if isinstance(event, dict) else []
    if not records:
        # Scheduled warmup ping (EventBridge, no SQS Records): keep this container
        # alive AND fully primed so the next real job skips the ~4.6s cold init and
        # the cold raster/PAD-US reads. Run prewarm synchronously (off the user path)
        # rather than relying on the init daemon, which Lambda freezes on return.
        _prewarm()
        return {"warmed": True}
    for record in records:
        msg = json.loads(record["body"])
        job_id = msg["job_id"]
        log.info("Processing job %s", job_id)
        jobs.process(job_id, msg["params"])
    return {"processed": len(records)}
