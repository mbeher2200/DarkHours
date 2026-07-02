"""PyNightSky HTTP API — a thin JSON layer over the engine.

Wraps predictor.assemble_night() as FastAPI endpoints, reusing the same
location/timezone resolution the CLI uses. No engine logic
lives here; handlers only resolve inputs, call the engine, and serialize.

Run locally:   uvicorn apps.api.main:app --reload --port 8080
Backend:       PYNIGHTSKY_BACKEND=local (default) or =aws (+ table/bucket env).
"""
# Configure JSON logging before any engine import emits records.
from apps.logging_config import configure as _configure_logging
_configure_logging()

import functools
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Request

from PyNightSkyPredictor import location as _loc
from PyNightSkyPredictor.predictor import assemble_night

from apps import jobs
from .serializers import night_report_to_dict

# X-Ray tracing: LAMBDA_TASK_ROOT is always set inside Lambda but never in local dev
# or tests, so this guard keeps tests clean and avoids patching urllib/boto3 locally.
_xray_enabled = False
if "LAMBDA_TASK_ROOT" in os.environ:
    try:
        from aws_xray_sdk.core import xray_recorder, patch_all as _xray_patch_all
        xray_recorder.configure(context_missing="LOG_ERROR")
        # Suppress INFO noise from the patcher ("successfully patched module ...") and
        # the init-phase lambda_launcher WARNINGs ("Subsegment discarded ...").
        logging.getLogger("aws_xray_sdk.core.patcher").setLevel(logging.WARNING)
        logging.getLogger("aws_xray_sdk.core.lambda_launcher").setLevel(logging.ERROR)
        _xray_patch_all()
        _xray_enabled = True
    except ImportError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm expensive resources in a daemon thread so the lifespan yields
    # immediately and Lambda Web Adapter gets its readiness signal without delay.
    # Previously this ran synchronously and caused LWA's 10s probe to time out.
    if "LAMBDA_TASK_ROOT" in os.environ:
        import threading

        def _prewarm() -> None:
            try:
                from PyNightSkyPredictor import sky_events as _se
                _se._ephemeris()   # mmap de421.bsp into the process
            except Exception as _e:
                logging.getLogger(__name__).debug("Ephemeris pre-warm failed: %s", _e)
            try:
                from PyNightSkyPredictor import ports as _p
                _p.get_backend().cache.get("__warmup__")   # open DynamoDB connection pool
            except Exception as _e:
                logging.getLogger(__name__).debug("Cache pre-warm failed: %s", _e)
            try:
                from PyNightSkyPredictor import light_dome as _ld
                _ld.load_lightdome_index()   # mmap/parse the ~MB light-dome H3 index once
            except Exception as _e:
                logging.getLogger(__name__).debug("Light-dome index pre-warm failed: %s", _e)
            try:
                jobs._sqs()   # build the boto3 SQS client off the first enqueue's path
            except Exception as _e:
                logging.getLogger(__name__).debug("SQS pre-warm failed: %s", _e)

        threading.Thread(target=_prewarm, daemon=True).start()
    yield


app = FastAPI(title="PyNightSky API", version="0.1.0",
              description="Night-sky quality scoring for astrophotography planning.",
              lifespan=lifespan)

# CORS origins come from env (comma-separated); default is none, so no site can
# read the API cross-origin in a browser. Non-browser clients (curl, server-to-
# server) are unaffected. The SPA origin is added via PYNIGHTSKY_CORS_ORIGINS in M7.
_cors_origins = [o.strip() for o in os.environ.get("PYNIGHTSKY_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Structured access log: path, status, duration, Lambda request-id.
_access_log = logging.getLogger("pynightsky.access")

class _AccessLog(BaseHTTPMiddleware):
    async def dispatch(self, request: _Request, call_next):
        t0 = time.monotonic()
        response = await call_next(request)
        _access_log.info(
            "request",
            extra={
                "path": request.url.path,
                "query": str(request.url.query),
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
                "request_id": request.headers.get("x-amzn-request-id", ""),
            },
        )
        return response

app.add_middleware(_AccessLog)

if _xray_enabled:
    try:
        from aws_xray_sdk.ext.starlette.middleware import XRayMiddleware
        app.add_middleware(XRayMiddleware, recorder=xray_recorder)
    except ImportError:
        pass


# ── input bounds (data sanity + abuse/DoS guards) ────────────────────────────
_MIN_DATE = date(1900, 1, 1)        # de421.bsp ephemeris coverage (~1900–2050)
_MAX_DATE = date(2050, 12, 31)
_MAX_NAME_LEN = 200                 # geocode query length cap
_NEARBY_RADIUS_DEFAULT = 60         # /nearby default search radius (miles)
_NEARBY_RADIUS_MAX = 120            # 10 of 11 sample rings; good density up to ~2.5h drive


# ── health check helpers ──────────────────────────────────────────────────────

_CACHE_CHECK_TTL = 60   # reuse cache round-trip result this long (seconds)
_cache_check_state: dict = {"ts": 0.0, "result": {}}


def _check_cache_health() -> dict:
    """Round-trip the active cache backend. For the local backend, also checks disk space.
    Result is cached for _CACHE_CHECK_TTL seconds so rapid health polls don't hammer DynamoDB."""
    now = time.monotonic()
    if now - _cache_check_state["ts"] < _CACHE_CHECK_TTL:
        return _cache_check_state["result"]
    try:
        from PyNightSkyPredictor import ports as _p
        from PyNightSkyPredictor.cache import _CACHE_DIR
        backend = _p.get_backend()
        cache = backend.cache
        cache.set("__health_probe__", 1, ttl_seconds=120)
        if cache.get("__health_probe__") != 1:
            result: dict = {"status": "error", "backend": backend._name,
                            "detail": "read-back mismatch after set"}
        else:
            result = {"status": "ok", "backend": backend._name}
            if backend._name == "local":
                probe_path = _CACHE_DIR if _CACHE_DIR.exists() else _CACHE_DIR.parent
                disk = shutil.disk_usage(probe_path)
                free_pct = disk.free / disk.total * 100
                result["disk_free_pct"] = round(free_pct, 1)
                if free_pct < 5:
                    result.update(status="error",
                                  detail=f"disk nearly full ({free_pct:.0f}% free)")
                elif free_pct < 15:
                    result.update(status="degraded",
                                  detail=f"low disk space ({free_pct:.0f}% free)")
    except Exception as e:
        result = {"status": "error", "detail": str(e)[:200]}
    _cache_check_state.update(ts=now, result=result)
    return result


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_date(s: str | None, field: str = "date") -> date:
    if not s:
        return datetime.now(timezone.utc).date()
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid {field} {s!r} (expected YYYY-MM-DD).")
    if not (_MIN_DATE <= d <= _MAX_DATE):
        raise HTTPException(
            400, f"{field} {s} is outside the supported ephemeris range "
                 f"{_MIN_DATE.isoformat()}..{_MAX_DATE.isoformat()}.")
    return d


def _resolve(location: str | None, lat: float | None, lon: float | None):
    """Resolve a request's place to (lat, lon, display_name, ZoneInfo)."""
    if location:
        try:
            la, lo, disp, tz_name = _loc.resolve(location)
        except ValueError as e:
            raise HTTPException(404, str(e))      # not found
        except RuntimeError as e:
            raise HTTPException(502, str(e))      # geocoder unreachable
        return la, lo, disp, ZoneInfo(tz_name)
    if lat is not None and lon is not None:
        try:
            tz = _loc.timezone_for(lat, lon)
        except ValueError as e:
            raise HTTPException(400, str(e))   # e.g. no timezone for the point
        disp = _loc.reverse_geocode(lat, lon) or f"{lat:.4f}°, {lon:.4f}°"
        return lat, lon, disp, tz
    raise HTTPException(400, "Provide 'location' or both 'lat' and 'lon'.")


# ── endpoints ────────────────────────────────────────────────────────────────

@app.post("/warmup")
async def warmup():
    """EventBridge scheduled warmup ping — keeps one Lambda container alive."""
    return {"warm": True}

@app.get("/healthz")
def healthz():
    """Readiness probe: cache connectivity and observed 3rd-party provider status.

    Provider states reflect real call outcomes — no synthetic outbound probes.
    A provider is absent from the response until the first request has been made.

    HTTP 503 when overall == error; 200 for ok or degraded.
    """
    from PyNightSkyPredictor import provider_health as _ph
    checks = {"cache": _check_cache_health(), **_ph.snapshot()}
    statuses = {c.get("status") for c in checks.values()}
    overall = ("error"    if "error"    in statuses else
               "degraded" if "degraded" in statuses else "ok")
    return JSONResponse(
        status_code=503 if overall == "error" else 200,
        content={"status": overall, "checks": checks},
    )


@app.get("/night")
def night(
    location: str | None = Query(None, max_length=_MAX_NAME_LEN),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    date: str | None = Query(None, description="YYYY-MM-DD; default today"),
    weather: bool = True,
    targets: bool = False,
    satellites: bool = False,
):
    """Single-night report for a location/date (mirrors the CLI single-night path)."""
    la, lo, disp, tz = _resolve(location, lat, lon)
    target = _parse_date(date)
    try:
        report = assemble_night(
            la, lo, target, tz, display_name=disp,
            fetch_weather=weather, fetch_targets=targets, fetch_satellites=satellites,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))          # e.g. polar day/night: no sunset
    d = night_report_to_dict(report)
    # Strip sections that were not requested — reduces payload 60–95%.
    if not satellites:
        d.pop("sat_passes", None)
        d.pop("starlink_trains", None)
    if not targets:
        d.pop("visible_targets", None)
        d.pop("mw_summary", None)
    if not weather:
        d.pop("weather_points", None)
    return d


@functools.lru_cache(maxsize=512)
def _suggest_cached(q: str) -> list:
    """In-process LRU cache for typeahead suggestions.  Avoids repeated Nominatim
    round-trips for the same prefix within the same Lambda container lifetime."""
    return _loc.suggest(q)


@app.get("/suggest")
def suggest(q: str = Query(..., min_length=1, max_length=_MAX_NAME_LEN)):
    """Typeahead place suggestions for the search box (autocomplete).

    Returns {"suggestions": [str, ...]} — display strings the client feeds back
    to /night as `location=` when one is picked. Best-effort: an empty list is a
    valid response when nothing matches.
    """
    try:
        return {"suggestions": _suggest_cached(q)}
    except RuntimeError as e:
        raise HTTPException(502, str(e))   # geocoder unreachable


def _accepted(job_id: str) -> JSONResponse:
    """202 with the job id + where to poll for the result."""
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "pending", "poll": f"/jobs/{job_id}"},
    )



@app.get("/nearby")
def nearby(
    location: str | None = Query(None, max_length=_MAX_NAME_LEN),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    radius: int = Query(_NEARBY_RADIUS_DEFAULT, ge=5, le=_NEARBY_RADIUS_MAX,
                        description="Search radius in miles (5–150)"),
):
    """Submit a nearby dark-sky search → 202 + job_id (poll /jobs/{id})."""
    # Only resolve what the job actually uses (lat/lon). The display name and
    # timezone that _resolve() also computes are discarded here, and the worker
    # reverse-geocodes the origin itself — so for raw coordinates (the web UI's
    # path) we skip the wasted timezone lookup + networked reverse-geocode.
    if location:
        la, lo, _disp, _tz = _resolve(location, None, None)   # name → coords (still needed)
    elif lat is not None and lon is not None:
        la, lo = lat, lon
    else:
        raise HTTPException(400, "Provide 'location' or both 'lat' and 'lon'.")
    job_id = jobs.submit({"type": "nearby", "lat": la, "lon": lo, "radius_miles": radius})
    return _accepted(job_id)


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    """Poll a submitted job. 404 until/unless it exists; otherwise the record:
    {status: pending|done|error, result?/error?}. `result` is the TripReport dict
    the synchronous endpoints used to return."""
    rec = jobs.get(job_id)
    if rec is None:
        raise HTTPException(404, f"Unknown or expired job {job_id!r}.")
    return rec
