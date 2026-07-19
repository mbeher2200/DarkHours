#!/usr/bin/env python3
"""
Calibration verification grid: legacy simplified-K&S vs Winkler-patched model.

Compares Δ mag/arcsec² and severity buckets across an illumination × separation
× moon-altitude × target-altitude grid at reference AOD (aod=None), plus the
ks_moon_credit curve.  Gate (docs in plan / memory note):

  (a) credit endpoints unchanged (bit-identical by _KS_NORM construction);
  (b) severity bucket flips in the calibrated regime — sep ≥ 45°, moon alt
      ≥ 30°, target alt = 45° — affect < 10% of those cells and never jump
      two buckets.  The 0.10/0.50/1.50 thresholds were tuned with NO
      target-altitude dimension (legacy model had none; every severity
      consumer uses the 45° default), so the tuned geometry is talt = 45°.
  (c) target alt 20°/70° rows show the NEW slant-path physics the legacy
      model could not represent, and near-moon (sep ≤ 20°) / low-moon
      (alt ≤ 15°) brightening is the Mie aureole + finite-horizon
      pathlength fix — both reported for documentation, not gated.

Run: .venv/bin/python scripts/verify_moonwash_grid.py
Exits non-zero if the gate fails.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darkhours import moonlight as m  # noqa: E402

# --- frozen legacy model (verbatim pre-Winkler ks_delta_mag) -----------------

_LEGACY_K_EXT = 0.172


def ks_delta_mag_legacy(illumination_pct, sep_deg, moon_alt_deg,
                        sky_sqm=21.6, moon_earth_dist_km=384_400.0):
    if illumination_pct <= 0 or moon_alt_deg <= 0:
        return 0.0
    illum  = illumination_pct / 100.0
    alpha  = math.degrees(math.acos(max(-1.0, min(1.0, 2.0 * illum - 1.0))))
    V_moon = -12.73 + 0.026 * alpha + 4e-9 * alpha**4
    I_moon = 10 ** (-0.4 * (V_moon + 16.57))
    I_moon *= (384_400.0 / moon_earth_dist_km) ** 2
    alt    = max(1.0, moon_alt_deg)
    X_moon = 1.0 / math.cos(math.radians(90.0 - alt))
    ext    = 10 ** (-0.4 * _LEGACY_K_EXT * X_moon)
    rho     = max(0.1, sep_deg)
    rho_rad = math.radians(rho)
    if rho > 10.0:
        f_rho = 10 ** 5.36 * (1.06 + math.cos(rho_rad) ** 2)
    else:
        f_rho = 6.2e7 / rho ** 2
    I_scatter = f_rho * ext * I_moon
    I_sky     = 10 ** ((27.78 - sky_sqm) / 2.5)
    return 2.5 * math.log10(1.0 + I_scatter / I_sky)


def severity(delta):
    if delta < 0.10:
        return "none"
    if delta < 0.50:
        return "minor"
    if delta < 1.50:
        return "moderate"
    return "severe"


_BUCKETS = ["none", "minor", "moderate", "severe"]

ILLUMS      = [5, 15, 30, 50, 75, 100]
SEPS        = [5, 10, 20, 45, 90, 120]
MOON_ALTS   = [5, 15, 30, 60]
TARGET_ALTS = [20, 45, 70]


def main():
    confusion = {}          # (old_bucket, new_bucket) -> count in calibrated regime
    calib_cells = 0
    calib_flips = 0
    two_bucket_jumps = []
    exempt_rows = []        # near-moon / low-moon documentation

    print(f"{'illum':>5} {'sep':>4} {'malt':>4} {'talt':>4} "
          f"{'d_old':>7} {'d_new':>7} {'diff':>7}  old->new")
    for il in ILLUMS:
        for sep in SEPS:
            for malt in MOON_ALTS:
                for talt in TARGET_ALTS:
                    d_old = ks_delta_mag_legacy(il, sep, malt)
                    d_new = m.ks_delta_mag(il, sep, malt, target_alt_deg=talt)
                    b_old, b_new = severity(d_old), severity(d_new)
                    calibrated = sep >= 45 and malt >= 30 and talt == 45
                    exempt = sep <= 20 or malt <= 15 or talt != 45
                    marker = ""
                    if calibrated:
                        calib_cells += 1
                        confusion[(b_old, b_new)] = confusion.get((b_old, b_new), 0) + 1
                        if b_old != b_new:
                            calib_flips += 1
                            marker = "  <-- FLIP (calibrated regime)"
                            jump = abs(_BUCKETS.index(b_old) - _BUCKETS.index(b_new))
                            if jump >= 2:
                                two_bucket_jumps.append((il, sep, malt, talt, b_old, b_new))
                    elif exempt and b_old != b_new:
                        exempt_rows.append((il, sep, malt, talt, d_old, d_new, b_old, b_new))
                        marker = "  (exempt: new physics — slant path / aureole)"
                    print(f"{il:>5} {sep:>4} {malt:>4} {talt:>4} "
                          f"{d_old:>7.3f} {d_new:>7.3f} {d_new-d_old:>+7.3f}  "
                          f"{b_old}->{b_new}{marker}")

    print("\n--- ks_moon_credit curve (must be identical) ---")
    max_credit_diff = 0.0
    for il in range(0, 101, 5):
        d_anchor_old = ks_delta_mag_legacy(il, 90.0, 30.0)
        credit_old = max(0.0, 1.0 - d_anchor_old / 1.50)
        credit_new = m.ks_moon_credit(il)
        max_credit_diff = max(max_credit_diff, abs(credit_old - credit_new))
        print(f"  illum {il:>3}%  old {credit_old:.6f}  new {credit_new:.6f}")

    print("\n--- severity confusion matrix (calibrated regime: sep>=45, moon alt>=30, talt=45) ---")
    for bo in _BUCKETS:
        row = "  ".join(f"{confusion.get((bo, bn), 0):>4}" for bn in _BUCKETS)
        print(f"  old {bo:>8} -> {row}")

    flip_pct = 100.0 * calib_flips / calib_cells if calib_cells else 0.0
    print(f"\ncalibrated cells: {calib_cells}, flips: {calib_flips} ({flip_pct:.1f}%)")
    print(f"two-bucket jumps in calibrated regime: {len(two_bucket_jumps)}")
    print(f"max ks_moon_credit diff: {max_credit_diff:.2e}")
    print(f"exempt-region bucket changes (documented, not gated): {len(exempt_rows)}")

    ok = (max_credit_diff < 1e-12) and (flip_pct < 10.0) and not two_bucket_jumps
    print("\nGATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
