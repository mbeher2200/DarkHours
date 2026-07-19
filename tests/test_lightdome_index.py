"""Hermetic tests for the precomputed light-dome H3 index loader + lookup.

Builds a tiny synthetic .npz and exercises load/lookup — no raster, no S3, no network.
"""
import numpy as np
import pytest

import darkhours.light_dome as ld

h3 = pytest.importorskip("h3")
RES = ld.LIGHTDOME_H3_RESOLUTION


def _write_index(path, entries):
    """entries: list of (lat, lon, scores8, heights8, dists8) — written sorted by cell."""
    cells = np.array(
        [h3.str_to_int(h3.latlng_to_cell(la, lo, RES)) for la, lo, *_ in entries],
        dtype=np.uint64,
    )
    order = np.argsort(cells, kind="stable")
    np.savez_compressed(
        path,
        cells=cells[order],
        scores=np.array([e[2] for e in entries], dtype=np.float16)[order],
        dome_heights=np.array([e[3] for e in entries], dtype=np.float16)[order],
        mean_distances=np.array([e[4] for e in entries], dtype=np.float16)[order],
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    ld._lightdome_index_cache = None
    yield
    ld._lightdome_index_cache = None


def _domed_scores():
    s = [0.05] * 8
    s[ld.DIRS_8.index("SW")] = 5.0          # a clear major dome to the SW
    s[ld.DIRS_8.index("NE")] = 0.01         # the darkest
    return s


def test_load_and_lookup_hit(tmp_path, monkeypatch):
    lat, lon = 34.8697, -111.4106
    heights = [1.0] * 8
    dists = [50.0] * 8
    _write_index(tmp_path / "ld.npz", [(lat, lon, _domed_scores(), heights, dists)])
    monkeypatch.setenv("PYNIGHTSKY_LIGHTDOME_H3_PATH", str(tmp_path / "ld.npz"))

    out = ld.lightdome_lookup(lat, lon)
    assert out is not None
    assert out["sky_state"] == "domed"
    assert out["darkest_direction"] == "NE"
    assert out["domes"][0]["direction"] == "SW"
    assert out["domes"][0]["severity"] == "major"


def test_lookup_miss_returns_none(tmp_path, monkeypatch):
    _write_index(tmp_path / "ld.npz", [(34.8697, -111.4106, _domed_scores(), [1.0] * 8, [50.0] * 8)])
    monkeypatch.setenv("PYNIGHTSKY_LIGHTDOME_H3_PATH", str(tmp_path / "ld.npz"))
    # a far-away coordinate is a different H3 cell → not in the index
    assert ld.lightdome_lookup(0.0, 0.0) is None


def test_sentinel_distance_maps_to_none(tmp_path, monkeypatch):
    lat, lon = 40.77, -113.89
    dists = [50.0] * 8
    dists[ld.DIRS_8.index("SW")] = -1.0      # no-glow sentinel on the flagged direction
    _write_index(tmp_path / "ld.npz", [(lat, lon, _domed_scores(), [1.0] * 8, dists)])
    monkeypatch.setenv("PYNIGHTSKY_LIGHTDOME_H3_PATH", str(tmp_path / "ld.npz"))

    out = ld.lightdome_lookup(lat, lon)
    sw = next(d for d in out["domes"] if d["direction"] == "SW")
    assert sw["mean_distance_mi"] is None


def test_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PYNIGHTSKY_LIGHTDOME_H3_PATH", str(tmp_path / "does_not_exist.npz"))
    assert ld.load_lightdome_index() is None
    assert ld.lightdome_lookup(34.8697, -111.4106) is None


def test_cell_round_trip():
    lat, lon = 34.8697, -111.4106
    cell = h3.latlng_to_cell(lat, lon, RES)
    rlat, rlon = h3.cell_to_latlng(cell)
    assert abs(rlat - lat) < 0.2 and abs(rlon - lon) < 0.2   # within a res-6 cell radius
