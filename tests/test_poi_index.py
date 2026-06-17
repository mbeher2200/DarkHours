"""Unit tests for the routable OSM POI index: encoder/loader round-trip, the dark-mask
intersection, and the POI-aware naming + drive-time gates. All offline + deterministic.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from PyNightSkyPredictor import darksky as ds


# Import the build-time encoder straight from the script (not a package) so the test
# exercises the SAME on-disk layout the loader reads — catches format drift.
_BUILDER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "osm_poi_builder.py"
_spec = importlib.util.spec_from_file_location("osm_poi_builder", _BUILDER_PATH)
osm_poi_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(osm_poi_builder)


@pytest.fixture(autouse=True)
def _reset_poi_cache():
    """Each test starts from a clean module-level loader cache."""
    ds._poi_h3_cache = None
    yield
    ds._poi_h3_cache = None


def _write_index(tmp_path, pois) -> Path:
    """pois: list of (lat, lon, name, type_label). Returns the .npz path."""
    import h3
    cells = [h3.str_to_int(h3.latlng_to_cell(la, lo, 7)) for la, lo, _, _ in pois]
    lats = [p[0] for p in pois]
    lons = [p[1] for p in pois]
    names = [p[2] for p in pois]
    types = [osm_poi_builder.POI_TYPE_LABELS.index(p[3]) for p in pois]
    out = tmp_path / "osm_pois.npz"
    osm_poi_builder.encode_poi_npz(out, cells, lats, lons, names, types)
    return out


def test_encode_load_roundtrip(tmp_path, monkeypatch):
    pois = [
        (38.5, -120.1, "Shriner Lake Campground", "camp_site"),
        (41.3, -95.8, "Lewis and Clark Monument", "viewpoint"),
        (35.0, -110.0, "Trailhead Lot", "parking"),
    ]
    path = _write_index(tmp_path, pois)
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", str(path))

    idx = ds._load_poi_h3_index()
    assert idx is not None
    assert idx.cells.size == 3
    assert idx.cells.dtype == np.uint64
    assert idx.lats.dtype == np.float32 and idx.lons.dtype == np.float32
    assert bool((idx.cells[:-1] <= idx.cells[1:]).all()), "cells must be ascending"
    # Names + types resolve correctly through the dictionary encoding.
    resolved = {idx.names[int(idx.name_codes[i])]: ds._POI_TYPE_LABELS[int(idx.poi_types[i])]
                for i in range(idx.cells.size)}
    assert resolved["Shriner Lake Campground"] == "camp_site"
    assert resolved["Trailhead Lot"] == "parking"


def test_loader_missing_file_caches_unavailable(monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", "/nonexistent/osm_pois.npz")
    assert ds._load_poi_h3_index() is None
    assert ds._poi_h3_cache is ds._POI_UNAVAILABLE


def _square_window(plat, plon, half=0.5, n=200):
    return plat - half, plat + half, plon - half, plon + half, n, n


def _mask_at(plat, plon, win):
    """A dark mask with a 3x3 dark patch at the POI's projected pixel."""
    min_lat, max_lat, min_lon, max_lon, rows, cols = win
    dark = np.zeros((rows, cols), dtype=bool)
    r = int(round((max_lat - plat) / (max_lat - min_lat) * (rows - 1)))
    c = int(round((plon - min_lon) / (max_lon - min_lon) * (cols - 1)))
    dark[max(0, r - 1):r + 2, max(0, c - 1):c + 2] = True
    return dark


def test_extract_poi_candidates_hit_and_bbox(tmp_path, monkeypatch):
    pois = [
        (38.5, -120.1, "In Window Campground", "camp_site"),
        (10.0, -50.0, "Far Away Lot", "parking"),   # outside the window → excluded
    ]
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", str(_write_index(tmp_path, pois)))
    idx = ds._load_poi_h3_index()

    win = _square_window(38.5, -120.1)
    min_lat, max_lat, min_lon, max_lon, rows, cols = win
    dark = _mask_at(38.5, -120.1, win)
    bortle = np.full((rows, cols), 2, dtype=int)
    sqm = np.full((rows, cols), 21.5)

    cands = ds._extract_poi_candidates(idx, dark, bortle, sqm,
                                       min_lat, max_lat, min_lon, max_lon, 38.5, -120.1)
    assert len(cands) == 1
    c = cands[0]
    assert c["is_poi"] is True
    assert c["name"] == "In Window Campground"
    assert c["poi_type"] == "camp_site"
    assert c["bortle_class"] == 2


def test_extract_poi_candidates_no_dark_hit_returns_empty(tmp_path, monkeypatch):
    pois = [(38.5, -120.1, "Campground", "camp_site")]
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", str(_write_index(tmp_path, pois)))
    idx = ds._load_poi_h3_index()
    win = _square_window(38.5, -120.1)
    min_lat, max_lat, min_lon, max_lon, rows, cols = win
    dark = np.zeros((rows, cols), dtype=bool)   # nothing dark
    bortle = np.full((rows, cols), 2, dtype=int)
    sqm = np.full((rows, cols), 21.5)
    assert ds._extract_poi_candidates(idx, dark, bortle, sqm,
                                      min_lat, max_lat, min_lon, max_lon, 38.5, -120.1) == []


def test_extract_dark_sky_falls_back_to_raw_when_no_poi(tmp_path, monkeypatch):
    """No POI in window → raw pixels returned, flagged is_poi=False/poi_type=None."""
    # POI far outside the raster window so the intersection is empty.
    pois = [(10.0, -50.0, "Far Lot", "parking")]
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", str(_write_index(tmp_path, pois)))
    idx = ds._load_poi_h3_index()

    rows = cols = 40
    viirs = np.zeros((rows, cols), dtype=float)          # all VIIRS-zero → dark
    falchi = np.zeros((rows, cols), dtype=float)         # pristine → Bortle 1
    min_lat, max_lat, min_lon, max_lon = 38.0, 39.0, -120.5, -119.5
    with patch.object(ds, "_HAS_GLM", False):            # skip ocean mask in the test
        cands = ds._extract_dark_sky_candidates(
            viirs, falchi, min_lat, max_lat, min_lon, max_lon,
            38.5, -120.0, 60, dark_threshold=3, poi_index=idx,
        )
    assert cands, "expected raw fallback candidates"
    assert all(c["is_poi"] is False and c["poi_type"] is None for c in cands)


def test_extract_dark_sky_returns_pois_when_intersecting(tmp_path, monkeypatch):
    """A POI inside the dark window → POI-first path returns is_poi=True (not raw pixels)."""
    # POI at the window center; the whole window is pristine/dark so its pixel is dark.
    pois = [(38.5, -120.0, "Center Campground", "camp_site")]
    monkeypatch.setenv("PYNIGHTSKY_POI_H3_PATH", str(_write_index(tmp_path, pois)))
    idx = ds._load_poi_h3_index()

    rows = cols = 60
    viirs = np.zeros((rows, cols), dtype=float)
    falchi = np.zeros((rows, cols), dtype=float)
    min_lat, max_lat, min_lon, max_lon = 38.0, 39.0, -120.5, -119.5
    with patch.object(ds, "_HAS_GLM", False):
        cands = ds._extract_dark_sky_candidates(
            viirs, falchi, min_lat, max_lat, min_lon, max_lon,
            38.5, -120.0, 60, dark_threshold=3, poi_index=idx,
        )
    assert cands, "expected a POI candidate"
    assert all(c["is_poi"] for c in cands)
    assert any(c["name"] == "Center Campground" and c["poi_type"] == "camp_site"
               for c in cands)


def test_aws_drive_times_skips_non_poi():
    """Raw fallback candidates are never routed; POIs are (GeoRoutes, DepartNow)."""
    poi = {"lat": 38.5, "lon": -120.1, "is_poi": True}
    raw = {"lat": 38.6, "lon": -120.2, "is_poi": False}
    with patch.object(ds, "cache") as mock_cache, \
         patch.object(ds, "_georoutes") as mock_gr:
        mock_cache.get.return_value = None
        mock_gr.return_value.calculate_route_matrix.return_value = {
            "RouteMatrix": [[{"Duration": 1800, "Distance": 40000}]]
        }
        ds._aws_drive_times(40.0, -121.0, [poi, raw])

    assert raw["drive_minutes"] is None
    assert poi["drive_minutes"] == 30
    kwargs = mock_gr.return_value.calculate_route_matrix.call_args.kwargs
    # Exactly one destination (the POI) was sent, traffic-aware, GeoRoutes shape.
    assert kwargs["Destinations"] == [{"Position": [-120.1, 38.5]}]
    assert kwargs["Origins"] == [{"Position": [-121.0, 40.0]}]
    assert kwargs["DepartNow"] is True
    assert kwargs["RoutingBoundary"] == {"Unbounded": True}


def test_dark_threshold_relaxes_for_dark_origins():
    """One class darker, capped at 3 — dark origins no longer demand near-impossible Bortle 1."""
    # (origin_bortle, expected dark_threshold)
    assert ds._dark_threshold(1) == 1
    assert ds._dark_threshold(2) == 1
    assert ds._dark_threshold(3) == 2   # was 1 — the Sterling Forest fix (surfaces Bortle 2)
    assert ds._dark_threshold(4) == 3   # was 2
    assert ds._dark_threshold(5) == 3   # unchanged (brighter origins capped at 3)
    assert ds._dark_threshold(8) == 3
    assert ds._dark_threshold(9) == 3


def test_offline_tier_name_poi_shortcircuits():
    """is_poi candidate → named offline (no Overpass/_settlement), unless blacklisted."""
    poi = {"lat": 38.5, "lon": -120.1, "name": "Shriner Lake Campground", "is_poi": True}
    # No PAD-US index: straight to the OSM name.
    assert ds._offline_tier_name(poi, None, None) == ("name", "Shriner Lake Campground")


def test_offline_tier_name_poi_discarded_on_blacklist():
    poi = {"lat": 38.5, "lon": -120.1, "name": "Base Lot", "is_poi": True}
    with patch.object(ds, "_padus_h3_lookup", return_value=("Fort Example", True)):
        assert ds._offline_tier_name(poi, object(), None) == ("discard", None)
