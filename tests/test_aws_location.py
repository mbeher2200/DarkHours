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


# ── Drive-time route matrix + per-leg caching (_aws_drive_times) ──────────────

class TestAwsDriveTimes:
    """The route-matrix call is the dominant warm find_nearby phase (~2 s) and was
    previously uncached. These cover the per-leg cache: hits skip the API, misses hit
    it once for just the missing legs, and failures stay un-cached."""

    @pytest.fixture
    def _mem_cache(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr("PyNightSkyPredictor.darksky.cache.get", lambda k: store.get(k))
        monkeypatch.setattr("PyNightSkyPredictor.darksky.cache.set",
                            lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
        return store

    @staticmethod
    def _matrix(*minutes):
        """A GeoRoutes CalculateRouteMatrix response: one row of Duration (None = no route)."""
        return {"RouteMatrix": [[
            ({"Duration": m * 60, "Distance": m * 1000} if m is not None
             else {"Error": "NoRoute"}) for m in minutes
        ]]}

    def test_miss_calls_matrix_once_and_caches(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_route_matrix.return_value = self._matrix(56, 88)
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True},
                    {"lat": 35.23, "lon": -111.07, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert [c["drive_minutes"] for c in clusters] == [56, 88]
        # road distance (GeoRoutes Distance) is captured too: _matrix sets m*1000 metres
        assert [c["drive_miles"] for c in clusters] == [round(56000 / 1609.34),
                                                        round(88000 / 1609.34)]
        client.calculate_route_matrix.assert_called_once()
        # both legs now cached as {minutes, road-miles}
        assert _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] == \
            {"m": 56, "mi": round(56000 / 1609.34)}

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
        client.calculate_route_matrix.assert_not_called()

    def test_partial_miss_queries_only_uncached(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        _mem_cache[ds._drive_cache_key(35.2, -111.6, 35.41, -111.46)] = 56
        client = MagicMock()
        client.calculate_route_matrix.return_value = self._matrix(88)  # only the miss
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True},
                    {"lat": 35.23, "lon": -111.07, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert [c["drive_minutes"] for c in clusters] == [56, 88]
        # only the single uncached destination was sent
        _, kwargs = client.calculate_route_matrix.call_args
        assert kwargs["Destinations"] == [{"Position": [-111.07, 35.23]}]

    def test_api_failure_leaves_none_and_uncached(self, monkeypatch, _mem_cache):
        import PyNightSkyPredictor.darksky as ds
        client = MagicMock()
        client.calculate_route_matrix.side_effect = RuntimeError("throttled")
        monkeypatch.setattr(ds, "_georoutes", lambda: client)
        clusters = [{"lat": 35.41, "lon": -111.46, "is_poi": True}]
        ds._aws_drive_times(35.2, -111.6, clusters)
        assert clusters[0]["drive_minutes"] is None
        assert _mem_cache == {}  # transient failure must not poison the cache
