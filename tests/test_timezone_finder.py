"""The process-wide TimezoneFinder in location.py.

Constructing a TimezoneFinder parses its bundled binary data, which costs orders
of magnitude more than the timezone_at() lookup it enables. The original
_tz_name_for built one per call and threw it away, so these tests pin the
construction count, not just the answers — the answers were always right.
"""
import threading

import pytest

from darkhours import location as loc


@pytest.fixture(autouse=True)
def _reset_finder():
    """Start each test with no finder built, and leave none behind."""
    loc._tf = None
    yield
    loc._tf = None


@pytest.fixture
def counting_finder(monkeypatch):
    """Replace TimezoneFinder with a counting subclass; yields the call list."""
    calls: list[int] = []
    real = loc.TimezoneFinder

    class _Counting(real):
        def __init__(self, *a, **kw):
            calls.append(1)
            super().__init__(*a, **kw)

    monkeypatch.setattr(loc, "TimezoneFinder", _Counting)
    return calls


def test_finder_is_built_once_across_many_lookups(counting_finder):
    """The regression guard: 25 lookups, one construction."""
    for _ in range(25):
        assert loc._tz_name_for(35.1983, -111.6513) == "America/Phoenix"
    assert len(counting_finder) == 1


@pytest.mark.parametrize("lat, lon, expected", [
    (35.1983, -111.6513, "America/Phoenix"),   # Flagstaff, AZ — no DST
    (51.4779,  -0.0015,  "Europe/London"),     # Greenwich
    (-33.8568, 151.2153, "Australia/Sydney"),  # southern hemisphere
])
def test_known_coordinates_resolve_to_known_zones(lat, lon, expected):
    """Sharing one instance must not change any answer."""
    assert loc._tz_name_for(lat, lon) == expected


def test_concurrent_lookups_agree_and_still_build_once(counting_finder):
    """The API serves sync endpoints from a threadpool, so concurrent callers are
    the normal case. All must agree, and none may build a second finder."""
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def _worker():
        try:
            barrier.wait()          # maximise overlap on the first construction
            results.append(loc._tz_name_for(35.1983, -111.6513))
        except BaseException as e:  # noqa: BLE001 — surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert results == ["America/Phoenix"] * 8
    assert len(counting_finder) == 1


def test_unresolvable_point_still_raises(monkeypatch):
    """The ValueError contract is unchanged. Stubbed rather than driven from real
    coordinates: timezonefinder answers the open ocean with an Etc/GMT zone, so
    there is no lat/lon that reaches this branch."""
    class _Nothing:
        def timezone_at(self, **kw):
            return None

    monkeypatch.setattr(loc, "_tf", _Nothing())
    with pytest.raises(ValueError, match="Could not determine timezone"):
        loc._tz_name_for(0.0, -140.0)
