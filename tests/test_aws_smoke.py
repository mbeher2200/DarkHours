"""Opt-in integration smoke against REAL AWS (DynamoDB cache + S3 COGs).

Skipped unless the aws backend is configured: PYNIGHTSKY_BACKEND=aws,
PYNIGHTSKY_CACHE_TABLE, PYNIGHTSKY_RASTER_BUCKET, and resolvable AWS credentials.
Mirrors the M2/M3 manual verification. Run with:

    eval "$(aws configure export-credentials --profile <profile> --format env)"
    PYNIGHTSKY_BACKEND=aws PYNIGHTSKY_CACHE_TABLE=<your-cache-table> \\
    PYNIGHTSKY_RASTER_BUCKET=<your-raster-bucket> AWS_REGION=us-east-1 \\
    python -m pytest -m aws -q
"""
import os
import pytest

pytestmark = pytest.mark.aws

_REQUIRED = ("PYNIGHTSKY_CACHE_TABLE", "PYNIGHTSKY_RASTER_BUCKET")


@pytest.fixture(autouse=True)
def _require_aws_env():
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing or os.environ.get("PYNIGHTSKY_BACKEND") != "aws":
        pytest.skip("set PYNIGHTSKY_BACKEND=aws + "
                    + ", ".join(_REQUIRED) + " (and AWS creds) to run the AWS smoke")
    from PyNightSkyPredictor import ports
    ports.reset_backend()   # rebuild for the aws backend
    yield
    ports.reset_backend()


def test_dynamo_cache_roundtrip_real():
    from PyNightSkyPredictor import cache
    cache.set("smoke_test_key", {"ok": True, "n": 1.25}, ttl_seconds=60)
    assert cache.get("smoke_test_key") == {"ok": True, "n": 1.25}
    cache.invalidate("smoke_test_key")
    assert cache.get("smoke_test_key") is None


def test_darksky_lookup_via_s3_real():
    from PyNightSkyPredictor import darksky
    nyc = darksky.lookup(40.7128, -74.0060)        # bright → VIIRS
    assert nyc and nyc["source"] == "VIIRS 2025" and nyc["bortle_class"] == 9
    dark = darksky.lookup(37.2309, -112.6377)      # dark → Falchi fallback
    assert dark and dark["source"] == "Falchi 2016"
