"""
Classification pipeline tests: raster value → SQM → Bortle class / LPI zone.

`tests/test_darksky_formulas.py` covers each conversion in isolation. This file covers
the *composition*, which is where the scalar path (`darksky.lookup`) and the vectorized
path (`_extract_dark_sky_candidates`) have to agree: both classify the same pixel, so
both must quantise the SQM the same way before thresholding it.

Offline — the raster source is a MagicMock seamed on `sample`, following the fixture
shape in `tests/test_darksky_routing_flag.py`.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

from darkhours import darksky as ds


# Bortle thresholds, brightest-first, excluding the 0.0 catch-all sentinel.
_THRESHOLDS = [t for t, _, _ in ds._BORTLE if t > 0.0]


# ---------------------------------------------------------------------------
# Inverse conversions — build a raster value that lands on a chosen SQM
# ---------------------------------------------------------------------------

def _radiance_for_sqm(sqm: float) -> float:
    """VIIRS radiance (nW/cm²/sr) whose regression value is exactly *sqm*."""
    return 10.0 ** ((21.7 - sqm) / 2.5) - 0.6


def _luminance_for_sqm(sqm: float) -> float:
    """Falchi luminance (mcd/m², pre-_FALCHI_SCALE) whose model value is exactly *sqm*."""
    scaled = ds._L_NATURAL * (10.0 ** ((ds._SQM_NATURAL - sqm) / 2.5) - 1.0)
    return scaled / ds._FALCHI_SCALE


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def raster(monkeypatch):
    """Inject (viirs, falchi) point samples and run lookup() with both caches bypassed."""
    values = {"viirs": 0.0, "falchi": 0.0}

    fake_src = MagicMock()
    fake_src.sample.side_effect = lambda dataset, lat, lon: values[dataset]
    fake_backend = MagicMock(raster_source=fake_src)
    monkeypatch.setattr(ds.ports, "get_backend", lambda: fake_backend)
    monkeypatch.setattr(ds.cache, "get", lambda k: None)
    monkeypatch.setattr(ds.cache, "set", lambda k, v, ttl_seconds=None: True)

    def _lookup(viirs=0.0, falchi=0.0, lat=40.0, lon=-100.0):
        values["viirs"], values["falchi"] = viirs, falchi
        ds._bortle_mem_cache.clear()
        return ds.lookup(lat, lon)

    ds._bortle_mem_cache.clear()
    yield _lookup
    ds._bortle_mem_cache.clear()


# ---------------------------------------------------------------------------
# Array-path reference — mirrors _extract_dark_sky_candidates
# ---------------------------------------------------------------------------

def _array_path_class(viirs: float, falchi: float) -> int:
    """Bortle class the vectorized path assigns to this pixel."""
    v = np.array([[viirs]], dtype=np.float64)
    f = np.array([[falchi]], dtype=np.float64)
    sqm_viirs = np.where(v > 0, 21.7 - 2.5 * np.log10(v + 0.6), np.nan)
    bortle = ds._sqm_to_bortle_array(sqm_viirs)

    scaled = f * ds._FALCHI_SCALE
    sqm_falchi = np.where(
        scaled > 0,
        ds._SQM_NATURAL - 2.5 * np.log10((scaled + ds._L_NATURAL) / ds._L_NATURAL),
        np.nan,
    )
    falchi_bortle = ds._sqm_to_bortle_array(sqm_falchi)
    viirs_zero = v == 0
    falchi_bortle = np.where(np.isnan(sqm_falchi) & viirs_zero, 1, falchi_bortle)
    bortle = np.where(viirs_zero & (bortle == 0), falchi_bortle, bortle)
    return int(bortle[0, 0])


# ---------------------------------------------------------------------------
# The guard: both paths must classify the same pixel identically
# ---------------------------------------------------------------------------

class TestPipelineParity:
    """Rounding the SQM before thresholding it makes these two disagree."""

    @pytest.mark.parametrize("threshold", _THRESHOLDS)
    @pytest.mark.parametrize("offset", [-0.049, -0.03, -0.01, -0.001, 0.0, 0.001, 0.03])
    def test_viirs_band_around_every_threshold(self, raster, threshold, offset):
        """Every [T-0.05, T) band, the exact boundary, and just above it."""
        target = threshold + offset
        radiance = _radiance_for_sqm(target)
        if radiance <= 0:          # above the VIIRS airglow ceiling — Falchi's job
            pytest.skip("SQM unreachable on the VIIRS branch")
        result = raster(viirs=radiance)
        assert result["bortle_class"] == _array_path_class(radiance, 0.0)

    @pytest.mark.parametrize("threshold", _THRESHOLDS)
    @pytest.mark.parametrize("offset", [-0.049, -0.03, -0.01, -0.001, 0.0, 0.001, 0.03])
    def test_falchi_band_around_every_threshold(self, raster, threshold, offset):
        target = threshold + offset
        luminance = _luminance_for_sqm(target)
        if luminance <= 0:
            pytest.skip("SQM unreachable on the Falchi branch")
        result = raster(viirs=0.0, falchi=luminance)
        assert result["bortle_class"] == _array_path_class(0.0, luminance)

    def test_no_input_reads_darker_than_the_unrounded_value(self, raster):
        """Quantisation must never promote a pixel toward a darker class."""
        for target in np.arange(16.0, 22.2, 0.01):
            radiance = _radiance_for_sqm(float(target))
            if radiance <= 0:
                continue
            result = raster(viirs=radiance)
            expected, _ = ds.sqm_to_bortle(ds.radiance_to_sqm(radiance))
            assert result["bortle_class"] >= expected, (
                f"SQM {target:.3f} classified darker than the unrounded value"
            )


# ---------------------------------------------------------------------------
# Payload contract
# ---------------------------------------------------------------------------

class TestPayload:
    def test_sqm_carries_the_display_quantum(self, raster):
        for target in (21.9533, 20.4471, 18.0129):
            radiance = _radiance_for_sqm(target)
            result = raster(viirs=radiance)
            assert abs(result["sqm"] - target) <= 0.005
            assert result["sqm"] == round(result["sqm"], ds._SQM_DISPLAY_DP)

    def test_carries_the_unrounded_sqm_for_exact_reclassification(self, raster):
        """A cached entry must be reclassifiable without a raster read: the key
        quantises the coordinate to 0.01°, coarser than either raster pixel, so it
        cannot identify the pixel the entry came from."""
        result = raster(viirs=1.0)
        assert result["sqm_raw"] == ds.radiance_to_sqm(1.0)
        assert ds.sqm_to_bortle(result["sqm_raw"])[0] == result["bortle_class"]
        assert ds.sqm_to_zone(result["sqm_raw"]) == result["lp_zone"]

    def test_does_not_record_the_caller_coordinate(self, raster):
        """Entries are shared by every caller in the same 0.01° cell, so a stored
        coordinate would be echoed back to the next one."""
        result = raster(viirs=1.0, lat=41.6626, lon=-77.8261)
        assert "lat" not in result and "lon" not in result

    def test_class_and_zone_agree_with_the_unrounded_sqm(self, raster):
        for target in np.arange(17.0, 22.2, 0.017):
            luminance = _luminance_for_sqm(float(target))
            if luminance <= 0:
                continue
            result = raster(viirs=0.0, falchi=luminance)
            raw = ds.luminance_to_sqm(luminance * ds._FALCHI_SCALE)
            assert result["bortle_class"] == ds.sqm_to_bortle(raw)[0]
            assert result["lp_zone"] == ds.sqm_to_zone(raw)


# ---------------------------------------------------------------------------
# Pinned reference reading
# ---------------------------------------------------------------------------

class TestCherrySpringsReference:
    """Falchi 2016 at Cherry Springs SP (41.6626, -77.8261): 0.0104002 mcd/m².

    x _FALCHI_SCALE = 0.0312007 -> SQM 21.9533. Sits 0.047 below the Bortle 1
    threshold (22.0) and 0.023 above the zone 1a/1b boundary (21.93), so it exercises
    both threshold tables at once.
    """

    FALCHI = 0.010400230996310711

    def test_class(self, raster):
        assert raster(viirs=0.0, falchi=self.FALCHI)["bortle_class"] == 2

    def test_zone(self, raster):
        assert raster(viirs=0.0, falchi=self.FALCHI)["lp_zone"] == "1b"

    def test_sqm(self, raster):
        assert raster(viirs=0.0, falchi=self.FALCHI)["sqm"] == pytest.approx(21.95, abs=0.005)

    def test_matches_the_array_path(self, raster):
        result = raster(viirs=0.0, falchi=self.FALCHI)
        assert result["bortle_class"] == _array_path_class(0.0, self.FALCHI)


# ---------------------------------------------------------------------------
# Source selection is unchanged by this work — pinned so it stays that way
# ---------------------------------------------------------------------------

class TestSourceSelection:
    def test_measurable_viirs_wins(self, raster):
        assert raster(viirs=5.0, falchi=1.0)["source"] == "VIIRS 2025"

    def test_viirs_zero_falls_back_to_falchi(self, raster):
        assert raster(viirs=0.0, falchi=1.0)["source"] == "Falchi 2016"

    def test_falchi_zero_is_natural_sky(self, raster):
        result = raster(viirs=0.0, falchi=0.0)
        assert result["sqm"] == pytest.approx(ds._SQM_NATURAL, abs=0.005)
        assert result["bortle_class"] == 1
