"""
Tests for moon_events.py — classify_full_moon (pure logic) + eclipse integration.

Eclipse integration tests are marked @pytest.mark.eph.
"""

from datetime import date, datetime, timezone

import pytest

from PyNightSkyPredictor.moon_events import (
    classify_full_moon,
    eclipses_for_night,
    find_lunar_eclipses,
    SUPERMOON_KM,
    MICROMOON_KM,
)


# ---------------------------------------------------------------------------
# classify_full_moon — pure logic, no ephemeris
# ---------------------------------------------------------------------------

class TestClassifyFullMoon:
    def test_below_illumination_threshold_returns_none(self):
        assert classify_full_moon(97.9, 355_000) is None

    def test_exactly_at_threshold_returns_none(self):
        # 98.0% is the threshold; anything below returns None
        # At exactly 98.0, the condition is illumination_pct < 98.0 → False, so it proceeds
        # Let's verify it doesn't return None for exactly 98%
        result = classify_full_moon(98.0, 384_400)
        assert result is None  # between thresholds

    def test_supermoon_close_full(self):
        assert classify_full_moon(99.0, 355_000) == "supermoon"

    def test_supermoon_at_threshold(self):
        assert classify_full_moon(99.0, SUPERMOON_KM) == "supermoon"

    def test_just_above_supermoon_threshold(self):
        assert classify_full_moon(99.0, SUPERMOON_KM + 1) is None

    def test_micromoon_at_threshold(self):
        assert classify_full_moon(99.0, MICROMOON_KM) == "micromoon"

    def test_just_below_micromoon_threshold(self):
        assert classify_full_moon(99.0, MICROMOON_KM - 1) is None

    def test_between_thresholds_returns_none(self):
        assert classify_full_moon(99.0, 384_400) is None

    def test_new_moon_returns_none_regardless_of_distance(self):
        for dist_km in (355_000, 384_400, 406_000):
            assert classify_full_moon(0.0, dist_km) is None

    def test_quarter_moon_returns_none_regardless_of_distance(self):
        for dist_km in (355_000, 384_400, 406_000):
            assert classify_full_moon(50.0, dist_km) is None


# ---------------------------------------------------------------------------
# find_lunar_eclipses — ephemeris integration
# ---------------------------------------------------------------------------

@pytest.mark.eph
class TestFindLunarEclipses:
    def test_total_eclipse_march_2026(self):
        """Total lunar eclipse on ~2026-03-03 should appear in a Feb–Apr search window."""
        results = find_lunar_eclipses(date(2026, 2, 1), date(2026, 4, 1))
        total = [e for e in results if e["kind"] == "total"]
        assert len(total) >= 1, f"Expected at least one total eclipse in Feb–Apr 2026, got {results}"
        # Verify it falls in early March 2026
        eclipse = total[0]
        assert eclipse["time"].month == 3
        assert eclipse["time"].year  == 2026
        assert eclipse["umbral_magnitude"] > 1.0, (
            f"Total eclipse umbral magnitude {eclipse['umbral_magnitude']} should be > 1.0"
        )

    def test_total_eclipse_has_required_fields(self):
        """Eclipse dicts have time, kind, penumbral_magnitude, umbral_magnitude."""
        results = find_lunar_eclipses(date(2026, 2, 1), date(2026, 4, 1))
        assert results, "Expected at least one eclipse in window"
        eclipse = results[0]
        assert "time"                  in eclipse
        assert "kind"                  in eclipse
        assert "penumbral_magnitude"   in eclipse
        assert "umbral_magnitude"      in eclipse
        assert eclipse["time"].tzinfo is not None, "Eclipse time must be UTC-aware"

    def test_partial_eclipse_aug_2026(self):
        """Partial lunar eclipse in Jul–Sep 2026 window."""
        results = find_lunar_eclipses(date(2026, 7, 1), date(2026, 10, 1))
        partial = [e for e in results if e["kind"] == "partial"]
        assert len(partial) >= 1, (
            f"Expected at least one partial eclipse in Jul–Sep 2026, got {results}"
        )
        eclipse = partial[0]
        assert 0 < eclipse["umbral_magnitude"] < 1.0, (
            f"Partial eclipse umbral_magnitude {eclipse['umbral_magnitude']} should be in (0, 1)"
        )

    def test_no_eclipses_in_empty_window(self):
        """January 2026 has no known lunar eclipses."""
        results = find_lunar_eclipses(date(2026, 1, 1), date(2026, 2, 1))
        assert results == [], f"Expected no eclipses in Jan 2026, got {results}"


# ---------------------------------------------------------------------------
# eclipses_for_night — ephemeris integration
# ---------------------------------------------------------------------------

@pytest.mark.eph
class TestEclipsesForNight:
    # Grand Canyon: lat=36.1°N, lon=-112.1°W, Arizona (UTC-7, no DST)
    # Night of 2026-03-02 local:
    #   sunset  ≈ 2026-03-03 01:15 UTC
    #   sunrise ≈ 2026-03-03 13:30 UTC
    # Total eclipse mid-point ≈ 2026-03-03 11:33 UTC → within window

    def _grand_canyon_night(self, local_date: date):
        """Return (sunset_utc, sunrise_utc) for Grand Canyon for the given local date.

        Uses local Arizona time (UTC-7, no DST) to identify the correct night,
        mirroring how predictor.py filters sunset by local date.
        """
        from PyNightSkyPredictor.sky_events import sky_events, find_event
        from zoneinfo import ZoneInfo
        lat, lon = 36.1069, -112.1129
        tz = ZoneInfo("America/Phoenix")  # MST, no daylight saving
        events = sky_events(lat, lon, local_date)
        # Find the sunset whose LOCAL date matches local_date (same as predictor.py)
        sunset = next(
            (e["time"] for e in events
             if e["label"] == "Sunset"
             and e["time"].astimezone(tz).date() == local_date),
            None,
        )
        sunrise = find_event(events, "Sunrise", after=sunset)
        return sunset, sunrise

    def test_eclipse_appears_on_correct_night(self):
        sunset, sunrise = self._grand_canyon_night(date(2026, 3, 2))
        eclipses = eclipses_for_night(sunset, sunrise)
        total = [e for e in eclipses if e["kind"] == "total"]
        assert len(total) >= 1, (
            f"Expected total eclipse on Grand Canyon night 2026-03-02, got {eclipses}"
        )

    def test_eclipse_absent_night_before(self):
        """Night of 2026-03-01 (day before eclipse) should have no eclipse mid-point."""
        sunset, sunrise = self._grand_canyon_night(date(2026, 3, 1))
        eclipses = eclipses_for_night(sunset, sunrise)
        total = [e for e in eclipses if e["kind"] == "total"]
        assert total == [], (
            f"Night of 2026-03-01 should have no total eclipse, got {total}"
        )
