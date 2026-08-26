"""Grouping individual satellite passes into Starlink trains.

_group_passes_into_trains had no direct test before this file — the grouping was
inline in starlink_train_passes, which needs real orbits to reach. That is how the
cursor bug these tests pin survived: the loop advanced to wherever its forward scan
stopped, consuming passes it had skipped for rising on a different azimuth, so a
second train crossing the same window was silently dropped.
"""
from datetime import datetime, timedelta, timezone

from darkhours.satellites import (
    _group_passes_into_trains,
    _TRAIN_AZ_TOLERANCE,
    _TRAIN_GAP_S,
    _TRAIN_MIN_COUNT,
)

_T0 = datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)


def _pass(offset_s: float, az: float, *, peak: float = 40.0,
          launch: str = "2026-08-19") -> dict:
    return {
        "rise_time":   _T0 + timedelta(seconds=offset_s),
        "rise_az":     az,
        "peak_alt":    peak,
        "dur_min":     4.0,
        "moon_sep":    90.0,
        "sky_dark":    True,
        "launch_date": launch,
    }


def _sorted(passes):
    return sorted(passes, key=lambda p: p["rise_time"])


# ---------------------------------------------------------------------------
# the bug
# ---------------------------------------------------------------------------

def test_two_interleaved_trains_are_both_reported():
    """The regression. Two trains crossing the same window on opposite azimuths,
    their passes interleaved in time. Starlink flies several inclinations, so
    simultaneous trains genuinely rise on different bearings."""
    passes = _sorted(
        [_pass(20 * k, 45.0) for k in range(_TRAIN_MIN_COUNT + 2)] +
        [_pass(20 * k + 10, 225.0) for k in range(_TRAIN_MIN_COUNT + 2)]
    )

    trains = _group_passes_into_trains(passes)

    assert len(trains) == 2, "the second train used to be swallowed by the cursor"
    assert {round(t.lead_az_deg) for t in trains} == {45, 225}
    assert all(t.satellite_count == _TRAIN_MIN_COUNT + 2 for t in trains)


def test_a_group_below_the_minimum_does_not_consume_its_passes():
    """A sub-threshold group is not a train, so its passes must stay available to
    join one. They used to be consumed by the same cursor jump."""
    short = [_pass(20 * k, 10.0) for k in range(_TRAIN_MIN_COUNT - 1)]
    full  = [_pass(20 * k + 5, 200.0) for k in range(_TRAIN_MIN_COUNT)]

    trains = _group_passes_into_trains(_sorted(short + full))

    assert len(trains) == 1
    assert trains[0].satellite_count == _TRAIN_MIN_COUNT
    assert round(trains[0].lead_az_deg) == 200


def test_no_satellite_is_counted_in_two_trains():
    """Passes claimed by a reported train must not seed or join another."""
    passes = _sorted(
        [_pass(20 * k, 45.0) for k in range(_TRAIN_MIN_COUNT)] +
        [_pass(20 * k + 8, 50.0) for k in range(_TRAIN_MIN_COUNT)]
    )

    trains = _group_passes_into_trains(passes)

    assert sum(t.satellite_count for t in trains) <= len(passes)


# ---------------------------------------------------------------------------
# the existing contract, previously unpinned
# ---------------------------------------------------------------------------

def test_a_single_clean_train_is_reported():
    passes = [_pass(20 * k, 120.0) for k in range(_TRAIN_MIN_COUNT + 3)]
    trains = _group_passes_into_trains(passes)
    assert len(trains) == 1
    assert trains[0].satellite_count == _TRAIN_MIN_COUNT + 3
    assert trains[0].first_rise == passes[0]["rise_time"]
    assert trains[0].last_rise == passes[-1]["rise_time"]


def test_too_few_satellites_is_not_a_train():
    passes = [_pass(20 * k, 120.0) for k in range(_TRAIN_MIN_COUNT - 1)]
    assert _group_passes_into_trains(passes) == []


def test_a_time_gap_larger_than_the_limit_splits_the_group():
    early = [_pass(20 * k, 120.0) for k in range(_TRAIN_MIN_COUNT)]
    late  = [_pass(_TRAIN_GAP_S + 200 + 20 * k, 120.0) for k in range(_TRAIN_MIN_COUNT)]
    trains = _group_passes_into_trains(_sorted(early + late))
    assert len(trains) == 2


def test_azimuth_outside_tolerance_is_not_a_member():
    """One satellite rising well off the lead's bearing is a different object,
    not a straggler."""
    passes = _sorted(
        [_pass(20 * k, 100.0) for k in range(_TRAIN_MIN_COUNT)] +
        [_pass(15, 100.0 + _TRAIN_AZ_TOLERANCE + 15)]
    )
    trains = _group_passes_into_trains(passes)
    assert len(trains) == 1
    assert trains[0].satellite_count == _TRAIN_MIN_COUNT


def test_empty_input_returns_empty():
    assert _group_passes_into_trains([]) == []


def test_train_summary_fields_come_from_the_group():
    passes = [_pass(20 * k, 77.0, peak=30.0 + k) for k in range(_TRAIN_MIN_COUNT)]
    train = _group_passes_into_trains(passes)[0]
    assert train.peak_alt_deg == round(max(p["peak_alt"] for p in passes), 1)
    assert round(train.lead_az_deg) == 77
    assert train.launch_date == "2026-08-19"
