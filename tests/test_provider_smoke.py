"""Live provider smoke tests — hit each external provider's API exactly once.

These make REAL network calls, so they are opt-in: skipped unless PYNIGHTSKY_LIVE=1
is set (keeps the default `pytest` run offline and deterministic). Run with:

    PYNIGHTSKY_LIVE=1 python -m pytest -m live -q

Covers every outbound data provider the engine depends on:
  * Open-Meteo            — primary weather forecast
  * 7Timer ASTRO         — seeing / transparency weather
  * Celestrak            — satellite TLEs
  * Nominatim            — forward geocoding (local backend)
  * AWS Location         — forward geocoding (aws backend; additionally needs the
                           aws backend env + credentials, else skipped)

Each test asserts only that the provider is reachable and returns a sane payload —
a connectivity/health check, not a correctness test of the parsing logic (those
live in the per-module unit tests with mocked responses).
"""
import os

import pytest

pytestmark = pytest.mark.live

# A bright, unambiguous location all providers can answer for.
_DENVER_LAT, _DENVER_LON = 39.7392, -104.9903
_ISS_NORAD = 25544  # ISS (ZARYA) — always present in Celestrak's catalogue


@pytest.fixture(autouse=True)
def _require_live():
    if not os.environ.get("PYNIGHTSKY_LIVE"):
        pytest.skip("set PYNIGHTSKY_LIVE=1 to run live provider smoke tests")


# ── Weather providers ────────────────────────────────────────────────────────

def test_open_meteo_live():
    from PyNightSkyPredictor import weather
    points = weather.OpenMeteoProvider().forecast(_DENVER_LAT, _DENVER_LON)
    assert points, "Open-Meteo returned no forecast points"
    assert points[0].cloud_cover_pct is not None


def test_seventimer_live():
    from PyNightSkyPredictor import weather
    points = weather.SevenTimerProvider().forecast(_DENVER_LAT, _DENVER_LON)
    assert points, "7Timer returned no forecast points"


# ── Celestrak (TLE) ──────────────────────────────────────────────────────────

def test_celestrak_live():
    from PyNightSkyPredictor import tle_provider
    raw = tle_provider._fetch_tle_raw(_ISS_NORAD)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert any(ln.startswith("1 ") for ln in lines), "no TLE line 1 from Celestrak"
    assert any(ln.startswith("2 ") for ln in lines), "no TLE line 2 from Celestrak"


# ── Location providers ───────────────────────────────────────────────────────

def test_nominatim_live():
    from PyNightSkyPredictor import location
    entry = location._geocode_via_nominatim("Denver, CO", "Denver, CO")
    assert entry is not None, "Nominatim returned no result for Denver, CO"
    assert entry["lat"] == pytest.approx(_DENVER_LAT, abs=0.4)
    assert entry["lon"] == pytest.approx(_DENVER_LON, abs=0.4)


def test_aws_location_live():
    required = ("PYNIGHTSKY_CACHE_TABLE", "PYNIGHTSKY_RASTER_BUCKET")
    if os.environ.get("PYNIGHTSKY_BACKEND") != "aws" or any(
        not os.environ.get(v) for v in required
    ):
        pytest.skip(
            "set PYNIGHTSKY_BACKEND=aws + "
            + ", ".join(required)
            + " (and AWS creds) to run the AWS Location smoke"
        )
    from PyNightSkyPredictor import ports, location
    ports.reset_backend()
    try:
        entry = location._geocode_via_aws("Denver, CO", "Denver, CO")
    except RuntimeError as e:
        # Distinguish "creds not usable in this environment" (skip) from a real
        # provider/service error (fail). Local SSO/login-based creds need
        # botocore[crt]; in Lambda/CI a task role resolves cleanly.
        msg = str(e).lower()
        if any(s in msg for s in ("credential", "botocore[crt]", "missing dependency",
                                  "unable to locate")):
            pytest.skip(f"AWS credentials not usable for boto3 here: {e}")
        raise
    finally:
        ports.reset_backend()
    assert entry is not None, "AWS Location returned no result for Denver, CO"
    assert entry["lat"] == pytest.approx(_DENVER_LAT, abs=0.4)
    assert entry["lon"] == pytest.approx(_DENVER_LON, abs=0.4)
