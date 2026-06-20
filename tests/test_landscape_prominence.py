"""Tests for Phase 3 landscape prominence classification and moon-scale labeling."""

import pytest

from PyNightSkyPredictor.targets import (
    _landscape_suitability,
    _SB_DIFFUSE_THRESHOLD,
    _ANGULAR_SIZE_MIN_ARCMIN,
)


# ---------------------------------------------------------------------------
# _landscape_suitability() — classification logic
# ---------------------------------------------------------------------------

def test_diffuse_high_sb():
    assert _landscape_suitability(17.0, 180) == "diffuse"

def test_diffuse_boundary_at_16():
    # Threshold is inclusive: SB == 16.0 → diffuse
    assert _landscape_suitability(16.0, 60) == "diffuse"

def test_not_diffuse_just_below_16():
    assert _landscape_suitability(15.9, 60) == "prominent"

def test_too_small_below_threshold():
    assert _landscape_suitability(13.0, 7) == "too_small"

def test_too_small_at_8_9():
    # Threshold is strict: size < 9.0 → too_small
    assert _landscape_suitability(13.0, 8.9) == "too_small"

def test_prominent_at_9_exactly():
    # 9.0 is NOT filtered
    assert _landscape_suitability(13.5, 9.0) == "prominent"

def test_prominent_normal_dso():
    # Orion-like object: high SB, large
    assert _landscape_suitability(13.0, 65) == "prominent"

def test_diffuse_takes_priority_over_size():
    # SB check runs before size check; Ring Nebula-like (tiny AND diffuse SB)
    assert _landscape_suitability(17.0, 1.4) == "diffuse"

def test_no_sb_cluster_large():
    # Clusters have no surface_brightness field; large cluster is prominent
    assert _landscape_suitability(None, 110) == "prominent"

def test_no_sb_cluster_small():
    # Small cluster (no SB) still fails on size
    assert _landscape_suitability(None, 8) == "too_small"

def test_no_angular_size_planet():
    # Planets/meteors/MW have no angular_size — always prominent
    assert _landscape_suitability(None, None) == "prominent"


# ---------------------------------------------------------------------------
# moonScaleLabel() — Python port for breakpoint verification
# ---------------------------------------------------------------------------
# This mirrors the TypeScript moonScaleLabel() in ReportCard.tsx.

MOON_ARCMIN = 30

def _moon_scale_label(arcmin):
    """Python port of moonScaleLabel() in ReportCard.tsx."""
    if arcmin is None:
        return None
    ratio = arcmin / MOON_ARCMIN
    if ratio >= 1.5:
        return f"{round(ratio)}x Moon"
    if ratio >= 1.0:
        return "1x Moon"
    if ratio >= 0.5:
        return "½ Moon"
    if ratio >= 0.3:
        return "⅓ Moon"
    return None


def test_moon_scale_large():
    # Andromeda: 190' → round(190/30) = 6
    assert _moon_scale_label(190) == "6x Moon"

def test_moon_scale_exact_1x():
    assert _moon_scale_label(30) == "1x Moon"

def test_moon_scale_half():
    # Helix: 25' → ratio 0.83
    assert _moon_scale_label(25) == "½ Moon"

def test_moon_scale_third():
    # Swan Nebula: 11' → ratio 0.37
    assert _moon_scale_label(11) == "⅓ Moon"

def test_moon_scale_null_compact():
    # Ring Nebula: 1.4' → ratio 0.047 → below ⅓ threshold
    assert _moon_scale_label(7) is None

def test_moon_scale_null_input():
    assert _moon_scale_label(None) is None
