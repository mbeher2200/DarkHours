"""Tests for predictor._apply_condition_vectors (Phase 1 Viability Engine)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from PyNightSkyPredictor.predictor import _apply_condition_vectors, _CLOUD_BLOCK_PCT, _MIN_VIABLE_MIN
from PyNightSkyPredictor.targets import TargetWindow, VisibleTarget
from PyNightSkyPredictor.weather import WeatherPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_BASE = datetime(2025, 7, 1, 22, 0, 0, tzinfo=_UTC)  # nominal "window start"


def _make_window(
    start_offset_h=0,
    end_offset_h=4,
    peak_offset_h=2,
    peak_alt=45.0,
    peak_az=180.0,
    moon_sep=90.0,
    moon_alt=10.0,
    moon_interference=False,
    photo_cutoff=None,
    photo_start=None,
):
    start = _BASE + timedelta(hours=start_offset_h)
    end   = _BASE + timedelta(hours=end_offset_h)
    peak  = _BASE + timedelta(hours=peak_offset_h)
    return TargetWindow(
        start=start,
        end=end,
        start_alt_deg=20.0,
        end_alt_deg=15.0,
        peak_time=peak,
        peak_alt_deg=peak_alt,
        peak_az_deg=peak_az,
        moon_interference=moon_interference,
        moon_sep_at_peak_deg=moon_sep,
        moon_alt_at_peak_deg=moon_alt,
        photo_cutoff=photo_cutoff,
        photo_start=photo_start,
    )


def _make_target(window):
    return VisibleTarget(name="M42", type="nebula", windows=[window], note=None)


def _wx(offset_h, cloud=0, transparency="Excellent", humidity=50):
    return WeatherPoint(
        time=_BASE + timedelta(hours=offset_h),
        cloud_cover_pct=cloud,
        seeing_arcsec=None,
        transparency=transparency,
        humidity_pct=humidity,
        wind_speed_ms=None,
        lifted_index=None,
        precip_type="none",
        temperature_c=15.0,
        feels_like_c=14.0,
    )


def _run(target, weather=None, light_dome=None, illumination=0.0):
    _apply_condition_vectors(
        [target],
        weather or [],
        light_dome,
        illumination,
    )


# ---------------------------------------------------------------------------
# Atmospheric Vector — MCVI
# ---------------------------------------------------------------------------

def test_no_weather_noop():
    """Empty weather list → fail-open: effective window = geometric window."""
    w = _make_window()
    t = _make_target(w)
    _run(t)
    assert w.effective_start == w.start
    assert w.effective_end   == w.end
    assert w.blockers        == []
    assert t.viability       == "ok"


def test_clear_night_no_blockers():
    """All weather points clear → effective window = geometric window."""
    w  = _make_window()
    wx = [_wx(i) for i in range(5)]
    t  = _make_target(w)
    _run(t, weather=wx)
    assert w.effective_start == w.start
    assert w.effective_end   == w.end
    assert w.blockers        == []
    assert t.viability       == "ok"


def test_early_night_cloud_clears_later():
    """Cloudy for first 2 hours, then clear — effective_start advances past cloud block."""
    w = _make_window(peak_offset_h=3)  # peak at hour 3 (in clear period)
    wx_pts = [
        _wx(0, cloud=90),   # blocked
        _wx(1, cloud=85),   # blocked
        _wx(2, cloud=20),   # clear
        _wx(3, cloud=10),   # clear (contains peak)
        _wx(4, cloud=5),    # clear
    ]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    # MCVI should find the block starting at hour 2
    assert w.effective_start is not None
    assert w.effective_start >= _BASE + timedelta(hours=2)
    assert w.effective_end   == w.end
    assert "cloud" not in w.blockers  # peak IS in a viable block
    assert w.best_time == w.peak_time
    assert t.viability in ("ok", "degraded")


def test_mid_night_gap_peak_in_first_block():
    """Clear → 1h cloudy gap → clear. Peak in first block → MCVI = first block."""
    w = _make_window(peak_offset_h=1)  # peak at hour 1 (first clear block)
    wx_pts = [
        _wx(0, cloud=0),    # clear
        _wx(1, cloud=5),    # clear — contains peak
        _wx(2, cloud=95),   # blocked (gap)
        _wx(3, cloud=10),   # clear (second block)
        _wx(4, cloud=5),    # clear
    ]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    # effective_end should be capped before the gap
    assert w.effective_start == w.start
    assert w.effective_end is not None
    assert w.effective_end <= _BASE + timedelta(hours=2)
    assert w.best_time == w.peak_time


def test_mid_night_gap_peak_in_gap_selects_longer_block():
    """Peak falls in cloudy gap → MCVI selects the longer viable block."""
    w = _make_window(peak_offset_h=2)  # peak at hour 2 (in gap)
    wx_pts = [
        _wx(0, cloud=0),    # clear  — block A: 1 point
        _wx(1, cloud=90),   # blocked
        _wx(2, cloud=80),   # blocked — peak is here
        _wx(3, cloud=90),   # blocked
        _wx(4, cloud=0),    # clear  — block B: 1 point (same duration, but B is after gap)
        # Add a second point to block B so it's clearly longer
    ]
    # Make first block shorter and second block longer
    wx_pts = [
        _wx(0, cloud=0),    # block A: 1 point
        _wx(1, cloud=95),   # blocked
        _wx(2, cloud=95),   # blocked — peak
        _wx(3, cloud=5),    # block B
        _wx(4, cloud=5),    # block B  (longer: 2 points)
    ]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    # Block B is longer; effective_start should be in block B
    assert w.effective_start is not None
    assert w.effective_start >= _BASE + timedelta(hours=3)


def test_fully_cloudy_night():
    """All weather points blocked → effective_start = effective_end = None, blocked."""
    w = _make_window()
    wx_pts = [_wx(i, cloud=95) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.effective_start is None
    assert w.effective_end   is None
    assert "cloud" in w.blockers
    assert w.best_time       is None
    assert t.viability       == "blocked"


def test_transparency_poor_blocks():
    """transparency=Poor → "transparency" in blockers (cloud may be fine)."""
    w = _make_window()
    wx_pts = [_wx(i, cloud=10, transparency="Poor") for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert "transparency" in w.blockers
    assert t.viability == "blocked"


def test_humidity_excluded():
    """High humidity alone must NOT trigger any blocker."""
    w = _make_window()
    wx_pts = [_wx(i, cloud=5, transparency="Excellent", humidity=95) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.blockers == []
    assert t.viability == "ok"


# ---------------------------------------------------------------------------
# Light Dome Vector
# ---------------------------------------------------------------------------

def _dome_info(direction_scores: dict, direction_heights: dict):
    """Build a minimal LightDomeSummary-shaped dict."""
    dirs = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return {
        "sky_state": "domed",
        "scores": {d: direction_scores.get(d, 0.0) for d in dirs},
        "dome_heights": {d: direction_heights.get(d, 0.0) for d in dirs},
        "darkest_direction": "N",
        "darkest_score": 0.01,
        "domes": [],
    }


def test_dome_block_at_peak_azimuth():
    """High dome score at target azimuth (S) → light_dome blocker."""
    # glow_toward at az=180 (S), alt=5 with score=1.0, theta=10 → glow ≈ 0.8 > 0.25
    ld = _dome_info({"S": 1.0, "SE": 0.5, "SW": 0.5}, {"S": 10.0, "SE": 10.0, "SW": 10.0})
    w  = _make_window(peak_az=180.0, peak_alt=5.0)
    t  = _make_target(w)
    _run(t, light_dome=ld)
    assert "light_dome" in w.blockers
    assert w.dome_glow_at_peak is not None
    assert w.dome_glow_at_peak >= 0.25
    assert t.viability in ("degraded", "blocked")


def test_dome_no_block_high_altitude():
    """Same dome, target at 60° altitude — altitude falloff keeps glow below threshold."""
    # glow = score / (1 + (alt/theta)^2) = 1.0 / (1 + (60/10)^2) = 1/37 ≈ 0.027
    ld = _dome_info({"S": 1.0, "SE": 0.5, "SW": 0.5}, {"S": 10.0, "SE": 10.0, "SW": 10.0})
    w  = _make_window(peak_az=180.0, peak_alt=60.0)
    t  = _make_target(w)
    _run(t, light_dome=ld)
    assert "light_dome" not in w.blockers
    assert w.dome_glow_at_peak is not None
    assert w.dome_glow_at_peak < 0.25


def test_dome_none_outside_coverage():
    """No dome info (outside CONUS) → dome_glow_at_peak is None, no blocker."""
    w = _make_window()
    t = _make_target(w)
    _run(t, light_dome=None)
    assert w.dome_glow_at_peak is None
    assert "light_dome" not in w.blockers


# ---------------------------------------------------------------------------
# Lunar Proximity Vector
# ---------------------------------------------------------------------------

def test_moon_washout_full_moon_tight_separation():
    """Full moon (100%), 10° sep → within 45° radius → moon_washout blocker."""
    w = _make_window(moon_sep=10.0, moon_alt=45.0)
    t = _make_target(w)
    _run(t, illumination=100.0)
    assert "moon_washout" in w.blockers
    assert t.viability in ("degraded", "blocked")


def test_moon_no_washout_crescent_exempt():
    """≤20% illumination (crescent exemption) → no moon_washout regardless of separation."""
    w = _make_window(moon_sep=5.0, moon_alt=45.0)
    t = _make_target(w)
    _run(t, illumination=15.0)
    assert "moon_washout" not in w.blockers


def test_moon_no_washout_wide_separation():
    """Bright moon (90%) but 120° separation; effective radius = 40.5° < 120° → no washout."""
    w = _make_window(moon_sep=120.0, moon_alt=45.0)
    t = _make_target(w)
    _run(t, illumination=90.0)
    assert "moon_washout" not in w.blockers


def test_moon_washout_inside_weighted_radius():
    """80% moon, 30° sep; effective radius = 36° > 30° → washout."""
    w = _make_window(moon_sep=30.0, moon_alt=30.0)
    t = _make_target(w)
    _run(t, illumination=80.0)
    assert "moon_washout" in w.blockers


# ---------------------------------------------------------------------------
# Best Time
# ---------------------------------------------------------------------------

def test_best_time_uses_peak_when_inside_effective():
    """Peak falls within the effective window → best_time = peak_time."""
    w = _make_window(peak_offset_h=2)
    wx_pts = [_wx(i) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.best_time == w.peak_time


def test_best_time_snaps_to_eff_end_when_peak_cut_off():
    """Weather cuts window at hour 1 but peak is at hour 2 → best_time = eff_end."""
    w = _make_window(peak_offset_h=2)
    wx_pts = [
        _wx(0, cloud=0),
        _wx(1, cloud=0),
        _wx(2, cloud=95),   # blocked — peak is here
        _wx(3, cloud=95),
        _wx(4, cloud=95),
    ]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    # peak (hour 2) is past effective_end (near hour 1); snap to effective_end
    assert w.best_time is not None
    assert w.best_time <= _BASE + timedelta(hours=2)


def test_best_time_none_when_fully_blocked():
    """Entirely cloudy → best_time = None."""
    w = _make_window()
    wx_pts = [_wx(i, cloud=95) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.best_time is None


def test_best_time_never_exceeds_effective_end():
    """Invariant: best_time <= effective_end when both are set."""
    w = _make_window(peak_offset_h=3)
    wx_pts = [
        _wx(0, cloud=0),
        _wx(1, cloud=0),
        _wx(2, cloud=95),
        _wx(3, cloud=95),
        _wx(4, cloud=95),
    ]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    if w.best_time is not None and w.effective_end is not None:
        assert w.best_time <= w.effective_end


# ---------------------------------------------------------------------------
# photo_start / photo_cutoff K&S integration
# ---------------------------------------------------------------------------

def test_photo_start_clamps_effective_start():
    """photo_start (K&S lower bound) must gate effective_start from going too early."""
    photo_start = _BASE + timedelta(hours=1)
    w = _make_window(photo_start=photo_start)
    # All weather clear, so MCVI would set effective_start = window.start (hour 0).
    # But photo_start is hour 1, so effective_start should be max(hour0, hour1) = hour1.
    wx_pts = [_wx(i) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.effective_start == photo_start


def test_photo_cutoff_clamps_effective_end():
    """photo_cutoff (K&S upper bound) gates effective_end."""
    photo_cutoff = _BASE + timedelta(hours=2)
    w = _make_window(photo_cutoff=photo_cutoff)
    wx_pts = [_wx(i) for i in range(5)]
    t = _make_target(w)
    _run(t, weather=wx_pts)
    assert w.effective_end <= photo_cutoff
