"""Tests for the meteor shower ZHR decay model and peak-time helpers in targets.py."""

from datetime import date

import pytest

from darkhours.targets import (
    _days_from_peak,
    _gate_half_window_days,
    _meteor_shower_note,
    _peak_datetime,
    _resolve_peak_year_offset,
    active_meteor_showers,
    effective_zhr,
    load_targets,
    meaningful_activity_half_window,
)


def _shower(name, **overrides):
    base = {
        "name": name,
        "type": "meteor_shower",
        "radiant_ra": "00h 00m 00s",
        "radiant_dec": "+00° 00' 00\"",
        "peak_month": 8,
        "peak_day": 12,
        "active_window_days": 14,
        "peak_zhr": 100,
        "b_rise": 0.1,
        "b_decline": 0.1,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# effective_zhr
# ---------------------------------------------------------------------------

def test_effective_zhr_exact_at_peak():
    assert effective_zhr(100, 0, 0.1, 0.2) == 100


def test_effective_zhr_monotonic_decay_both_sides():
    vals_before = [effective_zhr(100, -t, 0.1, 0.2) for t in (0, 1, 2, 5)]
    vals_after  = [effective_zhr(100, t, 0.1, 0.2) for t in (0, 1, 2, 5)]
    assert vals_before == sorted(vals_before, reverse=True)
    assert vals_after == sorted(vals_after, reverse=True)


def test_effective_zhr_asymmetric_sides_differ():
    # Same |t|, different b_rise/b_decline → different decayed values.
    before = effective_zhr(100, -3, 0.1, 0.5)
    after  = effective_zhr(100, 3, 0.1, 0.5)
    assert before != after
    assert before > after  # shallower b_rise decays slower


def test_effective_zhr_zero_peak_zhr():
    assert effective_zhr(0, 3, 0.1, 0.1) == 0.0
    assert effective_zhr(-5, 3, 0.1, 0.1) == 0.0


# ---------------------------------------------------------------------------
# _resolve_peak_year_offset / _days_from_peak / _meteor_shower_note
# year-wraparound regression (Quadrantids: peak Jan 3; Ursids: peak Dec 22)
# ---------------------------------------------------------------------------

def test_year_wraparound_quadrantids():
    entries = {e["name"]: e for e in load_targets() if e.get("type") == "meteor_shower"}
    qua = entries["Quadrantids"]

    # Late Dec (next year's peak is closer than this year's already-passed one)
    assert _days_from_peak(qua, date(2025, 12, 30)) == -4
    assert _meteor_shower_note(qua, date(2026, 1, 1)) == "2 days before peak"
    assert _meteor_shower_note(qua, date(2026, 1, 3)) == "Peak night"
    assert _meteor_shower_note(qua, date(2026, 1, 6)) == "3 days after peak"


def test_year_wraparound_ursids():
    entries = {e["name"]: e for e in load_targets() if e.get("type") == "meteor_shower"}
    urs = entries["Ursids"]
    assert _meteor_shower_note(urs, date(2026, 12, 22)) == "Peak night"
    assert _days_from_peak(urs, date(2026, 12, 20)) == -2


def test_peak_datetime_year_matches_days_from_peak():
    """_peak_datetime must resolve the SAME year as _days_from_peak for any
    given night_date — a Dec 30 night must not get 'days before peak' text
    for next January while peak_time_utc points at last January's instance.
    """
    entries = {e["name"]: e for e in load_targets() if e.get("type") == "meteor_shower"}
    qua = entries["Quadrantids"]
    for d in (date(2025, 12, 30), date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 6)):
        resolved = _resolve_peak_year_offset(qua, d)
        delta = _days_from_peak(qua, d)
        pt = _peak_datetime(qua, d)
        assert resolved[1] == delta
        assert pt.year == d.year + resolved[0]
        assert pt.month == 1 and pt.day == 3


def test_peak_datetime_none_without_peak_hour_utc():
    entry = _shower("Synthetic")
    entry.pop("peak_hour_utc", None)
    assert _peak_datetime(entry, date(2026, 8, 12)) is None


# ---------------------------------------------------------------------------
# meaningful_activity_half_window / _gate_half_window_days
# ---------------------------------------------------------------------------

def test_meaningful_activity_half_window_hand_computed():
    # peak_zhr=120, b=0.3, floor=2 -> log10(60)/0.3 ~= 5.93
    import math
    expected = math.log10(60) / 0.3
    assert meaningful_activity_half_window(120, 0.3, 0.3, floor=2.0) == pytest.approx(expected)


def test_meaningful_activity_half_window_uses_shallower_side():
    # Shallower (smaller) b dominates -> wider window.
    wide = meaningful_activity_half_window(100, 0.05, 0.5, floor=2.0)
    narrow = meaningful_activity_half_window(100, 0.5, 0.5, floor=2.0)
    assert wide > narrow


def test_gate_half_window_uses_max_not_min():
    # Curated window wider than decay-derived -> gate uses curated.
    entry_wide_curated = _shower("A", active_window_days=100, peak_zhr=100, b_rise=0.5, b_decline=0.5)
    assert _gate_half_window_days(entry_wide_curated) == pytest.approx(50.0)

    # Decay-derived wider than curated -> gate uses decay-derived.
    entry_wide_decay = _shower("B", active_window_days=2, peak_zhr=100, b_rise=0.01, b_decline=0.01)
    gate = _gate_half_window_days(entry_wide_decay)
    assert gate > 1.0  # wider than curated half (1.0)


def test_gate_never_narrows_curated_catalog_entries():
    for entry in load_targets():
        if entry.get("type") != "meteor_shower":
            continue
        assert _gate_half_window_days(entry) >= entry["active_window_days"] / 2


# ---------------------------------------------------------------------------
# active_meteor_showers (fast path)
# ---------------------------------------------------------------------------

def test_active_meteor_showers_zhr_effective_never_exceeds_peak():
    showers = active_meteor_showers(date(2026, 8, 12))
    assert any(s["name"] == "Perseids" for s in showers)
    for s in showers:
        if s["zhr_effective"] is not None:
            assert s["zhr_effective"] <= s["zhr"]


def test_active_meteor_showers_peak_time_utc_is_iso():
    showers = active_meteor_showers(date(2026, 8, 12))
    per = next(s for s in showers if s["name"] == "Perseids")
    assert per["peak_time_utc"] is not None
    assert per["peak_time_utc"].startswith("2026-08-12T")


def test_active_meteor_showers_at_exact_peak_matches_peak_zhr():
    showers = active_meteor_showers(date(2026, 8, 12))
    per = next(s for s in showers if s["name"] == "Perseids")
    assert per["note"] == "Peak night"
    assert per["zhr_effective"] == per["zhr"]
