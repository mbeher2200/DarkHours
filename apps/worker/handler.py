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

    # Pre-warm both S3 COGs during Lambda init so the first job doesn't pay for
    # cold GDAL VSI_CACHE misses (header reads + overview structure = ~10-15s on
    # a fresh container). Opening + sampling one pixel caches the COG index and
    # at least one tile, cutting the first real job's raster I/O to near-zero.
    def _prewarm_rasters() -> None:
        try:
            import rasterio
            from PyNightSkyPredictor import ports as _p
            src = _p.get_backend().raster_source
            for dataset in ("viirs", "falchi"):
                path = src.path_for(dataset, show_progress=False)
                with rasterio.open(path) as ds:
                    list(ds.sample([(0.0, 0.0)]))   # one pixel primes the file header + tile
        except Exception as _e:
            log.debug("Raster pre-warm failed: %s", _e)

    _prewarm_rasters()


def handler(event, context=None):
    records = event.get("Records", []) if isinstance(event, dict) else []
    for record in records:
        msg = json.loads(record["body"])
        job_id = msg["job_id"]
        log.info("Processing job %s", job_id)
        jobs.process(job_id, msg["params"])
    return {"processed": len(records)}
