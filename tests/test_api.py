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

_RASTERS = pathlib.Path.home() / ".darkhours"
_have_rasters = ((_RASTERS / "viirs_2025_raw.tif").exists()
                 and (_RASTERS / "world_atlas_2016.tif").exists())
requires_rasters = pytest.mark.skipif(
    not _have_rasters, reason="local VIIRS/Falchi rasters not present")


def test_healthz(monkeypatch):
    from darkhours import provider_health as _ph
    monkeypatch.setattr(main_mod, "_check_cache_health",
                        lambda: {"status": "ok", "backend": "local"})
    monkeypatch.setattr(_ph, "snapshot", lambda: {})
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "cache" in body["checks"]


def test_healthz_cache_error_returns_503(monkeypatch):
    from darkhours import provider_health as _ph
    monkeypatch.setattr(main_mod, "_check_cache_health",
                        lambda: {"status": "error", "detail": "DynamoDB unreachable"})
    monkeypatch.setattr(_ph, "snapshot", lambda: {})
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["status"] == "error"


def test_healthz_rate_limit_returns_degraded(monkeypatch):
    from darkhours import provider_health as _ph
    monkeypatch.setattr(main_mod, "_check_cache_health",
                        lambda: {"status": "ok", "backend": "local"})
    monkeypatch.setattr(_ph, "snapshot",
                        lambda: {"open_meteo": {"status": "degraded",
                                                "detail": "rate limited (HTTP 429)"}})
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["open_meteo"]["status"] == "degraded"


def test_healthz_includes_feature_flags_snapshot(monkeypatch):
    from darkhours import provider_health as _ph
    monkeypatch.setattr(main_mod, "_check_cache_health",
                        lambda: {"status": "ok", "backend": "local"})
    monkeypatch.setattr(_ph, "snapshot", lambda: {})
    monkeypatch.setattr(main_mod._ff, "snapshot", lambda: {"satellites": False})
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["feature_flags"] == {"satellites": False}


def test_night_requires_location_or_coords():
    assert client.get("/night").status_code == 400


def test_night_bad_date_returns_400():
    r = client.get("/night", params={"lat": 35.2, "lon": -111.6, "date": "not-a-date"})
    assert r.status_code == 400


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


def test_nearby_raw_coords_skip_geocoding(monkeypatch, _nearby_mocks):
    """Raw-coord /nearby must NOT reverse-geocode or look up a timezone — the job
    only needs lat/lon and the worker names the origin itself. Both are wasted
    enqueue latency, so we assert the handler never touches them."""
    def _boom(*a, **kw):
        raise AssertionError("raw-coord /nearby should not resolve a place")
    monkeypatch.setattr(main_mod._loc, "reverse_geocode", _boom)
    monkeypatch.setattr(main_mod._loc, "timezone_for", _boom)
    r = TestClient(app).get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 60})
    assert r.status_code == 202
    assert "job_id" in r.json()


def test_nearby_search_flag_off_returns_503_before_resolving(monkeypatch, _nearby_mocks):
    """The operator kill switch (darkhours/feature_flags.py) must reject before any
    resolution work or job submission — no cost spent on a disabled feature."""
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_NEARBY_SEARCH_DISABLE", "1")
    def _boom(*a, **kw):
        raise AssertionError("disabled job type must not resolve a location")
    monkeypatch.setattr(main_mod, "_resolve", _boom)
    submitted = []
    monkeypatch.setattr(main_mod.jobs, "submit", lambda params: submitted.append(params) or "unused")
    r = TestClient(app).get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 60})
    assert r.status_code == 503
    assert r.headers.get("retry-after") == "60"
    # {"detail": ...} — the FastAPI HTTPException shape, the only one the frontend's
    # error parsing (apps/web/src/api.ts) actually reads.
    assert r.json() == {"detail": "'nearby_search' is temporarily unavailable. Please try again shortly."}
    assert submitted == []


def test_nearby_search_flag_on_by_default(monkeypatch, _nearby_mocks):
    r = TestClient(app).get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 60})
    assert r.status_code == 202


# ── /calendar endpoint ────────────────────────────────────────────────────────

_CALENDAR_RESULT = {
    "date_start": "2026-07-02", "date_end": "2026-07-08",
    "locations": [], "nights": [], "ranked": [],
}


@pytest.fixture
def _calendar_mocks(monkeypatch):
    """Patch geocoding + run_job so /calendar tests need no rasters or network."""
    store: dict = {}
    monkeypatch.setattr(jobs._cache, "set",
                        lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    monkeypatch.setattr(jobs._cache, "get", lambda k: store.get(k))
    monkeypatch.delenv("PYNIGHTSKY_JOBS_QUEUE_URL", raising=False)
    monkeypatch.setattr(jobs, "run_job", lambda p: _CALENDAR_RESULT)
    return store


def test_calendar_returns_202(monkeypatch, _calendar_mocks):
    r = TestClient(app).get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 7})
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["poll"].startswith("/jobs/")


def test_calendar_job_polls_to_done(monkeypatch, _calendar_mocks):
    c = TestClient(app)
    r = c.get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 14})
    jid = r.json()["job_id"]
    poll = c.get(f"/jobs/{jid}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "done"
    assert poll.json()["result"]["date_start"] == "2026-07-02"


def test_calendar_days_too_large_422():
    assert client.get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 31}).status_code == 422


def test_calendar_days_too_small_422():
    assert client.get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 0}).status_code == 422


def test_calendar_days_at_max_boundary_202(monkeypatch, _calendar_mocks):
    """days=30 (_MAX_CALENDAR_DAYS) is the largest still-accepted range — the
    cap is exclusive on 31, not on 30 itself."""
    r = client.get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 30})
    assert r.status_code == 202


def test_calendar_invalid_start_400():
    r = client.get("/calendar", params={"lat": 35.2, "lon": -111.6, "start": "not-a-date"})
    assert r.status_code == 400


def test_calendar_requires_location_or_coords():
    assert client.get("/calendar", params={"days": 7}).status_code == 400


def test_trip_builder_flag_off_returns_503_before_resolving(monkeypatch, _calendar_mocks):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_TRIP_BUILDER_DISABLE", "1")
    def _boom(*a, **kw):
        raise AssertionError("disabled job type must not resolve a location")
    monkeypatch.setattr(main_mod, "_resolve", _boom)
    submitted = []
    monkeypatch.setattr(main_mod.jobs, "submit", lambda params: submitted.append(params) or "unused")
    r = TestClient(app).get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 7})
    assert r.status_code == 503
    assert r.headers.get("retry-after") == "60"
    assert r.json() == {"detail": "'trip_builder' is temporarily unavailable. Please try again shortly."}
    assert submitted == []


def test_trip_builder_flag_on_by_default(monkeypatch, _calendar_mocks):
    r = TestClient(app).get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 7})
    assert r.status_code == 202


def test_job_type_flags_are_independent(monkeypatch, _nearby_mocks, _calendar_mocks):
    """Disabling one job type must not affect the other, or /night."""
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_NEARBY_SEARCH_DISABLE", "1")
    c = TestClient(app)
    assert c.get("/nearby", params={"lat": 35.2, "lon": -111.6, "radius": 60}).status_code == 503
    assert c.get("/calendar", params={"lat": 35.2, "lon": -111.6, "days": 7}).status_code == 202
    assert c.get("/night").status_code == 400   # unaffected: still just "provide a location"


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


@pytest.mark.eph
@requires_rasters
def test_night_date_only_omits_location_fields():
    # date_only=true is used by the frontend's "View Details" drill-in, which
    # already has light_pollution/bortle_score/light_dome from the location's
    # initial full fetch — the server must omit them, not just leave them null.
    r = client.get("/night", params={
        "lat": 35.1983, "lon": -111.6513, "date": "2026-06-15",
        "weather": "false", "targets": "false", "satellites": "false",
        "date_only": "true",
    })
    assert r.status_code == 200
    d = r.json()
    assert "light_pollution" not in d
    assert "bortle_score" not in d
    assert "light_dome" not in d
    # display_name too — it's location-keyed like the others, and stripping it avoids
    # a wasted reverse-geocode lookup on every "View Details" click.
    assert "display_name" not in d
    # Date-dependent fields are still present.
    assert isinstance(d["sunset"], str)
    assert d["phase_name"] and d["score"] is not None


@pytest.mark.eph
@requires_rasters
def test_night_ignores_client_supplied_name_param(monkeypatch):
    # A previous version of this endpoint accepted a `name` param and used it verbatim
    # as display_name — anyone could craft a URL that put arbitrary text in another
    # user's report header. `name` is no longer a declared query param, so FastAPI
    # silently drops it; display_name always comes from the server-side resolver.
    monkeypatch.setattr(main_mod._loc, "reverse_geocode", lambda lat, lon: "Trusted Name")
    r = client.get("/night", params={
        "lat": 35.1983, "lon": -111.6513, "date": "2026-06-15",
        "weather": "false", "targets": "false", "satellites": "false",
        "name": "Anything The Client Wants To Inject",
    })
    assert r.status_code == 200
    assert r.json()["display_name"] == "Trusted Name"


# ---------------------------------------------------------------------------
# /suggest — two-tier cache (in-process LRU, then a shared cache)
# ---------------------------------------------------------------------------
# The shared-cache tier exists so a popular prefix is only fetched live once per
# TTL window, not once per cold Lambda container. These tests fake the shared
# cache with a plain dict (monkeypatching main_mod._cache.get/set, same
# pattern as jobs._cache elsewhere in this file) and clear the in-process LRU
# between calls to simulate "a different/cold container" without it.

def _fake_shared_cache(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(main_mod._cache, "get", lambda k: store.get(k))
    monkeypatch.setattr(main_mod._cache, "set",
                        lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    return store


def test_suggest_shared_cache_hit_skips_live_call(monkeypatch):
    main_mod._suggest_cached.cache_clear()
    _fake_shared_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(main_mod._loc, "suggest",
                        lambda q: calls.append(q) or ["Sedona, AZ"])

    r1 = client.get("/suggest", params={"q": "Sed"})
    assert r1.json() == {"suggestions": ["Sedona, AZ"]}
    assert calls == ["Sed"]

    main_mod._suggest_cached.cache_clear()   # simulate a different/cold container

    r2 = client.get("/suggest", params={"q": "Sed"})
    assert r2.json() == {"suggestions": ["Sedona, AZ"]}
    assert calls == ["Sed"], "shared-cache hit must not re-call the live provider"


def test_suggest_shared_cache_miss_populates_cache(monkeypatch):
    main_mod._suggest_cached.cache_clear()
    store = _fake_shared_cache(monkeypatch)
    monkeypatch.setattr(main_mod._loc, "suggest", lambda q: ["Zion National Park"])

    r = client.get("/suggest", params={"q": "Zio"})
    assert r.json() == {"suggestions": ["Zion National Park"]}
    assert store["suggest|Zio"] == ["Zion National Park"]


def test_suggest_empty_result_is_still_cached(monkeypatch):
    main_mod._suggest_cached.cache_clear()
    _fake_shared_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(main_mod._loc, "suggest", lambda q: calls.append(q) or [])

    r1 = client.get("/suggest", params={"q": "xyz"})
    assert r1.json() == {"suggestions": []}
    main_mod._suggest_cached.cache_clear()

    r2 = client.get("/suggest", params={"q": "xyz"})
    assert r2.json() == {"suggestions": []}
    assert calls == ["xyz"], "an empty-list result must still be a cache hit, not re-fetched"
