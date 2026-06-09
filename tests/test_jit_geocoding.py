"""Unit tests for the JIT reverse-geocode loop in _jit_geocode_candidates."""
from unittest.mock import patch

import pytest


def _make_candidates(n: int, distance_start: float = 0.0) -> list:
    return [
        {"lat": float(i), "lon": 0.0, "distance_miles": distance_start + float(i)}
        for i in range(n)
    ]


def _candidate(lat: float = 40.0, lon: float = -100.0, distance: float = 10.0) -> dict:
    return {"lat": lat, "lon": lon, "distance_miles": distance}


# ---------------------------------------------------------------------------
# Autouse fixture: isolate existing tests from the PAD-US index file on disk.
# The padus_index parameter to _jit_geocode_candidates defaults to None so
# these tests are unaffected by PAD-US logic; the fixture guards against
# _load_padus_h3_index side effects in any future find_nearby integration tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_padus_autoload():
    with patch("PyNightSkyPredictor.darksky._load_padus_h3_index", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Existing tests (assertions unchanged)
# ---------------------------------------------------------------------------

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


def test_jit_loop_same_name_deduped_regardless_of_distance():
    """Same name at any distance → only the nearest result is kept."""
    from PyNightSkyPredictor.darksky import _jit_geocode_candidates

    candidates = [
        {"lat": 35.0, "lon": -112.0, "distance_miles": 5.0},
        {"lat": 36.0, "lon": -112.0, "distance_miles": 50.0},
        {"lat": 37.0, "lon": -112.0, "distance_miles": 90.0},
    ]

    with patch("PyNightSkyPredictor.darksky._settlement",
               return_value="Coconino, AZ"):
        result = _jit_geocode_candidates(candidates, max_results=10)

    assert len(result) == 1
    assert result[0]["name"] == "Coconino, AZ"


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


# ---------------------------------------------------------------------------
# PAD-US Tier 1 tests
# ---------------------------------------------------------------------------

class TestPadusTier:
    """Three-tier pipeline: PAD-US → Overpass → reverse-geocoder."""

    # A non-None padus_index activates Tier 1; _padus_h3_lookup is mocked
    # so the dict content is irrelevant.
    _FAKE_INDEX: dict = {"sentinel": None}

    def test_blacklisted_candidate_discarded(self):
        """Blacklisted PAD-US cell → candidate is silently dropped; no network calls."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup",
                   return_value=("Restricted Area", True)), \
             patch("PyNightSkyPredictor.darksky._settlement") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10, padus_index=self._FAKE_INDEX
            )

        assert result == []
        mock_settle.assert_not_called()

    def test_good_name_skips_all_network_calls(self):
        """Non-blacklisted hit with good Unit_Nm → neither Overpass nor geocoder called."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup",
                   return_value=("Grand Canyon National Park", False)), \
             patch("PyNightSkyPredictor.darksky._settlement") as mock_settle, \
             patch("PyNightSkyPredictor.darksky._best_area_name_for_cluster") as mock_ov:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10,
                natural_areas=[{"name": "some area"}],
                padus_index=self._FAKE_INDEX,
            )

        assert len(result) == 1
        assert result[0]["name"] == "Grand Canyon National Park"
        mock_settle.assert_not_called()
        mock_ov.assert_not_called()

    def test_junk_name_falls_to_settlement(self):
        """Non-blacklisted hit with short Unit_Nm → _settlement() provides the name."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup",
                   return_value=("Park", False)), \
             patch("PyNightSkyPredictor.darksky._settlement",
                   return_value="Flagstaff, AZ") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10, padus_index=self._FAKE_INDEX
            )

        assert len(result) == 1
        assert result[0]["name"] == "Flagstaff, AZ"
        mock_settle.assert_called_once()

    def test_no_padus_hit_overpass_match_uses_area_name(self):
        """PAD-US miss + Overpass match → Overpass area name used directly."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup", return_value=None), \
             patch("PyNightSkyPredictor.darksky._best_area_name_for_cluster",
                   return_value="Coconino National Forest"), \
             patch("PyNightSkyPredictor.darksky._settlement") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10,
                natural_areas=[{}],
                padus_index=self._FAKE_INDEX,
            )

        assert len(result) == 1
        assert result[0]["name"] == "Coconino National Forest"
        mock_settle.assert_not_called()

    def test_no_padus_hit_overpass_miss_falls_to_settlement(self):
        """PAD-US miss + Overpass miss → _settlement() called; candidate NOT discarded."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup", return_value=None), \
             patch("PyNightSkyPredictor.darksky._best_area_name_for_cluster",
                   return_value=None), \
             patch("PyNightSkyPredictor.darksky._settlement",
                   return_value="Brawley, CA") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10,
                natural_areas=[{}],
                padus_index=self._FAKE_INDEX,
            )

        assert len(result) == 1
        assert result[0]["name"] == "Brawley, CA"
        mock_settle.assert_called_once()

    def test_no_padus_hit_overpass_none_falls_to_settlement(self):
        """PAD-US miss + natural_areas=None (Overpass disabled) → _settlement() called."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup", return_value=None), \
             patch("PyNightSkyPredictor.darksky._settlement",
                   return_value="Some Town") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10,
                natural_areas=None,
                padus_index=self._FAKE_INDEX,
            )

        assert len(result) == 1
        assert result[0]["name"] == "Some Town"
        mock_settle.assert_called_once()

    def test_lookup_exception_treated_as_miss(self):
        """Exception in _padus_h3_lookup is caught; candidate falls through to Tier 3."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        with patch("PyNightSkyPredictor.darksky._padus_h3_lookup",
                   side_effect=RuntimeError("h3 failure")), \
             patch("PyNightSkyPredictor.darksky._settlement",
                   return_value="Recovered Town") as mock_settle:
            result = _jit_geocode_candidates(
                [_candidate()], max_results=10,
                natural_areas=None,
                padus_index=self._FAKE_INDEX,
            )

        assert len(result) == 1
        assert result[0]["name"] == "Recovered Town"
        mock_settle.assert_called_once()

    def test_origin_name_excluded(self):
        """Candidates whose name matches the origin settlement are filtered out."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        candidates = [
            {"lat": 36.7, "lon": -119.8, "distance_miles": 5.0},   # resolves to origin
            {"lat": 37.0, "lon": -119.5, "distance_miles": 30.0},  # unique name
        ]
        def _settle(lat, lon):
            return "Fresno, CA" if lat < 37.0 else "Clovis, CA"

        with patch("PyNightSkyPredictor.darksky._settlement", side_effect=_settle):
            result = _jit_geocode_candidates(
                candidates, max_results=10,
                exclude={"Fresno, CA"},
            )

        assert len(result) == 1
        assert result[0]["name"] == "Clovis, CA"

    def test_padus_index_none_preserves_original_behavior(self):
        """padus_index=None → pipeline identical to pre-refactor (all Tier 3)."""
        from PyNightSkyPredictor.darksky import _jit_geocode_candidates

        # Unique lat per candidate so dedup doesn't collapse them.
        candidates = [
            {"lat": 40.0 + i, "lon": -100.0, "distance_miles": float(i)}
            for i in range(5)
        ]
        with patch("PyNightSkyPredictor.darksky._settlement",
                   side_effect=lambda lat, lon: f"Town {lat:.0f}") as mock_settle:
            result = _jit_geocode_candidates(candidates, max_results=10, padus_index=None)

        assert len(result) == 5
        assert mock_settle.call_count == 5


# ---------------------------------------------------------------------------
# _is_good_padus_name unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Grand Canyon National Park",         True),
    ("Yosemite National Park",             True),
    ("Coconino National Forest",           True),
    ("Arctic District Office",             False),
    ("Park",                               False),  # 4 chars: too short
    ("Land",                               False),  # 4 chars
    ("Blm",                                False),  # 3 chars: bureau code
    ("Usbr",                               False),  # 4 chars: bureau code
    ("Unknown",                            False),  # contains 'unknown'
    ("Unknown Park",                       False),
    ("County Land Local Other or Unknown",      False),
    ("Larimer County - Unknown",               False),
    ("Unnamed site - Monterey, County of",     False),  # real example
    ("Unnamed Site",                           False),
    ("El Centro Field Office",                 False),  # real example
    ("BLM District Office",                    False),
    ("Battleground Historic Site",             True),
    ("Acquired Site",                          True),
    ("2006003",                                False),  # pure numeric legacy ID
    ("",                                   False),
    ("   ",                                False),
    (None,                                 False),
])
def test_is_good_padus_name(name, expected):
    from PyNightSkyPredictor.darksky import _is_good_padus_name
    assert _is_good_padus_name(name) == expected


# ---------------------------------------------------------------------------
# _is_in_us unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lat, lon, expected", [
    (37.0,  -112.0,  True),   # Arizona
    (61.0,  -150.0,  True),   # Alaska
    (20.8,  -156.3,  True),   # Hawaii (Maui)
    (51.5,    -0.1,  False),  # London, UK
    (-33.9,  151.2,  False),  # Sydney, Australia
    (48.4,   -89.0,  True),   # Northern Ontario (in bbox — acceptable false positive)
    (25.8,  -100.3,  True),   # Northern Mexico (in bbox — acceptable false positive)
    (0.0,     0.0,   False),  # Gulf of Guinea
])
def test_is_in_us(lat, lon, expected):
    from PyNightSkyPredictor.darksky import _is_in_us
    assert _is_in_us(lat, lon) is expected
