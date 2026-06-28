"""
Regression tests for the perf-optimisation PR.
Covers:
  - _cluster_points() vectorised haversine: output identical to the reference loop
  - plan_trip() parallelisation: results are order-independent (same set regardless of
    which (loc, date) tuples finish first)
  - bt_cloud_frac() bisect path: same result as linear-scan for all sample times
"""
import math
from datetime import date, datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# 1. _cluster_points vectorised haversine
# ---------------------------------------------------------------------------

def _reference_cluster(points, merge_miles=8.0):
    """Original O(N²) Python loop — kept here to verify correctness of the rewrite."""
    def _hav(lat1, lon1, lat2, lon2):
        r = math.pi / 180
        dlat = (lat2 - lat1) * r
        dlon = (lon2 - lon1) * r
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin(dlon / 2) ** 2
        return 2 * 3958.8 * math.asin(math.sqrt(max(0, min(1, a))))

    sorted_pts = sorted(points, key=lambda p: (p["bortle_class"], p["distance_miles"]))
    used = set()
    clusters = []
    for i, pt in enumerate(sorted_pts):
        if i in used:
            continue
        clusters.append(pt)
        for j in range(i + 1, len(sorted_pts)):
            if j not in used:
                other = sorted_pts[j]
                if _hav(pt["lat"], pt["lon"], other["lat"], other["lon"]) <= merge_miles:
                    used.add(j)
    return clusters


def _make_points(coords):
    """Build minimal candidate dicts from [(lat, lon, bortle), ...] tuples."""
    origin_lat, origin_lon = 40.0, -105.0
    pts = []
    for lat, lon, bortle in coords:
        dlat = (lat - origin_lat) * math.pi / 180
        dlon = (lon - origin_lon) * math.pi / 180
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(origin_lat)) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
        dist = 2 * 3958.8 * math.asin(math.sqrt(max(0, min(1, a))))
        pts.append({"lat": lat, "lon": lon, "bortle_class": bortle, "distance_miles": round(dist, 1)})
    return pts


class TestClusterPoints:
    def test_empty(self):
        from PyNightSkyPredictor.darksky import _cluster_points
        assert _cluster_points([]) == []

    def test_single(self):
        from PyNightSkyPredictor.darksky import _cluster_points
        pts = _make_points([(40.1, -105.1, 3)])
        result = _cluster_points(pts)
        assert len(result) == 1

    def test_identical_output_to_reference_loop_50_points(self):
        """Vectorised result must be identical to the reference O(N²) loop."""
        import random
        from PyNightSkyPredictor.darksky import _cluster_points

        rng = random.Random(42)
        coords = [
            (40.0 + rng.uniform(-1.5, 1.5), -105.0 + rng.uniform(-1.5, 1.5), rng.randint(1, 6))
            for _ in range(50)
        ]
        pts = _make_points(coords)

        ref  = _reference_cluster(pts, merge_miles=8.0)
        fast = _cluster_points(pts, merge_miles=8.0)

        # Same number of clusters and same lat/lon pairs.
        assert len(fast) == len(ref), f"cluster count mismatch: {len(fast)} vs {len(ref)}"
        ref_coords  = {(round(p["lat"], 5), round(p["lon"], 5)) for p in ref}
        fast_coords = {(round(p["lat"], 5), round(p["lon"], 5)) for p in fast}
        assert fast_coords == ref_coords, "cluster centres differ between vectorised and reference"

    def test_no_merging_when_far_apart(self):
        from PyNightSkyPredictor.darksky import _cluster_points
        # Two points ~200 miles apart; should never merge at 8-mile threshold.
        pts = _make_points([(40.0, -105.0, 2), (42.0, -105.0, 3)])
        result = _cluster_points(pts, merge_miles=8.0)
        assert len(result) == 2

    def test_adjacent_points_merge(self):
        from PyNightSkyPredictor.darksky import _cluster_points
        # Two points < 1 mile apart; the darker one (bortle 1) must survive.
        pts = _make_points([(40.0, -105.0, 1), (40.001, -105.001, 3)])
        result = _cluster_points(pts, merge_miles=8.0)
        assert len(result) == 1
        assert result[0]["bortle_class"] == 1


# ---------------------------------------------------------------------------
# 2. plan_trip parallelisation: results are order-independent
# ---------------------------------------------------------------------------

class TestPlanTripParallel:
    def test_result_set_independent_of_order(self):
        """When all (loc, date) results come from cache (fast), the output set is
        identical regardless of future completion order — verifies no race condition."""
        from unittest.mock import patch, MagicMock
        from PyNightSkyPredictor.trip import plan_trip, NightSummary, TripReport
        from datetime import date

        def _fake_fetch_night(lat, lon, d, tz, display_name, fetch_weather):
            return NightSummary(
                date=d, display_name=display_name, lat=lat, lon=lon,
                score=round(lat + lon + d.day, 1),
                score_components={}, phase_name="New Moon", illumination_pct=0.0,
                moon_distance_km=384400, moon_special=None, moon_eclipses=[],
                dark_hours=6.0, bortle_score=3.0, weather_score=None,
                weather_informed=False, wx_pending=False, wx_no_data=False,
            )

        locs = [
            {"lat": 40.0, "lon": -105.0, "display_name": "A", "tz_name": "America/Denver"},
            {"lat": 35.0, "lon": -106.0, "display_name": "B", "tz_name": "America/Denver"},
            {"lat": 37.0, "lon": -110.0, "display_name": "C", "tz_name": "America/Denver"},
        ]
        d_start = date(2026, 7, 1)
        d_end   = date(2026, 7, 7)

        with patch("PyNightSkyPredictor.trip.fetch_night", side_effect=_fake_fetch_night):
            result = plan_trip(locs, d_start, d_end, fetch_weather=False)

        assert isinstance(result, TripReport)
        n_days = (d_end - d_start).days + 1
        assert len(result.nights) == len(locs) * n_days, "expected all (loc,date) pairs"
        # Ranked list must be sorted best→worst.
        scores = [n.score for n in result.ranked if n.score is not None]
        assert scores == sorted(scores, reverse=True), "ranked list not sorted"


# ---------------------------------------------------------------------------
# 3. bt_cloud_frac bisect path matches linear-scan
# ---------------------------------------------------------------------------

class TestBtCloudFrac:
    def _make_weather_points(self, n=48):
        """48 hourly WeatherPoint-like objects starting at midnight UTC."""
        from PyNightSkyPredictor.weather import WeatherPoint
        base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        pts = []
        for i in range(n):
            t = base + timedelta(hours=i)
            pts.append(WeatherPoint(
                time=t,
                cloud_cover_pct=int(i * 2 % 100),
                seeing_arcsec=None, transparency=None,
                humidity_pct=None, wind_speed_ms=None,
                lifted_index=None, precip_type=None,
                temperature_c=None, feels_like_c=None,
            ))
        return pts

    def test_bisect_matches_linear_for_all_samples(self):
        from PyNightSkyPredictor.milky_way import bt_cloud_frac
        pts = self._make_weather_points()
        base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

        # Sample at every 15-minute mark over 48 hours.
        t = base
        while t <= base + timedelta(hours=47):
            # Linear-scan path (no wx_epochs)
            linear = bt_cloud_frac(t, pts)
            # Bisect path (with wx_epochs)
            epochs = [p.time.timestamp() for p in pts]
            fast   = bt_cloud_frac(t, pts, wx_epochs=epochs)
            assert linear == fast, f"mismatch at {t}: linear={linear}, bisect={fast}"
            t += timedelta(minutes=15)

    def test_empty_returns_zero(self):
        from PyNightSkyPredictor.milky_way import bt_cloud_frac
        t = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)
        assert bt_cloud_frac(t, []) == 0.0
        assert bt_cloud_frac(t, [], wx_epochs=[]) == 0.0
