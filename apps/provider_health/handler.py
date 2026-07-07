"""Weather provider health monitor.

Runs on a schedule (EventBridge → Lambda) to poll the two weather providers the
engine depends on (Open-Meteo, 7Timer) and record UP/DOWN status + latency to
DynamoDB, so an SRE can alarm on sustained provider outages independently of
user traffic. Zero third-party imports beyond boto3 (ships with the runtime) —
this Lambda never touches the PyNightSkyPredictor engine, so it stays a tiny
zip with no rasterio/GDAL tax.

Env it expects: ``PROVIDER_HEALTH_TABLE``.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.exceptions import ClientError

PROVIDERS: list[dict[str, str]] = [
    {
        "id": "open-meteo",
        "url": (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=33.3943&longitude=-104.5230&current=temperature_2m"
        ),
    },
    {
        "id": "7timer",
        "url": "http://www.7timer.info/bin/api.pl?lon=-104.5230&lat=33.3943&product=astro&output=json",
    },
]

TIMEOUT_S = 4.0
RETRY_DELAY_S = 2.0
METRIC_NAMESPACE = "PyNightSky/WeatherProviders"

log = logging.getLogger()
log.setLevel(logging.INFO)

_dynamodb = boto3.resource("dynamodb")


def _table():
    return _dynamodb.Table(os.environ["PROVIDER_HEALTH_TABLE"])


def _log(level: str, provider: str, attempt: int, **fields: Any) -> None:
    getattr(log, level)(json.dumps({"provider": provider, "attempt_number": attempt, **fields}))


def _emit_provider_metrics(provider: str, latency_ms: float, is_up: bool) -> None:
    """CloudWatch embedded metric format — ProviderUp + HTTPVerificationLatency."""
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["Provider"]],
                "Metrics": [
                    {"Name": "ProviderUp", "Unit": "Count"},
                    {"Name": "HTTPVerificationLatency", "Unit": "Milliseconds"},
                ],
            }],
        },
        "Provider": provider,
        "ProviderUp": 1 if is_up else 0,
        "HTTPVerificationLatency": round(latency_ms, 2),
    }
    print(json.dumps(emf))


def _emit_dynamo_write_failure(provider: str) -> None:
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["Provider"]],
                "Metrics": [{"Name": "DynamoDBWriteFailure", "Unit": "Count"}],
            }],
        },
        "Provider": provider,
        "DynamoDBWriteFailure": 1,
    }
    print(json.dumps(emf))


def _probe(url: str) -> tuple[int, float]:
    """Returns (http_status_code, latency_ms). Raises on network failure."""
    req = urllib.request.Request(url, method="GET")

    # FIX: Build an opener that explicitly ONLY supports HTTP and HTTPS.
    # This completely removes the FileHandler and FTPHandler, permanently
    # neutralizing the Arbitrary File Read / SSRF vulnerabilities.
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler()
    )

    t0 = time.monotonic()

    # Use our locked-down opener instead of the default urllib.request.urlopen
    with opener.open(req, timeout=TIMEOUT_S) as resp:
        resp.read()  # drain body to free the connection
        status = resp.status

    return status, (time.monotonic() - t0) * 1000


def _write_status(provider_id: str, status: str, epoch: int) -> None:
    try:
        _table().put_item(Item={"provider_id": provider_id, "status": status, "last_checked": epoch})
    except ClientError as exc:
        _emit_dynamo_write_failure(provider_id)
        _log("error", provider_id, 0, event="dynamo_write_failure",
             error_type=type(exc).__name__, error_message=str(exc))


def _check_provider(provider: dict[str, str]) -> None:
    """One attempt, then (on failure) one retry after RETRY_DELAY_S, then persist."""
    pid, url = provider["id"], provider["url"]
    last_latency_ms = TIMEOUT_S * 1000

    for attempt in (1, 2):
        try:
            status_code, latency_ms = _probe(url)
            is_up = status_code == 200
            _log("info", pid, attempt, event="probe_complete", http_status=status_code,
                 latency_ms=round(latency_ms, 2), status="UP" if is_up else "DOWN")
            _emit_provider_metrics(pid, latency_ms, is_up)
            _write_status(pid, "UP" if is_up else "DOWN", int(time.time()))
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_latency_ms = TIMEOUT_S * 1000
            _log("warning", pid, attempt, event="probe_error",
                 status="RETRYING" if attempt == 1 else "DOWN",
                 error_type=type(exc).__name__, error_message=str(exc))
            if attempt == 1:
                time.sleep(RETRY_DELAY_S)

    _emit_provider_metrics(pid, last_latency_ms, is_up=False)
    _write_status(pid, "DOWN", int(time.time()))


def handler(event=None, context=None):
    """EventBridge target: check all providers independently, in parallel."""
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = {pool.submit(_check_provider, p): p["id"] for p in PROVIDERS}
        for future in as_completed(futures):
            pid = futures[future]
            try:
                future.result()
            except Exception as exc:  # a bug in one provider's check must not kill the run
                _log("error", pid, 0, event="unhandled_exception",
                     error_type=type(exc).__name__, error_message=str(exc))

    return {"checked": [p["id"] for p in PROVIDERS]}
