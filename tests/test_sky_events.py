"""
Tests for sky_events.py helpers.

Pure-math helpers (dark_moon_intervals, find_event, find_last_event) need no
fixtures.  Ephemeris-dependent integration tests are marked @pytest.mark.eph.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from darkhours.sky_events import dark_moon_intervals, find_event, find_last_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(hour: int, day: int = 14, month: int = 6) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=timezone.utc)


def _ev(label: str, hour: int, **kw) -> dict:
    return {"time": _utc(hour, **kw), "label": label}


# Reference night window: 03:00–09:00 UTC on 2026-06-14
_NS = _utc(3)
_NE = _utc(9)


# ---------------------------------------------------------------------------
# dark_moon_intervals
# ---------------------------------------------------------------------------

class TestDarkMoonIntervals:
    def test_no_moon_events_full_night_dark(self):
        """No Moonrise/Moonset events → entire night is dark."""
        events = [
            _ev("Sunset",  0),
            _ev("Sunrise", 12),
        ]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == [(_NS, _NE)]

    def test_moon_up_all_night_no_dark_interval(self):
        """Moon rises before night start, sets after night end → no dark time."""
        events = [
            _ev("Moonrise", 1),   # before night start (03:00)
            _ev("Moonset",  11),  # after night end   (09:00)
        ]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == []

    def test_moonrise_during_night_clips_first_interval(self):
        """Moon is down at night start, rises mid-night → dark only until moonrise."""
        moonrise_t = _utc(6)  # inside window
        events = [_ev("Moonrise", 6)]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == [(_NS, moonrise_t)]

    def test_moonset_during_night_clips_interval(self):
        """Moon is up at night start (rose before), sets mid-night → dark after moonset."""
        moonset_t = _utc(6)
        events = [
            _ev("Moonrise", 1),   # before night start → moon_up = True
            _ev("Moonset",  6),   # during night
        ]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == [(moonset_t, _NE)]

    def test_moonrise_and_moonset_in_night(self):
        """Moon rises then sets during the night → two dark intervals."""
        moonrise_t = _utc(5)
        moonset_t  = _utc(7)
        events = [
            _ev("Moonrise", 5),
            _ev("Moonset",  7),
        ]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == [(_NS, moonrise_t), (moonset_t, _NE)]

    def test_non_moon_events_ignored(self):
        """Events other than Moonrise/Moonset are ignored."""
        events = [
            _ev("Sunset",                   0),
            _ev("Astronomical night begins", 1),
            _ev("Sunrise",                  12),
        ]
        result = dark_moon_intervals(events, _NS, _NE)
        assert result == [(_NS, _NE)]


# ---------------------------------------------------------------------------
# find_event
# ---------------------------------------------------------------------------

class TestFindEvent:
    def _events(self):
        return [
            _ev("Sunset",   0),
            _ev("Moonrise", 2),
            _ev("Moonrise", 5),
            _ev("Sunrise",  11),
        ]

    def test_returns_first_match(self):
        result = find_event(self._events(), "Moonrise")
        assert result == _utc(2)

    def test_respects_after_bound(self):
        result = find_event(self._events(), "Moonrise", after=_utc(2))
        assert result == _utc(5)

    def test_respects_before_bound(self):
        result = find_event(self._events(), "Moonrise", before=_utc(5))
        assert result == _utc(2)

    def test_returns_none_when_missing(self):
        result = find_event(self._events(), "Jupiter")
        assert result is None

    def test_returns_none_outside_bounds(self):
        result = find_event(self._events(), "Moonrise", after=_utc(5))
        assert result is None  # no Moonrise after 05:00 in the list

    def test_after_and_before_combined(self):
        # Only Moonrise at 05:00 is in (02:00, 11:00)
        result = find_event(self._events(), "Moonrise", after=_utc(2), before=_utc(11))
        assert result == _utc(5)


# ---------------------------------------------------------------------------
# find_last_event
# ---------------------------------------------------------------------------

class TestFindLastEvent:
    def _events(self):
        return [
            _ev("Moonrise", 1),
            _ev("Moonset",  3),
            _ev("Moonrise", 6),
            _ev("Moonset",  9),
        ]

    def test_returns_last_before_bound(self):
        result = find_last_event(self._events(), "Moonrise", before=_utc(9))
        assert result == _utc(6)

    def test_before_bound_is_exclusive(self):
        # Moonrise at 06:00 is strictly before 06:00? No — equal is excluded.
        result = find_last_event(self._events(), "Moonrise", before=_utc(6))
        assert result == _utc(1)

    def test_returns_none_when_none_before_bound(self):
        result = find_last_event(self._events(), "Moonrise", before=_utc(0))
        assert result is None

    def test_label_mismatch_returns_none(self):
        result = find_last_event(self._events(), "Sunrise", before=_utc(12))
        assert result is None


# ---------------------------------------------------------------------------
# Phase naming (pure angle math, no ephemeris)
# ---------------------------------------------------------------------------

class TestPhaseNameFromAngle:
    """Principal phases are ±half-day windows around the instant; everything
    between is crescent/gibbous. Regression: 2026-07-08..13 (angles ~274–342°)
    must read Waning Crescent after the day of the quarter, not Third Quarter."""

    @pytest.mark.parametrize("angle,expected", [
        (0.0,   "New Moon"),
        (6.0,   "New Moon"),
        (6.2,   "Waxing Crescent"),
        (45.0,  "Waxing Crescent"),
        (83.8,  "Waxing Crescent"),
        (84.0,  "First Quarter"),
        (90.0,  "First Quarter"),
        (96.2,  "Waxing Gibbous"),
        (135.0, "Waxing Gibbous"),
        (180.0, "Full Moon"),
        (186.2, "Waning Gibbous"),
        (225.0, "Waning Gibbous"),
        (270.0, "Third Quarter"),
        (273.9, "Third Quarter"),   # evening of the quarter day (2026-07-07/08)
        (286.9, "Waning Crescent"), # one day later — the reported regression
        (297.1, "Waning Crescent"), # 2026-07-10, illum ~25%
        (341.9, "Waning Crescent"), # 2026-07-13, illum ~2.5%
        (356.4, "New Moon"),        # day of new moon (2026-07-14)
        (360.0, "New Moon"),
    ])
    def test_angle_bands(self, angle, expected):
        from darkhours.sky_events import phase_name_from_angle
        assert phase_name_from_angle(angle) == expected


# Ephemeris-based integration tests
# ---------------------------------------------------------------------------

@pytest.mark.eph
class TestMoonPhaseInfo:
    def test_known_full_moon_2026_05_31(self):
        from darkhours.sky_events import moon_phase_info
        # Full moon on 2026-05-31; check at 12:00 UTC (near peak illumination)
        t = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        phase_name, illum = moon_phase_info(t)
        assert illum >= 99.0, f"Expected full moon illumination ≥ 99%, got {illum}%"
        assert "Full" in phase_name, f"Expected 'Full' in phase name, got {phase_name!r}"

    def test_known_new_moon_2026_06_14(self):
        from darkhours.sky_events import moon_phase_info
        t = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
        phase_name, illum = moon_phase_info(t)
        assert illum < 2.0, f"Expected new moon illumination < 2%, got {illum}%"


@pytest.mark.eph
class TestSkyEventsIntegration:
    # Grand Canyon: lat=36.1°N, lon=-112.1°W, Arizona (no DST, UTC-7)
    LAT = 36.1069
    LON = -112.1129

    def test_sunset_grand_canyon_2026_03_02(self):
        from darkhours.sky_events import sky_events, find_event
        from zoneinfo import ZoneInfo
        d = date(2026, 3, 2)
        tz = ZoneInfo("America/Phoenix")
        events = sky_events(self.LAT, self.LON, d)
        sunset_utc = find_event(
            events, "Sunset",
            after=datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
        )
        assert sunset_utc is not None, "No sunset event found for Grand Canyon 2026-03-02"
        sunset_local = sunset_utc.astimezone(tz)
        # Arizona sunset on March 2 should fall between 18:00 and 19:00 MST
        assert sunset_local.hour == 18, (
            f"Sunset hour {sunset_local.hour} not between 18:00 and 19:00 MST"
        )

    def test_eclipse_night_has_moonrise_and_moonset(self):
        """The night of 2026-03-02 (total lunar eclipse ~11:33 UTC March 3) has moonrise and moonset."""
        from darkhours.sky_events import sky_events
        d = date(2026, 3, 2)
        events = sky_events(self.LAT, self.LON, d)
        labels = {e["label"] for e in events}
        assert "Moonrise" in labels, "No Moonrise found for eclipse night"
        assert "Moonset"  in labels, "No Moonset found for eclipse night"


# ---------------------------------------------------------------------------
# lunar_cycle_dark_analysis — per-location lock (no ephemeris needed: the
# Skyfield compute step itself is mocked out so these stay hermetic/fast)
# ---------------------------------------------------------------------------

def _fake_night(d: date, dark_hours: float = 5.0) -> dict:
    """A plausible per-night dark-cycle record for a given calendar date, matching
    the shape _compute_dark_hours_cycle() now returns (sunset/sunrise/night_start/
    night_end/dark_hours) — used to mock it out without needing real Skyfield data."""
    sunset  = datetime(d.year, d.month, d.day, 20, 0, tzinfo=timezone.utc)
    sunrise = sunset + timedelta(hours=10)
    return {
        "sunset":      sunset,
        "sunrise":     sunrise,
        "night_start": sunset + timedelta(hours=1, minutes=30),
        "night_end":   sunrise - timedelta(hours=1, minutes=30),
        "dark_hours":  dark_hours,
    }


class TestLunarCycleDarkAnalysis:
    """Regression coverage for the per-location lock that keeps concurrent
    /calendar requests from each redundantly computing their own overlapping
    30-night window (see scripts/profile_calendar.py: 20/30 calls paying the
    full Skyfield cost before the lock, ~1-8/30 after), and for the per-night
    record shape (sunset/sunrise/night_start/night_end/dark_hours) that
    replaced a bare dark-hours float list."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        import darkhours.sky_events as se
        # Fresh in-process state per test, and no real cache/network I/O.
        monkeypatch.setattr(se, "_mem_dark_cycle", {})
        monkeypatch.setattr(se, "_dark_cycle_locks", {})
        monkeypatch.setattr(se._cache, "get", lambda k: None)
        monkeypatch.setattr(se._cache, "set", lambda k, v, **kw: None)

    def test_overlapping_target_dates_reuse_one_computation(self, monkeypatch):
        import darkhours.sky_events as se
        from datetime import timedelta

        calls = []

        def fake_compute(lat, lon, target_date, tz):
            calls.append(target_date)
            window_start = target_date - timedelta(days=14)
            return [_fake_night(window_start + timedelta(days=i)) for i in range(30)]

        monkeypatch.setattr(se, "_compute_dark_hours_cycle", fake_compute)

        base = date(2026, 7, 2)
        r1 = se.lunar_cycle_dark_analysis(40.0, -105.0, base, None)
        r2 = se.lunar_cycle_dark_analysis(40.0, -105.0, base + timedelta(days=1), None)
        r3 = se.lunar_cycle_dark_analysis(40.0, -105.0, base + timedelta(days=5), None)

        assert len(calls) == 1, "second and third calls should reuse the first's cached window"
        assert r1["tonight_hours"] == r2["tonight_hours"] == r3["tonight_hours"] == 5.0
        assert r1["tonight"]["sunset"].date() == base
        assert r2["tonight"]["sunset"].date() == base + timedelta(days=1)

    def test_concurrent_overlapping_requests_dont_each_compute_independently(self, monkeypatch):
        import threading
        import time as _t
        from datetime import timedelta
        import darkhours.sky_events as se

        calls = []
        calls_lock = threading.Lock()

        def fake_compute(lat, lon, target_date, tz):
            with calls_lock:
                calls.append(target_date)
            _t.sleep(0.05)  # long enough for concurrent callers to actually race
            window_start = target_date - timedelta(days=14)
            return [_fake_night(window_start + timedelta(days=i)) for i in range(30)]

        monkeypatch.setattr(se, "_compute_dark_hours_cycle", fake_compute)

        base = date(2026, 7, 2)
        dates = [base + timedelta(days=i) for i in range(20)]  # all within one 30-night window
        results: list = [None] * len(dates)

        def worker(i, d):
            results[i] = se.lunar_cycle_dark_analysis(40.0, -105.0, d, None)

        threads = [threading.Thread(target=worker, args=(i, d)) for i, d in enumerate(dates)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Without the lock, all 20 concurrent callers would miss the empty
        # cache and compute independently. With it, far fewer real computes
        # are needed to cover all 20 (overlapping) nights.
        assert len(calls) < len(dates)
        assert all(r["tonight_hours"] == 5.0 for r in results)

    def test_dynamodb_hit_roundtrips_iso_strings_to_datetimes(self, monkeypatch):
        """json.dumps() (both cache backends — see cache.py) can't handle raw
        datetimes, so the DynamoDB layer stores ISO strings (_nights_to_json)
        and must parse them back (_nights_from_json) on a hit. A real
        LocalFileCache-style round trip through json.dumps/loads, not just an
        in-memory dict, so a missed conversion would actually surface here."""
        import json as _json
        import darkhours.sky_events as se

        base = date(2026, 7, 2)
        window_start = base - timedelta(days=14)
        nights = [_fake_night(window_start + timedelta(days=i), dark_hours=float(i)) for i in range(30)]
        stored = {"window_start": window_start.isoformat(), "nights": se._nights_to_json(nights)}

        # Round-trip through json.dumps/loads for real, like LocalFileCache/DynamoCache do.
        db_backing = {se._dark_cycle_db_key(40.0, -105.0, window_start): _json.loads(_json.dumps(stored))}
        monkeypatch.setattr(se._cache, "get", lambda k: db_backing.get(k))

        result = se.lunar_cycle_dark_analysis(40.0, -105.0, base, None)

        assert result["tonight_hours"] == 14.0  # index 14 == base itself
        assert isinstance(result["tonight"]["sunset"], datetime)
        assert result["tonight"]["sunset"].tzinfo is not None
        assert result["tonight"]["sunset"].date() == base

    def test_tonight_record_is_a_copy_not_a_cache_reference(self, monkeypatch):
        """The returned 'tonight' dict must be safe to mutate — it's read from a
        cache entry (_mem_dark_cycle) shared across every concurrent caller for
        overlapping dates. Mutating it must not corrupt what the next caller sees."""
        import darkhours.sky_events as se

        def fake_compute(lat, lon, target_date, tz):
            window_start = target_date - timedelta(days=14)
            return [_fake_night(window_start + timedelta(days=i)) for i in range(30)]

        monkeypatch.setattr(se, "_compute_dark_hours_cycle", fake_compute)

        base = date(2026, 7, 2)
        r1 = se.lunar_cycle_dark_analysis(40.0, -105.0, base, None)
        r1["tonight"]["sunset"] = "corrupted"
        r1["tonight"]["dark_hours"] = -999
        r1["tonight"]["new_key"] = "should not leak into the cache"

        r2 = se.lunar_cycle_dark_analysis(40.0, -105.0, base + timedelta(days=1), None)

        assert r2["tonight"]["sunset"] != "corrupted"
        assert r2["tonight"]["dark_hours"] == 5.0
        assert "new_key" not in r2["tonight"]
