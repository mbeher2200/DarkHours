"""
Tests for sky_events.py helpers.

Pure-math helpers (dark_moon_intervals, find_event, find_last_event) need no
fixtures.  Ephemeris-dependent integration tests are marked @pytest.mark.eph.
"""

from datetime import date, datetime, timezone

import pytest

from PyNightSkyPredictor.sky_events import dark_moon_intervals, find_event, find_last_event


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
# Ephemeris-based integration tests
# ---------------------------------------------------------------------------

@pytest.mark.eph
class TestMoonPhaseInfo:
    def test_known_full_moon_2026_05_31(self):
        from PyNightSkyPredictor.sky_events import moon_phase_info
        # Full moon on 2026-05-31; check at 12:00 UTC (near peak illumination)
        t = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        phase_name, illum = moon_phase_info(t)
        assert illum >= 99.0, f"Expected full moon illumination ≥ 99%, got {illum}%"
        assert "Full" in phase_name, f"Expected 'Full' in phase name, got {phase_name!r}"

    def test_known_new_moon_2026_06_14(self):
        from PyNightSkyPredictor.sky_events import moon_phase_info
        t = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
        phase_name, illum = moon_phase_info(t)
        assert illum < 2.0, f"Expected new moon illumination < 2%, got {illum}%"


@pytest.mark.eph
class TestSkyEventsIntegration:
    # Grand Canyon: lat=36.1°N, lon=-112.1°W, Arizona (no DST, UTC-7)
    LAT = 36.1069
    LON = -112.1129

    def test_sunset_grand_canyon_2026_03_02(self):
        from PyNightSkyPredictor.sky_events import sky_events, find_event
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
        from PyNightSkyPredictor.sky_events import sky_events
        d = date(2026, 3, 2)
        events = sky_events(self.LAT, self.LON, d)
        labels = {e["label"] for e in events}
        assert "Moonrise" in labels, "No Moonrise found for eclipse night"
        assert "Moonset"  in labels, "No Moonset found for eclipse night"
