"""Hermetic test for the routing operator flag in darksky.find_nearby() (see
darkhours/feature_flags.py). Reuses the synthetic-world find_nearby harness from
test_small_window.py, but on the "aws" backend (where drive-time routing is
attempted at all) with _aws_drive_times mocked, so no real AWS Location calls
happen either way — only the flag's effect on whether it's *attempted* is under
test.
"""
import math

import numpy as np
import pytest
from unittest.mock import MagicMock

import darkhours.darksky as ds
from tests.test_small_window import (
    OLAT, OLON, RADIUS, DARK_PATCH, DARK_PATCH_R, _radiance_for_bortle, DARK_ORIGIN,
)


def _world_read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape=None):
    rows = max(1, round((max_lat - min_lat) / 0.02))
    cols = max(1, round((max_lon - min_lon) / 0.02))
    row0 = round((90.0 - max_lat) / 0.02)
    col0 = round((min_lon + 180.0) / 0.02)
    lat_g, lon_g = np.meshgrid(
        90.0 - (row0 + np.arange(rows) + 0.5) * 0.02,
        -180.0 + (col0 + np.arange(cols) + 0.5) * 0.02,
        indexing="ij",
    )
    rad = np.full((rows, cols), _radiance_for_bortle(5), dtype=np.float64)
    patch = (lat_g - DARK_PATCH[0]) ** 2 + (lon_g - DARK_PATCH[1]) ** 2 <= DARK_PATCH_R ** 2
    rad[patch] = _radiance_for_bortle(1)
    return rad


@pytest.fixture
def aws_world(monkeypatch):
    """Same harness as test_small_window's `world` fixture, but backend='aws' —
    the only backend on which routing is ever attempted — with _aws_drive_times
    mocked so no real AWS Location call is possible regardless of the flag."""
    def read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape=None):
        return _world_read_window(dataset, min_lat, max_lat, min_lon, max_lon, out_shape)

    fake_src = MagicMock()
    fake_src.read_window.side_effect = read_window
    fake_src.grid_meta.return_value = (-180.0, 90.0, 0.02, 0.02)
    fake_backend = MagicMock(raster_source=fake_src)
    fake_backend._name = "aws"
    monkeypatch.setattr(ds.ports, "get_backend", lambda: fake_backend)

    ds._bortle_mem_cache.clear()
    ds._bortle_mem_cache[(round(OLAT, 2), round(OLON, 2))] = DARK_ORIGIN
    monkeypatch.setattr(ds, "lookup", lambda lat, lon: DARK_ORIGIN)
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
    yield
    ds._bortle_mem_cache.clear()


def _fake_drive_times(lat, lon, clusters):
    """Stand-in for the real AWS Location call: populates the same fields on each
    cluster dict so the caller's sort/serialization logic sees a consistent shape."""
    for c in clusters:
        c["drive_minutes"], c["drive_miles"], c["warnings"], c["tail_miles"] = 12, 6, [], None


def test_routing_flag_off_skips_aws_drive_times(monkeypatch, aws_world):
    monkeypatch.setenv("PYNIGHTSKY_FEATURE_ROUTING_DISABLE", "1")
    drive_times = MagicMock(side_effect=_fake_drive_times)
    monkeypatch.setattr(ds, "_aws_drive_times", drive_times)
    result = ds.find_nearby(OLAT, OLON, RADIUS)
    drive_times.assert_not_called()
    assert result is not None
    assert result["results"]  # the search itself still succeeds
    assert all(c["drive_minutes"] is None for c in result["results"])


def test_routing_flag_on_by_default_still_attempted(monkeypatch, aws_world):
    drive_times = MagicMock(side_effect=_fake_drive_times)
    monkeypatch.setattr(ds, "_aws_drive_times", drive_times)
    result = ds.find_nearby(OLAT, OLON, RADIUS)
    assert drive_times.called
    assert result is not None
    assert all(c["drive_minutes"] == 12 for c in result["results"])
