"""
Tests for aurora.py — dipole geomagnetic latitude, viewline/tier model,
look direction, and nightly_aurora/outlook_nightly_aurora/aurora_for_night assembly.
All hermetic: no network, no ephemeris, no real SWPC calls.
"""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from darkhours import aurora as a


# ---------------------------------------------------------------------------
# Reference sites (lat, lon) — dipole values verified against the formula
# ---------------------------------------------------------------------------

_FAIRBANKS   = (64.84, -147.72)   # maglat ≈ +65.7
_MINNEAPOLIS = (44.98,  -93.27)   # maglat ≈ +53.5
_DENVER      = (39.74, -104.99)   # maglat ≈ +47.3
_HOBART      = (-42.88, 147.33)   # maglat ≈ −49.6, look ≈ S

_UTC = timezone.utc


def _wx(t, cloud):
    """Minimal WeatherPoint stand-in: only .time and .cloud_cover_pct are read."""
    return SimpleNamespace(time=t, cloud_cover_pct=cloud)


def _rows(*bins):
    """[(iso_naive_utc, kp), ...] → SWPC kp forecast row dicts."""
    return [
        {"time_tag": t, "kp": kp, "observed": "predicted", "noaa_scale": None}
        for t, kp in bins
    ]


# ---------------------------------------------------------------------------
# geomagnetic_latitude — centered dipole
# ---------------------------------------------------------------------------

class TestGeomagneticLatitude:
    def test_reference_sites(self):
        assert a.geomagnetic_latitude(*_FAIRBANKS)   == pytest.approx(65.7,  abs=0.3)
        assert a.geomagnetic_latitude(*_MINNEAPOLIS) == pytest.approx(53.5,  abs=0.3)
        assert a.geomagnetic_latitude(*_DENVER)      == pytest.approx(47.3,  abs=0.3)
        assert a.geomagnetic_latitude(*_HOBART)      == pytest.approx(-49.6, abs=0.3)

    def test_dipole_pole_is_maglat_90(self):
        assert a.geomagnetic_latitude(a.GM_POLE_LAT, a.GM_POLE_LON) == pytest.approx(90.0)

    def test_dipole_equator_low(self):
        # Quito sits near the dipole equator
        assert abs(a.geomagnetic_latitude(-0.18, -78.47)) < 12.0


# ---------------------------------------------------------------------------
# Viewline + tiers
# ---------------------------------------------------------------------------

class TestViewlineAndTiers:
    def test_viewline_values(self):
        assert a.kp_to_viewline(0) == pytest.approx(66.5)
        assert a.kp_to_viewline(5) == pytest.approx(56.25)
        assert a.kp_to_viewline(9) == pytest.approx(48.05)

    def test_viewline_monotonic_decreasing(self):
        vals = [a.kp_to_viewline(k) for k in range(10)]
        assert all(x > y for x, y in zip(vals, vals[1:]))

    @pytest.mark.parametrize("margin,tier", [
        (-0.1, "overhead"),
        (0.0,  "overhead"),
        (2.9,  "naked_eye"),
        (3.0,  "naked_eye"),
        (8.9,  "photographic"),
        (9.0,  "photographic"),
        (9.1,  "none"),
    ])
    def test_tier_boundaries(self, margin, tier):
        # Place the site exactly `margin` degrees equatorward of the Kp-5 viewline.
        maglat = a.kp_to_viewline(5.0) - margin
        got_tier, got_margin = a.visibility_tier(maglat, 5.0)
        assert got_tier == tier
        assert got_margin == pytest.approx(margin)

    def test_g_scale(self):
        assert a.kp_to_g_scale(4.9)  is None
        assert a.kp_to_g_scale(5)    == "G1"
        assert a.kp_to_g_scale(6.33) == "G2"
        assert a.kp_to_g_scale(9)    == "G5"
        assert a.kp_to_g_scale(9.99) == "G5"


# ---------------------------------------------------------------------------
# Look direction
# ---------------------------------------------------------------------------

class TestLookDirection:
    def test_northern_hemisphere_looks_northish(self):
        for lat, lon in (_FAIRBANKS, _MINNEAPOLIS, _DENVER):
            b = a.look_bearing(lat, lon)
            assert b < 45.0 or b > 315.0, f"bearing {b} not northish"

    def test_hobart_looks_south(self):
        # Bearing to the SOUTH dipole pole — never a flipped north-pole bearing.
        b = a.look_bearing(*_HOBART)
        assert b == pytest.approx(180.0, abs=15.0)
        assert a._wind16(b) == "S"

    def test_wind16_buckets(self):
        assert a._wind16(0.0)    == "N"
        assert a._wind16(11.24)  == "N"
        assert a._wind16(11.3)   == "NNE"
        assert a._wind16(348.8)  == "N"
        assert a._wind16(348.7)  == "NNW"
        assert a._wind16(180.0)  == "S"


# ---------------------------------------------------------------------------
# nightly_aurora — window intersection, tiers, blockers
# ---------------------------------------------------------------------------

# Dark window 06:00–12:00 UTC (a long winter night, in bin-friendly units)
_DARK_START = datetime(2026, 1, 10, 6, 0, tzinfo=_UTC)
_DARK_END   = datetime(2026, 1, 10, 12, 0, tzinfo=_UTC)


class TestNightlyAurora:
    def test_no_darkness_returns_none(self):
        rows = _rows(("2026-01-10T06:00:00", 9.0))
        assert a.nightly_aurora(*_FAIRBANKS, None, None, kp_rows=rows) is None
        assert a.nightly_aurora(*_FAIRBANKS, _DARK_START, None, kp_rows=rows) is None

    def test_no_overlapping_bins_returns_none(self):
        rows = _rows(("2026-01-09T00:00:00", 9.0), ("2026-01-11T00:00:00", 9.0))
        assert a.nightly_aurora(*_FAIRBANKS, _DARK_START, _DARK_END, kp_rows=rows) is None

    def test_below_photographic_tier_returns_none(self):
        # Kp 1 viewline = 64.45; Denver maglat 47.3 → margin ~17° → none
        rows = _rows(("2026-01-10T06:00:00", 1.0))
        assert a.nightly_aurora(*_DENVER, _DARK_START, _DARK_END, kp_rows=rows) is None

    def test_location_awareness(self):
        # The same Kp 2 night: overhead in Fairbanks, nothing in Denver.
        rows = _rows(("2026-01-10T06:00:00", 2.0))
        fb = a.nightly_aurora(*_FAIRBANKS, _DARK_START, _DARK_END, kp_rows=rows)
        assert fb is not None and fb["tier"] == "overhead"
        assert a.nightly_aurora(*_DENVER, _DARK_START, _DARK_END, kp_rows=rows) is None

    def test_denver_kp5_photographic(self):
        rows = _rows(("2026-01-10T06:00:00", 5.0))
        r = a.nightly_aurora(*_DENVER, _DARK_START, _DARK_END, kp_rows=rows)
        assert r is not None
        assert r["tier"] == "photographic"
        assert r["noaa_scale"] == "G1"          # fallback from kp, row scale is None
        assert r["look_direction"] == "N"

    def test_max_bin_outside_dark_window_ignored(self):
        # Kp 8 bin ends exactly at dark start → excluded; the in-window Kp 2 drives.
        rows = _rows(("2026-01-10T03:00:00", 8.0), ("2026-01-10T06:00:00", 2.0))
        r = a.nightly_aurora(*_FAIRBANKS, _DARK_START, _DARK_END, kp_rows=rows)
        assert r is not None
        assert r["kp_max"] == pytest.approx(2.0)

    def test_peak_window_clipped_and_merged(self):
        # Two contiguous max bins 06–09 and 09–12 merge; a lower bin follows.
        rows = _rows(
            ("2026-01-10T06:00:00", 6.0),
            ("2026-01-10T09:00:00", 6.0),
            ("2026-01-10T12:00:00", 3.0),
        )
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END, kp_rows=rows)
        assert r["peak_start_utc"] == _DARK_START.isoformat()
        assert r["peak_end_utc"]   == _DARK_END.isoformat()

    def test_peak_window_clip_to_dark_bounds(self):
        # Single max bin 03:00–06:00 UTC overlapping a 05:00 dark start by 1 h.
        dark_start = datetime(2026, 1, 10, 5, 0, tzinfo=_UTC)
        rows = _rows(("2026-01-10T03:00:00", 6.0))
        r = a.nightly_aurora(*_MINNEAPOLIS, dark_start, _DARK_END, kp_rows=rows)
        assert r["peak_start_utc"] == dark_start.isoformat()
        assert r["peak_end_utc"]   == datetime(2026, 1, 10, 6, 0, tzinfo=_UTC).isoformat()

    def test_cloud_all_blocked(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        wx = [_wx(_DARK_START + timedelta(hours=h), 95) for h in range(3)]
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END,
                             kp_rows=rows, weather_points=wx)
        assert r["blockers"] == ["cloud"]
        assert r["viability"] == "blocked"

    def test_cloud_partial_degraded(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        wx = [_wx(_DARK_START, 95), _wx(_DARK_START + timedelta(hours=1), 10)]
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END,
                             kp_rows=rows, weather_points=wx)
        assert r["blockers"] == ["cloud"]
        assert r["viability"] == "degraded"

    def test_no_weather_fails_open(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END,
                             kp_rows=rows, weather_points=[])
        assert r["blockers"] == []
        assert r["viability"] == "ok"

    def test_light_dome_caution_degrades(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        # Strong dome toward N (the look direction), low elsewhere.
        scores  = {d: (0.9 if d == "N" else 0.0) for d in
                   ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]}
        heights = {d: 25.0 for d in scores}
        dome = {"scores": scores, "dome_heights": heights}
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END,
                             kp_rows=rows, light_dome=dome)
        assert r["light_dome_caution"] is True
        assert r["viability"] == "degraded"
        assert r["blockers"] == []              # caution degrades, never blocks

    def test_kp_source_and_stale_passthrough(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        rows[0]["observed"] = "estimated"
        r = a.nightly_aurora(*_MINNEAPOLIS, _DARK_START, _DARK_END,
                             kp_rows=rows, stale=True)
        assert r["kp_source"] == "estimated"
        assert r["stale"] is True

    def test_output_is_json_ready(self):
        rows = _rows(("2026-01-10T06:00:00", 6.0))
        r = a.nightly_aurora(*_FAIRBANKS, _DARK_START, _DARK_END, kp_rows=rows)
        assert json.loads(json.dumps(r)) == r


# ---------------------------------------------------------------------------
# outlook_nightly_aurora + aurora_for_night — 27-day path & unified dispatch
# ---------------------------------------------------------------------------

_NIGHT_DATE = date(2026, 1, 10)


class TestOutlookNightlyAurora:
    def test_uncovered_date_returns_none(self):
        assert a.outlook_nightly_aurora(*_MINNEAPOLIS, _NIGHT_DATE,
                                        _DARK_START, _DARK_END, outlook={}) is None

    def test_below_tier_returns_none(self):
        assert a.outlook_nightly_aurora(*_DENVER, _NIGHT_DATE,
                                        _DARK_START, _DARK_END,
                                        outlook={"2026-01-10": 2.0}) is None

    def test_no_darkness_returns_none(self):
        assert a.outlook_nightly_aurora(*_MINNEAPOLIS, _NIGHT_DATE, None, None,
                                        outlook={"2026-01-10": 6.0}) is None

    def test_qualifying_date_full_shape(self):
        r = a.outlook_nightly_aurora(*_MINNEAPOLIS, _NIGHT_DATE,
                                     _DARK_START, _DARK_END,
                                     outlook={"2026-01-10": 6.0})
        assert r["kp_source"] == "outlook"
        assert r["tier"] == "naked_eye"
        assert r["noaa_scale"] == "G2"
        # No intra-night resolution in the 27-day product → no peak window.
        assert r["peak_start_utc"] is None and r["peak_end_utc"] is None
        assert json.loads(json.dumps(r)) == r

    def test_cloud_check_runs_over_dark_window(self):
        wx = [_wx(_DARK_START + timedelta(hours=h), 95) for h in range(6)]
        r = a.outlook_nightly_aurora(*_MINNEAPOLIS, _NIGHT_DATE,
                                     _DARK_START, _DARK_END,
                                     outlook={"2026-01-10": 6.0},
                                     weather_points=wx)
        assert r["blockers"] == ["cloud"] and r["viability"] == "blocked"


class TestAuroraForNight:
    def test_bins_cover_night_use_kp_product(self):
        # Bins span the whole dark window → kp3day path, peak window present.
        rows = _rows(("2026-01-10T03:00:00", 6.0), ("2026-01-10T06:00:00", 6.0),
                     ("2026-01-10T09:00:00", 6.0), ("2026-01-10T12:00:00", 6.0))
        r = a.aurora_for_night(*_MINNEAPOLIS, _NIGHT_DATE, _DARK_START, _DARK_END,
                               kp_rows=rows, outlook={"2026-01-10": 9.0})
        assert r["kp_source"] == "predicted"
        assert r["kp_max"] == pytest.approx(6.0)   # NOT the outlook's 9.0
        assert r["peak_start_utc"] is not None

    def test_bins_below_tier_is_authoritative_no_fallback(self):
        # Full-coverage bins say quiet → None, even if the outlook says storm.
        rows = _rows(("2026-01-10T03:00:00", 1.0), ("2026-01-10T06:00:00", 1.0),
                     ("2026-01-10T09:00:00", 1.0), ("2026-01-10T12:00:00", 1.0))
        assert a.aurora_for_night(*_DENVER, _NIGHT_DATE, _DARK_START, _DARK_END,
                                  kp_rows=rows, outlook={"2026-01-10": 9.0}) is None

    def test_night_beyond_bins_falls_back_to_outlook(self):
        # The calendar-icon consistency case: bins end before the night starts.
        rows = _rows(("2026-01-08T00:00:00", 5.0))
        r = a.aurora_for_night(*_MINNEAPOLIS, _NIGHT_DATE, _DARK_START, _DARK_END,
                               kp_rows=rows, outlook={"2026-01-10": 6.0})
        assert r is not None and r["kp_source"] == "outlook"

    def test_partial_bin_coverage_prefers_outlook(self):
        # Bins cover only the first hour of the night → truncated sample; the
        # full-night outlook answer wins.
        rows = _rows(("2026-01-10T04:00:00", 2.0))  # ends 07:00, dark ends 12:00
        r = a.aurora_for_night(*_MINNEAPOLIS, _NIGHT_DATE, _DARK_START, _DARK_END,
                               kp_rows=rows, outlook={"2026-01-10": 6.0})
        assert r is not None and r["kp_source"] == "outlook"
        assert r["kp_max"] == pytest.approx(6.0)

    def test_no_data_at_all_returns_none(self):
        assert a.aurora_for_night(*_MINNEAPOLIS, _NIGHT_DATE, _DARK_START, _DARK_END,
                                  kp_rows=[], outlook={}) is None


# ---------------------------------------------------------------------------
# Trip NightSummary integration
# ---------------------------------------------------------------------------

class TestTripAurora:
    def test_cache_key_bumped_to_v6(self):
        from darkhours import trip
        assert "night_v6" in trip._cache_key(45.0, -93.0, date(2026, 8, 1), True)

    def test_aurora_survives_dict_round_trip(self):
        from darkhours import trip
        s = trip.NightSummary(
            date=date(2026, 8, 1), display_name="x", lat=45.0, lon=-93.0,
            score=7.0, score_components={}, phase_name="New Moon",
            illumination_pct=1.0, moon_distance_km=384_400, moon_special=None,
            moon_eclipses=[], dark_hours=6.0, bortle_score=8.0,
            weather_score=None, weather_informed=False,
            wx_pending=False, wx_no_data=False,
            aurora={"kp_max": 6.0, "tier": "naked_eye",
                    "noaa_scale": "G2", "source": "27day"},
        )
        restored = trip._from_dict(json.loads(json.dumps(trip._to_dict(s))))
        assert restored.aurora == s.aurora

    def test_missing_aurora_key_defaults_none(self):
        # Old cached night_v4-era dicts (or v5 dicts without the key) → None
        from darkhours import trip
        d = trip._to_dict(trip.NightSummary(
            date=date(2026, 8, 1), display_name="x", lat=45.0, lon=-93.0,
            score=7.0, score_components={}, phase_name="New Moon",
            illumination_pct=1.0, moon_distance_km=384_400, moon_special=None,
            moon_eclipses=[], dark_hours=6.0, bortle_score=8.0,
            weather_score=None, weather_informed=False,
            wx_pending=False, wx_no_data=False,
        ))
        d.pop("aurora")
        assert trip._from_dict(d).aurora is None


# ---------------------------------------------------------------------------
# Moonlight factor (tier-scaled, degrades only)
# ---------------------------------------------------------------------------

class TestMoonlightFactor:
    _WIN = (datetime(2026, 3, 1, 22, 0, tzinfo=_UTC),
            datetime(2026, 3, 2, 2, 0, tzinfo=_UTC))

    def _track(self, alt):
        """Constant-altitude moon track covering the window."""
        t0, t1 = self._WIN
        out, t = [], t0
        while t <= t1:
            out.append((t, alt))
            t += timedelta(minutes=30)
        return out

    def _cv(self, tier, illum, moon_alt=45.0, moon_alts=None):
        return a._condition_vector(
            *self._WIN, None, None, 0.0,
            tier=tier, moon_illum_pct=illum,
            moon_alts=self._track(moon_alt) if moon_alts is None else moon_alts,
        )

    def test_full_moon_degrades_photographic(self):
        blockers, viability, _, moon_caution = self._cv("photographic", 100.0)
        assert "moonlight" in blockers
        assert viability == "degraded"
        assert moon_caution is True

    def test_gibbous_degrades_photographic_but_not_naked_eye(self):
        """Δ ≈ 1.34 at 40% illumination: over the photographic threshold (0.50),
        under the naked-eye one (1.50)."""
        b_photo, v_photo, _, c_photo = self._cv("photographic", 40.0)
        b_naked, v_naked, _, c_naked = self._cv("naked_eye", 40.0)
        assert c_photo and "moonlight" in b_photo and v_photo == "degraded"
        assert not c_naked and "moonlight" not in b_naked and v_naked == "ok"

    def test_full_moon_degrades_naked_eye(self):
        blockers, viability, _, moon_caution = self._cv("naked_eye", 100.0)
        assert moon_caution and "moonlight" in blockers and viability == "degraded"

    def test_overhead_tier_punches_through_any_moon(self):
        blockers, viability, _, moon_caution = self._cv("overhead", 100.0)
        assert not moon_caution and "moonlight" not in blockers and viability == "ok"

    def test_crescent_degrades_nothing(self):
        blockers, viability, _, moon_caution = self._cv("photographic", 15.0)
        assert not moon_caution and blockers == [] and viability == "ok"

    def test_moon_below_horizon_no_caution(self):
        blockers, viability, _, moon_caution = self._cv("photographic", 100.0, moon_alt=-10.0)
        assert not moon_caution and blockers == [] and viability == "ok"

    def test_fail_open_without_track(self):
        """moon_alts=None (no ephemeris) → fail-open like missing weather."""
        blockers, viability, _, moon_caution = a._condition_vector(
            *self._WIN, None, None, 0.0,
            tier="photographic", moon_illum_pct=100.0, moon_alts=None)
        assert not moon_caution and blockers == [] and viability == "ok"

    def test_moonlight_caution_in_result_dict(self):
        rows = _rows(("2026-03-01 21:00:00", 7.0), ("2026-03-02 00:00:00", 7.0),
                     ("2026-03-02 03:00:00", 7.0))
        out = a.nightly_aurora(_MINNEAPOLIS[0], _MINNEAPOLIS[1],
                               *self._WIN, kp_rows=rows,
                               moon_illum_pct=100.0, moon_alts=self._track(45.0))
        assert out is not None
        assert "moonlight_caution" in out
        assert isinstance(out["moonlight_caution"], bool)

    def test_result_dict_defaults_false_without_moon_data(self):
        rows = _rows(("2026-03-01 21:00:00", 7.0), ("2026-03-02 00:00:00", 7.0),
                     ("2026-03-02 03:00:00", 7.0))
        out = a.nightly_aurora(_MINNEAPOLIS[0], _MINNEAPOLIS[1],
                               *self._WIN, kp_rows=rows)
        assert out is not None
        assert out["moonlight_caution"] is False
