"""Contract tests for the pluggable I/O adapters (ports & adapters).

The same behavioral contract runs against the local and AWS implementations —
LocalFileCache vs DynamoCache, LocalGeocodeStore vs DynamoGeocodeStore — proving
they are interchangeable. DynamoDB is mocked in-process with moto, so these are
hermetic: no real AWS, no network, fast. The opt-in real-AWS smoke lives in
test_aws_smoke.py.
"""
import pytest


def _make_moto_table(name="test-cache"):
    import boto3
    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _moto_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


# ── Cache contract (parametrized over both backends) ─────────────────────────

@pytest.fixture(params=["local", "dynamo"])
def cache_impl(request, tmp_path, monkeypatch):
    if request.param == "local":
        from PyNightSkyPredictor.cache import LocalFileCache
        yield LocalFileCache(cache_dir=tmp_path)
        return
    moto = pytest.importorskip("moto")
    _moto_env(monkeypatch)
    with moto.mock_aws():
        _make_moto_table()
        from PyNightSkyPredictor.cache import DynamoCache
        yield DynamoCache(table_name="test-cache")


def test_set_get_roundtrip_preserves_floats(cache_impl):
    v = {"n": 3.14159, "s": "x", "list": [1, 2.5, "z"], "b": True, "nested": {"a": 0.1}}
    cache_impl.set("k", v, ttl_seconds=300)
    assert cache_impl.get("k") == v  # exact JSON round-trip, floats intact


def test_get_missing_returns_none(cache_impl):
    assert cache_impl.get("absent") is None
    assert cache_impl.get_stale("absent") is None


def test_no_ttl_persists(cache_impl):
    cache_impl.set("k", "v")
    assert cache_impl.get("k") == "v"


def test_invalidate(cache_impl):
    cache_impl.set("k", "v", ttl_seconds=300)
    cache_impl.invalidate("k")
    assert cache_impl.get("k") is None


def test_expired_get_is_miss_but_stale_still_serves(cache_impl):
    """Stale-while-revalidate: get() misses on expiry but must NOT destroy the
    entry, so get_stale() can still serve it (regression for the bug where
    LocalFileCache.get deleted expired entries)."""
    cache_impl.set("k", {"tle": "data"}, ttl_seconds=-1)  # already expired
    assert cache_impl.get("k") is None
    assert cache_impl.get_stale("k") == {"tle": "data"}


def test_clear_all_preserves_system_records(cache_impl):
    cache_impl.set("regular", "v", ttl_seconds=300)
    cache_impl.set("__geocode__", {"home": 1})
    cache_impl.set("__dark_cycle__", {"w": [1.0]})
    cache_impl.clear_all()
    assert cache_impl.get("regular") is None
    assert cache_impl.get_stale("__geocode__") == {"home": 1}
    assert cache_impl.get_stale("__dark_cycle__") == {"w": [1.0]}


def test_clear_expired_removes_only_expired_nonsystem(cache_impl):
    cache_impl.set("fresh", "v", ttl_seconds=300)
    cache_impl.set("old", "v", ttl_seconds=-1)        # expired, regular → removed
    cache_impl.set("__sys__", "keep", ttl_seconds=-1)  # expired, system → kept
    cache_impl.clear_expired()
    assert cache_impl.get("fresh") == "v"
    assert cache_impl.get_stale("old") is None
    assert cache_impl.get_stale("__sys__") == "keep"


# ── GeocodeStore contract (parametrized over both backends) ──────────────────

@pytest.fixture(params=["local", "dynamo"])
def geocode_impl(request, tmp_path, monkeypatch):
    if request.param == "local":
        from PyNightSkyPredictor.location import LocalGeocodeStore
        yield LocalGeocodeStore(path=tmp_path / "locations.json")
        return
    moto = pytest.importorskip("moto")
    _moto_env(monkeypatch)
    with moto.mock_aws():
        _make_moto_table()
        from PyNightSkyPredictor.location import DynamoGeocodeStore
        yield DynamoGeocodeStore(table_name="test-cache")


def test_geocode_empty_load_is_dict(geocode_impl):
    assert geocode_impl.load() == {}


def test_geocode_save_load_roundtrip(geocode_impl):
    data = {"home": {"lat": 1.5, "lon": -2.5, "display_name": "Home", "tz_name": "UTC"},
            "dark site": {"lat": 36.42, "lon": -116.91, "display_name": "DV", "tz_name": "America/Los_Angeles"}}
    geocode_impl.save(data)
    assert geocode_impl.load() == data


# ── S3RasterSource grid resolution (no AWS needed) ───────────────────────────

def test_s3_resolves_grid_key_prefixes():
    from PyNightSkyPredictor.darksky import S3RasterSource
    src = S3RasterSource(bucket="my-bucket")
    # Grid pair on S3 is {key}.bin / {key}.json (no GDAL /vsis3 URI anymore).
    assert src._KEYS["viirs"] == "viirs_2025"
    assert src._KEYS["falchi"] == "world_atlas_2016"


def test_s3_unknown_dataset_raises():
    from PyNightSkyPredictor.darksky import S3RasterSource
    # Unknown dataset is rejected before any S3/network access.
    with pytest.raises(ValueError):
        S3RasterSource(bucket="b").sample("nope", 0.0, 0.0)


def test_s3_missing_bucket_raises_lazily(monkeypatch):
    from PyNightSkyPredictor.darksky import S3RasterSource
    monkeypatch.delenv("PYNIGHTSKY_RASTER_BUCKET", raising=False)
    src = S3RasterSource()          # construction must NOT raise (lazy)
    with pytest.raises(RuntimeError):
        src.sample("viirs", 0.0, 0.0)   # bucket resolved here → raises before S3
