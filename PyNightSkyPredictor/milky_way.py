#!/usr/bin/env python3
"""
Galactic coordinate helpers and Milky Way arch synthesis.

Public API
----------
gal_to_radec(l_deg, b_deg) -> (ra_hours, dec_deg)
    IAU galactic coordinates → ICRS equatorial J2000.

mw_max_visible(lat) -> int
    Maximum number of the 10 standard MW waypoints ever above 10° from lat.

mw_theoretical_core_max(lat) -> float
    Theoretical maximum altitude the galactic core can reach from lat.

milky_way_arch_summary(mw_targets, lat, moonrise, moonset,
                       moon_illumination_pct) -> dict | None
    Synthesise arch visibility, timing, and quality score from pre-computed
    VisibleTarget objects.  Returns None when the Galactic Core is absent.
"""

import math
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Galactic coordinate conversion
# ---------------------------------------------------------------------------

# IAU (1958) galactic ↔ ICRS rotation matrix, J2000 epoch.
# Each column is the ICRS unit vector of the galactic x/y/z axis:
#   x → galactic centre  (l=0°,  b=0°)
#   y → l=90°,           b=0°
#   z → galactic north pole (b=90°)
# Reference: Blaauw et al. 1960; Murray 1989.
_GAL_TO_ICRS = [
    [-0.0548755604, +0.4941094279, -0.8676661490],
    [-0.8734370902, -0.4448296300, -0.1980763734],
    [-0.4838350155, +0.7469822445, +0.4559837762],
]


def gal_to_radec(l_deg: float, b_deg: float) -> tuple[float, float]:
    """Convert galactic (l, b) to ICRS equatorial (RA hours, Dec degrees), J2000."""
    l = math.radians(l_deg)
    b = math.radians(b_deg)
    xg = math.cos(b) * math.cos(l)
    yg = math.cos(b) * math.sin(l)
    zg = math.sin(b)
    R  = _GAL_TO_ICRS
    xi = R[0][0]*xg + R[0][1]*yg + R[0][2]*zg
    yi = R[1][0]*xg + R[1][1]*yg + R[1][2]*zg
    zi = R[2][0]*xg + R[2][1]*yg + R[2][2]*zg
    dec = math.degrees(math.asin(max(-1.0, min(1.0, zi))))
    ra  = math.atan2(yi, xi)
    if ra < 0:
        ra += 2 * math.pi
    return ra * 12 / math.pi, dec   # (RA hours, Dec degrees)


def _arch_angle(alt1: float, az1: float, alt2: float, az2: float) -> float:
    """
    Angle of the galactic plane at point 1, relative to the horizon (degrees).

    Projects points onto the tangent plane at point 1 using a local
    East-Up coordinate system, then returns arctan(|Δup| / |Δeast|).
      0°  → band runs horizontally along the horizon
      90° → band rises straight up (ideal arch)

    Valid for angular separations < ~40°.
    """
    daz = az2 - az1
    if daz >  180: daz -= 360
    if daz < -180: daz += 360
    dx = daz * math.cos(math.radians(alt1))   # East component
    dy = alt2 - alt1                            # Up component
    return math.degrees(math.atan2(abs(dy), abs(dx)))


# ---------------------------------------------------------------------------
# Milky Way arch synthesis
# ---------------------------------------------------------------------------

# Fourteen waypoints covering the full galactic circle at approximately even spacing.
# The southern gap (Puppis→Scorpius) is now filled with Vela, Carina, Crux, and Norma,
# making the southern-hemisphere arch as complete as the northern one.
_MW_WAYPOINT_ORDER = [
    "Galactic Core",           # l=0°,   b=0°  dec -28.9°  summer anchor / nuclear bulge
    "Scutum Star Cloud",       # l=27°,  b=-3° dec  -6.6°  Scutum-Centaurus arm
    "Aquila Rift",             # l=45°,  b=0°  dec +10.7°  dark dust lane splitting the band
    "Cygnus Star Cloud",       # l=80°,  b=+1° dec +41.3°  northern arch peak
    "Cepheus Cloud",           # l=105°, b=+3° dec +60.4°  NH circumpolar band
    "Cassiopeia/Perseus",      # l=135°, b=-1° dec +59.6°  Double Cluster region
    "Galactic Anticenter",     # l=180°, b=0°  dec +28.9°  Auriga/Taurus
    "Monoceros",               # l=210°, b=-2° dec  +1.7°  winter southern band
    "Puppis Star Cloud",       # l=245°, b=0°  dec -28.2°  southern arch peak ↔ Cygnus
    "Vela Supernova Region",   # l=265°, b=-3° dec -46.5°  Gum Nebula / Vela SNR
    "Carina Nebula & Cloud",   # l=287°, b=-1° dec -59.7°  Eta Carinae; SH showpiece
    "Crux & Coalsack",         # l=302°, b=0°  dec -62.9°  Southern Cross + Coal Sack
    "Norma Star Cloud",        # l=330°, b=-2° dec -53.9°  dense cloud toward core
    "Scorpius Star Cloud",     # l=347°, b=-2° dec -40.9°  NGC 6231 region; Core→Norma bridge
]

# Approximate galactic-core declination for theoretical-max calculations.
_GALACTIC_CORE_DEC = -29.0


def mw_max_visible(lat: float) -> int:
    """
    Return the maximum number of MW waypoints that can ever be visible
    (peak altitude ≥ 10°) from the given latitude.

    Uses the nominal declinations of the 14 catalog waypoints.
    """
    # Nominal decs from gal_to_radec at each l, b=0 (pre-computed)
    # Order matches _MW_WAYPOINT_ORDER
    _WAYPOINT_DECS = [
        -28.9,  # Galactic Core          l=0°,   b=0°
         -6.6,  # Scutum Star Cloud      l=27°,  b=-3°
        +10.7,  # Aquila Rift            l=45°,  b=0°
        +41.3,  # Cygnus Star Cloud      l=80°,  b=+1°
        +60.4,  # Cepheus Cloud          l=105°, b=+3°
        +59.6,  # Cassiopeia/Perseus     l=135°, b=-1°
        +28.9,  # Galactic Anticenter    l=180°, b=0°
         +1.7,  # Monoceros              l=210°, b=-2°
        -28.2,  # Puppis Star Cloud      l=245°, b=0°
        -46.5,  # Vela Supernova Region  l=265°, b=-3°
        -59.7,  # Carina Nebula & Cloud  l=287°, b=-1°
        -62.9,  # Crux & Coalsack        l=302°, b=0°
        -53.9,  # Norma Star Cloud       l=330°, b=-2°
        -40.9,  # Scorpius Star Cloud    l=347°, b=-2°
    ]
    return sum(1 for dec in _WAYPOINT_DECS if 90 - abs(lat - dec) >= 10)


def mw_theoretical_core_max(lat: float) -> float:
    """Theoretical maximum altitude the galactic core can reach from this latitude."""
    return max(0.0, 90.0 - abs(lat - _GALACTIC_CORE_DEC))


# ---------------------------------------------------------------------------
# Best-viewing-time scoring + arch condition rules
# ---------------------------------------------------------------------------

# K&S-calibrated moon glow constants (mirrors the JS archGlowAt / best_time_proto.py).
# BT_K_MOON and BT_STEP_MIN are public — imported by predictor.py for per-window scoring.
_BT_CHAR_ALT    = 40.0   # characteristic altitude for moon glow (°)
BT_K_MOON       = 1.5    # penalty exponent: exp(-K × glow)
_BT_PHASE_GAMMA = 1.8    # K&S non-linearity: full moon ≈ 10× brighter than quarter
_BT_MAX_DEP     = 5.0    # full-moon glow lingers up to 5° below horizon; scales with phase
BT_STEP_MIN     = 15     # sample resolution (minutes)

# Arch-level condition constants (mirrors predictor.py / moonlight.py values).
_ARCH_CLOUD_BLOCK_PCT = 70    # cloud_cover_pct > this → arch sample is blocked
_ARCH_WEATHER_GAP_S   = 5400  # 90 min nearest-neighbour tolerance for weather lookup
_ARCH_WASHOUT_RADIUS  = 45.0  # degrees at 100 % illum for moon-washout check
_ARCH_CRESCENT_PCT    = 20.0  # illum below this → no moon washout (mirrors KS_CRESCENT_EXEMPTION_PCT)


def bt_moon_glow(moon_alt_deg: float, illum_frac: float) -> float:
    """Moon sky glow with K&S phase correction and post-moonset fade (0–1 scale)."""
    max_dep = _BT_MAX_DEP * illum_frac
    if moon_alt_deg <= -max_dep:
        return 0.0
    fade    = (moon_alt_deg + max_dep) / max_dep if moon_alt_deg < 0 else 1.0
    eff_alt = max(moon_alt_deg, 0.0)
    phase_b = illum_frac ** _BT_PHASE_GAMMA
    return fade * phase_b / (1.0 + (eff_alt / _BT_CHAR_ALT) ** 2)


def _bt_core_alt(t: datetime, core_w) -> float:
    """Piecewise-linear core altitude interpolated from window endpoints and peak."""
    if t <= core_w.peak_time:
        span = (core_w.peak_time - core_w.start).total_seconds()
        if span <= 0:
            return core_w.peak_alt_deg
        frac = max(0.0, min(1.0, (t - core_w.start).total_seconds() / span))
        return core_w.start_alt_deg + (core_w.peak_alt_deg - core_w.start_alt_deg) * frac
    else:
        span = (core_w.end - core_w.peak_time).total_seconds()
        if span <= 0:
            return core_w.end_alt_deg
        frac = max(0.0, min(1.0, (t - core_w.peak_time).total_seconds() / span))
        return core_w.peak_alt_deg + (core_w.end_alt_deg - core_w.peak_alt_deg) * frac


def bt_interp_moon_alt(t: datetime, moon_alts: list) -> float:
    """Linear-interpolate moon altitude at t from a (datetime, alt_deg) list."""
    before = [(ts, a) for ts, a in moon_alts if ts <= t]
    after  = [(ts, a) for ts, a in moon_alts if ts > t]
    if not before:
        return after[0][1] if after else -90.0
    if not after:
        return before[-1][1]
    t0, a0 = before[-1]
    t1, a1 = after[0]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return a0
    return a0 + (a1 - a0) * (t - t0).total_seconds() / span


def bt_cloud_frac(t: datetime, weather_points: list) -> float:
    """Nearest-neighbour cloud fraction (0–1) at time t from WeatherPoint list."""
    if not weather_points:
        return 0.0
    nearest = min(weather_points, key=lambda p: abs((p.time - t).total_seconds()))
    if nearest.cloud_cover_pct is None:
        return 0.0
    return nearest.cloud_cover_pct / 100.0


def _best_viewing_time(
    arch_start: datetime,
    arch_end:   datetime,
    core_w,
    moon_illum_pct: float,
    moon_alts: list | None,
    weather_points: list | None,
) -> datetime:
    """
    Return the best viewing time within [arch_start, arch_end] using:
        score = alt_score × moon_score × weather_score

    Falls back to core geometric peak when moon_alts and weather_points are
    both absent (e.g. unit tests, historical data without weather).
    """
    if not moon_alts and not weather_points:
        peak = core_w.peak_time
        return max(arch_start, min(arch_end, peak))

    illum_frac = moon_illum_pct / 100.0
    max_alt    = core_w.peak_alt_deg

    best_t, best_score = arch_start, -1.0
    t = arch_start
    while t <= arch_end:
        core_alt = _bt_core_alt(t, core_w)
        alt_s    = core_alt / max_alt if max_alt > 0 else 0.0

        if moon_alts:
            moon_alt = bt_interp_moon_alt(t, moon_alts)
            glow     = bt_moon_glow(moon_alt, illum_frac)
        else:
            glow = 0.0
        moon_s = math.exp(-BT_K_MOON * glow)

        cloud  = bt_cloud_frac(t, weather_points) if weather_points else 0.0
        wx_s   = max(0.0, 1.0 - cloud)

        score  = alt_s * moon_s * wx_s
        if score > best_score:
            best_score = score
            best_t     = t

        t += timedelta(minutes=BT_STEP_MIN)

    return best_t


def milky_way_arch_summary(
    mw_targets: list,
    lat: float = 0.0,
    moonrise: datetime | None = None,
    moonset:  datetime | None = None,
    moon_illumination_pct: float = 0.0,
    moon_alts: list | None = None,
    weather_points: list | None = None,
) -> dict | None:
    """
    Synthesise a Milky Way visibility summary from pre-computed visible targets.

    mw_targets            — list of VisibleTarget objects with type == "milky_way".
    lat                   — observer latitude in decimal degrees (used for quality score).
    moonrise / moonset    — times of moonrise/moonset this night (UTC-aware datetimes).
    moon_illumination_pct — moon illumination percentage (0–100).
    moon_alts             — optional list of (utc_datetime, alt_deg) pairs sampled at
                            _BT_STEP_MIN intervals across the night; used for best-time
                            scoring.  When absent, best_viewing_time falls back to the
                            geometric peak.
    weather_points        — optional list of WeatherPoint objects (from weather.py);
                            used for best-time cloud scoring.

    When the moon is bright (≥ 25 %) the arch window is clipped to the moon-free
    period: arch_end is capped at moonrise (if moon rises during the window), and
    arch_start is advanced to moonset (if the moon is already up at window start).
    The clipped duration flows into win_frac, naturally lowering the quality score.

    Returns None if the Galactic Core is not visible tonight.

    Returned dict keys
    ------------------
    arch_start / arch_end    datetime  — moon-free arch window
    arch_hours               float     — arch window duration in hours
    moon_limited             bool      — True if window was clipped by moon
    n_visible                int       — waypoints with any visible window tonight
    n_max_possible           int       — max waypoints ever visible from this latitude
    n_total                  int       — total catalog waypoints (10)
    local_score              float     — 0–10 quality score relative to lat ceiling
    core_peak_time           datetime  — geometric altitude peak (used for dome rendering)
    best_viewing_time        datetime  — scored peak: alt × moon(K&S) × weather
    core_peak_alt_deg        int       — rounded altitude in degrees
    core_peak_az_deg         int       — rounded azimuth in degrees
    arch_angle_deg           float | None
    farthest_name            str | None  — highest-peaking far-side waypoint
    farthest_peak_alt_deg    int  | None
    farthest_peak_az_deg     int  | None
    """
    if not mw_targets:
        return None

    by_name = {t.name: t for t in mw_targets}
    core = by_name.get("Galactic Core")
    if core is None:
        return None

    def _best(target):
        clean = [w for w in target.windows if not w.moon_interference]
        pool  = clean if clean else target.windows
        return max(pool, key=lambda w: w.peak_alt_deg)

    core_w = _best(core)

    # Far-end waypoint: highest-peaking visible waypoint from either arm
    # (index ≥ 2 excludes Core and Scutum — both within 36° of the core,
    # so they represent the same visual section).  Highest altitude naturally
    # selects Cygnus from the NH and Puppis/Norma from the SH.
    _FAR_SIDE = set(_MW_WAYPOINT_ORDER[2:])
    far_candidates = [
        (by_name[n], _best(by_name[n]))
        for n in _FAR_SIDE if n in by_name
    ]
    if far_candidates:
        farthest, farthest_w = max(far_candidates, key=lambda x: x[1].peak_alt_deg)
    else:
        farthest, farthest_w = None, None

    # Arch window: core ∩ far-end simultaneously above horizon.
    # Falls back to the core window when there is no overlap.
    if farthest_w:
        arch_start = max(core_w.start, farthest_w.start)
        arch_end   = min(core_w.end,   farthest_w.end)
        if arch_start >= arch_end:
            arch_start, arch_end = core_w.start, core_w.end
    else:
        arch_start, arch_end = core_w.start, core_w.end

    # Clip to the moon-affected period using per-waypoint K&S photo cutoffs.
    #
    # arch_end: use the earliest photo_cutoff among all visible waypoints.
    #   If K&S ran but produced no cutoffs, the moon brightening never exceeded
    #   the contrast threshold — trust that result and leave arch_end unchanged.
    #   Only fall back to the blunt moonrise heuristic when K&S couldn't run
    #   (no surface_brightness data in the catalog).
    #
    # arch_start: still advanced to moonset when the moon is already up at the
    #   start of the window (moonset-during-window scenario).  Per-sample cutoffs
    #   only capture the end of the usable period, not the recovery after moonset.
    _MOON_ILLUM_THRESHOLD = 25.0
    moon_limited = False

    # Collect all photo_cutoffs from visible waypoints that fall inside the arch window
    all_photo_cutoffs = [
        w.photo_cutoff
        for t in mw_targets
        for w in t.windows
        if w.photo_cutoff is not None and arch_start < w.photo_cutoff < arch_end
    ]

    # Determine whether K&S was actually run for any waypoint window.
    # ks_ran = True  → K&S was computed; if no cutoffs resulted, the moon is not
    #                   degrading these waypoints enough to clip them → trust K&S,
    #                   do NOT fall back to the blunt moonrise heuristic.
    # ks_ran = False → no surface_brightness data; fall back to moonrise as before.
    ks_ran = any(
        w.ks_computed or w.photo_cutoff is not None
        for t in mw_targets
        for w in t.windows
    )

    if all_photo_cutoffs:
        arch_end     = min(all_photo_cutoffs)
        moon_limited = True
    elif not ks_ran and moon_illumination_pct >= _MOON_ILLUM_THRESHOLD:
        # Legacy fallback: only when K&S couldn't run (no SB catalog data).
        if moonrise and arch_start < moonrise < arch_end:
            arch_end     = moonrise
            moon_limited = True

    # Arch-start advance: when the moon is up at arch_start, K&S photo_start
    # marks the first sample where each waypoint becomes photo-viable again.
    # Use the latest such start across all visible waypoints — the arch is only
    # fully usable once every waypoint clears the moon-brightening threshold.
    # Fall back to the legacy moonset heuristic only when K&S couldn't run.
    all_photo_starts = [
        w.photo_start
        for t in mw_targets
        for w in t.windows
        if w.photo_start is not None
        and arch_start < w.photo_start < arch_end
    ]
    if all_photo_starts:
        arch_start   = max(all_photo_starts)
        moon_limited = True
    elif not ks_ran and moon_illumination_pct >= _MOON_ILLUM_THRESHOLD:
        # Legacy moonset advance fallback (no K&S data available)
        if moonset and arch_start < moonset < arch_end:
            arch_start   = moonset
            moon_limited = True

    arch_hours = max(0.0, (arch_end - arch_start).total_seconds() / 3600)

    # ── Weather viability for the arch window ────────────────────────────────
    # Nearest-neighbour cloud check at 15-min samples across [arch_start, arch_end].
    # The arch is wide-field (can't repoint), so we use a clear-fraction model
    # rather than the DSO/planet "find a contiguous clear block" approach.
    weather_blocked  = False
    weather_limited  = False
    clear_arch_hours = arch_hours

    if weather_points and arch_hours > 0:
        _step   = timedelta(minutes=BT_STEP_MIN)
        _t      = arch_start
        _total  = 0
        _clear  = 0
        while _t <= arch_end:
            _near = min(weather_points, key=lambda p: abs((p.time - _t).total_seconds()))
            if abs((_near.time - _t).total_seconds()) <= _ARCH_WEATHER_GAP_S:
                _total += 1
                if _near.cloud_cover_pct is None or _near.cloud_cover_pct <= _ARCH_CLOUD_BLOCK_PCT:
                    _clear += 1
            _t += _step
        if _total > 0:
            clear_arch_hours = arch_hours * (_clear / _total)
            if _clear == 0:
                weather_blocked = True
            elif _clear < _total:
                weather_limited = True

    # ── Moon washout for arch (galactic core too close to the moon) ──────────
    # Mirrors the per-window proximity check in predictor._apply_condition_vectors.
    arch_moon_washout = (
        moon_illumination_pct > _ARCH_CRESCENT_PCT
        and core_w.moon_sep_at_peak_deg is not None
        and core_w.moon_sep_at_peak_deg
            < _ARCH_WASHOUT_RADIUS * (moon_illumination_pct / 100.0)
    )

    # ── Latitude-relative quality score (0–10) ───────────────────────────────
    # How good is tonight compared to the best this latitude can ever offer?
    #   50% — core altitude vs theoretical ceiling for this lat
    #   30% — visible waypoints vs max possible from this lat
    #   20% — arch window hours (reference: 5 h = full marks)
    theo_max     = mw_theoretical_core_max(lat)
    n_max        = mw_max_visible(lat)
    alt_frac     = (core_w.peak_alt_deg / theo_max)  if theo_max > 0 else 0.0
    cov_frac     = (len(mw_targets) / n_max)         if n_max    > 0 else 0.0
    win_frac     = min(1.0, arch_hours / 5.0)
    # Moon penalty: direct interference OR moon-limited window.
    # Weather penalty: 0 when fully blocked, 0.7 when partially limited (same scale as moon).
    moon_penalised   = core_w.moon_interference or moon_limited
    moon_penalty     = 0.7 if moon_penalised else 1.0
    weather_penalty  = 0.0 if weather_blocked else (0.7 if weather_limited else 1.0)
    raw              = 0.50 * alt_frac + 0.30 * cov_frac + 0.20 * win_frac
    local_score      = round(min(10.0, raw * moon_penalty * weather_penalty * 10), 1)

    core_peak_in_window = arch_start <= core_w.peak_time <= arch_end

    best_viewing_time = _best_viewing_time(
        arch_start, arch_end, core_w,
        moon_illumination_pct, moon_alts, weather_points,
    )

    return {
        "arch_start":            arch_start,
        "arch_end":              arch_end,
        "arch_hours":            round(arch_hours, 1),
        "moon_limited":          moon_limited,
        "moon_penalised":        moon_penalised,
        "weather_blocked":       weather_blocked,
        "weather_limited":       weather_limited,
        "clear_arch_hours":      round(clear_arch_hours, 1),
        "arch_moon_washout":     arch_moon_washout,
        "n_visible":             len(mw_targets),
        "n_max_possible":        n_max,
        "n_total":               len(_MW_WAYPOINT_ORDER),
        "local_score":           local_score,
        "alt_score":             round(alt_frac * 10, 1),
        "cov_score":             round(cov_frac * 10, 1),
        "win_score":             round(win_frac * 10, 1),
        "core_peak_time":        core_w.peak_time,
        "best_viewing_time":     best_viewing_time,
        "core_peak_in_window":   core_peak_in_window,
        "core_peak_alt_deg":     round(core_w.peak_alt_deg),
        "core_peak_az_deg":      round(core_w.peak_az_deg),
        "arch_angle_deg":        core_w.arch_angle_deg,
        "farthest_name":         farthest.name if farthest else None,
        "farthest_peak_alt_deg": round(farthest_w.peak_alt_deg) if farthest_w else None,
        "farthest_peak_az_deg":  round(farthest_w.peak_az_deg)  if farthest_w else None,
        "core_moon_sep_deg":     getattr(core_w, "moon_sep_at_peak_deg", None),
        "core_moon_alt_deg":     getattr(core_w, "moon_alt_at_peak_deg", None),
    }
