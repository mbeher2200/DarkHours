"""The invariant: a user request never retrieves TLEs.

TLE retrieval must not happen on the request path. That was previously a convention
propped up by a cache TTL and a scheduled warmer, and it failed silently for months:
the Starlink group entry was physically un-cacheable (1.8 MB against DynamoDB's
400 KB item limit), so every /night?satellites=true re-downloaded it from Celestrak
— 113 requests over 10s in a 14-day window, max 30.8s.

The fix is structural rather than conditional: predictor calls cached_tle /
cached_starlink_trains, which contain no network code. These tests hold that line.
Everything here is hermetic — no network, no ephemeris, no AWS.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import darkhours.predictor as predictor
from darkhours import _http, tle_provider as tle

_ISS_RAW = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   24191.72490000  .00022888  00000+0  40424-3 0  9998\n"
    "2 25544  51.6416  55.2282 0002997  69.2059  25.5940 15.49162413448760"
)
_BORTLE_INFO = {
    "sqm": 21.5, "bortle_class": 3, "bortle_desc": "Rural",
    "lp_zone": "2", "below_detection": False, "source": "VIIRS 2025",
}


class _StateCache:
    """Cache port stub with independently controllable fresh/stale contents."""

    def __init__(self, fresh=None, stale=None):
        self.fresh = fresh or {}
        self.stale = stale or {}
        self.written = {}

    def get(self, key):
        return self.fresh.get(key)

    def get_stale(self, key):
        return self.stale.get(key, self.fresh.get(key))

    def set(self, key, value, ttl_seconds=None):
        self.written[key] = value
        return True


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound HTTP from here on is a test failure, not a slow test."""
    def _boom(*a, **kw):
        raise AssertionError(
            "the request path made an outbound HTTP call — TLE retrieval has leaked "
            "back onto the user path"
        )
    monkeypatch.setattr(_http, "urlopen", _boom)
    return _boom


@pytest.fixture
def stub_engine(monkeypatch):
    """Mock everything assemble_night needs except the satellite path under test."""
    monkeypatch.setattr(predictor._ds, "lookup", lambda lat, lon: dict(_BORTLE_INFO))
    monkeypatch.setattr(predictor._ld, "lightdome_lookup", lambda lat, lon: None)
    monkeypatch.setattr(predictor.se, "moon_phase_info", lambda at_utc: ("Waxing Crescent", 35.0))
    monkeypatch.setattr(predictor._me, "moon_distance_km", lambda at_utc: 384_400.0)
    monkeypatch.setattr(predictor._me, "classify_full_moon", lambda illum, dist: None)
    monkeypatch.setattr(predictor._me, "eclipses_for_night", lambda sunset, sunrise: [])
    monkeypatch.setattr(predictor._tgt, "visible_targets", lambda *a, **kw: [])

    today   = datetime.now(timezone.utc).date()
    sunset  = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=20)
    sunrise = sunset + timedelta(hours=8)
    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {
            "tonight_hours": 4.0, "mean_hours": 4.0, "stdev_hours": 0.0,
            "score": 8.0,
            "tonight": {
                "sunset": sunset, "sunrise": sunrise,
                "night_start": sunset + timedelta(hours=1, minutes=30),
                "night_end": sunrise - timedelta(hours=1, minutes=30),
                "dark_hours": 4.0,
            },
        },
    )
    return today


def _install_cache(monkeypatch, cache):
    from darkhours import ports as _ports
    monkeypatch.setattr(_ports.get_backend(), "_cache", cache)


def _assemble(target):
    return predictor.assemble_night(
        40.0, -105.0, target, ZoneInfo("America/Denver"),
        use_cycle_window=True, fetch_weather=False, fetch_satellites=True,
    )


# ---------------------------------------------------------------------------
# The invariant, at every cache state
# ---------------------------------------------------------------------------

def test_request_path_never_fetches_with_a_warm_cache(monkeypatch, stub_engine, no_network):
    _install_cache(monkeypatch, _StateCache(fresh={
        tle._tle_key(n): _ISS_RAW for n, _ in tle.TRACKED_SATELLITES
    } | {tle._STARLINK_TRAINS_CACHE_KEY: []}))

    report = _assemble(stub_engine)

    assert report.sat_starlink_unavailable is False
    assert report.sat_network_error is False


def test_request_path_never_fetches_with_a_stale_only_cache(monkeypatch, stub_engine, no_network):
    _install_cache(monkeypatch, _StateCache(stale={
        tle._tle_key(n): _ISS_RAW for n, _ in tle.TRACKED_SATELLITES
    } | {tle._STARLINK_TRAINS_CACHE_KEY: []}))

    report = _assemble(stub_engine)

    assert report.sat_tle_stale is True, "expired TLEs are still served, flagged stale"
    assert report.sat_network_error is False


def test_request_path_never_fetches_with_an_empty_cache(monkeypatch, stub_engine, no_network):
    """The strict contract: a cold cache degrades, it does not fetch.

    This is the case that previously cost 1.8 MB and up to 30s of a user's time. An
    empty cache means the warmer is broken, which is an operator problem — the user
    gets the rest of their forecast and a flag saying satellites are unavailable.
    """
    _install_cache(monkeypatch, _StateCache())

    report = _assemble(stub_engine)

    assert report.sat_network_error is True
    assert report.sat_starlink_unavailable is True
    assert report.sat_passes == []
    # ...and the rest of the report still assembled.
    assert report.bortle_score > 0
    assert report.score_components is not None


def test_predictor_calls_only_the_cache_only_readers(monkeypatch, stub_engine):
    """Pins the call sites themselves, so a future edit that swaps cached_tle back to
    get_tle fails here even if the cache happens to be warm in every other test."""
    for name in ("get_tle", "get_starlink_train_tles", "refresh_tle",
                 "refresh_starlink_trains", "warm_cache"):
        monkeypatch.setattr(tle, name, mock.MagicMock(
            side_effect=AssertionError(f"predictor must not call tle_provider.{name}")))
    _install_cache(monkeypatch, _StateCache(fresh={
        tle._tle_key(n): _ISS_RAW for n, _ in tle.TRACKED_SATELLITES
    } | {tle._STARLINK_TRAINS_CACHE_KEY: []}))

    _assemble(stub_engine)


def test_cached_readers_contain_no_network_call(no_network):
    """Belt and braces: call them directly against an empty cache."""
    with mock.patch.object(tle, "_cache", _StateCache()):
        assert tle.cached_tle(25544).lines is None
        assert tle.cached_starlink_trains()[0] == []


# ---------------------------------------------------------------------------
# The root cause: what actually gets written
# ---------------------------------------------------------------------------

def _synthetic_group(n_sats: int) -> str:
    """A GROUP=starlink-shaped block. The real one is ~1.8 MB / ~10,700 satellites."""
    today = datetime.now(timezone.utc).date()
    doy   = today.timetuple().tm_yday
    yy    = today.year % 100
    out = []
    for i in range(n_sats):
        # Mean motion 15.90 → raising phase; today's launch date → inside the window.
        out.append(f"STARLINK-{i:05d}")
        out.append(f"1 {50000 + i:05d}U {yy:02d}{doy:03d}A   26235.50000000  "
                   f".00002182  00000+0  40768-4 0  9992")
        out.append(f"2 {50000 + i:05d}  53.0000 100.0000 0001000  90.0000 270.0000 "
                   f"15.90000000    10")
    return "\n".join(out)


def test_starlink_refresh_caches_the_filtered_list_not_the_raw_group(monkeypatch):
    """The bug, pinned.

    refresh_starlink_trains used to cache the raw response. Measured live: 1,804,320
    bytes as a DynamoDB item, 4.5x the 400 KB ceiling. put_item could never succeed,
    so the row never existed and every request re-downloaded the group. What lands in
    the cache now has to fit.
    """
    raw = _synthetic_group(10_700)
    assert len(raw.encode()) > 1_500_000, "the fixture must be big enough to matter"

    cache = _StateCache()
    monkeypatch.setattr(tle, "_cache", cache)
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.side_effect = [raw.encode(), b""]
    monkeypatch.setattr(_http, "urlopen", mock.MagicMock(return_value=resp))

    trains, stale, error = tle.refresh_starlink_trains()

    assert error is None and stale is False
    stored = cache.written[tle._STARLINK_TRAINS_CACHE_KEY]
    assert isinstance(stored, list)
    assert len(json.dumps(stored).encode()) < 380_000, \
        "the cached value must fit inside a DynamoDB item"
    assert len(stored) == tle._STARLINK_MAX_TRAINS, "the stored list must be capped"
    assert raw not in [v for v in cache.written.values() if isinstance(v, str)], \
        "the raw group block must never reach the cache"


def test_cached_starlink_trains_reads_an_empty_list_as_data_not_as_a_miss(monkeypatch):
    """An empty train list is the common case — there is frequently no launch in the
    raising phase. Reading it as "nothing cached" would send the most common request
    straight back to Celestrak."""
    cache = _StateCache(fresh={tle._STARLINK_TRAINS_CACHE_KEY: []})
    monkeypatch.setattr(tle, "_cache", cache)

    trains, stale, error = tle.cached_starlink_trains()

    assert trains == [] and stale is False and error is None


def test_cached_starlink_trains_round_trips_the_json_shape(monkeypatch):
    """The cache stores JSON, so tuples come back as lists — callers expect tuples."""
    cache = _StateCache(fresh={tle._STARLINK_TRAINS_CACHE_KEY: [["S-1", "1 aaa", "2 bbb"]]})
    monkeypatch.setattr(tle, "_cache", cache)

    trains, _, _ = tle.cached_starlink_trains()

    assert trains == [("S-1", "1 aaa", "2 bbb")]
