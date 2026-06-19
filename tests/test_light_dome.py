"""Hermetic tests for the directional light-dome analyzer.

All synthetic numpy arrays — no rasterio, no S3, no network, no ephemeris.
"""
import math

import numpy as np
import pytest

from PyNightSkyPredictor.light_dome import (
    DIRS_8,
    LightDomeAnalyzer,
    glow_toward,
    summarize_horizons,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _radiance_for_bortle(bortle: int) -> float:
    """VIIRS radiance mapping to the given Bortle class (mirrors test_light_dome_array)."""
    sqm_lower = {1: 22.0, 2: 21.7, 3: 21.3, 4: 20.8,
                 5: 20.0, 6: 19.1, 7: 18.0, 8: 17.0, 9: 0.0}
    lower = sqm_lower.get(bortle, 17.0)
    sqm = lower + 0.05 if lower > 0 else 0.05
    L = 10 ** ((21.7 - sqm) / 2.5) - 0.6
    return max(L, 0.001)


def _coarse_analyzer(resolution_deg: float = 0.05, radius_miles: float = 30.0):
    """Small kernel (fast) — 0.05° ≈ 3.45 mi/px → N=19 at 30-mile radius."""
    return LightDomeAnalyzer(radius_miles=radius_miles, resolution_deg=resolution_deg)


def _ground_distance_grid(analyzer: LightDomeAnalyzer, lat: float) -> np.ndarray:
    """Replicate the analyzer's per-pixel ground distance (miles) for building scenes."""
    deg_mi = analyzer.resolution_deg * 69.0
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    d_north = -analyzer._row_off * deg_mi
    d_east = analyzer._col_off * deg_mi * cos_lat
    return np.hypot(d_north, d_east)


# ---------------------------------------------------------------------------
# Direction / orientation
# ---------------------------------------------------------------------------

class TestDirectionMapping:

    @pytest.mark.parametrize("d_row, d_col, expected", [
        (-3, 0, "N"),   # rows decrease northward
        (0, 3, "E"),
        (3, 0, "S"),
        (0, -3, "W"),
        (-3, 3, "NE"),
        (3, 3, "SE"),
        (3, -3, "SW"),
        (-3, -3, "NW"),
    ])
    def test_point_source_lands_in_correct_sector(self, d_row, d_col, expected):
        a = _coarse_analyzer()
        c = a._half
        win = np.zeros(a.kernel_shape, dtype=np.float64)
        win[c + d_row, c + d_col] = 100.0  # single bright pixel

        scores = a.analyze_destination_horizons(0.0, 0.0, win)

        assert scores[expected] > 0.0
        # every other direction must be exactly zero
        for direction, score in scores.items():
            if direction != expected:
                assert score == 0.0, f"{direction} leaked from a {expected} source"

    def test_returns_all_eight_directions(self):
        a = _coarse_analyzer()
        win = np.ones(a.kernel_shape, dtype=np.float64)
        scores = a.analyze_destination_horizons(0.0, 0.0, win)
        assert set(scores) == set(DIRS_8)
        assert len(scores) == 8


# ---------------------------------------------------------------------------
# Walker d**-2.5 decay (in ground miles, not pixels)
# ---------------------------------------------------------------------------

class TestWalkerDecay:

    def test_doubling_distance_drops_score_by_2_pow_2_5(self):
        a = _coarse_analyzer()
        c = a._half

        near = np.zeros(a.kernel_shape, dtype=np.float64)
        near[c - 3, c] = 1.0          # 3 px due north
        far = np.zeros(a.kernel_shape, dtype=np.float64)
        far[c - 6, c] = 1.0           # 6 px due north (2x the distance)

        s_near = a.analyze_destination_horizons(0.0, 0.0, near)["N"]
        s_far = a.analyze_destination_horizons(0.0, 0.0, far)["N"]

        # rel=1e-5: weights are stored float32 (halves per-band memory at 150mi default).
        assert s_near / s_far == pytest.approx(2 ** 2.5, rel=1e-5)


# ---------------------------------------------------------------------------
# Resolution independence (the cell-area normalization)
# ---------------------------------------------------------------------------

class TestResolutionIndependence:

    def test_distributed_source_score_is_resolution_stable(self):
        """A filled annular source covering a fixed ground area should score the same
        at two resolutions — NOT scale ~4x as a raw pixel sum would when pixels shrink."""
        a_coarse = LightDomeAnalyzer(radius_miles=30.0, resolution_deg=0.02)
        a_fine = LightDomeAnalyzer(radius_miles=30.0, resolution_deg=0.01)

        def total_score(a: LightDomeAnalyzer) -> float:
            dist = _ground_distance_grid(a, lat=0.0)
            win = np.where((dist >= 10.0) & (dist <= 20.0), 5.0, 0.0)
            return sum(a.analyze_destination_horizons(0.0, 0.0, win).values())

        coarse, fine = total_score(a_coarse), total_score(a_fine)
        # Within discretization error of the annulus boundary — far from the 4x a
        # non-normalized raw sum would show.
        assert coarse == pytest.approx(fine, rel=0.10)


# ---------------------------------------------------------------------------
# Latitude (cos-lat) correction
# ---------------------------------------------------------------------------

class TestLatitudeCorrection:

    def test_east_and_north_equal_at_equator(self):
        a = _coarse_analyzer()
        c = a._half
        east = np.zeros(a.kernel_shape, dtype=np.float64); east[c, c + 4] = 1.0
        north = np.zeros(a.kernel_shape, dtype=np.float64); north[c - 4, c] = 1.0
        se = a.analyze_destination_horizons(0.0, 0.0, east)["E"]
        sn = a.analyze_destination_horizons(0.0, 0.0, north)["N"]
        assert se == pytest.approx(sn, rel=1e-9)

    def test_east_outweighs_north_at_high_latitude(self):
        """At 60°N a degree of longitude is half a degree of latitude on the ground, so an
        equal pixel-offset east source is physically *closer* → higher d**-2.5 weight."""
        a = _coarse_analyzer()
        c = a._half
        east = np.zeros(a.kernel_shape, dtype=np.float64); east[c, c + 4] = 1.0
        north = np.zeros(a.kernel_shape, dtype=np.float64); north[c - 4, c] = 1.0
        se = a.analyze_destination_horizons(60.0, 0.0, east)["E"]
        sn = a.analyze_destination_horizons(60.0, 0.0, north)["N"]
        assert se > sn


# ---------------------------------------------------------------------------
# Shape guard
# ---------------------------------------------------------------------------

class TestShapeGuard:

    def test_wrong_shape_raises(self):
        a = _coarse_analyzer()
        bad = np.zeros((a._n - 2, a._n), dtype=np.float64)
        with pytest.raises(ValueError, match="kernel shape"):
            a.analyze_destination_horizons(0.0, 0.0, bad)


# ---------------------------------------------------------------------------
# Normalization / summarize_horizons
# ---------------------------------------------------------------------------

class TestSummarizeHorizons:

    def test_uniform_dark_has_darkest_but_no_domes(self):
        scores = {d: 0.001 for d in DIRS_8}
        out = summarize_horizons(scores)
        assert out["darkest_direction"] in DIRS_8
        assert out["domes"] == []

    def test_uniform_bright_is_not_flagged_as_dome(self):
        # All directions equally (very) bright: significant, but none stands out.
        scores = {d: 10.0 for d in DIRS_8}
        out = summarize_horizons(scores)
        assert out["domes"] == []

    def test_single_major_dome_to_sw(self):
        # Explicit thresholds: this test pins the flagging *logic*, not the calibrated
        # default constants (those are validated separately in test_calibrated_defaults).
        scores = {d: 0.05 for d in DIRS_8}
        scores["SW"] = 5.8
        scores["NE"] = 0.02  # the darkest
        out = summarize_horizons(scores, minor_threshold=0.5, major_threshold=3.0)

        assert out["darkest_direction"] == "NE"
        assert len(out["domes"]) == 1
        dome = out["domes"][0]
        assert dome["direction"] == "SW"
        assert dome["severity"] == "major"
        assert dome["label"] == "Major light dome to the SW"

    def test_minor_vs_major_severity_split(self):
        scores = {d: 0.02 for d in DIRS_8}
        scores["E"] = 1.0    # significant + prominent, below major → minor
        scores["W"] = 4.0    # above major
        out = summarize_horizons(scores, minor_threshold=0.5, major_threshold=3.0)

        sev = {dome["direction"]: dome["severity"] for dome in out["domes"]}
        assert sev["E"] == "minor"
        assert sev["W"] == "major"
        # worst-first ordering
        assert out["domes"][0]["direction"] == "W"

    def test_calibrated_defaults_separate_rural_from_metro(self):
        """Guards the empirical calibration: a rural-ceiling worst score (~0.1) is not a
        dome, while a near-metro worst score (~3.5) is major — under the default constants."""
        # rural-like: worst direction at the observed rural/pristine ceiling
        rural = {d: 0.01 for d in DIRS_8}
        rural["E"] = 0.1
        assert summarize_horizons(rural)["domes"] == []

        # metro-like: a dominant city dome at the observed near-metro floor
        metro = {d: 0.05 for d in DIRS_8}
        metro["SW"] = 3.5
        out = summarize_horizons(metro)
        assert len(out["domes"]) == 1
        assert out["domes"][0]["severity"] == "major"

    def test_empty_scores_raises(self):
        with pytest.raises(ValueError):
            summarize_horizons({})


class TestSkyState:

    def test_dark_state_when_no_domes(self):
        out = summarize_horizons({d: 0.001 for d in DIRS_8})
        assert out["sky_state"] == "dark"
        assert out["domes"] == []
        assert "darkest_score" in out

    def test_domed_state_when_some_domes_but_dark_side_remains(self):
        scores = {d: 0.05 for d in DIRS_8}
        scores["SW"] = 5.8          # one big dome; darkest direction stays dark
        out = summarize_horizons(scores)
        assert out["sky_state"] == "domed"
        assert out["domes"]

    def test_urban_state_when_every_direction_is_major(self):
        # darkest >= major_threshold → no dark horizon anywhere (Roswell-like).
        scores = {d: 33.0 for d in DIRS_8}
        scores["E"] = 170.0
        out = summarize_horizons(scores)              # default major_threshold=3.0
        assert out["sky_state"] == "urban"
        # darkest_score is reported so the UI can caveat the "darkest" direction
        assert out["darkest_score"] == 33.0

    def test_bright_state_uniform_glow_no_standout(self):
        # Uniformly washed (no dome stands out) but every direction carries real glow,
        # darkest between minor and major → "bright", NOT "dark".
        scores = {d: 2.5 for d in DIRS_8}          # uniform; prominence gate suppresses domes
        out = summarize_horizons(scores, major_threshold=3.0)
        assert out["domes"] == []
        assert out["sky_state"] == "bright"

    def test_urban_boundary(self):
        # darkest just below MAJOR with uniform glow → bright; at/above → urban.
        below = summarize_horizons({d: 2.9 for d in DIRS_8}, major_threshold=3.0)
        assert below["sky_state"] == "bright"
        at = summarize_horizons({d: 3.0 for d in DIRS_8}, major_threshold=3.0)
        assert at["sky_state"] == "urban"


# ---------------------------------------------------------------------------
# Realistic radiance integration sanity
# ---------------------------------------------------------------------------

class TestRealisticScene:

    def test_bortle_city_to_one_side_dominates(self):
        """A Bortle-8 city patch to the south scores far above a Bortle-3 field elsewhere."""
        a = _coarse_analyzer()
        c = a._half
        win = np.full(a.kernel_shape, _radiance_for_bortle(3), dtype=np.float64)
        # bright city block a few pixels due south
        win[c + 2 : c + 5, c - 1 : c + 2] = _radiance_for_bortle(8)

        scores = a.analyze_destination_horizons(0.0, 0.0, win)
        assert scores["S"] == max(scores.values())
        assert scores["S"] > scores["N"]


# ---------------------------------------------------------------------------
# Soft (tent) binning
# ---------------------------------------------------------------------------

class TestSoftBinning:

    def test_off_axis_source_shares_between_two_neighbors_only(self):
        """A pixel between N and NE feeds both — and only those two — proportionally."""
        a = _coarse_analyzer()
        c = a._half
        win = np.zeros(a.kernel_shape, dtype=np.float64)
        win[c - 5, c + 2] = 100.0          # bearing ≈ atan2(2,5) ≈ 21.8° → between N and NE

        scores = a.analyze_destination_horizons(0.0, 0.0, win)
        assert scores["N"] > 0 and scores["NE"] > 0
        for d in ("E", "SE", "S", "SW", "W", "NW"):
            assert scores[d] == 0.0
        # closer to N (21.8° < 22.5°), so N gets the larger share
        assert scores["N"] > scores["NE"]

    def test_partition_of_unity_conserves_total(self):
        """Soft binning splits weight, never creates or destroys it: total is unchanged
        vs a single on-axis pixel of the same value."""
        a = _coarse_analyzer()
        c = a._half
        on_axis = np.zeros(a.kernel_shape, dtype=np.float64); on_axis[c - 4, c] = 100.0
        off_axis = np.zeros(a.kernel_shape, dtype=np.float64); off_axis[c - 4, c + 1] = 100.0
        # same radius? no — but total weight share must still sum to that pixel's full weight.
        s_off = a.analyze_destination_horizons(0.0, 0.0, off_axis)
        # reconstruct the single pixel's full (unsplit) contribution via the kernel
        w, dist, lo, hi, frac, area = a._kernel_for_latitude(0.0)
        full = 100.0 * float(w[c - 4, c + 1]) * area
        assert sum(s_off.values()) == pytest.approx(full, rel=1e-5)


# ---------------------------------------------------------------------------
# Mean distance & dome apparent height
# ---------------------------------------------------------------------------

class TestMeanDistanceAndDomeHeight:

    def test_near_source_is_closer_and_taller_than_far_source(self):
        a = _coarse_analyzer()  # 0.05° ≈ 3.45 mi/px
        c = a._half

        near = np.zeros(a.kernel_shape, dtype=np.float64); near[c + 3, c] = 100.0   # 3 px S
        far = np.zeros(a.kernel_shape, dtype=np.float64); far[c + 9, c] = 100.0      # 9 px S

        dn = a.analyze_horizons_detailed(0.0, 0.0, near)["S"]
        df = a.analyze_horizons_detailed(0.0, 0.0, far)["S"]

        assert dn["mean_distance_mi"] < df["mean_distance_mi"]
        assert dn["dome_height_deg"] > df["dome_height_deg"]   # closer dome rises higher
        # sanity: ~3 px * 3.45 mi/px ≈ 10.4 mi
        assert dn["mean_distance_mi"] == pytest.approx(3 * a.resolution_deg * 69.0, rel=1e-3)

    def test_no_glow_direction_reports_none_distance(self):
        a = _coarse_analyzer()
        c = a._half
        win = np.zeros(a.kernel_shape, dtype=np.float64); win[c + 3, c] = 100.0   # only S
        detailed = a.analyze_horizons_detailed(0.0, 0.0, win)
        assert detailed["N"]["mean_distance_mi"] is None
        assert detailed["N"]["dome_height_deg"] == 0.0


# ---------------------------------------------------------------------------
# glow_toward — target sampling
# ---------------------------------------------------------------------------

class TestGlowToward:

    def _southern_detail(self):
        a = _coarse_analyzer()
        c = a._half
        win = np.zeros(a.kernel_shape, dtype=np.float64); win[c + 4, c] = 1000.0   # bright S
        return a.analyze_horizons_detailed(0.0, 0.0, win)

    def test_horizon_equals_score_and_decays_with_altitude(self):
        d = self._southern_detail()
        theta = d["S"]["dome_height_deg"]
        at_horizon = glow_toward(d, 180.0, 0.0)
        at_theta = glow_toward(d, 180.0, theta)
        high = glow_toward(d, 180.0, 80.0)

        assert at_horizon == pytest.approx(d["S"]["score"], rel=1e-9)
        assert at_theta == pytest.approx(d["S"]["score"] / 2.0, rel=1e-6)  # half at dome height
        assert high < at_theta

    def test_dark_direction_has_negligible_glow(self):
        d = self._southern_detail()
        assert glow_toward(d, 0.0, 10.0) < glow_toward(d, 180.0, 10.0)

    def test_detailed_dict_flows_through_summarize(self):
        d = self._southern_detail()
        out = summarize_horizons(d, minor_threshold=0.0, major_threshold=1e9)
        s_dome = next(x for x in out["domes"] if x["direction"] == "S")
        assert "mean_distance_mi" in s_dome and "dome_height_deg" in s_dome
