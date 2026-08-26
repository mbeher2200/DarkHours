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
from datetime import date, datetime, timedelta, timezone
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

_TODAY      = datetime.now(timezone.utc).date()
_DESIGNATOR = f"{_TODAY.year}-042"


def _synthetic_group(n_sats: int, designator_suffix: str = "042") -> str:
    """A GROUP=starlink-shaped GP CSV response. The real one is ~1.7 MB / ~11,000 rows.

    CSV, not TLE: FORMAT=TLE omits every object with a 6-digit catalog number, which
    since 2026-07-11 means every recent launch — the reason this filter reported zero.
    """
    cols = ["OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY",
            "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY",
            "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID",
            "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT",
            "MEAN_MOTION_DDOT"]
    designator = f"{_TODAY.year}-{designator_suffix}A"
    out = [",".join(cols)]
    for i in range(n_sats):
        # Mean motion 15.90 → below every operational shell → raising phase.
        out.append(",".join([
            f"STARLINK-{i:05d}", designator, "2026-08-25T12:00:00.000000",
            "15.90000000", ".0001000", "53.0000", "100.0000", "90.0000", "270.0000",
            "0", "U", str(50000 + i), "999", "100", ".0000408", ".00002182", "0",
        ]))
    return "\n".join(out)


def _recent_launch_dates(days_ago: int = 1) -> dict:
    return {_DESIGNATOR: _TODAY - timedelta(days=days_ago)}


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

    trains, stale, error = tle.refresh_starlink_trains(
        launch_dates=_recent_launch_dates()
    )

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
    cache = _StateCache(fresh={
        tle._STARLINK_TRAINS_CACHE_KEY: [["S-1", "1 aaa", "2 bbb", "2026-08-12"]]
    })
    monkeypatch.setattr(tle, "_cache", cache)

    trains, _, _ = tle.cached_starlink_trains()

    assert trains == [("S-1", "1 aaa", "2 bbb", "2026-08-12")]


def test_cached_starlink_trains_tolerates_a_three_element_row(monkeypatch):
    """Rows written before the launch date rode along must still be readable —
    they are simply a train whose launch date we cannot show."""
    cache = _StateCache(fresh={tle._STARLINK_TRAINS_CACHE_KEY: [["S-1", "1 aaa", "2 bbb"]]})
    monkeypatch.setattr(tle, "_cache", cache)

    trains, _, _ = tle.cached_starlink_trains()

    assert trains == [("S-1", "1 aaa", "2 bbb", None)]


# ---------------------------------------------------------------------------
# The launch date: a TLE carries a launch *number*, not a date
# ---------------------------------------------------------------------------

def test_cospar_designator_reads_a_launch_number_not_a_day_of_year():
    """The bug that made the feature render nothing, pinned to ground truth.

    The International Designator's middle field is the launch's sequence number
    within its year. The ISS is 1998-067A and launched on 1998-11-20; day 67 of
    1998 is 8 March. Starlink 2026-159 launched on 2026-07-11; day 159 is 8 June —
    which is exactly the date the old day-of-year reading produced, putting the
    newest launch in the feed 76 days in the past and outside every train window.
    """
    iss = "1 25544U 98067A   24191.72490000  .00022888  00000+0  40424-3 0  9998"
    assert tle._cospar_designator(iss) == "1998-067"

    starlink = "1 69975U 26159A   26235.52033823  .00235854  00000+0  15329-2 0  9991"
    assert tle._cospar_designator(starlink) == "2026-159"

    assert tle._cospar_designator("1 25544U          24191.72490000") is None


def test_six_digit_catalog_numbers_survive_the_filter():
    """The bug, pinned.

    Celestrak assigns 6-digit catalog numbers to everything catalogued since
    2026-07-11 and does not render those objects in FORMAT=TLE at all. Asking for
    TLE therefore returned a constellation frozen at the last 5-digit launch, no
    launch inside the recency window matched, and the feature reported zero trains
    every night for weeks while looking healthy.

    A 5-character TLE catalog field cannot hold 100001, so the filter re-encodes it
    Alpha-5 ("A0001") on the way into the cache. This asserts the number survives
    that round trip rather than being silently truncated to 10000 — sgp4 parses a
    raw 6-digit line without complaint and shifts every field one column.
    """
    from sgp4.api import Satrec

    raw = _synthetic_group(1).replace(",50000,", ",100001,")
    trains = tle._filter_train_tles(raw, _recent_launch_dates(days_ago=1))

    assert len(trains) == 1
    _name, line1, line2, _launched = trains[0]
    assert Satrec.twoline2rv(line1, line2).satnum == 100001


def test_tle_format_body_is_rejected_not_silently_empty():
    """A FORMAT=TLE body reaching this filter means the URL regressed. It must raise:
    returning [] is indistinguishable from a genuine night with no trains, which is
    how the outage above stayed invisible."""
    tle_text = "\n".join([
        "STARLINK-1234",
        "1 50000U 26042A   26235.50000000  .00002182  00000+0  40768-4 0  9992",
        "2 50000  53.0000 100.0000 0001000  90.0000 270.0000 15.90000000    10",
    ])
    with pytest.raises(ValueError, match="not OMM CSV"):
        tle._filter_train_tles(tle_text, _recent_launch_dates(days_ago=1))


def test_filter_needs_launch_dates_and_fails_closed_without_them(caplog):
    """No launch dates → no trains, loudly.

    Mean motion alone cannot tell a raising batch from an old satellite being
    lowered for re-entry: ~880 of the ~10,700 satellites in the real group are above
    the threshold at any moment, and most are decaying. Emitting them unfiltered
    would put hundreds of stale satellites on screen labelled as trains.
    """
    raw = _synthetic_group(30)

    with caplog.at_level("ERROR"):
        assert tle._filter_train_tles(raw, {}) == []
    assert "launch dates" in caplog.text


def test_filter_drops_raising_satellites_from_an_old_launch():
    """Still raising is not the same as still a train — an older batch has spread
    around its orbit even while it climbs."""
    raw = _synthetic_group(30)

    inside  = tle._filter_train_tles(raw, _recent_launch_dates(days_ago=1))
    edge    = tle._filter_train_tles(
        raw, _recent_launch_dates(days_ago=tle._STARLINK_RECENT_DAYS))
    outside = tle._filter_train_tles(
        raw, _recent_launch_dates(days_ago=tle._STARLINK_RECENT_DAYS + 1))

    assert len(inside) == 30
    assert len(edge) == 30, "the cutoff is inclusive"
    assert outside == []


def test_filter_carries_the_launch_date_and_orders_newest_first():
    """satellites.py renders "launched Nd ago" from this, and the cap must truncate
    the oldest batch rather than an arbitrary one."""
    older_designator = f"{_TODAY.year}-041"
    raw = _synthetic_group(2) + "\n" + _synthetic_group(2, designator_suffix="041")
    dates = {
        _DESIGNATOR:      _TODAY - timedelta(days=2),
        older_designator: _TODAY - timedelta(days=9),
    }

    trains = tle._filter_train_tles(raw, dates)

    assert len(trains) == 4
    assert all(len(t) == 4 for t in trains)
    assert [t[3] for t in trains] == [
        (_TODAY - timedelta(days=2)).isoformat()] * 2 + [
        (_TODAY - timedelta(days=9)).isoformat()] * 2


def test_satcat_parsing_keeps_recent_launches_keyed_by_designator():
    csv_text = (
        "OBJECT_NAME,OBJECT_ID,NORAD_CAT_ID,LAUNCH_DATE\n"
        "STARLINK-38024,2026-159A,69975,2026-07-11\n"
        "STARLINK-37993,2026-159B,69976,2026-07-11\n"
        "STARLINK-1008,2019-074B,44714,2019-11-11\n"
        "BROKEN,2026-160A,99999,\n"
    )
    dates = tle._parse_satcat_launch_dates(csv_text, date(2026, 8, 23))

    assert dates == {"2026-159": date(2026, 7, 11)}, \
        "old launches are pruned, undated rows are skipped, pieces collapse to a launch"


def test_revalidation_ages_the_cached_train_list():
    """Celestrak's 403 says the group has not changed, but the 21-day window has
    moved and the raw block is deliberately not kept. Each entry carries its own
    launch date so the list can be aged in place."""
    fresh = ("S-1", "1 a", "2 b", (_TODAY - timedelta(days=3)).isoformat())
    aged  = ("S-2", "1 c", "2 d",
             (_TODAY - timedelta(days=tle._STARLINK_RECENT_DAYS + 5)).isoformat())
    undated = ("S-3", "1 e", "2 f", None)

    assert tle._prune_expired_trains([fresh, aged, undated]) == [fresh]


def test_satellites_reads_the_launch_date_tle_provider_attaches():
    """The seam, pinned from both sides.

    satellites.py used to re-derive the launch date from the TLE with the same
    day-of-year misreading, so "launched 5d ago" in the report was wrong whenever it
    appeared at all. It now reads the date tle_provider attached, and the two halves
    have to agree on the tuple shape or the report silently loses the date again.
    """
    from darkhours.satellites import _entry_launch_date

    raw = _synthetic_group(1)
    launched = _TODAY - timedelta(days=4)
    entry = tle._filter_train_tles(raw, {_DESIGNATOR: launched})[0]

    assert _entry_launch_date(entry) == launched
    assert _entry_launch_date(entry[:3]) is None, "a legacy 3-element row has no date"
    assert _entry_launch_date(("n", "1", "2", "not-a-date")) is None
