"""Hermetic tests for the operator feature-flag gates in predictor.assemble_night()
(satellites, aurora, milky_way, live_haze — see darkhours/feature_flags.py). Verifies
that disabling a flag skips the underlying fetch/computation entirely (no network
attempted) while the rest of the report still assembles; enabling it (the default)
leaves the existing call sites untouched. Mocks out darksky/light_dome/sky_events/
moon_events/targets, same base as test_assemble_night_cycle_window.py, so this needs
no rasters, ephemeris, or network.

Uses "today" (real UTC date) as the target throughout, not a fixed calendar date:
several of the gates under test (satellites, aurora, live haze) are themselves
date-windowed relative to wall-clock "now", so a hardcoded past date would skip
them for reasons unrelated to the flag being tested.
"""
import types
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

import darkhours.predictor as predictor
from darkhours import tle_provider

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
    monkeypatch.setattr(predictor._tgt, "visible_targets", lambda *a, **kw: [])
    from darkhours import ports as _ports
    monkeypatch.setattr(_ports.get_backend().cache, "get", lambda key: None)
    monkeypatch.setattr(_ports.get_backend().cache, "set", lambda *a, **kw: None)
    monkeypatch.setattr(predictor.wx, "forecast", lambda lat, lon: ([], "stub", "2026-07-14T00:00:00Z"))

    today   = datetime.now(timezone.utc).date()
    sunset  = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=20)
    sunrise = sunset + timedelta(hours=8)
    tonight = _tonight_record(sunset, sunrise, dark_hours=4.0)
    monkeypatch.setattr(
        predictor.se, "lunar_cycle_dark_analysis",
        lambda lat, lon, d, tz: {
            "tonight_hours": 4.0, "mean_hours": 4.0, "stdev_hours": 0.0,
            "score": 8.0, "tonight": tonight,
        },
    )
    return today


def _assemble(target, **kw):
    return predictor.assemble_night(
        40.0, -105.0, target, ZoneInfo("America/Denver"),
        use_cycle_window=True, **kw,
    )


# ---------------------------------------------------------------------------
# satellites
# ---------------------------------------------------------------------------

def test_satellites_flag_off_skips_tle_fetch(monkeypatch, _mocks):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_SATELLITES_DISABLE", "1")
    get_tle = mock.MagicMock()
    monkeypatch.setattr(tle_provider, "get_tle", get_tle)
    report = _assemble(_mocks, fetch_weather=False, fetch_satellites=True)
    get_tle.assert_not_called()
    assert report.sat_passes == []


def test_satellites_flag_on_by_default_still_fetches(monkeypatch, _mocks):
    get_tle = mock.MagicMock(return_value=types.SimpleNamespace(lines=None, stale=False))
    get_starlink = mock.MagicMock(return_value=([], False, None))
    monkeypatch.setattr(tle_provider, "get_tle", get_tle)
    monkeypatch.setattr(tle_provider, "get_starlink_train_tles", get_starlink)
    _assemble(_mocks, fetch_weather=False, fetch_satellites=True)


def test_starlink_fetch_raising_does_not_crash_the_report(monkeypatch, _mocks):
    """Not a feature-flag test — a resilience gap found live in production (a
    Celestrak read timeout raised out of get_starlink_train_tles() and crashed the
    whole /night response, tel_provider.py fixed at the source separately). This is
    the defense-in-depth layer: even an unanticipated future failure mode here must
    not take down weather/moon/darksky, which had already succeeded."""
    get_tle = mock.MagicMock(return_value=types.SimpleNamespace(lines=None, stale=False))
    get_starlink = mock.MagicMock(side_effect=TimeoutError("The read operation timed out"))
    monkeypatch.setattr(tle_provider, "get_tle", get_tle)
    monkeypatch.setattr(tle_provider, "get_starlink_train_tles", get_starlink)
    report = _assemble(_mocks, fetch_weather=False, fetch_satellites=True)
    assert report.sat_starlink_unavailable is True
    assert report.starlink_trains == []
    assert get_tle.called


# ---------------------------------------------------------------------------
# aurora
# ---------------------------------------------------------------------------

def test_aurora_flag_off_skips_kp_fetch(monkeypatch, _mocks):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_AURORA_DISABLE", "1")
    fetch_kp = mock.MagicMock(return_value=([], False))
    monkeypatch.setattr(predictor._aur, "fetch_kp_forecast", fetch_kp)
    report = _assemble(_mocks, fetch_weather=False, fetch_aurora=True)
    fetch_kp.assert_not_called()
    assert report.aurora is None


def test_aurora_flag_on_by_default_still_fetches(monkeypatch, _mocks):
    fetch_kp = mock.MagicMock(return_value=([], False))
    monkeypatch.setattr(predictor._aur, "fetch_kp_forecast", fetch_kp)
    _assemble(_mocks, fetch_weather=False, fetch_aurora=True)
    assert fetch_kp.called


# ---------------------------------------------------------------------------
# milky_way
# ---------------------------------------------------------------------------

def _mw_target():
    return types.SimpleNamespace(type="milky_way", windows=[])


def test_milky_way_flag_off_skips_arch_summary(monkeypatch, _mocks):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_MILKY_WAY_DISABLE", "1")
    monkeypatch.setattr(predictor._tgt, "visible_targets", lambda *a, **kw: [_mw_target()])
    # _apply_condition_vectors is unrelated to the gate under test and expects a
    # fully-populated TargetWindow; stub it out so the minimal target above suffices.
    monkeypatch.setattr(predictor, "_apply_condition_vectors", lambda *a, **kw: None)
    mw_arch = mock.MagicMock()
    monkeypatch.setattr(predictor, "_mw_arch_summary", mw_arch)
    report = _assemble(_mocks, fetch_weather=False, fetch_targets=True)
    mw_arch.assert_not_called()
    assert report.mw_summary is None


def test_milky_way_flag_on_by_default_still_summarizes(monkeypatch, _mocks):
    monkeypatch.setattr(predictor._tgt, "visible_targets", lambda *a, **kw: [_mw_target()])
    monkeypatch.setattr(predictor, "_apply_condition_vectors", lambda *a, **kw: None)
    mw_arch = mock.MagicMock(return_value={})
    monkeypatch.setattr(predictor, "_mw_arch_summary", mw_arch)
    _assemble(_mocks, fetch_weather=False, fetch_targets=True)
    assert mw_arch.called


# ---------------------------------------------------------------------------
# live_haze
# ---------------------------------------------------------------------------

def test_live_haze_flag_off_skips_aqicn_fetch(monkeypatch, _mocks):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_LIVE_HAZE_DISABLE", "1")
    haze = mock.MagicMock()
    monkeypatch.setattr(predictor._aqicn, "current_haze", haze)
    _assemble(_mocks, fetch_weather=True)
    haze.assert_not_called()


def test_live_haze_flag_on_by_default_still_fetches(monkeypatch, _mocks):
    haze = mock.MagicMock(return_value=None)
    monkeypatch.setattr(predictor._aqicn, "current_haze", haze)
    _assemble(_mocks, fetch_weather=True)
    assert haze.called
