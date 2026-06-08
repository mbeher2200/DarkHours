"""Unit tests for the JIT reverse-geocode loop in _jit_geocode_candidates."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_padus(monkeypatch):
    """Prevent real PADUS data from interfering with _settlement mock tests."""
    monkeypatch.setattr("PyNightSkyPredictor.darksky._padus_cache", [])


def _make_candidates(n: int, distance_start: float = 0.0) -> list:
    return [
        {"lat": float(i), "lon": 0.0, "distance_miles": distance_start + float(i)}
        for i in range(n)
    ]


def test_jit_loop_stops_at_max_results():
    """All unique names: assert exactly 10 geocode calls and 10 results."""
    from PyNightSkyPredictor.darksky import _jit_geocode_candidates

    candidates = [
        {
            "lat": 40.0 + i * 0.01,
            "lon": -100.0 + i * 0.01,
            "bortle_class": 3,
            "sqm": 21.0,
            "distance_miles": float(i),
            "direction": "N",
            "priority_score": float(i),
        }
        for i in range(500)
    ]

    with patch("PyNightSkyPredictor.darksky._settlement",
               side_effect=lambda lat, lon: f"City {lat:.2f}") as mock_settlement:
        result = _jit_geocode_candidates(candidates, max_results=10)

    assert len(result) == 10, "Loop must stop exactly at _MAX_RESULTS"
    assert mock_settlement.call_count == 10, (
        "Proves we did NOT process the remaining 490 candidates"
    )
    names = [c["name"] for c in result]
    assert len(names) == len(set(names)), "All returned names must be unique"


def test_jit_loop_deduplicates_names():
    """Same name, same 40-mile bucket: duplicates are dropped within the bucket."""
    from PyNightSkyPredictor.darksky import _jit_geocode_candidates

    # All 50 candidates within 0–39 miles (bucket 0)
    candidates = _make_candidates(50, distance_start=0.0)

    def _rg_with_dupes(lat, lon):
        if lat < 5.0:
            return "Metropolis"  # 5 candidates, same name, same distance bucket
        return f"City {lat}"     # rest are unique

    with patch("PyNightSkyPredictor.darksky._settlement",
               side_effect=_rg_with_dupes) as mock_settlement:
        result = _jit_geocode_candidates(candidates, max_results=10)

    assert len(result) == 10
    # 1 kept + 4 discarded (same name, same bucket) + 9 unique = 14 total calls
    assert mock_settlement.call_count == 14
    names = [c["name"] for c in result]
    assert len(names) == len(set(names))


def test_jit_loop_same_county_different_distance():
    """Same county name at 0–39 mi and 40–79 mi must both appear (different buckets)."""
    from PyNightSkyPredictor.darksky import _jit_geocode_candidates

    candidates = [
        {"lat": 35.0, "lon": -112.0, "distance_miles": 5.0},   # bucket 0
        {"lat": 36.0, "lon": -112.0, "distance_miles": 50.0},  # bucket 1
        {"lat": 37.0, "lon": -112.0, "distance_miles": 90.0},  # bucket 2
    ]

    with patch("PyNightSkyPredictor.darksky._settlement",
               return_value="Coconino, AZ"):
        result = _jit_geocode_candidates(candidates, max_results=10)

    assert len(result) == 3, "Each distance bucket should produce one result"
    assert all(c["name"] == "Coconino, AZ" for c in result)


def test_jit_loop_keeps_rural_candidates():
    """None returns (remote rural areas) get a coordinate fallback and are NOT dropped."""
    from PyNightSkyPredictor.darksky import _jit_geocode_candidates

    candidates = [
        {"lat": float(i), "lon": -112.0, "distance_miles": float(i)}
        for i in range(20)
    ]

    # First 5 return None (remote wilderness — no city), rest return unique city names
    def _rg_with_nones(lat, lon):
        if lat < 5.0:
            return None
        return f"City {lat}"

    with patch("PyNightSkyPredictor.darksky._settlement",
               side_effect=_rg_with_nones):
        result = _jit_geocode_candidates(candidates, max_results=10)

    assert len(result) == 10
    # The first 5 should appear with coordinate-based names, not be dropped
    coord_named = [c for c in result if "°" in c["name"]]
    assert len(coord_named) == 5
