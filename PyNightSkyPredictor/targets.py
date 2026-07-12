#!/usr/bin/env python3
"""Visible target computation for night sky planning."""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from skyfield.api import Loader, Star, load, wgs84

from . import config as _cfg
from . import milky_way as _mw
from . import moonlight as _ml

try:
    from skyfield.magnitudelib import planetary_magnitude as _planetary_magnitude
except ImportError:
    _planetary_magnitude = None  # Skyfield < 1.39; planet brightness filtering disabled

log = logging.getLogger(__name__)

# Galactic coordinate helpers live in milky_way.py; re-export for any
# callers that import gal_to_radec directly from targets.
from .milky_way import gal_to_radec


_c = _cfg.load()["targets"]
DEFAULT_MIN_ELEVATION  = float(_c["min_elevation_deg"])
DEFAULT_MOON_MIN_SEP   = float(_c["moon_min_separation_deg"])
DEFAULT_MOON_MAX_ILLUM = float(_c["moon_max_illumination_pct"])
SAMPLE_INTERVAL_MIN    = 10
PLANET_PRIME_MIN_ALT   = 20  # planets are bright enough to be prime at lower altitudes

# Landscape prominence thresholds
_SB_DIFFUSE_THRESHOLD    = 16.0   # mag/arcsec² — nebulae above this require narrowband filter
_GALAXY_SB_THRESHOLD     = 13.8   # mag/arcsec² — galaxy disk SB averaged over core+faint arms
_ANGULAR_SIZE_MIN_ARCMIN = 9.0    # below this, too compact for wide-field landscape

_CATALOG_PATH = Path(__file__).parent / "targets.json"

_PLANET_BODIES = {
    "mercury": "mercury",
    "venus":   "venus",
    "mars":    "mars barycenter",
    "jupiter": "jupiter barycenter",
    "saturn":  "saturn barycenter",
    "uranus":  "uranus barycenter",
    "neptune": "neptune barycenter",
}


@dataclass
class TargetWindow:
    start: datetime
    end: datetime
    start_alt_deg: float
    end_alt_deg: float
    peak_time: datetime
    peak_alt_deg: float
    peak_az_deg: float = 0.0
    moon_interference: bool = False
    arch_angle_deg: float | None = None        # milky_way only: plane angle from horizon
    moon_sep_at_peak_deg: float | None = None  # angular separation from moon at peak time
    moon_alt_at_peak_deg: float | None = None  # moon altitude at peak time (for K&S model)
    moon_wash_severity: "str | None" = None    # 'none'|'minor'|'moderate'|'severe' at peak (site SQM +
                                                 # AOD + slant path); None = peak geometry unavailable
    photo_cutoff: "datetime | None" = None     # last sample where astrophotography is viable
    visual_cutoff: "datetime | None" = None    # last sample where visual observation is viable
    photo_start: "datetime | None" = None      # first viable sample (set when moon delays window start)
    ks_computed: bool = False                  # True when K&S was run and the full window is viable
    # Phase 1: Condition Vectors (set by predictor._apply_condition_vectors)
    effective_start: "datetime | None" = None  # MCVI lower bound; UI binds displayed window here
    effective_end: "datetime | None" = None    # MCVI upper bound; condition-gated window end
    best_time: "datetime | None" = None        # recommended observation moment within effective window
    blockers: list = field(default_factory=list)  # ["cloud","transparency","light_dome","moon_washout","low_radiant"]
    weather_score_at_best: "int | None" = None    # rate_conditions() score at best_time
    dome_glow_at_peak: "float | None" = None      # glow_toward() at (peak_az_deg, peak_alt_deg)
    local_rate_at_peak: "float | None" = None     # meteor showers only: zhr_effective × sin(radiant_alt) × lm_factor;
                                                    # set by predictor._apply_condition_vectors; None for non-shower targets
    lm_factor_at_peak: "float | None" = None      # meteor showers only: r^(lm − 6.5) limiting-magnitude degradation
                                                    # from the moon-brightened site sky; 1.0 = pristine, None = not computed


@dataclass
class VisibleTarget:
    name: str
    type: str
    windows: list      # list[TargetWindow]
    note: str | None   # e.g. "3 days before peak" for meteor showers
    viability: str = "ok"  # "ok" | "degraded" | "blocked" — set by _apply_condition_vectors
    angular_size_arcmin: "float | None" = None   # from catalog; None for planets/meteors/MW
    landscape_suitability: str = "prominent"     # "prominent" | "diffuse" | "too_small"
    zhr_effective: "float | None" = None         # meteor showers only: day-decayed peak ZHR (IMO log-linear decay);
                                                   # set by _compute_target; None for non-shower types
    population_index: "float | None" = None      # meteor showers only: IMO magnitude-distribution index r;
                                                   # from catalog; drives the limiting-magnitude rate degradation


def _landscape_suitability(
    sb: "float | None",
    angular_size: "float | None",
    ttype: str = "",
) -> str:
    """
    Classify a target's suitability for wide-angle landscape astrophotography.

    Returns 'prominent' | 'diffuse' | 'too_small'.
    Galaxies use a tighter SB gate (13.8) than nebulae (16.0): catalog SB for
    face-on spirals averages the bright nucleus over the faint disk, so objects
    like M101 (14.8) or M33 (14.2) read acceptable in catalogs but are invisible
    noise in a stationary wide-field exposure.
    Objects with no angular_size (planets, meteor showers, milky_way) default to 'prominent'.
    """
    if angular_size is None:
        return "prominent"
    sb_threshold = _GALAXY_SB_THRESHOLD if ttype == "galaxy" else _SB_DIFFUSE_THRESHOLD
    if sb is not None and sb >= sb_threshold:
        return "diffuse"
    if angular_size < _ANGULAR_SIZE_MIN_ARCMIN:
        return "too_small"
    return "prominent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_load = Loader(str(Path(__file__).resolve().parent))


def _ephemeris():
    return _load("de421.bsp")


def _parse_ra(s: str) -> float:
    """'05h 35m 17s' → decimal hours."""
    parts = [p for p in re.split(r"[hms\s]+", s.strip()) if p]
    h   = float(parts[0])
    m   = float(parts[1]) if len(parts) > 1 else 0.0
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return h + m / 60 + sec / 3600


def _parse_dec(s: str) -> float:
    """'±DD° MM' SS"' → signed decimal degrees."""
    s    = s.strip()
    sign = -1 if s.startswith("-") else 1
    parts = [p for p in re.split(r"[°'\"°\s]+", s.lstrip("+-")) if p]
    d   = float(parts[0])
    m   = float(parts[1]) if len(parts) > 1 else 0.0
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return sign * (d + m / 60 + sec / 3600)


def _sky_object(entry: dict) -> Star:
    """Build a Skyfield Star from a catalog entry.

    Supports three coordinate formats:
      galactic_l / galactic_b  — converted via IAU rotation matrix (milky_way)
      ra / dec                 — standard equatorial J2000
      radiant_ra / radiant_dec — meteor shower radiants
    """
    if "galactic_l" in entry:
        ra_h, dec_d = gal_to_radec(entry["galactic_l"], entry.get("galactic_b", 0.0))
        return Star(ra_hours=ra_h, dec_degrees=dec_d)
    ra_key  = "ra"  if "ra"  in entry else "radiant_ra"
    dec_key = "dec" if "dec" in entry else "radiant_dec"
    return Star(ra_hours=_parse_ra(entry[ra_key]),
                dec_degrees=_parse_dec(entry[dec_key]))


def _make_window(alt_deg, az_deg, sample_dts, start_idx, end_idx):
    """Return (TargetWindow, [indices]) for a contiguous above-threshold segment."""
    indices     = list(range(start_idx, end_idx + 1))
    seg         = alt_deg[start_idx:end_idx + 1]
    peak_offset = int(np.argmax(seg))
    peak_idx    = start_idx + peak_offset
    return (
        TargetWindow(
            start=sample_dts[start_idx],
            end=sample_dts[end_idx],
            start_alt_deg=float(alt_deg[start_idx]),
            end_alt_deg=float(alt_deg[end_idx]),
            peak_time=sample_dts[peak_idx],
            peak_alt_deg=float(alt_deg[peak_idx]),
            peak_az_deg=float(az_deg[peak_idx]),
        ),
        indices,
    )


def _find_windows(alt_deg, az_deg, sample_dts: list, min_elev: float) -> list:
    """Return list of (TargetWindow, [indices]) for each above-threshold segment."""
    result    = []
    in_window = False
    start_idx = None

    for i, alt in enumerate(alt_deg):
        above = bool(alt >= min_elev)
        if above and not in_window:
            in_window = True
            start_idx = i
        elif not above and in_window:
            in_window = False
            result.append(_make_window(alt_deg, az_deg, sample_dts, start_idx, i - 1))

    if in_window:
        result.append(_make_window(alt_deg, az_deg, sample_dts, start_idx, len(sample_dts) - 1))

    return result


def _moon_interferes(sep_deg, moon_alt_deg, moon_dist_km, target_alt_deg,
                     window_indices: list, illumination_pct: float,
                     aod: "float | None" = None) -> bool:
    """True if the moon produces ≥ moderate sky brightening (Δμ ≥ 0.50) at any window sample.

    Uses the scattering model rather than a binary illumination/separation gate,
    so a 49%-illuminated moon is evaluated on the same physics as a 51% moon.
    Returns False immediately for new moon or if the moon is always below the horizon.
    """
    if not window_indices or illumination_pct <= 0:
        return False
    for i in window_indices:
        if _ml.ks_delta_mag(illumination_pct, float(sep_deg[i]),
                            float(moon_alt_deg[i]),
                            moon_earth_dist_km=float(moon_dist_km[i]),
                            aod=aod,
                            target_alt_deg=float(target_alt_deg[i])) >= _ml.KS_MODERATE_THRESH:
            return True
    return False


_ZHR_DECAY_FLOOR = 2.0  # meteors/hr — below this, indistinguishable from sporadic background


def _resolve_peak_year_offset(entry: dict, night_date) -> "tuple[int, int] | None":
    """Find the year_offset in (0, -1, 1) whose calendar peak date is nearest
    night_date (handles year-boundary showers: Quadrantids, Ursids). Returns
    (year_offset, delta_days), or None if peak_month/peak_day never form a
    valid date across all three offsets.

    The single source of truth for "which year's peak is this night closest
    to" — _days_from_peak() and _peak_datetime() both call this so they can
    never disagree about which peak instance a given night belongs to.
    """
    from datetime import date as _date

    peak_month = entry["peak_month"]
    peak_day   = entry["peak_day"]
    best = None  # (year_offset, delta)
    for year_offset in (0, -1, 1):
        try:
            peak  = _date(night_date.year + year_offset, peak_month, peak_day)
            delta = (night_date - peak).days
            if best is None or abs(delta) < abs(best[1]):
                best = (year_offset, delta)
        except ValueError:
            continue
    return best


def _days_from_peak(entry: dict, night_date) -> "float | None":
    """Signed day-delta from night_date to the nearest instance of this
    shower's peak. Extracted so decay math and note-text share one
    peak-finding implementation.
    """
    resolved = _resolve_peak_year_offset(entry, night_date)
    return resolved[1] if resolved else None


def _peak_datetime(entry: dict, night_date) -> "datetime | None":
    """UTC datetime of this shower's peak, for whichever year
    _resolve_peak_year_offset() picked for night_date — guaranteed
    consistent with _days_from_peak()'s choice of year for the same
    night_date. Returns None if the catalog entry has no peak_hour_utc yet
    (not all showers have been sourced with a peak time-of-day); callers
    should render a date-only peak in that case, not a fabricated time.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    if entry.get("peak_hour_utc") is None:
        return None
    resolved = _resolve_peak_year_offset(entry, night_date)
    if resolved is None:
        return None
    year_offset, _delta = resolved
    peak_month = entry["peak_month"]
    peak_day   = entry["peak_day"]
    return (
        _dt(night_date.year + year_offset, peak_month, peak_day, tzinfo=_tz.utc)
        + _td(hours=entry["peak_hour_utc"])
    )


def effective_zhr(peak_zhr: float, days_from_peak: float, b_rise: float, b_decline: float) -> float:
    """IMO/NASA-MEO-style asymmetric log-linear decay: ZHR(t) = peak_zhr * 10^(-B*|t|).

    b_rise applies before peak (t < 0), b_decline at/after peak (t >= 0) — real
    showers commonly rise faster approaching peak than they decline afterward.

    CAVEAT: the canonical IMO/meteor-science formulation uses solar longitude
    (λ☉) as the time axis, not calendar days. This module already keys every
    shower's peak off peak_month/peak_day (see _resolve_peak_year_offset), so
    using calendar-day offset here is a deliberate simplification consistent
    with the rest of the module. Solar longitude advances ~0.9856°/day on
    average, so B values (per degree of solar longitude) carry over to
    per-day within ~1.5% error most of the year — except near perihelion
    (early January), where Earth moves ~3.4% faster than the mean rate,
    relevant to Quadrantids (peak Jan 3) and marginally Ursids (peak Dec 22).
    """
    if peak_zhr <= 0:
        return 0.0
    b = b_rise if days_from_peak < 0 else b_decline
    return peak_zhr * (10 ** (-b * abs(days_from_peak)))


def meaningful_activity_half_window(peak_zhr: float, b_rise: float, b_decline: float,
                                     floor: float = _ZHR_DECAY_FLOOR) -> float:
    """Day-offset beyond which decayed ZHR drops below `floor` on either side —
    solves peak_zhr * 10^(-B*t) = floor for t, using min(b_rise, b_decline)
    (the shallower/slower-decaying side) so the window is generous in both
    directions. This is a pre-filter half-window, not a display value.
    """
    b_min = min(b_rise, b_decline)
    if peak_zhr <= floor or b_min <= 0:
        return 0.0
    return math.log10(peak_zhr / floor) / b_min


def _gate_half_window_days(entry: dict) -> float:
    """Cheap gate deciding whether a shower is worth computing tonight at all —
    used by both the fast path (active_meteor_showers) and the slow/geometry
    path (_compute_target's early exit via _meteor_shower_note).

    Takes MAX(curated active_window_days/2, decay-derived half-window) — not
    min() — because this is a performance pre-filter where a false negative
    (wrongly dropping a still-active shower) is worse than a false positive
    (computing geometry for a negligible one; cheap either way). Concretely:
    Taurids are hand-curated to 50-60 day windows because their activity is
    genuinely broad and low-ZHR; an imperfect b_rise/b_decline value should
    never be able to silently shrink that below the curated floor.
    """
    b_rise    = entry.get("b_rise", 0.0)
    b_decline = entry.get("b_decline", 0.0)
    half_curated = entry["active_window_days"] / 2
    half_decay   = meaningful_activity_half_window(entry.get("peak_zhr", 0), b_rise, b_decline)
    return max(half_curated, half_decay)


def _meteor_shower_note(entry: dict, night_date) -> str | None:
    """
    Return a proximity string (e.g. '3 days before peak') or None if the
    night is outside the shower's active window.

    Handles year-boundary showers (e.g. Quadrantids: peak Jan 3, active Dec–Jan)
    by trying adjacent years and picking the closest peak.
    """
    half = _gate_half_window_days(entry)
    best_delta = _days_from_peak(entry, night_date)

    if best_delta is None or abs(best_delta) > half:
        return None

    if best_delta == 0:
        return "Peak night"
    n = abs(best_delta)
    direction = "before" if best_delta < 0 else "after"
    return f"{n} day{'s' if n != 1 else ''} {direction} peak"


# ---------------------------------------------------------------------------
# Per-target computation
# ---------------------------------------------------------------------------

def _compute_target(entry: dict, observer, eph, t_array, sample_dts: list,
                    moon_astr, moon_alt_deg_all, moon_dist_km_all,
                    illumination_pct: float, night_date,
                    min_elevation: float,
                    obs_start: datetime, obs_end: datetime,
                    sky_sqm: float | None = None,
                    aod: float | None = None) -> "VisibleTarget | None":
    name     = entry["name"]
    ttype    = entry["type"]
    min_elev = entry.get("min_elevation", min_elevation)

    note = None
    zhr_eff = None
    if ttype == "meteor_shower":
        note = _meteor_shower_note(entry, night_date)
        if note is None:
            return None  # outside active window
        delta = _days_from_peak(entry, night_date)
        if delta is not None:
            zhr_eff = round(
                effective_zhr(entry.get("peak_zhr", 0), delta,
                              entry.get("b_rise", 0.0), entry.get("b_decline", 0.0)),
                1,
            )

    try:
        if ttype == "planet":
            key = _PLANET_BODIES.get(name.lower())
            if not key:
                log.warning("Unknown planet %r — skipping", name)
                return None
            body = eph[key]
        else:
            body = _sky_object(entry)
    except (KeyError, ValueError) as e:
        log.warning("Skipping target %r: %s", name, e)
        return None

    astrometric = observer.at(t_array).observe(body)
    alt, az, _  = astrometric.apparent().altaz()
    alt_deg     = alt.degrees
    az_deg      = az.degrees
    sep_deg     = astrometric.separation_from(moon_astr).degrees

    # Clip to the effective observation window for this target type
    mask             = np.array([obs_start <= dt <= obs_end for dt in sample_dts])
    obs_sample_idxs  = np.where(mask)[0]          # indices into t_array / sample_dts
    obs_alt          = alt_deg[mask]
    obs_az           = az_deg[mask]
    obs_sep          = sep_deg[mask]
    obs_moon_alt     = moon_alt_deg_all[mask]      # moon altitude at each masked sample
    obs_moon_dist    = moon_dist_km_all[mask]      # Earth-Moon distance at each masked sample
    obs_dts          = [dt for dt, m in zip(sample_dts, mask) if m]

    windows_with_idx = _find_windows(obs_alt, obs_az, obs_dts, min_elev)
    if not windows_with_idx:
        return None

    # Hoist catalog photometric data — same values for every window of this target.
    sb  = entry.get("surface_brightness")   # mag/arcsec² (extended objects)
    mag = entry.get("magnitude")             # integrated V mag (any object)
    _sqm = sky_sqm if sky_sqm is not None else _ml.KS_NATURAL_SKY

    # For planets, override mag with the dynamically-computed apparent magnitude.
    # Skyfield's planetary_magnitude() accounts for phase angle and distance, so
    # Mars near opposition (-2.9) and Mars at aphelion (+1.8) are handled correctly.
    # We evaluate at the observation-window midpoint — magnitude drifts < 0.01 mag/night.
    if ttype == "planet" and _planetary_magnitude is not None and len(obs_sample_idxs) > 0:
        try:
            mid_i        = int(obs_sample_idxs[len(obs_sample_idxs) // 2])
            planet_astr  = observer.at(t_array[mid_i]).observe(body)
            mag          = float(_planetary_magnitude(planet_astr))
            log.debug("Planet %r apparent magnitude: %.2f", name, mag)
        except Exception as e:
            log.debug("planetary_magnitude failed for %r: %s", name, e)

    # has_catalog_data: True when we can evaluate site photo-viability.
    # Meteor showers are exempt — their activity is gated by peak_day window,
    # not sky brightness (individual meteors are bright transient events).
    has_catalog_data = (
        (sb is not None or mag is not None)
        and ttype not in ("meteor_shower",)
    )

    # Tracks whether ANY sample in ANY window passes the photo contrast check.
    # If this remains False after all windows are processed, the site's baseline
    # sky brightness (from light pollution, not just the moon) is too severe and
    # the target is suppressed entirely.
    any_photo_ok_global = False

    windows = []
    for window, indices in windows_with_idx:
        window.moon_interference = _moon_interferes(obs_sep, obs_moon_alt, obs_moon_dist,
                                                    obs_alt, indices, illumination_pct,
                                                    aod=aod)

        # Store moon separation and altitude at peak time for the K&S sky brightness model.
        try:
            peak_obs_idx = obs_dts.index(window.peak_time)
            window.moon_sep_at_peak_deg = float(obs_sep[peak_obs_idx])
            window.moon_alt_at_peak_deg = float(obs_moon_alt[peak_obs_idx])
            # Serialized severity at peak — the single source of truth for the
            # UI and CLI (site SQM, AOD, distance and slant path included,
            # unlike the legacy frontend mirror).  'none' = computed and
            # negligible; None (field default) = peak geometry unavailable.
            sev = _ml.moon_wash_severity(
                illumination_pct,
                window.moon_sep_at_peak_deg,
                window.moon_alt_at_peak_deg,
                aod=aod,
                target_alt_deg=window.peak_alt_deg,
                sky_sqm=_sqm,
                moon_earth_dist_km=float(obs_moon_dist[peak_obs_idx]),
            )
            window.moon_wash_severity = sev if sev is not None else "none"
        except Exception as e:
            log.debug("Moon sep/alt at peak failed for %r: %s", name, e)

        # Per-sample photo/visual usability cutoffs.
        # Iterate through each sample in this window and find the last one where
        # the sky background (dark sky + K&S moon contribution) still provides
        # enough contrast for the target.  We record the LAST usable sample so
        # the cutoff datetime is inclusive — i.e. "last moment it was usable."
        if has_catalog_data:
            # Select per-type contrast / offset thresholds.
            # SB-based (extended objects): need sky − target ≥ contrast headroom.
            # Mag-based (compact objects): need mag < sky − offset.
            if ttype == "milky_way":
                photo_contrast  = _ml.MW_PHOTO_SB_CONTRAST
                visual_contrast = _ml.MW_VISUAL_SB_CONTRAST
            else:
                photo_contrast  = _ml.PHOTO_SB_CONTRAST
                visual_contrast = _ml.VISUAL_SB_CONTRAST

            if ttype == "planet":
                compact_photo  = _ml.PLANET_PHOTO_OFFSET
                compact_visual = _ml.PLANET_VISUAL_OFFSET
            else:
                compact_photo  = _ml.COMPACT_PHOTO_OFFSET
                compact_visual = _ml.COMPACT_VISUAL_OFFSET

            win_indices = [i for i, dt in enumerate(obs_dts)
                           if window.start <= dt <= window.end]
            first_photo_ok = None
            last_photo_ok  = None
            last_visual_ok = None
            for i in win_indices:
                sep      = float(obs_sep[i])
                malt     = float(obs_moon_alt[i])
                mdist    = float(obs_moon_dist[i])
                delta    = _ml.ks_delta_mag(illumination_pct, sep, malt, _sqm, mdist,
                                            aod=aod, target_alt_deg=float(obs_alt[i]))
                sky_now  = _sqm - delta   # effective sky brightness this sample

                if sb is not None:
                    photo_ok  = sb  < sky_now - photo_contrast
                    visual_ok = sb  < sky_now - visual_contrast
                else:
                    photo_ok  = mag < sky_now - compact_photo
                    visual_ok = mag < sky_now - compact_visual

                if photo_ok:
                    if last_photo_ok is None:
                        first_photo_ok = obs_dts[i]   # first viable sample in window
                    last_photo_ok = obs_dts[i]
                    any_photo_ok_global = True
                if visual_ok:
                    last_visual_ok = obs_dts[i]

            # Only set a cutoff if it falls before the natural window end
            # (otherwise the target is usable all the way through).
            if last_photo_ok is not None and last_photo_ok < window.end:
                window.photo_cutoff = last_photo_ok
            elif last_photo_ok is not None:
                # K&S was computed and every sample in this window passes —
                # mark explicitly so the arch summary doesn't fall back to the
                # legacy moonrise heuristic.
                window.ks_computed = True
            if last_visual_ok is not None and last_visual_ok < window.end:
                window.visual_cutoff = last_visual_ok
            # Set photo_start when the first viable sample is not the window start —
            # this happens when the moon is already up and K&S takes time to clear.
            if first_photo_ok is not None and first_photo_ok > window.start:
                window.photo_start = first_photo_ok

        # For Milky Way waypoints, compute the arch angle (plane vs horizon)
        # using a reference point 30° further along the galactic plane.
        if ttype == "milky_way" and "galactic_l" in entry:
            try:
                ref_l = (entry["galactic_l"] + 30) % 360
                ref_b = entry.get("galactic_b", 0.0)
                ref_ra, ref_dec = gal_to_radec(ref_l, ref_b)
                ref_star = Star(ra_hours=ref_ra, dec_degrees=ref_dec)
                # Evaluate reference at the window's peak time only
                peak_idx = sample_dts.index(window.peak_time)
                t_peak   = t_array[peak_idx]
                ref_alt, ref_az, _ = (
                    observer.at(t_peak).observe(ref_star).apparent().altaz()
                )
                window.arch_angle_deg = round(
                    _mw._arch_angle(window.peak_alt_deg, window.peak_az_deg,
                                    float(ref_alt.degrees), float(ref_az.degrees)), 1
                )
            except Exception as e:
                log.debug("Arch angle computation failed for %r: %s", name, e)

        windows.append(window)

    # Suppress targets with catalog data where no sample passes the photo contrast
    # check.  This happens when the site's baseline sky brightness (light pollution)
    # is already too high — the moon is irrelevant, the sky itself is too bright.
    # Planets and meteor showers are exempt (handled above via has_catalog_data).
    if has_catalog_data and not any_photo_ok_global:
        log.debug(
            "Suppressing %r — zero photo-viable samples at site SQM %.1f",
            name, _sqm,
        )
        return None

    angular_size = entry.get("angular_size_arcmin")
    land_suit    = _landscape_suitability(entry.get("surface_brightness"), angular_size, ttype)
    return VisibleTarget(
        name=name, type=ttype, windows=windows, note=note,
        angular_size_arcmin=angular_size,
        landscape_suitability=land_suit,
        zhr_effective=zhr_eff,
        population_index=entry.get("population_index") if ttype == "meteor_shower" else None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_targets() -> list:
    """Load and return raw catalog entries. Returns [] on missing or malformed file."""
    if not _CATALOG_PATH.exists():
        log.warning("targets.json not found at %s", _CATALOG_PATH)
        return []
    try:
        return json.loads(_CATALOG_PATH.read_text())
    except Exception as e:
        log.warning("Failed to load targets.json: %s", e)
        return []


def active_meteor_showers(target_date) -> list:
    """
    Return basic info for all meteor showers active on target_date.

    Does not compute sky positions — fast date-arithmetic only.

    Returns a list of dicts:
        [{"name": str, "note": str, "zhr": int,
          "zhr_effective": float | None, "peak_time_utc": str | None}, ...]
    Showers outside their active window are excluded.
    """
    results = []
    for entry in load_targets():
        if entry.get("type") != "meteor_shower":
            continue
        note = _meteor_shower_note(entry, target_date)
        if note is not None:
            delta = _days_from_peak(entry, target_date)
            zhr_eff = (
                round(effective_zhr(entry.get("peak_zhr", 0), delta,
                                     entry.get("b_rise", 0.0), entry.get("b_decline", 0.0)), 1)
                if delta is not None else None
            )
            peak_dt = _peak_datetime(entry, target_date)
            results.append({
                "name": entry["name"],
                "note": note,
                "zhr":  entry.get("peak_zhr", 0),
                "zhr_effective": zhr_eff,
                "peak_time_utc": peak_dt.isoformat() if peak_dt is not None else None,
            })
    return results


def visible_targets(
    lat: float,
    lon: float,
    sunset: datetime,
    sunrise: datetime,
    illumination_pct: float,
    night_start: datetime | None = None,
    night_end: datetime | None   = None,
    min_elevation: float = DEFAULT_MIN_ELEVATION,
    sky_sqm: float | None = None,
    tz: "ZoneInfo | None" = None,
    aod: float | None = None,
) -> list:
    """
    Return targets visible during the night.

    DSOs and meteor showers are clipped to astronomical darkness
    (night_start–night_end). Planets use the full sunset–sunrise window
    since they are often worth observing during twilight.
    Falls back to sunset/sunrise for both if night bounds are unavailable.

    aod — night-representative aerosol optical depth for the moonlight
    scattering model; None means reference clear sky.
    """
    catalog = load_targets()
    if not catalog:
        return []

    ts       = load.timescale()
    eph      = _ephemeris()
    observer = eph["earth"] + wgs84.latlon(lat, lon)

    # Full sample window sunset→sunrise (needed for planet twilight coverage).
    # Anchor times (sunset, night_start, night_end, sunrise) are injected as
    # exact sample points so that window start/end times always align precisely
    # with the reported astronomical night boundaries — without them the last
    # eligible sample can fall up to (SAMPLE_INTERVAL_MIN - 1) minutes early.
    total_min  = int((sunrise - sunset).total_seconds() / 60)
    sample_dts = [
        sunset + timedelta(minutes=i)
        for i in range(0, total_min + SAMPLE_INTERVAL_MIN, SAMPLE_INTERVAL_MIN)
    ]
    if sample_dts and sample_dts[-1] > sunrise:
        sample_dts[-1] = sunrise

    # Inject boundary anchors if they don't already coincide with a grid point.
    anchors = [t for t in (night_start, night_end) if t is not None]
    if anchors:
        sample_dts = sorted(set(sample_dts) | set(anchors))

    t_array    = ts.from_datetimes(sample_dts)
    moon_astr  = observer.at(t_array).observe(eph["moon"])
    moon_alt_v, _, _ = moon_astr.apparent().altaz()
    moon_alt_deg_all = moon_alt_v.degrees          # ndarray, one value per sample
    moon_dist_km_all = moon_astr.distance().km     # ndarray, Earth-Moon distance per sample
    # sunset is UTC-aware; its raw .date() is the UTC calendar date, which for
    # evening sunsets west of UTC (most of the Americas/Europe) is frequently
    # one day ahead of the observer's local calendar date — meteor shower
    # peak-proximity text/decay math needs the LOCAL date the night belongs to.
    night_date = sunset.astimezone(tz).date() if tz is not None else sunset.date()

    # Use provided SQM or fall back to the K&S natural-sky baseline (Bortle 2).
    _sky_sqm = sky_sqm if sky_sqm is not None else _ml.KS_NATURAL_SKY

    dark_start = night_start or sunset
    dark_end   = night_end   or sunrise

    results = []
    for entry in catalog:
        is_planet = entry["type"] == "planet"
        obs_start = sunset     if is_planet else dark_start
        obs_end   = sunrise    if is_planet else dark_end
        try:
            result = _compute_target(
                entry, observer, eph, t_array, sample_dts,
                moon_astr, moon_alt_deg_all, moon_dist_km_all,
                illumination_pct, night_date,
                min_elevation,
                obs_start, obs_end,
                sky_sqm=_sky_sqm,
                aod=aod,
            )
        except Exception as e:
            log.warning("Error computing target %r: %s", entry.get("name"), e)
            result = None

        if result is not None:
            results.append(result)

    results.sort(key=lambda t: max(w.peak_alt_deg for w in t.windows), reverse=True)
    return results


def is_prime(target, min_peak_alt: float, min_window_hours: float,
             dark_intervals: list | None = None) -> bool:
    """True if the target has a clean window meeting altitude and duration thresholds.

    Milky Way targets skip the altitude floor (the arch is inherently low from
    mid-latitudes) but still require the minimum window duration — without it,
    setting waypoints with 1–30 minute windows show up as prime.

    When every window has moon_interference=True (K&S ≥ 0.50 at some sample),
    fall back to checking overlap with the geometric dark intervals (moon
    physically below the horizon).  A target whose overnight window straddles
    moonset gains a genuine moon-free sub-period; if that sub-period is long
    enough, the target qualifies.  On full-moon nights dark_intervals=[] so no
    window can pass this fallback — the moon-dominated message fires correctly.
    """
    clean = [w for w in target.windows if not w.moon_interference]
    if not clean:
        # No fully K&S-clean windows.  Check whether any window has a moon-free
        # overlap with the geometric dark intervals (moonset → astronomical end).
        if not dark_intervals:
            return False
        for w in target.windows:
            for di_start, di_end in dark_intervals:
                overlap_s = max(w.start, di_start)
                overlap_e = min(w.end,   di_end)
                overlap_h = (overlap_e - overlap_s).total_seconds() / 3600
                if overlap_h >= min_window_hours:
                    if target.type == "milky_way":
                        return True
                    eff_alt = PLANET_PRIME_MIN_ALT if target.type == "planet" else min_peak_alt
                    return w.peak_alt_deg >= eff_alt
        return False

    best       = max(clean, key=lambda w: w.peak_alt_deg)
    duration_h = (best.end - best.start).total_seconds() / 3600
    if target.type == "milky_way":
        return duration_h >= min_window_hours
    # Planets are bright enough to be prime at lower altitudes than DSOs.
    effective_min_alt = PLANET_PRIME_MIN_ALT if target.type == "planet" else min_peak_alt
    return best.peak_alt_deg >= effective_min_alt and duration_h >= min_window_hours


# K&S model and sky-brightness constants live in moonlight.py.
# MW arch functions live in milky_way.py.
# Re-export the names that pynightsky.py and predictor.py currently import
# directly from targets, so their import lines stay unchanged.
from .moonlight import (
    ks_moon_credit,
    moon_wash_severity,
    KS_CRESCENT_EXEMPTION_PCT,
)
from .milky_way import milky_way_arch_summary, mw_theoretical_core_max
