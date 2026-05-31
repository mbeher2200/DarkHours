"""PyNightSky HTTP API — a thin JSON layer over the engine.

Wraps predictor.assemble_night() and trip.plan_trip() as FastAPI endpoints,
reusing the same location/timezone resolution the CLI uses. No engine logic
lives here; handlers only resolve inputs, call the engine, and serialize.

Run locally:   uvicorn apps.api.main:app --reload --port 8080
Backend:       PYNIGHTSKY_BACKEND=local (default) or =aws (+ table/bucket env).
"""
import calendar as _cal
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

# Permissive CORS for now; tighten to the SPA origin in M7.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid date {s!r} (expected YYYY-MM-DD).")


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
        return lat, lon, f"{lat:.4f}°, {lon:.4f}°", _loc.timezone_for(lat, lon)
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
    return start, start.replace(day=last)


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Liveness probe for App Runner — no AWS calls, always fast."""
    return {"status": "ok"}


@app.get("/night")
def night(
    location: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
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
    location: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
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
    locs = []
    for name in locations:
        try:
            la, lo, disp, tz_name = _loc.resolve(name)
        except ValueError as e:
            raise HTTPException(404, f"{name!r}: {e}")
        except RuntimeError as e:
            raise HTTPException(502, f"{name!r}: {e}")
        locs.append({"lat": la, "lon": lo, "display_name": disp, "tz_name": tz_name})
    report = _trip.plan_trip(locs, _parse_date(start), _parse_date(end), fetch_weather=weather)
    return trip_report_to_dict(report)
