"""Unit tests for the AWS Location Service geocoding adapters.

All boto3 calls are mocked with unittest.mock — no real AWS needed.
Tests cover both the forward geocoder (location.py) and the reverse
geocoder (darksky.py), as well as the backend-dispatch logic.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_location_client():
    """darksky caches one process-wide boto3 'location' client; drop it between tests
    so each test's patched boto3.client is the one actually used."""
    import PyNightSkyPredictor.darksky as _darksky
    _darksky._reset_location_client()
    yield
    _darksky._reset_location_client()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _aws_env(monkeypatch):
    """Set the env vars that put both modules in aws-backend mode."""
    monkeypatch.setenv("PYNIGHTSKY_BACKEND", "aws")
    monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-place-index")
    monkeypatch.setenv("PYNIGHTSKY_CACHE_TABLE", "test-cache")
    monkeypatch.setenv("PYNIGHTSKY_RASTER_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


def _forward_response(lon, lat, label="Denver, CO, USA"):
    """Minimal boto3 search_place_index_for_text response."""
    return {
        "Results": [
            {"Place": {"Geometry": {"Point": [lon, lat]}, "Label": label}}
        ]
    }


def _reverse_response(label, municipality="Denver", region="Colorado"):
    """Minimal boto3 search_place_index_for_position response."""
    return {
        "Results": [
            {
                "Place": {
                    "Label": label,
                    "Municipality": municipality,
                    "Region": region,
                }
            }
        ]
    }


# ── Forward geocoding (_geocode_via_aws) ──────────────────────────────────────

class TestGeocodeViaAws:
    def test_returns_entry_on_success(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
        mock_client = MagicMock()
        mock_client.search_place_index_for_text.return_value = _forward_response(-104.9903, 39.7392)

        with patch("boto3.client", return_value=mock_client):
            from PyNightSkyPredictor.location import _geocode_via_aws
            result = _geocode_via_aws("Denver, CO", "Denver, CO")

        assert result["lat"] == pytest.approx(39.7392)
        assert result["lon"] == pytest.approx(-104.9903)
        assert result["display_name"] == "Denver, CO, USA"
        assert "tz_name" in result

        mock_client.search_place_index_for_text.assert_called_once_with(
            IndexName="test-index",
            Text="Denver, CO",
            MaxResults=1,
        )

    def test_returns_none_on_empty_results(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
        mock_client = MagicMock()
        mock_client.search_place_index_for_text.return_value = {"Results": []}

        with patch("boto3.client", return_value=mock_client):
            from PyNightSkyPredictor.location import _geocode_via_aws
            result = _geocode_via_aws("Nonexistent Place XYZ", "Nonexistent Place XYZ")

        assert result is None

    def test_raises_on_boto3_error(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
        mock_client = MagicMock()
        mock_client.search_place_index_for_text.side_effect = RuntimeError("network error")

        with patch("boto3.client", return_value=mock_client):
            from PyNightSkyPredictor.location import _geocode_via_aws
            with pytest.raises(RuntimeError, match="Geocoding error"):
                _geocode_via_aws("Denver", "Denver")

    def test_uses_place_index_env_var(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "my-custom-index")
        mock_client = MagicMock()
        mock_client.search_place_index_for_text.return_value = _forward_response(-104.9903, 39.7392)

        with patch("boto3.client", return_value=mock_client):
            from PyNightSkyPredictor.location import _geocode_via_aws
            _geocode_via_aws("Denver", "Denver")

        call_kwargs = mock_client.search_place_index_for_text.call_args[1]
        assert call_kwargs["IndexName"] == "my-custom-index"


# ── resolve() dispatch ────────────────────────────────────────────────────────

class TestResolveDispatch:
    def test_aws_backend_calls_aws_geocoder(self, monkeypatch):
        _aws_env(monkeypatch)
        import PyNightSkyPredictor.location as loc_mod
        monkeypatch.setattr(loc_mod, "_mem_geocode", {})

        # Isolate: patch both geocoders + the geocode store so no real I/O
        mock_aws_geo = MagicMock(return_value={
            "lat": 39.7392, "lon": -104.9903,
            "display_name": "Denver, CO, USA", "tz_name": "America/Denver",
        })
        mock_nom_geo = MagicMock()
        mock_store = MagicMock()
        mock_store.load.return_value = {}

        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

        with patch("PyNightSkyPredictor.location._geocode_via_aws", mock_aws_geo), \
             patch("PyNightSkyPredictor.location._geocode_via_nominatim", mock_nom_geo), \
             patch("PyNightSkyPredictor.location._load", return_value={}), \
             patch("PyNightSkyPredictor.location._save"):
            from PyNightSkyPredictor.location import resolve
            lat, lon, disp, tz = resolve("Denver, CO")

        mock_aws_geo.assert_called_once()
        mock_nom_geo.assert_not_called()
        assert lat == pytest.approx(39.7392)

    def test_local_backend_calls_nominatim(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_BACKEND", "local")
        import PyNightSkyPredictor.location as loc_mod
        monkeypatch.setattr(loc_mod, "_mem_geocode", {})

        mock_nom_geo = MagicMock(return_value={
            "lat": 39.7392, "lon": -104.9903,
            "display_name": "Denver, CO, USA", "tz_name": "America/Denver",
        })
        mock_aws_geo = MagicMock()

        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

        with patch("PyNightSkyPredictor.location._geocode_via_nominatim", mock_nom_geo), \
             patch("PyNightSkyPredictor.location._geocode_via_aws", mock_aws_geo), \
             patch("PyNightSkyPredictor.location._load", return_value={}), \
             patch("PyNightSkyPredictor.location._save"):
            from PyNightSkyPredictor.location import resolve
            resolve("Denver, CO")

        mock_nom_geo.assert_called_once()
        mock_aws_geo.assert_not_called()

    def test_cache_hit_skips_geocoding(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_BACKEND", "local")
        import PyNightSkyPredictor.location as loc_mod
        monkeypatch.setattr(loc_mod, "_mem_geocode", {})
        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

        cached = {
            "denver, co": {
                "lat": 39.7392, "lon": -104.9903,
                "display_name": "Denver", "tz_name": "America/Denver",
            }
        }
        mock_aws_geo = MagicMock()
        mock_nom_geo = MagicMock()

        with patch("PyNightSkyPredictor.location._geocode_via_nominatim", mock_nom_geo), \
             patch("PyNightSkyPredictor.location._geocode_via_aws", mock_aws_geo), \
             patch("PyNightSkyPredictor.location._load", return_value=cached), \
             patch("PyNightSkyPredictor.location._save"):
            from PyNightSkyPredictor.location import resolve
            lat, lon, disp, tz = resolve("Denver, CO")

        mock_nom_geo.assert_not_called()
        mock_aws_geo.assert_not_called()
        assert lat == pytest.approx(39.7392)


# ── Reverse geocoding (_aws_location_settlement) ──────────────────────────────

class TestAwsLocationSettlement:
    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch):
        """Isolate the module-level cache used by _aws_location_settlement."""
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
        monkeypatch.setenv("PYNIGHTSKY_BACKEND", "local")   # cache module needs a backend
        monkeypatch.setenv("PYNIGHTSKY_CACHE_TABLE", "x")
        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

    def _call(self, boto_response):
        mock_client = MagicMock()
        mock_client.search_place_index_for_position.return_value = boto_response
        with patch("boto3.client", return_value=mock_client), \
             patch("PyNightSkyPredictor.darksky.cache") as mock_cache:
            mock_cache.get.return_value = None  # force cache miss
            from PyNightSkyPredictor.darksky import _aws_location_settlement
            return _aws_location_settlement(39.7392, -104.9903), mock_client, mock_cache

    def test_us_city_returns_city_state(self):
        resp = _reverse_response("Denver, CO, USA", municipality="Denver", region="Colorado")
        result, client, mock_cache = self._call(resp)
        assert result == "Denver, CO"
        mock_cache.set.assert_called_once()

    def test_passes_lon_lat_order_to_aws(self):
        resp = _reverse_response("Denver, CO, USA", municipality="Denver")
        _, client, _ = self._call(resp)
        call_kwargs = client.search_place_index_for_position.call_args[1]
        assert call_kwargs["Position"] == pytest.approx([-104.9903, 39.7392])

    def test_empty_results_returns_over_water(self):
        resp = {"Results": []}
        result, _, mock_cache = self._call(resp)
        from PyNightSkyPredictor.darksky import _OVER_WATER
        assert result == _OVER_WATER

    def test_cache_hit_skips_boto3(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_PLACE_INDEX", "test-index")
        mock_client = MagicMock()
        with patch("boto3.client", return_value=mock_client), \
             patch("PyNightSkyPredictor.darksky.cache") as mock_cache:
            mock_cache.get.return_value = "Denver, CO"
            from PyNightSkyPredictor.darksky import _aws_location_settlement
            result = _aws_location_settlement(39.7392, -104.9903)
        assert result == "Denver, CO"
        mock_client.search_place_index_for_position.assert_not_called()

    def test_boto3_error_returns_none(self):
        mock_client = MagicMock()
        mock_client.search_place_index_for_position.side_effect = RuntimeError("timeout")
        with patch("boto3.client", return_value=mock_client), \
             patch("PyNightSkyPredictor.darksky.cache") as mock_cache:
            mock_cache.get.return_value = None
            from PyNightSkyPredictor.darksky import _aws_location_settlement
            result = _aws_location_settlement(39.7392, -104.9903)
        assert result is None


# ── _settlement() dispatcher ──────────────────────────────────────────────────

class TestSettlementDispatch:
    def test_aws_backend_calls_aws(self, monkeypatch):
        _aws_env(monkeypatch)
        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

        mock_aws = MagicMock(return_value="Denver, CO")
        mock_nom = MagicMock()

        with patch("PyNightSkyPredictor.darksky._aws_location_settlement", mock_aws), \
             patch("PyNightSkyPredictor.darksky._nominatim_settlement", mock_nom):
            from PyNightSkyPredictor.darksky import _settlement
            result = _settlement(39.7392, -104.9903)

        mock_aws.assert_called_once_with(39.7392, -104.9903)
        mock_nom.assert_not_called()
        assert result == "Denver, CO"

    def test_local_backend_calls_nominatim(self, monkeypatch):
        monkeypatch.setenv("PYNIGHTSKY_BACKEND", "local")
        import PyNightSkyPredictor.ports as ports_mod
        ports_mod.reset_backend()

        mock_aws = MagicMock()
        mock_nom = MagicMock(return_value="Denver, CO")

        with patch("PyNightSkyPredictor.darksky._aws_location_settlement", mock_aws), \
             patch("PyNightSkyPredictor.darksky._nominatim_settlement", mock_nom):
            from PyNightSkyPredictor.darksky import _settlement
            result = _settlement(39.7392, -104.9903)

        mock_nom.assert_called_once_with(39.7392, -104.9903)
        mock_aws.assert_not_called()
        assert result == "Denver, CO"


# ── Drive-time routing + per-leg caching (_aws_drive_times) ───────────────────

class TestAwsDriveTimes:
    """Point-to-point CalculateRoutes (NOT the batched CalculateRouteMatrix — that
    endpoint's response has no Notices/Legs, so it can't detect a ferry-bridged or
    unpaved route; see _aws_route_one) is the dominant warm find_nearby phase and was
    previously uncached. These cover the per-leg cache: hits skip the API, misses call
    it once per missing leg (parallelized, bounded fan-out), and failures stay un-cached."""

    @pytest.fixture
    def _mem_cache(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr("PyNightSkyPredictor.darksky.cache.get", lambda k: store.get(k))
        monkeypatch.setattr("PyNightSkyPredictor.darksky.cache.set",
                            lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
        return store

    @staticmethod
    def _route_resp(minutes, meters, dirt_road=False, arrival_lat=None, arrival_lon=None):
        """A GeoRoutes CalculateRoutes response for a single (successful) vehicle route.
        arrival_lat/lon simulate GeoRoutes snapping the destination to the nearest road
        (VehicleLegDetails.Arrival.Place.Position); omitted means "no gap to check"."""
        notices = [{"Code": "ViolatedAvoidDirtRoad"}] if dirt_road else []
        vehicle_details = {"Notices": notices}
        if arrival_lat is not None:
            vehicle_details["Arrival"] = {"Place": {"Position": [arrival_lon, arrival_lat]}}
        return {"Routes": [{
            "Legs": [{"Type": "Vehicle", "VehicleLegDetails": vehicle_details}],
            "Summary": {"Duration": minutes * 60, "Distance": meters},
        }]}

    @staticmethod
    def _by_destination(mapping):
        """side_effect dispatcher: routes calls to the fixture keyed by [lon, lat]."""
        def _dispatch(**kwargs):
            return mapping[tuple(kwargs["Destination"])]
        return _dispatch

    def test_miss_calls_routes_per_leg_and_caches(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_routes.side_effect = self._by_destination({
            (-111.46, 35.41): self._route_resp(56, 56000),
            (-111.07, 35.23): self._route_resp(88, 88000),
        })
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True},
                    {"lat": 35.23, "lon": -111.07, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert [c["drive_minutes"] for c in clusters] == [56, 88]
        assert [c["drive_miles"] for c in clusters] == [round(56000 / 1609.34),
                                                        round(88000 / 1609.34)]
        assert client.calculate_routes.call_count == 2
        # both legs now cached as {minutes, road-miles, warnings, tail_miles}
        assert _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] == \
            {"m": 56, "mi": round(56000 / 1609.34), "w": [], "t": None}
        # best-effort avoidance is requested on every call
        for call in client.calculate_routes.call_args_list:
            assert call.kwargs["Avoid"] == {"Ferries": True, "DirtRoads": True}

    def test_full_cache_hit_skips_api(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] = 56
        _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.23, -111.07)] = ds._DRIVE_NO_ROUTE
        client = MagicMock()
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True},
                    {"lat": 35.23, "lon": -111.07, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert [c["drive_minutes"] for c in clusters] == [56, None]  # sentinel → None
        client.calculate_routes.assert_not_called()

    def test_partial_miss_queries_only_uncached(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] = 56
        client = MagicMock()
        client.calculate_routes.return_value = self._route_resp(88, 88000)  # only the miss
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True},
                    {"lat": 35.23, "lon": -111.07, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert [c["drive_minutes"] for c in clusters] == [56, 88]
        # only the single uncached destination was requested
        client.calculate_routes.assert_called_once()
        _, kwargs = client.calculate_routes.call_args
        assert kwargs["Destination"] == [-111.07, 35.23]

    def test_api_failure_leaves_none_and_uncached(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_routes.side_effect = RuntimeError("throttled")
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] is None
        assert _mem_cache == {}  # transient failure must not poison the cache

    def test_no_route_at_all_is_cached_as_no_route(self, monkeypatch, _mem_cache):
        """An empty Routes list (genuinely no path) collapses to the same sentinel as a
        ferry-only route — both are "not driveable", just for different reasons."""
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_routes.return_value = {"Routes": []}
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] is None
        assert _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] == ds._DRIVE_NO_ROUTE

    def test_ferry_leg_treated_as_unroutable(self, monkeypatch, _mem_cache):
        """A best-effort Avoid=Ferries route that still has to cross a Ro-Ro ferry (e.g.
        Juneau→Hoonah) comes back with a real Duration and a structural Legs[].Type ==
        "Ferry" entry — confirmed live against AWS this leg also carries a real
        "ViolatedAvoidFerry" notice, but nested under Legs[].FerryLegDetails.Notices[],
        not the VehicleLegDetails shape checked elsewhere, so leg Type is the simpler
        signal to key off. Either way it's not an actual drivable route, so it must
        collapse to the same "no route" state as an outright routing failure, not
        silently report the deceptive ETA."""
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_routes.return_value = {"Routes": [{
            "Legs": [{"Type": "Vehicle"}, {"Type": "Ferry"}, {"Type": "Vehicle"}],
            "Summary": {"Duration": 228 * 60, "Distance": 90 * 1000},
        }]}
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 58.1, "lon": -135.3, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] is None
        assert clusters[0]["drive_miles"] is None
        assert clusters[0]["warnings"] == []
        assert clusters[0]["tail_miles"] is None
        assert _mem_cache[ds._drive_cache_key(35.2, -111.6, 58.1, -135.3)] == ds._DRIVE_NO_ROUTE

    def test_dirt_road_violation_surfaces_as_warning(self, monkeypatch, _mem_cache):
        """A non-fatal avoidance violation (unpaved road), reported via
        VehicleLegDetails.Notices on the leg, still yields a real ETA, but the caller
        gets a warning string to show the user rather than a silently-dropped flag."""
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_routes.return_value = self._route_resp(45, 20000, dirt_road=True)
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] == 45
        assert clusters[0]["warnings"] == ["Dirt roads"]
        assert _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] == \
            {"m": 45, "mi": round(20000 / 1609.34), "w": ["Dirt roads"], "t": None}

    def test_unreachable_destination_surfaces_tail_gap(self, monkeypatch, _mem_cache):
        """GeoRoutes can't route to a destination with no nearby road (e.g. an island
        lighthouse reachable only by boat — confirmed live against Sand Island Lighthouse,
        WI), so it silently snaps the arrival to the nearest road and reports a normal
        Duration/Distance for THAT point instead. The drivable portion is still real
        (drive_minutes/drive_miles stay populated), but the gap between the requested
        destination and the actual arrival point (>1km) must surface as tail_miles rather
        than being silently dropped."""
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        # destination is the island; arrival snaps ~0.02 deg (~1.38 mi) south on the mainland
        client.calculate_routes.return_value = self._route_resp(
            180, 259000, arrival_lat=35.98, arrival_lon=-111.6)
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 36.0, "lon": -111.6, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] == 180   # the drivable portion is still real
        assert clusters[0]["tail_miles"] == pytest.approx(1.38, abs=0.05)
        cached = _mem_cache[ds._drive_cache_key(35.2, -111.6, 36.0, -111.6)]
        assert cached["t"] == pytest.approx(1.38, abs=0.05)

    def test_normal_road_snap_does_not_trigger_tail_gap(self, monkeypatch, _mem_cache):
        """A routine few-meters snap to the nearest driveway/road (every real destination
        gets one) must NOT be mistaken for an unreachable-destination gap."""
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        # ~0.0005 deg (~55m) south — well under the 1km threshold
        client.calculate_routes.return_value = self._route_resp(
            45, 20000, arrival_lat=35.9995, arrival_lon=-111.46)
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 36.0, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["tail_miles"] is None

    def test_cached_warnings_round_trip(self, monkeypatch, _mem_cache):
        """A dict cache entry with a 'w' warnings list and no 't' key (older cache
        schema) is unpacked back onto the cluster without error."""
        import PyNightSkyPredictor.darksky as ds
        _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] = \
            {"m": 45, "mi": 12, "w": ["Dirt roads"]}
        client = MagicMock()
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["warnings"] == ["Dirt roads"]
        assert clusters[0]["tail_miles"] is None
        client.calculate_routes.assert_not_called()
