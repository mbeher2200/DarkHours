"""HTTP API tests via FastAPI TestClient on the local backend.

The healthz + error-path tests are hermetic (no rasters, no network — coordinate
resolution uses the offline TimezoneFinder). The full /night success test needs
the local VIIRS/Falchi rasters, so it's skipped when they're absent (e.g. CI);
the real cloud path is covered by the @pytest.mark.aws smoke instead.
"""
import pathlib
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import apps.api.main as main_mod
from apps import jobs
from apps.api.main import app

client = TestClient(app)

_RASTERS = pathlib.Path.home() / ".pynightsky-predictor"
_have_rasters = ((_RASTERS / "viirs_2025_raw.tif").exists()
                 and (_RASTERS / "world_atlas_2016.tif").exists())
requires_rasters = pytest.mark.skipif(
    not _have_rasters, reason="local VIIRS/Falchi rasters not present")


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_night_requires_location_or_coords():
    assert client.get("/night").status_code == 400


def test_night_bad_date_returns_400():
    r = client.get("/night", params={"lat": 35.2, "lon": -111.6, "date": "not-a-date"})
    assert r.status_code == 400


def test_calendar_bad_month_returns_400():
    r = client.get("/calendar", params={"lat": 35.2, "lon": -111.6, "month": "2026-13"})
    assert r.status_code == 400


def test_trip_missing_params_returns_422():
    # locations/start/end are required query params → FastAPI validation error
    assert client.get("/trip").status_code == 422


# ── input bounds (data sanity + abuse/DoS guards) — all hermetic ─────────────

def test_night_lat_out_of_range_422():
    assert client.get("/night", params={"lat": 999, "lon": 0}).status_code == 422


def test_night_lon_out_of_range_422():
    assert client.get("/night", params={"lat": 0, "lon": 999}).status_code == 422


def test_night_date_outside_ephemeris_400():
    r = client.get("/night", params={"lat": 35.2, "lon": -111.6, "date": "1850-01-01"})
    assert r.status_code == 400


def test_night_location_too_long_422():
    assert client.get("/night", params={"location": "x" * 201}).status_code == 422


def test_trip_range_too_large_400():
    r = client.get("/trip", params={"locations": "x", "start": "2026-01-01", "end": "2026-12-31"})
    assert r.status_code == 400


def test_trip_end_before_start_400():
    r = client.get("/trip", params={"locations": "x", "start": "2026-06-10", "end": "2026-06-01"})
    assert r.status_code == 400


def test_trip_too_many_locations_400():
    params = [("locations", f"loc{i}") for i in range(11)] + [("start", "2026-06-01"), ("end", "2026-06-02")]
    assert client.get("/trip", params=params).status_code == 400


# ── /nearby endpoint ──────────────────────────────────────────────────────────

_NEARBY_RESULT = {
    "origin_bortle": 7, "origin_sqm": 19.5, "radius_miles": 60,
    "results": [], "light_domes": [], "has_dark_sky": False, "best_available": None,
}


@pytest.fixture
def _nearby_mocks(monkeypatch):
    """Patch geocoding + run_job so /nearby tests need no rasters or network."""
    store: dict = {}
    monkeypatch.setattr(jobs._cache, "set",
                        lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    monkeypatch.setattr(jobs._cache, "get", lambda k: store.get(k))
    monkeypatch.delenv("PYNIGHTSKY_JOBS_QUEUE_URL", raising=False)
    monkeypatch.setattr(main_mod._loc, "timezone_for",
                        lambda lat, lon: ZoneInfo("America/Denver"))
    monkeypatch.setattr(jobs, "run_job", lambda p: _NEARBY_RESULT)
    return store


def test_nearby_returns_202(monkeypatch, _nearby_mocks):
    r = TestClient(app).get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 60})
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["poll"].startswith("/jobs/")


def test_nearby_job_polls_to_done(monkeypatch, _nearby_mocks):
    c = TestClient(app)
    r = c.get("/nearby", params={"lat": 35.2, "lon": -111.6})
    jid = r.json()["job_id"]
    poll = c.get(f"/jobs/{jid}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "done"
    assert poll.json()["result"]["origin_bortle"] == 7


def test_nearby_radius_too_large_422():
    assert client.get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 999}).status_code == 422


def test_nearby_radius_too_small_422():
    assert client.get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 1}).status_code == 422


def test_nearby_requires_location_or_coords():
    assert client.get("/nearby", params={"radius": 60}).status_code == 400


@pytest.mark.eph
@requires_rasters
def test_night_by_coords_matches_baseline():
    # coords → no geocoding; weather=false → no network. Deterministic astro + LP.
    r = client.get("/night", params={
        "lat": 35.1983, "lon": -111.6513, "date": "2026-06-15",
        "weather": "false", "targets": "false", "satellites": "false",
    })
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["sunset"], str)                  # datetime → ISO 8601
    assert d["phase_name"] and d["score"] is not None
    # Matches the M2/M3 light-pollution baseline for this coordinate.
    assert d["light_pollution"]["source"] == "VIIRS 2025"
    assert d["light_pollution"]["bortle_class"] == 7
    assert d["bortle_score"] is not None
