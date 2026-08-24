"""Tests for satellites.py — pass geometry and the visible-window invariant.

satellite_passes() had no test file at all until a user reported a pass whose rise
time came after its peak time. The clamp helper is tested hermetically; the pass
geometry needs the ephemeris and is marked @pytest.mark.eph.
"""
from datetime import datetime, timezone

import pytest

from darkhours import satellites as sat

# Real Celestrak elements, epoch 2026-08-23. Fixed here rather than fetched so the
# regression below pins the exact geometry that was reported broken.
_TIANGONG = (
    "CSS (TIANHE)",
    "1 48274U 21035A   26235.49167945  .00015233  00000+0  19266-3 0  9999",
    "2 48274  41.4684 273.4428 0001586 254.2728 105.7935 15.59190448303679",
)
_HUBBLE = (
    "HST",
    "1 20580U 90037B   26235.60513988  .00005833  00000+0  17920-3 0  9992",
    "2 20580  28.4738 339.5545 0001998 160.5878 199.4794 15.31432165798913",
)

# White Sands, NM — the location in the original report.
_LAT, _LON = 32.78, -106.17
_T0 = datetime(2026, 8, 25,  1, 30, tzinfo=timezone.utc)
_T1 = datetime(2026, 8, 25, 12, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _clamp_to_window — hermetic
# ---------------------------------------------------------------------------

class _T:
    """Minimal stand-in for a Skyfield Time (only .tt is read)."""
    def __init__(self, tt): self.tt = tt


class TestClampToWindow:
    def test_inside_window_is_unchanged(self):
        lo, mid, hi = _T(10.0), _T(15.0), _T(20.0)
        assert sat._clamp_to_window(mid, lo, hi) is mid

    def test_before_window_clamps_to_start(self):
        lo, hi = _T(10.0), _T(20.0)
        assert sat._clamp_to_window(_T(5.0), lo, hi) is lo

    def test_after_window_clamps_to_end(self):
        lo, hi = _T(10.0), _T(20.0)
        assert sat._clamp_to_window(_T(25.0), lo, hi) is hi

    def test_boundaries_are_inclusive(self):
        lo, hi = _T(10.0), _T(20.0)
        assert sat._clamp_to_window(lo, lo, hi) is lo
        assert sat._clamp_to_window(hi, lo, hi) is hi


# ---------------------------------------------------------------------------
# Pass geometry — needs the ephemeris
# ---------------------------------------------------------------------------

@pytest.mark.eph
class TestPassGeometry:
    def _passes(self, tle):
        return sat.satellite_passes(tle, _LAT, _LON, _T0, _T1) or []

    @pytest.mark.parametrize("tle,label", [(_TIANGONG, "Tiangong"), (_HUBBLE, "Hubble")])
    def test_rise_peak_set_are_ordered(self, tle, label):
        """The invariant a user should never have had to notice.

        rise_time <= peak_time <= set_time. peak_time is documented as the maximum
        altitude *within the visible window*, and rise/set are shadow-corrected, so
        an unclamped geometric culmination could fall outside its own pass.
        """
        passes = self._passes(tle)
        assert passes, f"expected passes for {label} in this window"
        for p in passes:
            assert p.rise_time <= p.peak_time <= p.set_time, (
                f"{label} pass at {p.rise_time:%H:%M:%S}Z: "
                f"rise={p.rise_time:%H:%M:%S} peak={p.peak_time:%H:%M:%S} "
                f"set={p.set_time:%H:%M:%S}"
            )

    @pytest.mark.parametrize("tle,label", [(_TIANGONG, "Tiangong"), (_HUBBLE, "Hubble")])
    def test_peak_is_the_highest_visible_point(self, tle, label):
        """peak_alt must be >= the altitude at both ends of the visible window.

        The reported bug quoted a peak altitude the observer could never see: the
        satellite was still in Earth's shadow at the geometric culmination and only
        appeared afterwards, already descending. peak_alt was 39.2 deg while the
        satellite became visible at 23.1 deg.
        """
        for p in self._passes(tle):
            assert p.peak_alt_deg >= p.rise_alt_deg - 0.05, (
                f"{label} pass at {p.rise_time:%H:%M:%S}Z reports a peak "
                f"({p.peak_alt_deg} deg) below its own rise ({p.rise_alt_deg} deg)"
            )
            assert p.peak_alt_deg >= p.set_alt_deg - 0.05

    def test_shadow_exit_after_culmination_pins_peak_to_rise(self):
        """The exact reported case: Tiangong over White Sands, 04:26Z.

        The satellite leaves Earth's shadow past culmination, so the highest point
        anyone can observe is the instant it appears. Previously this reported
        rise 10:26:25Z with a peak of 10:24:54Z — a peak 91 seconds before the pass
        it belongs to. (04:26 MDT as the user saw it on the site.)
        """
        target = [p for p in self._passes(_TIANGONG)
                  if p.rise_time.hour == 10 and p.rise_time.minute == 26]
        assert len(target) == 1, "the reported pass is missing from this window"
        p = target[0]
        assert p.peak_time == p.rise_time
        assert p.peak_alt_deg == p.rise_alt_deg
        assert p.rise_alt_deg > sat._MIN_PASS_ALT, \
            "this pass must begin above the floor — that is what makes it a shadow exit"

    def test_duration_matches_the_visible_window(self):
        """Duration is set_time - rise_time and stays consistent with them.

        The reported pass showed 2m against a peak 91s before its rise, which made
        the duration look wrong too; the duration was right and the peak was not.
        """
        for p in self._passes(_TIANGONG):
            span_min = (p.set_time - p.rise_time).total_seconds() / 60.0
            assert p.duration_min == pytest.approx(span_min, abs=0.06)
