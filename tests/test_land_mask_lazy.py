"""Tests for the lazy global-land-mask import in darkhours.darksky.

``global_land_mask`` materialises a (21600, 43200) bool array (~933 MB) the moment it is
imported, and only ``find_nearby`` ever calls ``is_land``. The module is therefore loaded
on first use rather than at import. These tests lock the contract that made that safe:

  1. Importing darksky (or either Lambda entrypoint) does not import global_land_mask.
  2. The accessor returns the real module when the package is installed, and memoizes it.
  3. A monkeypatched ``_glm`` is handed back untouched, so existing tests keep working.
  4. ``_HAS_GLM = False`` short-circuits at the call sites *without* triggering the import.
  5. Candidate extraction is unchanged whether the module was preloaded or loaded lazily.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

import darkhours.darksky as ds

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _probe(body: str) -> str:
    """Run `body` in a clean interpreter with the repo importable; return its stdout."""
    out = subprocess.run(
        [sys.executable, "-c", body],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, f"probe failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip()


class TestImportStaysLazy:
    @pytest.mark.parametrize("module", [
        "darkhours.darksky",
        "apps.worker.handler",
        "apps.api.handler",
    ])
    def test_importing_does_not_load_global_land_mask(self, module):
        """The whole point: none of these may drag in the 933 MB array at import."""
        loaded = _probe(
            f"import sys; import {module}; "
            "print('global_land_mask' in sys.modules)"
        )
        assert loaded == "False", (
            f"{module} imported global_land_mask at module scope; that costs ~933 MB of "
            "resident memory on every Lambda container. Keep it behind _land_mask_mod()."
        )


class TestAccessor:
    def test_returns_the_real_module(self, monkeypatch):
        pytest.importorskip("global_land_mask")
        monkeypatch.setattr(ds, "_glm", None)
        monkeypatch.setattr(ds, "_HAS_GLM", True)

        mod = ds._land_mask_mod()

        assert mod is not None
        assert bool(mod.is_land(40.0, -105.0)) is True      # Colorado
        assert bool(mod.is_land(0.0, -140.0)) is False      # mid-Pacific

    def test_memoizes(self, monkeypatch):
        pytest.importorskip("global_land_mask")
        monkeypatch.setattr(ds, "_glm", None)
        monkeypatch.setattr(ds, "_HAS_GLM", True)

        assert ds._land_mask_mod() is ds._land_mask_mod()

    def test_returns_a_patched_glm_untouched(self, monkeypatch):
        """~30 existing test sites monkeypatch _glm; the accessor must not overwrite them."""
        fake = MagicMock(is_land=lambda a, b: np.ones(np.shape(a), dtype=bool))
        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", fake)

        assert ds._land_mask_mod() is fake

    def test_missing_package_flips_the_flag_and_returns_none(self, monkeypatch):
        monkeypatch.setattr(ds, "_glm", None)
        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setitem(sys.modules, "global_land_mask", None)  # forces ImportError

        assert ds._land_mask_mod() is None
        assert ds._HAS_GLM is False

    def test_disabled_flag_short_circuits_without_importing(self, monkeypatch):
        """_HAS_GLM=False must be checked *before* the accessor, or laziness is defeated."""
        monkeypatch.setattr(ds, "_glm", None)
        monkeypatch.setattr(ds, "_HAS_GLM", False)

        assert ds._land_mask_mod() is None

    def test_call_sites_do_not_import_when_disabled(self):
        """End-to-end: with the flag off, extraction must not pull the module in."""
        loaded = _probe(
            "import sys, numpy as np\n"
            "import darkhours.darksky as ds\n"
            "ds._HAS_GLM = False\n"
            "viirs = np.zeros((2, 2), dtype=float)\n"
            "ds._extract_dark_sky_candidates(\n"
            "    viirs, None, 35.0, 36.0, -113.0, -111.0, 35.5, -112.0,\n"
            "    radius_miles=150, dark_threshold=3)\n"
            "print('global_land_mask' in sys.modules)\n"
        )
        assert loaded == "False"


class TestExtractionParity:
    """Lazy loading must not change what _extract_dark_sky_candidates returns."""

    _BOUNDS = dict(radius_miles=150, dark_threshold=3)

    def _run(self):
        viirs = np.zeros((2, 2), dtype=float)
        return ds._extract_dark_sky_candidates(
            viirs, None, 35.0, 36.0, -113.0, -111.0, 35.5, -112.0, **self._BOUNDS
        )

    def test_same_results_preloaded_vs_lazy(self, monkeypatch):
        pytest.importorskip("global_land_mask")

        monkeypatch.setattr(ds, "_HAS_GLM", True)
        monkeypatch.setattr(ds, "_glm", None)
        lazy = self._run()                       # accessor imports it mid-call

        from global_land_mask import globe
        monkeypatch.setattr(ds, "_glm", globe)   # already resident, as before the change
        preloaded = self._run()

        assert lazy == preloaded
