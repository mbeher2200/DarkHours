"""Async job lifecycle for the long /calendar + /nearby computes (M6.3).

Fully-async contract: the endpoints resolve + validate synchronously (fast, cached
geocode; 4xx on bad input immediately), then hand the heavy multi-night compute to a
job. In the cloud the job is enqueued to SQS and a container-Lambda worker runs it;
with no queue configured (local/dev/tests) the job runs INLINE, so the very same
endpoints work everywhere and the CLI stays the parity oracle.

Job records live in the shared cache (``job|<id>``) with a TTL:
    {"status": "pending"}                      # enqueued, not yet run
    {"status": "done",  "result": {...}}       # finished
    {"status": "error", "error": "..."}        # failed (recorded, not retried)

The result is a TripReport dict — the same JSON the synchronous endpoints used to
return — so clients get an identical payload, just one poll later.
"""
import json
import os
import uuid
from datetime import date

from PyNightSkyPredictor import cache as _cache
from PyNightSkyPredictor import trip as _trip
from PyNightSkyPredictor.darksky import find_nearby as _find_nearby
from apps.api.serializers import trip_report_to_dict

_JOB_PREFIX = "job|"
JOB_TTL = 24 * 3600                       # results auto-expire after a day
_QUEUE_URL_ENV = "PYNIGHTSKY_JOBS_QUEUE_URL"

_sqs_client = None


def _sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3  # lazy: only the cloud (enqueue) path needs it
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        _sqs_client = boto3.client("sqs", region_name=region)
    return _sqs_client


def _run_nearby_job(params: dict) -> dict:
    """Execute a resolved nearby job → JSON-safe dict from find_nearby()."""
    result = _find_nearby(params["lat"], params["lon"], int(params.get("radius_miles", 60)))
    if result is None:
        raise RuntimeError("Light pollution data unavailable (raster grid unreadable).")
    return result


def run_job(params: dict) -> dict:
    """Dispatch a job to the appropriate handler based on params['type'].

    params for "nearby"   : {"type": "nearby", "lat": float, "lon": float, "radius_miles": int}
    params for "calendar" : {"type": "calendar", "locs": [...], "start": "YYYY-MM-DD",
                             "end": "YYYY-MM-DD", "weather": bool, "weather_horizon_days"?: int}
    Defaulting to the calendar path when "type" is absent keeps old records working.
    """
    if params.get("type") == "nearby":
        return _run_nearby_job(params)
    # calendar path — locations already geocoded at submit time
    report = _trip.plan_trip(
        params["locs"],
        date.fromisoformat(params["start"]),
        date.fromisoformat(params["end"]),
        fetch_weather=bool(params.get("weather", False)),
        weather_horizon_days=params.get("weather_horizon_days") or _trip._FORECAST_DAYS,
    )
    return trip_report_to_dict(report)


def process(job_id: str, params: dict) -> None:
    """Run a job and store its terminal record (done/error). Never raises — a failure
    is recorded so the poller sees it and SQS doesn't redeliver forever."""
    key = _JOB_PREFIX + job_id
    try:
        _cache.set(key, {"status": "done", "result": run_job(params)}, ttl_seconds=JOB_TTL)
    except Exception as e:  # noqa: BLE001 — surface the message to the poller
        _cache.set(key, {"status": "error", "error": str(e)}, ttl_seconds=JOB_TTL)


def submit(params: dict) -> str:
    """Create a job for *params* and return its id. Enqueue to SQS if a queue is
    configured; otherwise run it inline (local/dev/tests have the same contract)."""
    job_id = uuid.uuid4().hex
    queue_url = os.environ.get(_QUEUE_URL_ENV)
    if queue_url:
        _cache.set(_JOB_PREFIX + job_id, {"status": "pending"}, ttl_seconds=JOB_TTL)
        _sqs().send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"job_id": job_id, "params": params}),
        )
    else:
        process(job_id, params)
    return job_id


def get(job_id: str) -> dict | None:
    """Return the job record ({status, result?/error?}) or None if unknown/expired."""
    return _cache.get(_JOB_PREFIX + job_id)
