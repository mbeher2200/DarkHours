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

if "LAMBDA_TASK_ROOT" in os.environ:
    try:
        from aws_xray_sdk.core import patch_all as _xray_patch_all
        logging.getLogger("aws_xray_sdk.core.patcher").setLevel(logging.WARNING)
        logging.getLogger("aws_xray_sdk.core.lambda_launcher").setLevel(logging.ERROR)
        _xray_patch_all()
    except ImportError:
        pass

    # Pre-warm the first-job cost centres in a BACKGROUND DAEMON THREAD (mirrors the
    # API's lifespan prewarm in apps/api/main.py). Doing this synchronously at module
    # init blew Lambda's 10 s init budget (INIT_REPORT Status: timeout), so the wasted
    # init re-ran into the first invoke. Threaded, module init returns immediately and
    # the warming proceeds in the background, benefiting subsequent jobs on the same
    # container. Each step is independently guarded so one failure can't wedge the rest.
    def _prewarm() -> None:
        # S3 grids: open (fetch the tiny .json) + sample one pixel (a few-byte ranged
        # GET) to prime each dataset's GridArray, cutting cold raster I/O on the first job.
        try:
            from PyNightSkyPredictor import ports as _p
            src = _p.get_backend().raster_source
            for dataset in ("viirs", "falchi"):
                src.sample(dataset, 0.0, 0.0)
        except Exception as _e:
            log.debug("Raster pre-warm failed: %s", _e)
        # PAD-US H3 index (columnar load) used by find_nearby Tier 1.
        try:
            from PyNightSkyPredictor import darksky as _ds
            _ds._load_padus_h3_index()
        except Exception as _e:
            log.debug("PAD-US pre-warm failed: %s", _e)
        # DynamoDB connection pool (same warmup call the API uses).
        try:
            from PyNightSkyPredictor import ports as _p
            _p.get_backend().cache.get("__warmup__")
        except Exception as _e:
            log.debug("Cache pre-warm failed: %s", _e)
        # Ephemeris (mmap de421.bsp) — needed by trip/calendar jobs.
        try:
            from PyNightSkyPredictor import sky_events as _se
            _se._ephemeris()
        except Exception as _e:
            log.debug("Ephemeris pre-warm failed: %s", _e)

    import threading
    threading.Thread(target=_prewarm, daemon=True).start()


def handler(event, context=None):
    records = event.get("Records", []) if isinstance(event, dict) else []
    for record in records:
        msg = json.loads(record["body"])
        job_id = msg["job_id"]
        log.info("Processing job %s", job_id)
        jobs.process(job_id, msg["params"])
    return {"processed": len(records)}
