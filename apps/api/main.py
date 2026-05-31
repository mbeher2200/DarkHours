"""PyNightSky HTTP API — a thin JSON layer over the engine.

Wraps predictor.assemble_night() and trip.plan_trip() as FastAPI endpoints,
reusing the same location/timezone resolution the CLI uses. No engine logic
lives here; handlers only resolve inputs, call the engine, and serialize.

Run locally:   uvicorn apps.api.main:app --reload --port 8080
Backend:       PYNIGHTSKY_BACKEND=local (default) or =aws (+ table/bucket env).
"""
import calendar as _cal
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from PyNightSkyPredictor import location as _loc
from PyNightSkyPredictor import trip as _trip
from PyNightSkyPredictor.predictor import assemble_night

from .serializers import night_report_to_dict, trip_report_to_dict

app = FastAPI(title="PyNightSky API", version="0.1.0",
              description="Night-sky quality scoring for astrophotography planning.")

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


# ── input bounds (data sanity + abuse/DoS guards) ────────────────────────────
_MIN_DATE = date(1900, 1, 1)        # de421.bsp ephemeris coverage (~1900–2050)
_MAX_DATE = date(2050, 12, 31)
_MAX_TRIP_DAYS = 30                  # /trip date-range span cap
_MAX_TRIP_LOCATIONS = 10            # /trip location-count cap
_MAX_NAME_LEN = 200                 # geocode query length cap


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_date(s: str | None, field: str = "date") -> date:
    if not s:
        return date.today()
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
        return lat, lon, f"{lat:.4f}°, {lon:.4f}°", tz
    raise HTTPException(400, "Provide 'location' or both 'lat' and 'lon'.")


def _month_bounds(month: str | None) -> tuple[date, date]:
    if not month:
        start = date.today().replace(day=1)
    else:
        try:
            start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise HTTPException(400, f"Invalid month {month!r} (expected YYYY-MM).")
    last = _cal.monthrange(start.year, start.month)[1]
    end = start.replace(day=last)
    if start < _MIN_DATE or end > _MAX_DATE:
        raise HTTPException(
            400, f"month {start.strftime('%Y-%m')} is outside the supported range "
                 f"{_MIN_DATE.year}..{_MAX_DATE.year}.")
    return start, end


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Liveness probe for App Runner — no AWS calls, always fast."""
    return {"status": "ok"}


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
    return night_report_to_dict(report)


@app.get("/calendar")
def calendar(
    location: str | None = Query(None, max_length=_MAX_NAME_LEN),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    month: str | None = Query(None, description="YYYY-MM; default current month"),
    weather: bool = False,
):
    """Month-view night scores for one location (synchronous; see M6 for async)."""
    la, lo, disp, tz = _resolve(location, lat, lon)
    start, end = _month_bounds(month)
    loc_dict = {"lat": la, "lon": lo, "display_name": disp, "tz_name": str(tz)}
    report = _trip.plan_trip([loc_dict], start, end, fetch_weather=weather)
    return trip_report_to_dict(report)


@app.get("/trip")
def trip(
    locations: list[str] = Query(..., description="Repeatable location name(s) to compare"),
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    weather: bool = False,
):
    """Multi-location score matrix across a date range (synchronous; see M6 for async)."""
    if len(locations) > _MAX_TRIP_LOCATIONS:
        raise HTTPException(400, f"Too many locations: {len(locations)} (max {_MAX_TRIP_LOCATIONS}).")
    s, e = _parse_date(start, "start"), _parse_date(end, "end")
    if e < s:
        raise HTTPException(400, "'end' must be on or after 'start'.")
    if (e - s).days > _MAX_TRIP_DAYS:
        raise HTTPException(
            400, f"Date range too large: {(e - s).days} days (max {_MAX_TRIP_DAYS}). "
                 f"Narrow the range — async support for longer trips is planned (M6).")
    locs = []
    for name in locations:
        if len(name) > _MAX_NAME_LEN:
            raise HTTPException(400, f"Location name too long (max {_MAX_NAME_LEN}).")
        try:
            la, lo, disp, tz_name = _loc.resolve(name)
        except ValueError as ex:
            raise HTTPException(404, f"{name!r}: {ex}")
        except RuntimeError as ex:
            raise HTTPException(502, f"{name!r}: {ex}")
        locs.append({"lat": la, "lon": lo, "display_name": disp, "tz_name": tz_name})
    report = _trip.plan_trip(locs, s, e, fetch_weather=weather)
    return trip_report_to_dict(report)
