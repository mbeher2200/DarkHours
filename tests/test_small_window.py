"""
Tests for right-sized raster windows in find_nearby (PYNIGHTSKY_SMALL_WINDOW).

find_nearby fetches a radius-sized window when the origin may be bright and a
VIIRS-only 150-mile window only when the origin resolves dark (dome detection
is the sole consumer of the outer band). These tests drive find_nearby
end-to-end against a synthetic world backend — no S3, no network — and assert
on which windows get fetched and that outputs match the legacy always-big path.

The synthetic world is a function of absolute lat/lon, so any window over the
same area sees the same pixels regardless of window size.
"""
import math
import time

import numpy as np
import pytest
from unittest.mock import MagicMock

import darkhours.darksky as ds

# ---------------------------------------------------------------------------
# Synthetic world
# ---------------------------------------------------------------------------

RES = 0.02          # deg/pixel of the synthetic grid
OLAT, OLON = 35.0, -111.0
RADIUS = 60

# Bortle-1 dark patch ~25 miles east of the origin (inside the search radius).
DARK_PATCH = (35.0, OLON + 25.0 / (69.0 * math.cos(math.radians(OLAT))))
DARK_PATCH_R = 0.06
# Bortle-9 city blob ~30 miles north (feeds dome detection for dark origins).
CITY = (OLAT + 30.0 / 69.0, OLON)
CITY_R = 0.06

BRIGHT_ORIGIN = {"bortle_class": 9, "sqm": 16.5}
DARK_ORIGIN = {"bortle_class": 4, "sqm": 20.5}


def _radiance_for_bortle(bortle: int) -> float:
    """VIIRS radiance landing safely inside the given Bortle class."""
    sqm_lower = {
        1: 22.0, 2: 21.7, 3: 21.3, 4: 20.8,
        5: 20.0, 6: 19.1, 7: 18.0, 8: 17.0, 9: 0.0,
    }
    lower = sqm_lower.get(bortle, 17.0)
    sqm = lower + 0.05 if lower > 0 else 0.05
    return max(10 ** ((21.7 - sqm) / 2.5) - 0.6, 0.001)


def _world_read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape=None):
    # Snap to an absolute pixel lattice (origin 90N/180W), like the real tiled
    # reader: overlapping windows of any size see identical pixel values.
    rows = max(1, round((max_lat - min_lat) / RES))
    cols = max(1, round((max_lon - min_lon) / RES))
    row0 = round((90.0 - max_lat) / RES)
    col0 = round((min_lon + 180.0) / RES)
    lat_g, lon_g = np.meshgrid(
        90.0 - (row0 + np.arange(rows) + 0.5) * RES,
        -180.0 + (col0 + np.arange(cols) + 0.5) * RES,
        indexing="ij",
    )
    rad = np.full((rows, cols), _radiance_for_bortle(5), dtype=np.float64)
    patch = (lat_g - DARK_PATCH[0]) ** 2 + (lon_g - DARK_PATCH[1]) ** 2 <= DARK_PATCH_R ** 2
    rad[patch] = _radiance_for_bortle(1)
    city = (lat_g - CITY[0]) ** 2 + (lon_g - CITY[1]) ** 2 <= CITY_R ** 2
    rad[city] = _radiance_for_bortle(9)
    return rad


def _small_bounds(radius=RADIUS):
    r = radius + ds._SMALL_WINDOW_PAD_MILES
    dlat = r / 69.0
    dlon = r / max(69.0 * math.cos(math.radians(OLAT)), 0.01)
    return (OLAT - dlat, OLAT + dlat, OLON - dlon, OLON + dlon)


def _big_bounds(radius=RADIUS):
    r = max(radius, 150)
    dlat = r / 69.0
    dlon = r / max(69.0 * math.cos(math.radians(OLAT)), 0.01)
    return (OLAT - dlat, OLAT + dlat, OLON - dlon, OLON + dlon)


def _bounds_match(call_bounds, expected):
    return all(abs(a - b) < 1e-9 for a, b in zip(call_bounds, expected))


# ---------------------------------------------------------------------------
# Harness: hermetic find_nearby (no S3, no network, no indexes)
# ---------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch):
    """Wire find_nearby to the synthetic world; return the read_window call log."""
    calls: list[tuple[str, tuple]] = []

    def read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape=None):
        calls.append((dataset, (min_lat, max_lat, min_lon, max_lon)))
        return _world_read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape)

    fake_src = MagicMock()
    fake_src.read_window.side_effect = read_window
    fake_backend = MagicMock(raster_source=fake_src)
    fake_backend._name = "local"
    monkeypatch.setattr(ds.ports, "get_backend", lambda: fake_backend)

    ds._bortle_mem_cache.clear()
    monkeypatch.setattr(ds, "_HAS_GLM", False)
    monkeypatch.setattr(ds, "_is_in_us", lambda lat, lon: False)
    monkeypatch.setattr(ds, "_settlement", lambda lat, lon: f"Place {lat:.2f},{lon:.2f}")
    monkeypatch.setattr(ds, "_get_nominatim_county_city", lambda lat, lon: None)
    monkeypatch.setattr(ds, "_overpass_natural_areas_in_radius", lambda lat, lon, r: [])
    monkeypatch.setattr(
        ds, "_jit_geocode_candidates",
        lambda cands, maxr, areas, padus_index=None, exclude=None:
            [dict(c, name=f"Site {c['lat']:.3f},{c['lon']:.3f}") for c in cands[:maxr]],
    )
    yield calls
    ds._bortle_mem_cache.clear()


def _seed_peek(info):
    ds._bortle_mem_cache[(round(OLAT, 2), round(OLON, 2))] = info


# ---------------------------------------------------------------------------
# Window selection paths
# ---------------------------------------------------------------------------

class TestWindowSelection:

    def test_bright_peek_small_window_only(self, world, monkeypatch):
        _seed_peek(BRIGHT_ORIGIN)
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: BRIGHT_ORIGIN)
        result = ds.find_nearby(OLAT, OLON, RADIUS)
        assert len(world) == 2
        assert {d for d, _ in world} == {"viirs", "falchi"}
        assert all(_bounds_match(b, _small_bounds()) for _, b in world)
        assert result["light_domes"] == []
        assert result["results"]          # dark patch still surfaced

    def test_dark_peek_single_big_fetch(self, world, monkeypatch):
        _seed_peek(DARK_ORIGIN)
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: DARK_ORIGIN)
        result = ds.find_nearby(OLAT, OLON, RADIUS)
        assert len(world) == 2
        assert all(_bounds_match(b, _big_bounds()) for _, b in world)
        assert len(result["light_domes"]) == 1   # the city blob, named
        assert result["light_domes"][0]["name"]

    def test_miss_then_bright_no_second_fetch(self, world, monkeypatch):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: BRIGHT_ORIGIN)
        result = ds.find_nearby(OLAT, OLON, RADIUS)
        assert len(world) == 2
        assert all(_bounds_match(b, _small_bounds()) for _, b in world)
        assert result["light_domes"] == []

    def test_miss_then_dark_big_viirs_fetch(self, world, monkeypatch):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: DARK_ORIGIN)
        seen = {}
        orig_domes = ds._find_light_domes_from_array

        def spy(arr, *args, **kwargs):
            seen["shape"] = arr.shape
            seen["bounds"] = args[:4]
            seen["kwargs"] = kwargs
            return orig_domes(arr, *args, **kwargs)

        monkeypatch.setattr(ds, "_find_light_domes_from_array", spy)
        result = ds.find_nearby(OLAT, OLON, RADIUS)

        assert len(world) == 3
        small = [c for c in world if _bounds_match(c[1], _small_bounds())]
        big = [c for c in world if _bounds_match(c[1], _big_bounds())]
        assert {d for d, _ in small} == {"viirs", "falchi"}
        assert [d for d, _ in big] == ["viirs"]   # VIIRS-only dome fetch

        # Detector got the big window and self-computes its grids.
        assert _bounds_match(seen["bounds"], _big_bounds())
        big_rows = round((_big_bounds()[1] - _big_bounds()[0]) / RES)
        assert seen["shape"][0] == big_rows
        for key in ("lat_grid", "lon_grid", "land_mask", "viirs_sqm_arr"):
            assert seen["kwargs"][key] is None
        assert len(result["light_domes"]) == 1

    def test_radius_ge_150_is_legacy(self, world, monkeypatch):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: DARK_ORIGIN)
        ds.find_nearby(OLAT, OLON, 200)
        assert len(world) == 2
        assert all(_bounds_match(b, _big_bounds(radius=200)) for _, b in world)

    def test_bortle_none_fallback_fires_big_fetch(self, world, monkeypatch):
        # bortle_class None → origin_bortle falls back to 5 → dome search runs.
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: {"bortle_class": None, "sqm": None})
        ds.find_nearby(OLAT, OLON, RADIUS)
        assert len(world) == 3
        assert any(d == "viirs" and _bounds_match(b, _big_bounds()) for d, b in world)

    def test_flag_disable_legacy(self, world, monkeypatch):
        monkeypatch.setattr(ds, "_SMALL_WINDOW", False)
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: BRIGHT_ORIGIN)
        ds.find_nearby(OLAT, OLON, RADIUS)
        assert len(world) == 2
        assert all(_bounds_match(b, _big_bounds()) for _, b in world)


# ---------------------------------------------------------------------------
# Degradation paths
# ---------------------------------------------------------------------------

class TestDegradation:

    def test_early_exit_clean(self, world, monkeypatch):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: None)
        assert ds.find_nearby(OLAT, OLON, RADIUS) is None
        # The two small submits may still be in flight (shutdown(wait=False));
        # give the executor threads a beat, then confirm no dome fetch happened.
        deadline = time.time() + 2.0
        while len(world) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(world) == 2
        assert all(_bounds_match(b, _small_bounds()) for _, b in world)

    def test_big_fetch_failure_degrades(self, world, monkeypatch):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: DARK_ORIGIN)
        backend = ds.ports.get_backend()
        inner = backend.raster_source.read_window.side_effect

        def failing(dataset, min_lat, max_lat, min_lon, max_lon, out_shape=None):
            if _bounds_match((min_lat, max_lat, min_lon, max_lon), _big_bounds()):
                raise RuntimeError("simulated S3 failure on dome window")
            return inner(dataset, min_lat, max_lat, min_lon, max_lon, out_shape)

        backend.raster_source.read_window.side_effect = failing
        result = ds.find_nearby(OLAT, OLON, RADIUS)
        assert result is not None
        assert result["light_domes"] == []   # domes degrade
        assert result["results"]             # dark-sky results intact


# ---------------------------------------------------------------------------
# Output equivalence vs the legacy always-big path (flag off = reference)
# ---------------------------------------------------------------------------

def _assert_results_close(new, legacy):
    """Match each legacy cluster to its nearest new cluster (order can shuffle:
    the window-size change shifts assigned pixel coords by <= ~1 synthetic px,
    which reorders near-tied distance sorts)."""
    assert len(new) == len(legacy)
    remaining = list(new)
    for b in legacy:
        a = min(remaining, key=lambda c: abs(c["lat"] - b["lat"]) + abs(c["lon"] - b["lon"]))
        assert a["bortle_class"] == b["bortle_class"]
        assert abs(a["lat"] - b["lat"]) <= 2.0 * RES
        assert abs(a["lon"] - b["lon"]) <= 2.0 * RES
        assert abs(a["distance_miles"] - b["distance_miles"]) <= 2.0 * RES * 69.0
        remaining.remove(a)


class TestOutputEquivalence:

    def _run_both(self, monkeypatch, origin):
        monkeypatch.setattr(ds, "lookup", lambda lat, lon: origin)
        monkeypatch.setattr(ds, "_SMALL_WINDOW", False)
        legacy = ds.find_nearby(OLAT, OLON, RADIUS)
        ds._bortle_mem_cache.clear()
        monkeypatch.setattr(ds, "_SMALL_WINDOW", True)
        new = ds.find_nearby(OLAT, OLON, RADIUS)
        return new, legacy

    def test_output_equivalence_bright(self, world, monkeypatch):
        new, legacy = self._run_both(monkeypatch, BRIGHT_ORIGIN)
        assert new["origin_bortle"] == legacy["origin_bortle"]
        assert new["light_domes"] == legacy["light_domes"] == []
        assert new["has_dark_sky"] == legacy["has_dark_sky"]
        _assert_results_close(new["results"], legacy["results"])

    def test_output_equivalence_dark(self, world, monkeypatch):
        new, legacy = self._run_both(monkeypatch, DARK_ORIGIN)
        assert new["origin_bortle"] == legacy["origin_bortle"]
        # Dome window bounds/array are identical on both paths → exact equality.
        assert new["light_domes"] == legacy["light_domes"]
        assert len(new["light_domes"]) == 1
        _assert_results_close(new["results"], legacy["results"])
