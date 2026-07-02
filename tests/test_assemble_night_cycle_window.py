"""Hermetic tests for assemble_night()'s use_cycle_window path — the calendar/
trip lightweight branch that derives sunset/sunrise/night_start/night_end/
dark_hours_tonight from lunar_cycle_dark_analysis()'s window instead of an
independent sky_events() call (see PyNightSkyPredictor/sky_events.py). Mocks
out darksky/light_dome/sky_events/moon_events entirely so this needs no
rasters, ephemeris, or network — default pytest run stays offline.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import PyNightSkyPredictor.predictor as predictor
from PyNightSkyPredictor.moonlight import ks_moon_credit

_BORTLE_INFO = {
    "sqm": 21.5, "bortle_class": 3, "bortle_desc": "Rural",
    "lp_zone": "2", "below_detection": False, "source": "VIIRS 2025",
}
_ILLUMINATION_PCT = 35.0
_PHASE_NAME = "Waxing Crescent"


def _tonight_record(sunset, sunrise, dark_hours):
    return {
        "sunset":      sunset,
        "sunrise":     sunrise,
        "night_start": sunset + timedelta(hours=1, minutes=30),
        "night_end":   sunrise - timedelta(hours=1, minutes=30),
        "dark_hours":  dark_hours,
    }


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    monkeypatch.setattr(predictor._ds, "lookup", lambda lat, lon: dict(_BORTLE_INFO))
    monkeypatch.setattr(predictor._ld, "lightdome_lookup", lambda lat, lon: None)
    monkeypatch.setattr(predictor.se, "moon_phase_info", lambda at_utc: (_PHASE_NAME, _ILLUMINATION_PCT))
    monkeypatch.setattr(predictor._me, "moon_distance_km", lambda at_utc: 384_400.0)
    monkeypatch.setattr(predictor._me, "classify_full_moon", lambda illum, dist: None)
    monkeypatch.setattr(predictor._me, "eclipses_for_night", lambda sunset, sunrise: [])


def test_use_cycle_window_skips_sky_events_and_moonrise_moonset(monkeypatch):
    target  = date(2026, 7, 15)
    sunset  = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    sunrise = sunset + timedelta(hours=8)
    tonight = _tonight_record(sunset, sunrise, dark_hours=4.0)

    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {
            "tonight_hours": 4.0, "mean_hours": 4.0, "stdev_hours": 0.0,
            "score": 8.0, "tonight": tonight,
        },
    )

    def _boom(*a, **kw):
        raise AssertionError("use_cycle_window=True must not call sky_events()")
    monkeypatch.setattr(predictor.se, "sky_events", _boom)

    report = predictor.assemble_night(
        40.0, -105.0, target, ZoneInfo("America/Denver"),
        fetch_weather=False, use_cycle_window=True,
    )

    assert report.dark_score == 8.0
    assert report.dark_hours == 4.0
    assert report.events == []
    assert report.moonrise is None and report.moonset is None


def test_use_cycle_window_moon_score_matches_the_shared_formula(monkeypatch):
    """The lightweight branch doesn't reimplement the moon_score formula — it
    feeds the same shared code a different sunset/sunrise/dark_hours_tonight.
    Verify the output actually matches what that formula computes for these
    inputs, not just that it runs without error."""
    target  = date(2026, 7, 15)
    sunset  = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    sunrise = sunset + timedelta(hours=8)
    dark_hours_tonight = 4.0
    tonight = _tonight_record(sunset, sunrise, dark_hours=dark_hours_tonight)
    night_start, night_end = tonight["night_start"], tonight["night_end"]

    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {
            "tonight_hours": dark_hours_tonight, "mean_hours": dark_hours_tonight,
            "stdev_hours": 0.0, "score": 7.5, "tonight": tonight,
        },
    )
    monkeypatch.setattr(predictor.se, "sky_events",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not be called")))

    report = predictor.assemble_night(
        40.0, -105.0, target, ZoneInfo("America/Denver"),
        fetch_weather=False, use_cycle_window=True,
    )

    total_astro_hours = (night_end - night_start).total_seconds() / 3600
    moonlit_frac = 1.0 - (dark_hours_tonight / total_astro_hours)
    expected_moon_score = round(10 * ((1 - moonlit_frac) + moonlit_frac * ks_moon_credit(_ILLUMINATION_PCT)), 1)

    assert report.score_components["moon"] == expected_moon_score
    assert report.dark_hours == dark_hours_tonight


def test_use_cycle_window_raises_when_tonight_sunset_missing(monkeypatch):
    """If the dark-cycle window couldn't resolve a sunset for this date (the
    unresolvable-edge-case fallback), raise the same ValueError the
    full-precision path raises today rather than let a broken record through."""
    target = date(2026, 7, 15)
    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {
            "tonight_hours": 0.0, "mean_hours": 0.0, "stdev_hours": 0.0, "score": 0.0,
            "tonight": {"sunset": None, "sunrise": None, "night_start": None,
                        "night_end": None, "dark_hours": 0.0},
        },
    )

    with pytest.raises(ValueError, match="No sunset found"):
        predictor.assemble_night(
            40.0, -105.0, target, ZoneInfo("America/Denver"),
            fetch_weather=False, use_cycle_window=True,
        )


def test_default_use_cycle_window_false_still_calls_sky_events(monkeypatch):
    """Byte-compatibility guard: every existing caller (/night, CLI) leaves
    use_cycle_window at its False default and must still hit the full-precision
    sky_events() path."""
    called = {"sky_events": False}

    def fake_sky_events(lat, lon, d):
        called["sky_events"] = True
        sunset  = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
        sunrise = sunset + timedelta(hours=8)
        return [
            {"time": sunset, "label": "Sunset"},
            {"time": sunrise, "label": "Sunrise"},
        ]

    monkeypatch.setattr(predictor.se, "sky_events", fake_sky_events)
    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {"tonight_hours": 0.0, "mean_hours": 0.0,
                                  "stdev_hours": 0.0, "score": 0.0,
                                  "tonight": {"sunset": None, "sunrise": None,
                                              "night_start": None, "night_end": None,
                                              "dark_hours": 0.0}},
    )

    predictor.assemble_night(
        40.0, -105.0, date(2026, 7, 15), ZoneInfo("America/Denver"),
        fetch_weather=False,
    )

    assert called["sky_events"] is True
