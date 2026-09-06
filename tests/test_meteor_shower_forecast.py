"""Tests for predictor.meteor_shower_forecast — observer specific realistic
meteor-shower rate forecast (date-decayed ZHR × radiant altitude × moonlight-
aware limiting-magnitude degradation from site light pollution).
"""

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from darkhours import predictor
from darkhours.moonlight import KS_NATURAL_SKY, ks_delta_mag, nelm_from_sqm
from darkhours.targets import TargetWindow, VisibleTarget

_SUNSET  = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
_SUNRISE = _SUNSET + timedelta(hours=8)
_NIGHT_START = _SUNSET + timedelta(hours=1)
_NIGHT_END   = _SUNRISE - timedelta(hours=1)

_BORTLE_INFO = {
    "sqm": 21.5, "bortle_class": 3, "bortle_desc": "Rural",
    "lp_zone": "2", "below_detection": False, "source": "VIIRS 2025",
}


def _events(include_sunset=True, include_sunrise=True):
    events = []
    if include_sunset:
        events.append({"time": _SUNSET, "label": "Sunset"})
    events.append({"time": _NIGHT_START, "label": "Astronomical night begins"})
    events.append({"time": _NIGHT_END, "label": "Astronomical night ends"})
    if include_sunrise:
        events.append({"time": _SUNRISE, "label": "Sunrise"})
    return events


def _shower(name, zhr=100, zhr_effective=50.0, note="Peak night"):
    return {
        "name": name, "note": note, "zhr": zhr,
        "zhr_effective": zhr_effective,
        "peak_time_utc": _SUNSET.isoformat(),
    }


def _visible_target(name, peak_alt_deg, population_index=2.0,
                     moon_sep_deg=90.0, moon_alt_deg=-10.0, zhr_effective=50.0):
    window = TargetWindow(
        start=_NIGHT_START, end=_NIGHT_END,
        start_alt_deg=peak_alt_deg, end_alt_deg=peak_alt_deg,
        peak_time=_NIGHT_START + timedelta(hours=1),
        peak_alt_deg=peak_alt_deg, peak_az_deg=180.0,
        moon_sep_at_peak_deg=moon_sep_deg, moon_alt_at_peak_deg=moon_alt_deg,
    )
    return VisibleTarget(
        name=name, type="meteor_shower", windows=[window], note="Peak night",
        zhr_effective=zhr_effective, population_index=population_index,
    )


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    monkeypatch.setattr(predictor.loc, "timezone_for", lambda lat, lon: ZoneInfo("UTC"))
    monkeypatch.setattr(predictor._ds, "lookup", lambda lat, lon: dict(_BORTLE_INFO))
    monkeypatch.setattr(predictor.se, "moon_phase_info", lambda at_utc: ("Waxing Crescent", 35.0))


def _mock_pipeline(monkeypatch, showers, visible, events=None):
    monkeypatch.setattr(predictor._tgt, "active_meteor_showers", lambda target: showers)
    monkeypatch.setattr(predictor.se, "sky_events", lambda lat, lon, target: events or _events())
    monkeypatch.setattr(predictor._tgt, "visible_targets", lambda *a, **kw: visible)


def test_no_active_showers_returns_empty_without_sky_events(monkeypatch):
    monkeypatch.setattr(predictor._tgt, "active_meteor_showers", lambda target: [])

    def _boom(*a, **kw):
        raise AssertionError("sky_events should not be called when no showers are active")
    monkeypatch.setattr(predictor.se, "sky_events", _boom)

    assert predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9) == []


def test_raises_when_no_sunset_found(monkeypatch):
    _mock_pipeline(monkeypatch, [_shower("Perseids")], [], events=_events(include_sunset=False))
    with pytest.raises(ValueError, match="No sunset found"):
        predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)


def test_raises_when_no_sunrise_found(monkeypatch):
    _mock_pipeline(monkeypatch, [_shower("Perseids")], [], events=_events(include_sunrise=False))
    with pytest.raises(ValueError, match="No sunrise found"):
        predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)


def test_visible_targets_called_with_shower_only_filter_and_full_horizon(monkeypatch):
    calls = []

    def fake_visible_targets(*a, **kw):
        calls.append(kw)
        return []

    monkeypatch.setattr(predictor._tgt, "active_meteor_showers", lambda target: [_shower("Perseids")])
    monkeypatch.setattr(predictor.se, "sky_events", lambda lat, lon, target: _events())
    monkeypatch.setattr(predictor._tgt, "visible_targets", fake_visible_targets)

    predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)

    assert len(calls) == 1
    assert calls[0]["target_types"] == {"meteor_shower"}
    assert calls[0]["min_elevation"] == 0.0
    assert calls[0]["sky_sqm"] == _BORTLE_INFO["sqm"]


# ---------------------------------------------------------------------------
# Rate math
# ---------------------------------------------------------------------------

def test_realistic_rate_matches_zhr_times_altitude_times_lm_factor(monkeypatch):
    vt = _visible_target("Perseids", peak_alt_deg=30.0, population_index=2.0, zhr_effective=50.0)
    _mock_pipeline(monkeypatch, [_shower("Perseids", zhr=100, zhr_effective=50.0)], [vt])

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    assert len(results) == 1
    r = results[0]

    delta = ks_delta_mag(35.0, 90.0, -10.0, _BORTLE_INFO["sqm"], target_alt_deg=30.0)
    lm = nelm_from_sqm(_BORTLE_INFO["sqm"] - delta)
    expected_lm_factor = min(1.0, 2.0 ** (lm - 6.5))
    expected_rate = round(50.0 * (0.5) * expected_lm_factor, 1)  # sin(30deg) == 0.5

    assert r["name"] == "Perseids"
    assert r["zhr"] == 100
    assert r["zhr_effective"] == 50.0
    assert r["radiant_alt_deg"] == 30.0
    assert r["lm_factor"] == pytest.approx(round(expected_lm_factor, 3))
    assert r["realistic_rate_per_hour"] == pytest.approx(expected_rate)
    assert r["site_sqm"] == _BORTLE_INFO["sqm"]


def test_shower_missing_from_visible_targets_gets_zero_rate(monkeypatch):
    # Active per calendar (note/zhr present) but geometry engine found no window
    # for it at all — e.g. the catalog radiant never clears the horizon at min_elevation=0.
    _mock_pipeline(monkeypatch, [_shower("Perseids")], [])

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    assert len(results) == 1
    r = results[0]
    assert r["radiant_alt_deg"] is None
    assert r["lm_factor"] is None
    assert r["realistic_rate_per_hour"] == 0.0


def test_radiant_below_horizon_gets_zero_rate(monkeypatch):
    vt = _visible_target("Perseids", peak_alt_deg=-5.0)
    _mock_pipeline(monkeypatch, [_shower("Perseids")], [vt])

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    r = results[0]
    assert r["radiant_alt_deg"] is None
    assert r["lm_factor"] is None
    assert r["realistic_rate_per_hour"] == 0.0


def test_missing_population_index_skips_lm_factor_but_still_rates(monkeypatch):
    vt = _visible_target("Perseids", peak_alt_deg=45.0, population_index=None, zhr_effective=50.0)
    _mock_pipeline(monkeypatch, [_shower("Perseids", zhr_effective=50.0)], [vt])

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    r = results[0]
    expected_rate = round(50.0 * math.sin(math.radians(45.0)), 1)
    assert r["lm_factor"] is None
    assert r["realistic_rate_per_hour"] == pytest.approx(expected_rate)


def test_darksky_lookup_none_falls_back_to_natural_sky(monkeypatch):
    monkeypatch.setattr(predictor._ds, "lookup", lambda lat, lon: None)
    vt = _visible_target("Perseids", peak_alt_deg=45.0)
    _mock_pipeline(monkeypatch, [_shower("Perseids")], [vt])

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    assert results[0]["site_sqm"] == KS_NATURAL_SKY


def test_results_sorted_by_realistic_rate_descending(monkeypatch):
    low  = _visible_target("Low",  peak_alt_deg=10.0, zhr_effective=10.0)
    high = _visible_target("High", peak_alt_deg=80.0, zhr_effective=100.0)
    _mock_pipeline(
        monkeypatch,
        [_shower("Low", zhr_effective=10.0), _shower("High", zhr_effective=100.0)],
        [low, high],
    )

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)
    assert [r["name"] for r in results] == ["High", "Low"]
    assert results[0]["realistic_rate_per_hour"] >= results[1]["realistic_rate_per_hour"]


# ---------------------------------------------------------------------------
# End-to-end smoke test against the real catalog/ephemeris (darksky still mocked
# to avoid requiring local light-pollution rasters).
# ---------------------------------------------------------------------------

@pytest.mark.eph
def test_perseids_peak_night_end_to_end(monkeypatch):
    monkeypatch.setattr(predictor._ds, "lookup", lambda lat, lon: dict(_BORTLE_INFO))

    results = predictor.meteor_shower_forecast(date(2026, 8, 12), 35.7, -80.9)

    per = next(r for r in results if r["name"] == "Perseids")
    assert per["note"] == "Peak night"
    assert per["zhr_effective"] == per["zhr"]
    assert per["radiant_alt_deg"] is not None
    assert 0.0 < per["radiant_alt_deg"] < 90.0
    assert per["lm_factor"] is not None
    assert 0.0 < per["lm_factor"] <= 1.0
    assert per["realistic_rate_per_hour"] > 0.0
    assert per["site_sqm"] == _BORTLE_INFO["sqm"]
    # Results are sorted most-active-first.
    assert results[0]["realistic_rate_per_hour"] >= results[-1]["realistic_rate_per_hour"]
