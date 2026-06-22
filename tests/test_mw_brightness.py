"""
Pure-math tests for the Milky Way arch brightness model mirroring
apps/web/src/ReportCard.tsx intrinsicBrightness() / washoutFactor() / archGlowAt().

No network, no ephemeris, no backend imports — runs instantly.
Run with: pytest tests/test_mw_brightness.py
"""
import math
import pytest


# ── Python mirrors of the TypeScript functions ────────────────────────────────

LD_DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def ld_tent(arr: list, az_deg: float) -> float:
    """Linear interpolation between 8 cardinal direction scores. Mirrors ldTent()."""
    az = az_deg % 360
    idx_f = az / 45.0
    lo = int(idx_f) % 8
    hi = (lo + 1) % 8
    t = idx_f - int(idx_f)
    return arr[lo] * (1 - t) + arr[hi] * t


def arch_glow_at(scores: dict, az_deg: float, alt_deg: float) -> float:
    """Sky glow for MW arch at (az_deg, alt_deg). Mirrors archGlowAt() in ReportCard.tsx.
    Uses fixed 40° characteristic altitude — distinct from glowToward() which uses dome heights."""
    scores8 = [scores.get(d, 0.0) for d in LD_DIRS]
    score = ld_tent(scores8, az_deg)
    alt = max(0.0, alt_deg)
    return score / (1 + (alt / 40) ** 2)


def intrinsic_brightness(l_deg: float) -> float:
    """Two-component bulge + disk + floor brightness model. Returns [0, 1]."""
    norm  = l_deg % 360
    delta = min(norm, 360 - norm)   # angular distance from core, 0–180°
    x     = delta / 180              # normalised 0→1
    bulge = 0.70 * math.exp(-x * x / (2 * 0.28 * 0.28))
    disk  = 0.18 * (1 - x * 0.7)
    return max(0.0, min(1.0, 0.12 + bulge + disk))


def washout_factor(glow: float) -> float:
    """Attenuation from light-dome glow index. Returns [0, 1]."""
    return math.exp(-0.8 * glow)


def combined_brightness(l_deg: float, glow: float = 0.0) -> float:
    return intrinsic_brightness(l_deg) * washout_factor(glow)


# ── Scenario A: Dark sky — intrinsic brightness only ─────────────────────────
# Golden dataset: Cherry Springs State Park, PA (41.663°N, 77.831°W), 2026-07-15 04:00 UTC
# (These are intrinsic values only — independent of time/location.)

class TestIntrinsicBrightness:

    def test_core_is_maximum(self):
        assert intrinsic_brightness(0) == pytest.approx(1.0, abs=0.001)

    def test_scutum(self):
        # l=27° target ~0.87; model gives 0.888
        assert intrinsic_brightness(27) == pytest.approx(0.888, abs=0.01)

    def test_aquila(self):
        # l=45° target ~0.70; model gives 0.738
        assert intrinsic_brightness(45) == pytest.approx(0.738, abs=0.01)

    def test_cygnus(self):
        # l=80° target ~0.45; model gives 0.443
        assert intrinsic_brightness(80) == pytest.approx(0.443, abs=0.01)

    def test_cassiopeia(self):
        # l=135° target ~0.25; model gives 0.225
        assert intrinsic_brightness(135) == pytest.approx(0.225, abs=0.02)

    def test_anticenter(self):
        # l=180° target 0.15–0.18; model gives 0.175
        v = intrinsic_brightness(180)
        assert 0.14 <= v <= 0.19

    def test_output_in_unit_range(self):
        for l in range(0, 360, 5):
            v = intrinsic_brightness(l)
            assert 0.0 <= v <= 1.0, f"out of range at l={l}: {v}"

    def test_monotone_from_core_to_anticenter(self):
        """Brightness must be non-increasing from l=0° toward l=180°."""
        prev = intrinsic_brightness(0)
        for l in range(5, 181, 5):
            curr = intrinsic_brightness(l)
            assert curr <= prev + 0.001, f"non-monotone at l={l}: {curr:.4f} > {prev:.4f}"
            prev = curr

    def test_symmetry_scorpius_equals_13(self):
        """l=347° (Scorpius, 13° from core) same brightness as l=13°."""
        assert intrinsic_brightness(347) == pytest.approx(intrinsic_brightness(13), abs=0.001)

    def test_negative_l_wraps(self):
        """Negative longitude wraps identically to positive equivalent."""
        assert intrinsic_brightness(-5) == pytest.approx(intrinsic_brightness(355), abs=0.001)

    def test_floor_at_anticenter(self):
        """Anticenter always has a non-zero floor (faint disk + floor term)."""
        assert intrinsic_brightness(180) >= 0.10

    def test_golden_dataset_opacity_values(self):
        """Spot-check the final glowOpacity / coreOpacity for dark sky."""
        expected = [
            (0,   1.000, 0.350, 0.850),
            (27,  0.888, 0.311, 0.755),
            (45,  0.738, 0.258, 0.627),
            (80,  0.443, 0.155, 0.377),
            (135, 0.225, 0.079, 0.191),
            (180, 0.175, 0.061, 0.149),
        ]
        for l, intrinsic, glow_op, core_op in expected:
            I = intrinsic_brightness(l)
            assert I == pytest.approx(intrinsic, abs=0.005), f"l={l} intrinsic mismatch"
            assert I * 0.35 == pytest.approx(glow_op, abs=0.005), f"l={l} glowOpacity mismatch"
            assert I * 0.85 == pytest.approx(core_op, abs=0.005), f"l={l} coreOpacity mismatch"


# ── Washout model ─────────────────────────────────────────────────────────────

class TestWashoutFactor:

    def test_zero_glow_no_attenuation(self):
        assert washout_factor(0.0) == pytest.approx(1.0, abs=0.001)

    def test_minor_dome(self):
        # LD_MINOR = 0.25 → exp(-0.2) ≈ 0.819
        assert washout_factor(0.25) == pytest.approx(0.819, abs=0.005)

    def test_moderate(self):
        # glow=1.0 → exp(-0.8) ≈ 0.449
        assert washout_factor(1.0) == pytest.approx(0.449, abs=0.005)

    def test_major_dome(self):
        # LD_MAJOR = 3.0 → exp(-2.4) ≈ 0.091
        assert washout_factor(3.0) == pytest.approx(0.091, abs=0.005)

    def test_monotone_decreasing(self):
        vals = [washout_factor(g) for g in [0, 0.5, 1.0, 2.0, 3.0, 5.0]]
        assert vals == sorted(vals, reverse=True)

    def test_never_negative(self):
        for g in [0, 0.1, 1, 3, 10]:
            assert washout_factor(g) >= 0.0


# ── Scenario B: Major southern dome ──────────────────────────────────────────

class TestScenarioB:

    def test_core_with_major_dome(self):
        """Core (l=0°) at glow=2.5 → combined ≈ 0.135."""
        # I(0)=1.0, W=exp(-2.0)=0.1353
        c = combined_brightness(0, glow=2.5)
        assert c == pytest.approx(0.135, abs=0.005)

    def test_anticenter_with_minor_dome(self):
        """Anticenter (l=180°) at glow=0.25 → combined = I(180) × W(0.25)."""
        c = combined_brightness(180, glow=0.25)
        assert c == pytest.approx(intrinsic_brightness(180) * washout_factor(0.25), abs=0.001)

    def test_combined_glow_and_core_opacity(self):
        """Under major dome, core glowOpacity ~ 0.047, coreOpacity ~ 0.115."""
        c = combined_brightness(0, glow=2.5)
        assert c * 0.35 == pytest.approx(0.047, abs=0.005)
        assert c * 0.85 == pytest.approx(0.115, abs=0.005)


# ── All-sky washout: galactic shape must remain visible ──────────────────────

class TestAllSkyWashout:

    def test_core_brighter_than_anticenter_under_uniform_glow(self):
        """Core/anticenter ratio is purely intrinsic (washout cancels): ~5.7×."""
        glow = 2.0
        core_b       = combined_brightness(0,   glow)
        anticenter_b = combined_brightness(180, glow)
        ratio = core_b / anticenter_b
        assert ratio >= 2.0, f"core/anticenter ratio {ratio:.2f} < 2.0 under glow=2.0"

    @pytest.mark.parametrize("glow", [0.5, 1.0, 2.0, 3.0])
    def test_core_always_brightest_at_any_uniform_glow(self, glow):
        """Core (l=0°) must be the brightest point at any uniform glow level."""
        core_b = combined_brightness(0, glow)
        for l in range(5, 181, 5):
            assert combined_brightness(l, glow) <= core_b + 0.001, \
                f"l={l} exceeded core brightness at glow={glow}"

    def test_edge_case_null_dome_equals_zero_glow(self):
        """Null light dome (fallback) is identical to glow=0 everywhere."""
        for l in range(0, 360, 45):
            no_dome = intrinsic_brightness(l) * washout_factor(0)
            assert no_dome == pytest.approx(intrinsic_brightness(l), abs=1e-9)


# ── archGlowAt: atmospheric scattering model for arch brightness ──────────────

class TestArchGlowAt:
    """Mirrors archGlowAt() in ReportCard.tsx.

    Key design: 40° characteristic altitude — city glow scatters through the
    atmosphere and degrades sky brightness well above the geometric dome boundary
    (which is only 1–3° for a city 30+ mi away). At alt=40°, glow = score/2.
    """

    SEDONA = {'N': 0.0, 'NE': 0.0, 'E': 0.017, 'SE': 0.061,
              'S': 1.081, 'SW': 0.298, 'W': 0.0, 'NW': 0.0}
    DARK   = {d: 0.0 for d in LD_DIRS}

    def test_dark_sky_returns_zero(self):
        """No domes → glow=0 at any az/alt."""
        for az in range(0, 360, 45):
            for alt in [0, 30, 60, 90]:
                assert arch_glow_at(self.DARK, az, alt) == pytest.approx(0.0, abs=1e-9)

    def test_horizon_equals_score(self):
        """At alt=0° the denominator is 1, so glow equals the interpolated score."""
        scores = {'S': 1.5, **{d: 0.0 for d in LD_DIRS if d != 'S'}}
        glow = arch_glow_at(scores, 180, 0)
        assert glow == pytest.approx(1.5, abs=1e-6)

    def test_characteristic_altitude_halves_glow(self):
        """At alt=40° (the characteristic altitude), glow = score / 2."""
        scores = {'S': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'S'}}
        glow = arch_glow_at(scores, 180, 40)
        assert glow == pytest.approx(0.5, abs=1e-6)

    def test_high_altitude_strongly_attenuated(self):
        """At alt=80°, glow = score / (1 + (80/40)²) = score / 5."""
        scores = {'S': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'S'}}
        glow = arch_glow_at(scores, 180, 80)
        assert glow == pytest.approx(1.0 / 5.0, abs=1e-6)

    def test_monotone_decreasing_with_altitude(self):
        """Glow must decrease strictly as altitude rises (for constant az/score)."""
        scores = {'E': 2.0, **{d: 0.0 for d in LD_DIRS if d != 'E'}}
        prev = arch_glow_at(scores, 90, 0)
        for alt in [10, 20, 40, 60, 80, 90]:
            curr = arch_glow_at(scores, 90, alt)
            assert curr < prev, f"glow not decreasing at alt={alt}: {curr:.4f} >= {prev:.4f}"
            prev = curr

    def test_negative_altitude_clamped_to_zero(self):
        """Negative alt values (below horizon) clamp to 0, same as horizon."""
        scores = {'N': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'N'}}
        assert arch_glow_at(scores, 0, -10) == pytest.approx(arch_glow_at(scores, 0, 0), abs=1e-9)

    def test_cardinal_direction_no_bleeding(self):
        """A pure S dome (score=1.0) at az=0° (N) should produce ~0 glow."""
        scores = {'S': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'S'}}
        glow = arch_glow_at(scores, 0, 30)
        assert glow == pytest.approx(0.0, abs=1e-9)

    def test_sedona_phoenix_dome_core(self):
        """Sedona/AZ: Phoenix dome S score=1.081, core at az=180° alt=27°.

        glow = 1.081 / (1 + (27/40)²) ≈ 0.743
        This was ~0.00175 with the old glowToward formula (dome_height=1.69°),
        which produced no visible washout. The fix makes washout meaningful.
        """
        glow = arch_glow_at(self.SEDONA, 180, 27)
        assert glow == pytest.approx(0.743, abs=0.002)

    def test_sedona_core_washout_significant(self):
        """With Sedona/AZ dome, core washoutFactor must be ≤ 0.60 (≥40% reduction)."""
        glow = arch_glow_at(self.SEDONA, 180, 27)
        W = washout_factor(glow)
        assert W <= 0.60, f"washout too weak: W={W:.3f} (glow={glow:.3f})"

    def test_sedona_anticenter_barely_affected(self):
        """Anticenter is opposite the dome; glow at az=0°/alt=83° should be near-zero."""
        glow = arch_glow_at(self.SEDONA, 0, 83)
        assert glow < 0.05, f"anticenter over-washed: glow={glow:.3f}"

    def test_diagonal_interpolation(self):
        """az=135° (exactly SE) with SE=1.0, all others 0 → score=1.0 at horizon."""
        scores = {'SE': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'SE'}}
        glow = arch_glow_at(scores, 135, 0)
        assert glow == pytest.approx(1.0, abs=1e-6)

    def test_interpolation_midpoint(self):
        """az=22.5° (midpoint between N=0° and NE=45°) with N=1, NE=1 → score=1.0."""
        scores = {'N': 1.0, 'NE': 1.0, **{d: 0.0 for d in LD_DIRS if d not in ('N', 'NE')}}
        glow = arch_glow_at(scores, 22.5, 0)
        assert glow == pytest.approx(1.0, abs=1e-6)

    def test_az_wraps_at_360(self):
        """az=360° is identical to az=0° (both are due North)."""
        scores = {'N': 1.0, **{d: 0.0 for d in LD_DIRS if d != 'N'}}
        assert arch_glow_at(scores, 360, 30) == pytest.approx(arch_glow_at(scores, 0, 30), abs=1e-9)
