#!/usr/bin/env python3
"""
Prototype: weather- and moon-aware 'best time' scoring for the MW arch window.

Compares the current altitude-only algorithm against a combined
altitude × moon × weather score across 6 synthetic scenarios.

Usage:  python scripts/best_time_proto.py [--json]
"""
from __future__ import annotations
import json, math, sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# ── Tuning constants ──────────────────────────────────────────────────────────
CHAR_ALT_DEG    = 40.0   # moon glow characteristic altitude (mirrors archGlowAt JS)
K_MOON          = 1.5    # penalty exponent: exp(-K × glow). 1.5 chosen so that
                         #   full moon at 40° (glow=0.5) → 47% penalty, not crushing
                         #   full moon near horizon (glow≈1.0) → 78% penalty
PHASE_GAMMA     = 1.8    # K&S phase non-linearity: full moon is ~10× brighter than
                         #   quarter moon in sky illumination (not 2× as linear suggests).
                         #   (illum/100)^1.8 maps 50% → 0.29 vs linear 0.50, matching
                         #   the measured ~8–12× full/quarter ratio.
MAX_DEPRESSION  = 5.0    # degrees below horizon a FULL moon still glows appreciably.
                         #   Scales linearly with phase: quarter moon ≈ 2.5°, crescent ≈ 0°.
STEP_MIN        = 15     # sample resolution (minutes)

UTC = timezone.utc


# ── Sample & scoring ──────────────────────────────────────────────────────────

@dataclass
class Sample:
    t:          datetime
    core_alt:   float   # galactic core altitude, °
    moon_alt:   float   # moon altitude, ° (≤ 0 = below horizon)
    moon_illum: float   # illumination 0–100
    cloud:      float   # cloud fraction 0–1  (0 = clear)


def moon_glow(s: Sample) -> float:
    """Sky glow from moon — two refinements over the naive version:

    1. Phase non-linearity (K&S): brightness ∝ (illum/100)^PHASE_GAMMA.
       Full moon is ~10× brighter than quarter, not 2×.

    2. Post-moonset fade: glow doesn't snap to zero at the horizon.
       A full moon is still appreciable ~5° below; that depth scales
       linearly with illumination so a crescent cuts off at the horizon.
    """
    illum_frac   = s.moon_illum / 100.0
    max_dep      = MAX_DEPRESSION * illum_frac          # phase-scaled depression limit
    if s.moon_alt <= -max_dep:
        return 0.0
    # Fade linearly from 1.0 at horizon to 0.0 at max depression
    fade    = (s.moon_alt + max_dep) / max_dep if s.moon_alt < 0 else 1.0
    eff_alt = max(s.moon_alt, 0.0)                     # clamp for altitude denominator
    phase_b = illum_frac ** PHASE_GAMMA                # K&S non-linear phase brightness
    return fade * phase_b / (1.0 + (eff_alt / CHAR_ALT_DEG) ** 2)


def score_sample(s: Sample, max_alt: float) -> dict:
    alt_s  = s.core_alt / max_alt if max_alt > 0 else 0.0
    glow   = moon_glow(s)
    moon_s = math.exp(-K_MOON * glow)
    wx_s   = max(0.0, 1.0 - s.cloud)
    total  = alt_s * moon_s * wx_s
    return dict(alt_s=alt_s, glow=glow, moon_s=moon_s, wx_s=wx_s, total=total)


# ── Scenario builders ─────────────────────────────────────────────────────────

def _bell(t_start: datetime, t_peak: datetime, t_end: datetime,
          peak_alt: float, base_alt: float = 2.0):
    """Piecewise-linear bell: rises to peak, then descends."""
    rise = (t_peak  - t_start).total_seconds()
    fall = (t_end   - t_peak ).total_seconds()
    def fn(t: datetime) -> float:
        dt = (t - t_start).total_seconds()
        if dt <= rise:
            f = dt / rise if rise else 0.0
        else:
            f = 1.0 - (dt - rise) / fall if fall else 0.0
        return base_alt + (peak_alt - base_alt) * max(0.0, min(1.0, f))
    return fn


def _linear(t0: datetime, v0: float, t1: datetime, v1: float, clamp: bool = True):
    span = (t1 - t0).total_seconds()
    def fn(t: datetime) -> float:
        f = (t - t0).total_seconds() / span if span else 0.0
        if clamp:
            f = max(0.0, min(1.0, f))
        return v0 + (v1 - v0) * f
    return fn


def _const(v: float):
    return lambda _t: v


def _samples(t_start, t_end, alt_fn, moon_alt_fn, illum, cloud_fn) -> list[Sample]:
    out, t = [], t_start
    while t <= t_end:
        out.append(Sample(t=t, core_alt=alt_fn(t),
                          moon_alt=moon_alt_fn(t), moon_illum=illum,
                          cloud=cloud_fn(t)))
        t += timedelta(minutes=STEP_MIN)
    return out


def _hm(t: datetime) -> str:
    return t.strftime("%-I:%M %p")


# ── Six scenarios ─────────────────────────────────────────────────────────────

def build_scenarios() -> list[dict]:
    # All start at 23:00 UTC, end 04:00 UTC (5-hour window)
    T0  = datetime(2026, 6, 22,  0, 0, tzinfo=UTC)   # midnight
    T1  = T0 + timedelta(hours=5)                      # 5 AM

    # Core altitude bell: peaks at T0+90 min = 01:30 UTC, 40° alt
    PEAK_T   = T0 + timedelta(hours=1, minutes=30)
    ALT_BELL = _bell(T0, PEAK_T, T1, peak_alt=40.0)

    scenarios = []

    # ── S1: Dark sky, perfect weather (baseline) ─────────────────────────────
    scenarios.append(dict(
        name   = "Dark sky, clear all night",
        label  = "Baseline",
        samples = _samples(T0, T1, ALT_BELL,
                           _const(-1.0),   # moon below horizon
                           illum=0, cloud_fn=_const(0.0)),
        note   = "No moon, no clouds — altitude wins",
    ))

    # ── S2: Quarter moon setting mid-window ──────────────────────────────────
    # Moon descends from 18° at midnight to 0° at 2:00 AM (-9°/hr).
    # Continues below horizon to T1 so post-moonset fade can operate.
    MOON_SET_2 = T0 + timedelta(hours=2)   # moon sets at 02:00
    scenarios.append(dict(
        name   = "Quarter moon (50%) setting 2 AM",
        label  = "Low moon",
        samples = _samples(T0, T1, ALT_BELL,
                           _linear(T0, 18.0, T1, -27.0, clamp=False),  # -9°/hr × 5h
                           illum=50, cloud_fn=_const(0.0)),
        note   = "Moon pushes best time past 2 AM",
    ))

    # ── S3: Gibbous moon setting late ────────────────────────────────────────
    # Moon descends from 28° at midnight to 0° at 2:45 AM (-10.18°/hr).
    # Continues below horizon so post-moonset fade can operate.
    MOON_SET_3 = T0 + timedelta(hours=2, minutes=45)   # moon sets 02:45
    scenarios.append(dict(
        name   = "Gibbous moon (80%) setting 2:45 AM",
        label  = "Bright moon",
        samples = _samples(T0, T1, ALT_BELL,
                           _linear(T0, 28.0, T1, -22.9, clamp=False),  # -10.18°/hr × 5h
                           illum=80, cloud_fn=_const(0.0)),
        note   = "Bright moon causes large shift",
    ))

    # ── S4: Cloudy early, clearing late ──────────────────────────────────────
    scenarios.append(dict(
        name   = "Cloudy early (70%), clearing by 3 AM",
        label  = "Late clear",
        samples = _samples(T0, T1, ALT_BELL,
                           _const(-1.0),
                           illum=0, cloud_fn=_linear(T0, 0.70, T0+timedelta(hours=3), 0.05)),
        note   = "Weather forces a trade-off vs. altitude",
    ))

    # ── S5: Clear early, deteriorating ───────────────────────────────────────
    scenarios.append(dict(
        name   = "Clear early, socking in (70%) by 3 AM",
        label  = "Worsening",
        samples = _samples(T0, T1, ALT_BELL,
                           _const(-1.0),
                           illum=0, cloud_fn=_linear(T0, 0.05, T0+timedelta(hours=3), 0.70)),
        note   = "New picks the clear window before clouds",
    ))

    # ── S6: Gibbous moon + clearing (competing effects) ──────────────────────
    # Moon descends from 25° at midnight to 0° at 2:00 AM (-12.5°/hr).
    # Continues below horizon so post-moonset fade can operate.
    MOON_SET_6 = T0 + timedelta(hours=2)
    scenarios.append(dict(
        name   = "Gibbous moon (70%) + cloudy-to-clear",
        label  = "Competing",
        samples = _samples(T0, T1, ALT_BELL,
                           _linear(T0, 25.0, T1, -37.5, clamp=False),  # -12.5°/hr × 5h
                           illum=70,
                           cloud_fn=_linear(T0, 0.55, T0+timedelta(hours=3, minutes=30), 0.08)),
        note   = "Moon and clouds both push later; altitude pulls earlier",
    ))

    return scenarios


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyse(scenario: dict) -> dict:
    samples  = scenario["samples"]
    max_alt  = max(s.core_alt for s in samples)
    old_best = max(samples, key=lambda s: s.core_alt)
    new_best = max(samples, key=lambda s: score_sample(s, max_alt)["total"])
    old_sc   = score_sample(old_best, max_alt)
    new_sc   = score_sample(new_best, max_alt)
    shift    = (new_best.t - old_best.t).total_seconds() / 60

    # Per-30-min detail table
    detail = []
    for s in samples:
        if s.t.minute % 30 == 0:
            sc = score_sample(s, max_alt)
            detail.append(dict(
                time     = _hm(s.t),
                core_alt = round(s.core_alt, 1),
                moon_alt = round(s.moon_alt, 1) if s.moon_alt > -MAX_DEPRESSION else None,
                cloud_pct= round(s.cloud * 100),
                alt_s    = round(sc["alt_s"],  2),
                moon_s   = round(sc["moon_s"], 2),
                wx_s     = round(sc["wx_s"],   2),
                total    = round(sc["total"],  3),
                is_old   = s.t == old_best.t,
                is_new   = s.t == new_best.t,
            ))

    return dict(
        name    = scenario["name"],
        label   = scenario["label"],
        note    = scenario["note"],
        old_t   = _hm(old_best.t),
        new_t   = _hm(new_best.t),
        shift_m = round(shift),
        old_alt = round(old_best.core_alt, 1),
        new_alt = round(new_best.core_alt, 1),
        old_moon_s = round(old_sc["moon_s"], 2),
        new_moon_s = round(new_sc["moon_s"], 2),
        old_wx_s   = round(old_sc["wx_s"],   2),
        new_wx_s   = round(new_sc["wx_s"],   2),
        old_total  = round(old_sc["total"],   3),
        new_total  = round(new_sc["total"],   3),
        detail  = detail,
    )


def main():
    as_json = "--json" in sys.argv
    scenarios = build_scenarios()
    results   = [analyse(s) for s in scenarios]

    if as_json:
        print(json.dumps(results, indent=2))
        return

    # ── Pretty-print summary table ────────────────────────────────────────────
    W = 110
    print("=" * W)
    print("MW Best-Time Algorithm: Altitude-Only  vs  Altitude × Moon × Weather")
    print(f"  Moon penalty model: exp(−{K_MOON} × glow),  glow = illum/100 / (1+(alt/{CHAR_ALT_DEG}°)²)")
    print("=" * W)
    hdr = f"{'Scenario':<38} {'Old time':>8} {'New time':>8} {'Shift':>7}  {'Old alt':>7} {'New alt':>7}  {'Old moon':>9} {'New moon':>9}  {'Old wx':>7} {'New wx':>7}  {'Old Σ':>7} {'New Σ':>7}"
    print(hdr)
    print("-" * W)
    for r in results:
        shift_str = f"+{r['shift_m']} min" if r['shift_m'] > 0 else (f"{r['shift_m']} min" if r['shift_m'] < 0 else "  0 min")
        print(f"{r['name']:<38} {r['old_t']:>8} {r['new_t']:>8} {shift_str:>7}  "
              f"{r['old_alt']:>6.1f}° {r['new_alt']:>6.1f}°  "
              f"{r['old_moon_s']:>9.2f} {r['new_moon_s']:>9.2f}  "
              f"{r['old_wx_s']:>7.2f} {r['new_wx_s']:>7.2f}  "
              f"{r['old_total']:>7.3f} {r['new_total']:>7.3f}")

    # ── Per-scenario detail ───────────────────────────────────────────────────
    for r in results:
        print()
        print(f"  ── {r['name']} ({r['note']}) ──")
        print(f"  {'Time':<9} {'CoreAlt':>7} {'MoonAlt':>8} {'Cloud%':>7}  {'AltS':>5} {'MoonS':>6} {'WxS':>5} {'Total':>6}")
        for row in r["detail"]:
            marker = ""
            if row["is_old"] and row["is_new"]:
                marker = "◀▶ old=new"
            elif row["is_old"]:
                marker = "◀  old best"
            elif row["is_new"]:
                marker = " ▶ new best"
            moon_str = f"{row['moon_alt']:>6.1f}°" if row["moon_alt"] is not None else "  below"
            print(f"  {row['time']:<9} {row['core_alt']:>6.1f}° {moon_str:>8} {row['cloud_pct']:>6}%  "
                  f"{row['alt_s']:>5.2f} {row['moon_s']:>6.2f} {row['wx_s']:>5.2f} {row['total']:>6.3f}  {marker}")

    print()
    print("=" * W)
    print(f"K_MOON={K_MOON}  CHAR_ALT={CHAR_ALT_DEG}°  step={STEP_MIN} min")
    print()


if __name__ == "__main__":
    main()
