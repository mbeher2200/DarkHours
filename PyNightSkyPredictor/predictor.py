#!/usr/bin/env python3
"""Night sky prediction engine — assembles a NightReport for a given location and date."""

import bisect as _bisect
import concurrent.futures as _futures
import dataclasses
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from . import aurora as _aur
from . import darksky as _ds
from . import light_dome as _ld
from . import moon_events as _me
from . import ports as _ports
from . import scoring
from . import sky_events as se
from . import targets as _tgt
from .milky_way import (
    milky_way_arch_summary as _mw_arch_summary,
    mw_theoretical_core_max as _mw_core_max,
    bt_moon_glow as _bt_moon_glow,
    bt_interp_moon_alt as _bt_interp_moon_alt,
    bt_cloud_frac as _bt_cloud_frac,
    BT_K_MOON as _BT_K_MOON,
    BT_STEP_MIN as _BT_STEP_MIN,
)
from .moonlight import ks_moon_credit, KS_CRESCENT_EXEMPTION_PCT
from . import weather as wx

log = logging.getLogger(__name__)

_WX_CACHE_TTL = 1800  # 30 minutes


def _wx_serialize(points: list, source: str, fetched_at: str) -> dict:
    """Serialize weather forecast results for DynamoDB caching."""
    return {
        "source": source,
        "fetched_at": fetched_at,
        "points": [
            {**dataclasses.asdict(p), "time": p.time.isoformat()}
            for p in points
        ],
    }


def _wx_deserialize(cached: dict) -> tuple:
    """Deserialize cached weather data back to (list[WeatherPoint], source, fetched_at)."""
    field_names = {f.name for f in dataclasses.fields(wx.WeatherPoint)}
    points = []
    for p in cached["points"]:
        kwargs = {k: v for k, v in p.items() if k in field_names}
        kwargs["time"] = datetime.fromisoformat(kwargs["time"])
        points.append(wx.WeatherPoint(**kwargs))
    # .get() default for backward compat with cache entries written before this upgrade
    # (TTL is 30 min, so stale-shape entries self-expire quickly regardless).
    return points, cached["source"], cached.get("fetched_at")


def _scope_wx_source(source: str | None, night_points: list) -> str | None:
    """Re-scope a fetch-wide source label to the night being reported.

    wx.forecast() labels the source over the whole ~16-day fetch, so it says
    "+ 7Timer" whenever any day in the series got seeing data — but 7Timer
    only covers ~3 days. Keep the suffix only if this night's points actually
    carry seeing; otherwise the provenance badge would credit 7Timer on nights
    it never reached.
    """
    if source and "+ 7Timer" in source and not any(p.seeing_arcsec is not None for p in night_points):
        return source.replace(" + 7Timer", "")
    return source


@dataclass
class NightReport:
    # Location & date
    date: date
    lat: float
    lon: float
    display_name: str
    tz_name: str

    # Sky events in the night window (UTC-aware datetimes)
    events: list          # [{"time": datetime, "label": str}, ...]

    # Key event times (UTC, timezone-aware)
    sunset: datetime
    sunrise: datetime
    night_start: datetime | None
    night_end: datetime | None
    moonrise: datetime | None
    moonset: datetime | None

    # Moon
    phase_name: str
    illumination_pct: float
    moon_score: float
    moon_distance_km: float
    moon_special: str | None       # 'supermoon' | 'micromoon' | None
    moon_eclipses: list            # list[dict] — eclipses during this night

    # Dark time
    dark_intervals: list  # [(start_utc, end_utc), ...]
    dark_hours: float     # total moon-free dark hours tonight
    dark_cycle: dict      # {tonight_hours, mean_hours, stdev_hours, score}
    dark_score: float

    # Light pollution (raw darksky.lookup() result)
    light_pollution: dict | None
    bortle_score: float | None

    # Weather
    weather_points: list  # list[WeatherPoint]
    weather_score: float | None
    wx_source: str | None  # e.g. "Open-Meteo + 7Timer" or "Open-Meteo"
    wx_fetched_at: str | None  # ISO 8601 UTC — moment the primary weather HTTP call returned
    wx_pending: bool
    wx_no_data: bool
    wx_archive_error: bool
    wx_error: str | None

    # Overall
    score: float | None
    score_components: dict  # {moon, dark, weather, bortle}

    # Visible targets (populated when fetch_targets=True)
    visible_targets: list = field(default_factory=list)
    mw_summary:      dict | None = None   # milky_way_arch_summary output (MW targets only)

    # Light dome — precomputed H3 index lookup (summarize_horizons shape); None outside coverage
    light_dome: dict | None = None

    # Active meteor showers tonight (always populated)
    active_showers: list  = field(default_factory=list)

    # Aurora outlook (nightly_aurora shape) — None below the photographic tier,
    # outside the Kp forecast horizon, or when the night has no true darkness
    aurora: dict | None = None

    # ISS (and other satellite) passes (populated when fetch_satellites=True)
    sat_passes:               list = field(default_factory=list)
    sat_stale:                bool = False  # True → past date, no historical TLE
    sat_future_stale:         bool = False  # True → too far ahead, TLE has expired
    sat_future_warn:          bool = False  # True → 3-7 days out, accuracy warning only
    sat_tle_stale:            bool = False  # True → Celestrak unreachable; using expired cache
    sat_network_error:        bool = False  # True → Celestrak unreachable AND no cached TLE
    # Starlink trains (populated when fetch_satellites=True)
    starlink_trains:          list = field(default_factory=list)  # list[StarlinkTrain]
    sat_starlink_unavailable: bool = False  # True → Starlink group TLE fetch failed


# ---------------------------------------------------------------------------
# Phase 1: Condition-Driven Viability Engine
# ---------------------------------------------------------------------------

_CLOUD_BLOCK_PCT          = 70    # cloud_cover_pct > this → blocked
_DOME_BLOCK_SCORE         = 0.25  # glow_toward() >= this → light_dome blocker (= MINOR_DOME_THRESHOLD)
_WEATHER_GAP_SECS         = 5400  # 90 min nearest-neighbour tolerance (matches _merge_7timer)
_MIN_VIABLE_MIN           = 30    # effective window must be at least this long to be viable
_MOON_WASHOUT_RADIUS_DEG  = 45.0  # washout zone radius at 100% illumination;
                                   # scales linearly → effective = 45° × (illum/100)
_LOW_RADIANT_ALT_DEG      = 25.0  # meteor showers only: radiant altitude below this →
                                   # "low_radiant" blocker. sin(25°) ≈ 0.42 — local rate
                                   # collapses to <45% of the decayed-ZHR figure from
                                   # foreshortening/extinction alone, even under an
                                   # otherwise clear, dark, moonless sky.


def _bt_window_best(
    window,
    eff_start: datetime,
    eff_end:   datetime,
    illum_pct: float,
    moon_alts: "list | None",
    weather_points: list,
) -> datetime:
    """
    Return the best observation time within [eff_start, eff_end] using:
        score = alt_score × moon_score × weather_score

    alt_score:   piecewise-linear altitude track from window geometry.
    moon_score:  exp(−K × glow) with K&S phase correction and post-moonset fade.
    wx_score:    1 − cloud_fraction (nearest-neighbour from weather_points).

    Falls back to geometric peak (snapped to effective window) when moon_alts
    is absent — this preserves existing behaviour for unit tests without ephemeris.
    """
    illum_frac = illum_pct / 100.0
    max_alt    = window.peak_alt_deg
    if max_alt <= 0:
        return eff_start

    # Pre-build epoch list once so bt_cloud_frac can bisect instead of linear-scan.
    wx_epochs = [p.time.timestamp() for p in weather_points] if weather_points else None

    best_t, best_score = eff_start, -1.0
    t = eff_start
    while t <= eff_end:
        # Piecewise-linear altitude interpolation through start → peak → end
        if t <= window.peak_time:
            span = (window.peak_time - window.start).total_seconds()
            frac = (t - window.start).total_seconds() / span if span > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            alt  = window.start_alt_deg + (max_alt - window.start_alt_deg) * frac
        else:
            span = (window.end - window.peak_time).total_seconds()
            frac = (t - window.peak_time).total_seconds() / span if span > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            alt  = max_alt + (window.end_alt_deg - max_alt) * frac

        alt_s = alt / max_alt

        if moon_alts:
            moon_alt = _bt_interp_moon_alt(t, moon_alts)
            glow     = _bt_moon_glow(moon_alt, illum_frac)
        else:
            glow = 0.0
        moon_s = math.exp(-_BT_K_MOON * glow)

        cloud  = _bt_cloud_frac(t, weather_points, wx_epochs) if weather_points else 0.0
        wx_s   = max(0.0, 1.0 - cloud)

        score  = alt_s * moon_s * wx_s
        if score > best_score:
            best_score = score
            best_t     = t

        t += timedelta(minutes=_BT_STEP_MIN)

    return best_t


def _apply_condition_vectors(
    targets: list,
    weather_points: list,
    light_dome_info: "dict | None",
    illumination_pct: float,
    moon_alts: "list | None" = None,
) -> None:
    """Post-process VisibleTarget list with atmospheric, dome, and lunar viability vectors.

    Mutates TargetWindow and VisibleTarget fields in-place. Called after both the
    target future and the weather future have resolved in assemble_night().
    """
    # Pre-build the detailed-format dict glow_toward() needs, reconstructed from
    # the summarize_horizons() output (scores + dome_heights are already present).
    detailed_for_glow = None
    if light_dome_info is not None:
        detailed_for_glow = {
            d: {
                "score": light_dome_info["scores"][d],
                "dome_height_deg": light_dome_info["dome_heights"][d],
            }
            for d in _ld.DIRS_8
        }

    for target in targets:
        for window in target.windows:
            blockers: list[str] = []

            # --- Atmospheric Vector (MCVI) -----------------------------------
            # Collect candidate weather points bracketing this window.
            gap = timedelta(seconds=_WEATHER_GAP_SECS)
            candidates = [
                p for p in weather_points
                if window.start - gap <= p.time <= window.end + gap
            ]

            if not candidates:
                # No weather data → fail-open: use geometric limits.
                eff_start = window.start
                eff_end   = window.end
            else:
                # Tag each candidate as viable (cloud + transparency only; no humidity).
                def _wx_viable(p) -> bool:
                    cloud_ok = (p.cloud_cover_pct is None or
                                p.cloud_cover_pct <= _CLOUD_BLOCK_PCT)
                    transp_ok = (p.transparency is None or
                                 p.transparency != "Poor")
                    return cloud_ok and transp_ok

                tagged = [(p, _wx_viable(p)) for p in sorted(candidates, key=lambda p: p.time)]

                # Build contiguous viable blocks from the tagged points.
                viable_blocks: list[tuple] = []  # (block_start_time, block_end_time)
                block_start = None
                block_end   = None
                for p, ok in tagged:
                    if ok:
                        if block_start is None:
                            block_start = p.time
                        block_end = p.time
                    else:
                        if block_start is not None:
                            viable_blocks.append((block_start, block_end))
                        block_start = block_end = None
                if block_start is not None:
                    viable_blocks.append((block_start, block_end))

                if not viable_blocks:
                    # Entire window is blocked.
                    eff_start = eff_end = None
                    # Determine which blocker types fired.
                    cloud_fired = any(
                        p.cloud_cover_pct is not None and p.cloud_cover_pct > _CLOUD_BLOCK_PCT
                        for p, _ in tagged
                    )
                    transp_fired = any(
                        p.transparency == "Poor"
                        for p, _ in tagged
                    )
                    if cloud_fired:
                        blockers.append("cloud")
                    if transp_fired:
                        blockers.append("transparency")
                else:
                    # Priority A: block containing peak_time.
                    peak = window.peak_time or window.start
                    optimal = next(
                        (b for b in viable_blocks if b[0] <= peak <= b[1]),
                        None,
                    )
                    if optimal is None:
                        # Priority B: longest block.
                        optimal = max(
                            viable_blocks,
                            key=lambda b: (b[1] - b[0]).total_seconds(),
                        )

                    # Truncate against physical and K&S photographic limits.
                    start_candidates = [
                        t for t in [window.start, window.photo_start, optimal[0]]
                        if t is not None
                    ]
                    end_candidates = [
                        t for t in [window.photo_cutoff, window.end, optimal[1]]
                        if t is not None
                    ]
                    eff_start = max(start_candidates)
                    eff_end   = min(end_candidates)

            window.effective_start = eff_start
            window.effective_end   = eff_end

            # --- Light Dome Vector -------------------------------------------
            if detailed_for_glow is not None:
                glow = _ld.glow_toward(
                    detailed_for_glow,
                    window.peak_az_deg,
                    window.peak_alt_deg,
                )
                window.dome_glow_at_peak = round(glow, 4)
                if glow >= _DOME_BLOCK_SCORE:
                    blockers.append("light_dome")
            else:
                window.dome_glow_at_peak = None

            # --- Lunar Proximity Vector --------------------------------------
            # Geometric proximity check: target within (radius × illum/100)°
            # of the moon triggers "moon_washout". Distinct from the K&S
            # photometric model used for photo_cutoff — this labels the
            # "pointing directly at the moon" case for the Phase 2 UI badge.
            if (illumination_pct > KS_CRESCENT_EXEMPTION_PCT
                    and window.moon_sep_at_peak_deg is not None):
                effective_radius = _MOON_WASHOUT_RADIUS_DEG * (illumination_pct / 100.0)
                if window.moon_sep_at_peak_deg < effective_radius:
                    blockers.append("moon_washout")

            # --- Radiant Altitude / Local Rate Vector (meteor showers only) ----
            # window.peak_alt_deg/peak_az_deg ARE the radiant's alt/az for shower
            # targets (_sky_object builds the Star from radiant_ra/radiant_dec),
            # so no extra geometry is needed here. local_rate_at_peak is the
            # zenith/radiant-altitude-corrected rate a visual observer would
            # actually see (ZHR convention divides observed rate by sin(radiant
            # alt) to normalize to zenith; inverted, that's the multiplication
            # below) — feeds both the scorecard banner's "local rate" figure and
            # the low_radiant blocker. Scoped to meteor_shower so DSO/planet
            # blocker logic is untouched.
            if target.type == "meteor_shower":
                zhr_eff = target.zhr_effective
                if zhr_eff is not None and window.peak_alt_deg is not None:
                    window.local_rate_at_peak = (
                        round(zhr_eff * math.sin(math.radians(window.peak_alt_deg)), 1)
                        if window.peak_alt_deg > 0 else 0.0
                    )
                    if window.peak_alt_deg < _LOW_RADIANT_ALT_DEG:
                        blockers.append("low_radiant")
                else:
                    window.local_rate_at_peak = None

            window.blockers = blockers

            # --- Best Time (K&S scored: altitude × moon × weather) ----------
            if eff_start is None or eff_end is None:
                window.best_time = None
            else:
                window.best_time = _bt_window_best(
                    window, eff_start, eff_end,
                    illumination_pct, moon_alts, weather_points,
                )

            # --- Weather score at best time ----------------------------------
            if window.best_time is not None and weather_points:
                _bt_epoch  = window.best_time.timestamp()
                _wt_epochs = [p.time.timestamp() for p in weather_points]
                _idx = _bisect.bisect_left(_wt_epochs, _bt_epoch)
                if _idx == 0:
                    nearest = weather_points[0]
                elif _idx >= len(weather_points):
                    nearest = weather_points[-1]
                else:
                    _b, _a = weather_points[_idx - 1], weather_points[_idx]
                    nearest = _b if (_bt_epoch - _wt_epochs[_idx - 1]) <= (_wt_epochs[_idx] - _bt_epoch) else _a
                if abs((nearest.time - window.best_time).total_seconds()) <= _WEATHER_GAP_SECS:
                    window.weather_score_at_best = wx.rate_conditions(nearest)

        # --- VisibleTarget viability rollup ----------------------------------
        best_w = max(target.windows, key=lambda w: w.peak_alt_deg)
        if best_w.effective_start is None or best_w.effective_end is None:
            target.viability = "blocked"
        else:
            eff_duration_min = (
                (best_w.effective_end - best_w.effective_start).total_seconds() / 60
            )
            if eff_duration_min < _MIN_VIABLE_MIN:
                target.viability = "blocked"
            elif best_w.blockers:
                target.viability = "degraded"
            else:
                target.viability = "ok"


def assemble_night(
    lat: float,
    lon: float,
    target: date,
    tz: ZoneInfo,
    display_name: str = "",
    fetch_weather: bool = True,
    fetch_targets: bool = False,
    fetch_satellites: bool = False,
    fetch_aurora: bool = True,
    use_cycle_window: bool = False,
) -> NightReport:
    """
    Compute a complete NightReport for the given location and date.

    Raises ValueError if no sunset or sunrise can be found for the
    date/location (e.g. polar day/night).

    use_cycle_window=True (calendar/trip path only — /night and the CLI leave
    this False): derive sunset/sunrise/night_start/night_end/dark_hours_tonight
    from lunar_cycle_dark_analysis()'s already-computed 30-night window instead
    of an independent sky_events() call. No event timeline, no moonrise/moonset —
    NightSummary (the calendar path's result type) doesn't carry any of those.
    This extends the same fixed-twilight-offset approximation dark_score already
    accepts (see sky_events.py) to moon_score/weather-windowing too, but only
    for this path; output is otherwise unaffected.
    """
    def _local(dt):
        return dt.astimezone(tz)

    _t = {}  # timing checkpoints — emitted as a single log line at end
    _t0 = time.monotonic()
    _now = datetime.now(timezone.utc)

    # --- I/O kicked off immediately — independent of all Skyfield work ---
    # darksky (S3 raster) and weather (HTTP) need only lat/lon, which we have now.
    # They run concurrently with sky_events + moon + lunar_cycle on the main thread.
    _wx_cache_key = f"wx2|{lat:.2f}|{lon:.2f}"
    _wx_exc       = None
    _wx_future: _futures.Future | None = None

    # TLE fetches also need nothing from Skyfield — start them alongside weather.
    _tle_futures: dict[int, _futures.Future] = {}
    _sl_future:   _futures.Future | None     = None
    _sat_stale        = False
    _sat_days_offset  = (target - (datetime.now(timezone.utc).date() - timedelta(days=1))).days

    _max_workers = 4
    if fetch_satellites and _sat_days_offset >= 0:
        # 3 individual TLE fetches + 1 Starlink group fetch on top of ds + wx + aurora
        _max_workers = 9

    with _futures.ThreadPoolExecutor(max_workers=_max_workers) as _pool:
        _ds_future = _pool.submit(_ds.lookup, lat, lon)

        # Aurora forecast — global SWPC products (cached, single-flight): the
        # 3-day Kp forecast within its horizon, the 27-day outlook beyond it
        # (so the report agrees with the calendar icon the outlook produced).
        # The gate uses _now (UTC), never a local-time date: SWPC time_tags are
        # naive-UTC, and a local date would prematurely block the fetch for
        # Pacific evenings where "tonight" is already tomorrow in UTC (the −1
        # day end mirrors the weather heuristic below). Past/far-future dates
        # skip the fetch entirely, keeping those paths offline.
        _aurora_future: _futures.Future | None = None
        _aurora_days_ahead = (target - _now.date()).days
        if fetch_aurora and -1 <= _aurora_days_ahead <= _aur.OUTLOOK_HORIZON_DAYS:
            def _aurora_io(_days=_aurora_days_ahead):
                rows, r_stale = (
                    _aur.fetch_kp_forecast()
                    if _days <= _aur.KP_FORECAST_HORIZON_DAYS + 1 else ([], False)
                )
                # Prefetch the outlook whenever the Kp bins might not span the
                # whole night (the boundary nights and everything beyond).
                if _days >= _aur.KP_FORECAST_HORIZON_DAYS or not rows:
                    outlook, o_stale = _aur.fetch_27day_outlook()
                else:
                    outlook, o_stale = None, False
                return rows, r_stale, outlook, o_stale
            _aurora_future = _pool.submit(_aurora_io)

        # Heuristic: start weather for tonight-or-future dates without waiting for
        # sunrise. "Tonight" may be yesterday in UTC when the night spans midnight
        # (e.g. 03:00 UTC — still before sunrise for a US location). Subtracting
        # 1 day catches that case; the precise _future_date check after sky_events
        # discards the thread result if the night has already fully passed.
        #
        # The DynamoDB cache check runs inside the thread so the main thread never
        # blocks on it: on a cache miss the HTTP fetch starts immediately after the
        # check with zero gap; on a hit the result is ready without touching the
        # main thread at all.
        if fetch_weather and target >= datetime.now(timezone.utc).date() - timedelta(days=1):
            def _wx_io(_key=_wx_cache_key):
                _c = _ports.get_backend().cache.get(_key)
                if _c is not None:
                    return ("hit", _c)
                with wx.lock_for(lat, lon):
                    _c = _ports.get_backend().cache.get(_key)
                    if _c is not None:
                        return ("hit", _c)
                    _fresh = wx.forecast(lat, lon)
                    try:
                        _ports.get_backend().cache.set(_key, _wx_serialize(*_fresh), ttl_seconds=_WX_CACHE_TTL)
                    except Exception as _ce:
                        log.debug("Weather cache write failed (non-fatal): %s", _ce)
                    return ("fresh", _fresh)
            _wx_future = _pool.submit(_wx_io)

        # TLE fetches — start immediately, overlaps with ~600 ms of Skyfield work.
        if fetch_satellites:
            from . import tle_provider as _tle_mod
            _sat_stale = _sat_days_offset < 0
            if not _sat_stale:
                for _norad_id, _ in _tle_mod.TRACKED_SATELLITES:
                    _tle_futures[_norad_id] = _pool.submit(_tle_mod.get_tle, _norad_id)
                _sl_future = _pool.submit(_tle_mod.get_starlink_train_tles)

        # --- Skyfield work runs concurrently with the I/O threads above ---
        _precomputed_dark_hours_tonight = None
        cycle = None
        if use_cycle_window:
            # Calendar/trip path: reuse the (already-deduplicated across a calendar
            # range — see sky_events.py's per-location lock) dark-cycle window
            # instead of an independent sky_events() call.
            _t["sky_events_ms"] = 0
            _tc = time.monotonic()
            cycle      = se.lunar_cycle_dark_analysis(lat, lon, target, tz)
            dark_score = cycle["score"]
            _t["lunar_cycle_ms"] = round((time.monotonic() - _tc) * 1000)

            tonight = cycle["tonight"]
            sunset  = tonight["sunset"]
            if not sunset:
                raise ValueError(f"No sunset found for {target} at {lat:.4f}, {lon:.4f}")
            sunrise = tonight["sunrise"]
            if not sunrise:
                raise ValueError(f"No sunrise found after sunset on {target}")

            moonrise = moonset = None
            night_start = tonight["night_start"]
            night_end   = tonight["night_end"]
            night_events = []
            _precomputed_dark_hours_tonight = tonight["dark_hours"]
            _tc = time.monotonic()  # for the event_parse_ms line below (~0ms here)
        else:
            events = se.sky_events(lat, lon, target)
            _t["sky_events_ms"] = round((time.monotonic() - _t0) * 1000)

            # --- Key event times ---
            _tc = time.monotonic()
            sunset = next(
                (e["time"] for e in events
                 if e["label"] == "Sunset" and _local(e["time"]).date() == target),
                None,
            )
            if not sunset:
                raise ValueError(f"No sunset found for {target} at {lat:.4f}, {lon:.4f}")

            sunrise = se.find_event(events, "Sunrise", after=sunset)
            if not sunrise:
                raise ValueError(f"No sunrise found after sunset on {target}")

            moonrise    = se.find_last_event(events, "Moonrise", before=sunrise)
            moonset     = se.find_event(events, "Moonset", after=sunset)
            night_start = se.find_event(events, "Astronomical night begins", after=sunset, before=sunrise)
            night_end   = se.find_event(events, "Astronomical night ends",   after=night_start or sunset, before=sunrise)

            # Events within the display window (sunset/moonrise → sunrise/moonset)
            window_start = min(sunset, moonrise) if moonrise and moonrise < sunset else sunset
            window_end   = max(sunrise, moonset) if moonset  and moonset  > sunrise else sunrise
            night_events = [e for e in events if window_start <= e["time"] <= window_end]

        _t["event_parse_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Moon ---
        _tc = time.monotonic()
        phase_name, illumination = se.moon_phase_info(sunset)
        moon_dist_km   = _me.moon_distance_km(sunset)
        moon_special   = _me.classify_full_moon(illumination, moon_dist_km)
        moon_eclipses  = _me.eclipses_for_night(sunset, sunrise)
        _t["moon_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Dark intervals ---
        if night_start and night_end:
            if _precomputed_dark_hours_tonight is not None:
                # use_cycle_window path: already computed by the dark-cycle window
                # loop (which folds moonrise/moonset into this scalar internally —
                # see sky_events.py); NightSummary doesn't need the raw intervals.
                dark_hours_tonight = _precomputed_dark_hours_tonight
                intervals = []
            else:
                intervals          = se.dark_moon_intervals(events, night_start, night_end)
                dark_hours_tonight = sum((e - s).total_seconds() for s, e in intervals) / 3600
            total_astro_hours  = (night_end - night_start).total_seconds() / 3600

            # Moon score: weight moonlit fraction by K&S sky-brightening credit rather
            # than the naive (1 − illum/100) approximation.  K&S is evaluated at the
            # site-wide proxy geometry (90° sep, 30° alt) — the darkest accessible sky.
            moonlit_frac = 1.0 - (dark_hours_tonight / total_astro_hours) if total_astro_hours > 0 else 0.0
            moon_score   = round(10 * ((1 - moonlit_frac) + moonlit_frac * ks_moon_credit(illumination)), 1)

            # Crescent exemption for the *displayed* Clear Dark Sky Hours
            if illumination <= KS_CRESCENT_EXEMPTION_PCT and total_astro_hours > 0:
                display_dark_hours     = total_astro_hours
                display_dark_intervals = [(night_start, night_end)]
            else:
                display_dark_hours     = dark_hours_tonight
                display_dark_intervals = intervals
        else:
            # No astronomical darkness (polar summer / always dark)
            intervals              = []
            dark_hours_tonight     = 0.0
            display_dark_hours     = 0.0
            display_dark_intervals = []
            moon_score             = round(10 * ks_moon_credit(illumination), 1)

        # --- Lunar cycle dark analysis ---
        if cycle is None:
            _tc = time.monotonic()
            cycle      = se.lunar_cycle_dark_analysis(lat, lon, target, tz)
            dark_score = cycle["score"]
            _t["lunar_cycle_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Collect I/O (weather + darksky started at function entry) ---
        _tc = time.monotonic()
        _future_date = sunrise >= _now

        # 1. Guarantee ds_info is bound to an empty dictionary
        ds_info = {}
        try:
            # 2. Collect darksky. Safely catch exceptions if the cache is wiped.
            _ds_res = _ds_future.result()
            if _ds_res:
                ds_info = _ds_res
        except Exception as e:
            log.warning("Dark sky lookup failed (missing cache files): %s", e)

        # Light dome — precomputed H3 index lookup (O(log n), no raster read), so it's
        # safe on the initial page-load path. None when outside the index coverage.
        light_dome_info = _ld.lightdome_lookup(lat, lon)

        # Collect weather future (cache check + HTTP fetch both ran off-thread).
        _wx_fetched = None
        _wx_cached  = None
        if _wx_future is not None:
            if _future_date:
                try:
                    _wx_tag, _wx_data = _wx_future.result()
                    if _wx_tag == "hit":
                        _wx_cached = _wx_data
                    else:
                        _wx_fetched = _wx_data  # already cached inside _wx_io
                except RuntimeError as _e:
                    _wx_exc = _e

        _t["io_wait_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Phase 2: satellite passes + visible_targets in parallel ---
        _tc = time.monotonic()

        sat_pass_list             = []
        starlink_train_list       = []
        sat_future_stale          = False
        sat_future_warn           = False
        sat_tle_stale             = False
        sat_network_error         = False
        sat_starlink_unavailable  = False

        _pass_futures: list[tuple[str, bool, _futures.Future]] = []
        _sl_passes_future: _futures.Future | None = None

        if fetch_satellites and not _sat_stale:
            from . import satellites as _sat_mod
            sat_future_warn = _sat_days_offset > 3
            for _norad_id, _sat_name in _tle_mod.TRACKED_SATELLITES:
                _tle_result = _tle_futures[_norad_id].result()
                if _tle_result.lines is None:
                    sat_network_error = True
                    continue
                if _tle_result.stale:
                    sat_tle_stale = True
                _pass_futures.append((
                    _sat_name,
                    _tle_result.stale,
                    _pool.submit(_sat_mod.satellite_passes,
                                 _tle_result.lines, lat, lon, sunset, sunrise),
                ))
            if _sl_future is not None:
                _sl_tles, _, _sl_error = _sl_future.result()
                if _sl_error and not _sl_tles:
                    sat_starlink_unavailable = True
                elif _sl_tles:
                    _sl_passes_future = _pool.submit(
                        _sat_mod.starlink_train_passes, _sl_tles, lat, lon, sunset, sunrise
                    )

        _vt_future: _futures.Future | None = None
        if fetch_targets:
            _site_sqm = ds_info.get("sqm") if ds_info else None
            _vt_future = _pool.submit(
                _tgt.visible_targets, lat, lon, sunset, sunrise, illumination,
                night_start=night_start, night_end=night_end, sky_sqm=_site_sqm, tz=tz,
            )

        # Collect satellite passes
        for _sat_name, _, _pass_f in _pass_futures:
            _result = _pass_f.result()
            if _result is None:
                sat_future_stale = True
                sat_future_warn  = False
                continue
            for _sp in _result:
                _sp.satellite_name = _sat_name
            sat_pass_list.extend(_result)
        sat_pass_list.sort(key=lambda p: p.rise_time)

        if _sl_passes_future is not None:
            starlink_train_list = _sl_passes_future.result()

        target_list = _vt_future.result() if _vt_future is not None else []
        _t["sat_targets_ms"] = round((time.monotonic() - _tc) * 1000)

    # executor exits — all futures are resolved

    mw_summary = None   # populated after weather — see below

    bortle_score = (
        round(max(0.0, (10 - ds_info.get("bortle_class", 10)) / 9 * 10), 1)
        if ds_info and ds_info.get("bortle_class") is not None
        else None
    )

    # --- Weather ---
    night_points     = []
    weather_score    = None
    wx_source        = None
    wx_fetched_at    = None
    wx_error         = None
    wx_pending       = False
    wx_no_data       = False
    wx_archive_error = False

    if fetch_weather:
        try:
            if not _future_date:
                # Past date: sequential fetch (no parallelism needed — uncommon path)
                try:
                    days_ago = (_now.date() - target).days
                    if days_ago <= wx.OpenMeteoPastProvider._MAX_PAST_DAYS:
                        provider = wx.OpenMeteoPastProvider(days_ago + 2)
                    else:
                        start_str = target.strftime("%Y-%m-%d")
                        end_str   = (target + timedelta(days=1)).strftime("%Y-%m-%d")
                        provider  = wx.OpenMeteoHistoricalProvider(start_str, end_str)

                    points  = provider.forecast(lat, lon)
                    before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                    during  = [p for p in points if sunset < p.time < sunrise]
                    after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                    night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                    if during or after:
                        if any(p.cloud_cover_pct is not None for p in night_points):
                            weather_score = scoring.weighted_weather_score(
                                night_points, night_start, night_end, wx.rate_conditions
                            )
                            wx_source     = provider.name
                            wx_fetched_at = datetime.now(timezone.utc).isoformat()
                        else:
                            wx_no_data   = True
                            night_points = []
                    else:
                        wx_no_data   = True
                        night_points = []
                except RuntimeError:
                    wx_archive_error = days_ago > wx.OpenMeteoPastProvider._MAX_PAST_DAYS
                    wx_no_data       = not wx_archive_error
                    night_points     = []
            else:
                # Future date: use cached or concurrently-fetched result
                if _wx_exc is not None:
                    raise _wx_exc
                if _wx_cached is not None:
                    points, wx_source, wx_fetched_at = _wx_deserialize(_wx_cached)
                elif _wx_fetched is not None:
                    points, wx_source, wx_fetched_at = _wx_fetched
                else:
                    # Pass target string parameters into the provider to bypass the 7-day programmatic limitation
                    points, wx_source, wx_fetched_at = wx.forecast(lat, lon)

                before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                during  = [p for p in points if sunset < p.time < sunrise]
                after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                if during or after:
                    if any(p.cloud_cover_pct is not None for p in night_points):
                        weather_score = scoring.weighted_weather_score(
                            night_points, night_start, night_end, wx.rate_conditions
                        )
                        wx_source = _scope_wx_source(wx_source, night_points)
                    else:
                        wx_no_data    = True
                        wx_source     = None
                        wx_fetched_at = None
                        night_points  = []
                else:
                    wx_pending    = True
                    wx_source     = None
                    wx_fetched_at = None
                    night_points  = []
        except RuntimeError as e:
            wx_error = str(e)

    # --- Active meteor showers (always computed — fast date check only) ---
    active_showers = _tgt.active_meteor_showers(target)

    # --- Aurora outlook (fetches already resolved off-thread; per-location math
    # is cheap). On the use_cycle_window path night_start/night_end come from
    # the fixed-twilight-offset approximation — minutes-scale error against the
    # 3-hour Kp bins, acceptable. Non-fatal on any failure.
    aurora_info = None
    if _aurora_future is not None and night_start and night_end:
        try:
            _kp_rows, _kp_stale, _kp_outlook, _outlook_stale = _aurora_future.result()
            aurora_info = _aur.aurora_for_night(
                lat, lon, target, night_start, night_end,
                kp_rows=_kp_rows, kp_stale=_kp_stale,
                outlook=_kp_outlook, outlook_stale=_outlook_stale,
                weather_points=night_points,
                light_dome=light_dome_info,
            )
        except Exception as _ae:
            log.debug("aurora computation failed (non-fatal): %s", _ae)

    # --- Moon altitude track (used for best-time scoring across all target types) ---
    # Sampled sunset→sunrise at 15-min resolution.  Requires de421.bsp; falls back
    # gracefully (moon_alts=None) when the ephemeris is absent (e.g. unit tests).
    _moon_alts: list | None = None
    if fetch_targets and target_list:
        try:
            _step         = timedelta(minutes=15)
            _sample_times = []
            _t_samp       = sunset
            while _t_samp <= sunrise:
                _sample_times.append(_t_samp)
                _t_samp += _step
            _moon_alt_vals = se.moon_altitude_track(lat, lon, _sample_times)
            _moon_alts     = list(zip(_sample_times, _moon_alt_vals))
        except Exception as _mae:
            log.debug("moon_altitude_track failed (non-fatal): %s", _mae)

    # --- Milky Way arch summary (needs weather + moon_alts for best-viewing-time) ---
    if fetch_targets:
        _mw_targets = [t for t in target_list if t.type == "milky_way"]
        if _mw_targets:
            try:
                mw_summary = _mw_arch_summary(
                    _mw_targets,
                    lat=lat,
                    moonrise=moonrise,
                    moonset=moonset,
                    moon_illumination_pct=illumination,
                    moon_alts=_moon_alts,
                    weather_points=night_points or None,
                )
                mw_summary["core_max_alt_deg"] = round(_mw_core_max(lat))
            except Exception as _e:
                log.debug("mw_arch_summary failed: %s", _e)

    # --- Phase 1: Condition Vectors — apply after weather + moon_alts resolved ---
    if fetch_targets and target_list:
        _apply_condition_vectors(target_list, night_points, light_dome_info, illumination, _moon_alts)

    # --- Overall rating ---
    _tc = time.monotonic()
    rating = scoring.rate_night(moon_score, dark_score, weather_score, bortle_score)
    _t["scoring_ms"] = round((time.monotonic() - _tc) * 1000)
    _t["total_ms"] = round((time.monotonic() - _t0) * 1000)

    log.info(
        "assemble_night timing lat=%.2f lon=%.2f date=%s wx_cached=%s | "
        "sky_events=%dms event_parse=%dms moon=%dms lunar_cycle=%dms "
        "io_wait=%dms sat_targets=%dms scoring=%dms total=%dms",
        lat, lon, target, _wx_cached is not None,
        _t["sky_events_ms"], _t["event_parse_ms"], _t["moon_ms"],
        _t["lunar_cycle_ms"], _t["io_wait_ms"],
        _t.get("sat_targets_ms", 0), _t["scoring_ms"],
        _t["total_ms"],
    )

    return NightReport(
        date=target,
        lat=lat,
        lon=lon,
        display_name=display_name,
        tz_name=str(tz),
        events=night_events,
        sunset=sunset,
        sunrise=sunrise,
        night_start=night_start,
        night_end=night_end,
        moonrise=moonrise,
        moonset=moonset,
        phase_name=phase_name,
        illumination_pct=illumination,
        moon_score=moon_score,
        moon_distance_km=round(moon_dist_km),
        moon_special=moon_special,
        moon_eclipses=moon_eclipses,
        dark_intervals=display_dark_intervals,
        dark_hours=round(display_dark_hours, 2),
        dark_cycle=cycle,
        dark_score=dark_score,
        light_pollution=ds_info,
        bortle_score=bortle_score,
        light_dome=light_dome_info,
        weather_points=night_points,
        weather_score=weather_score,
        wx_source=wx_source,
        wx_fetched_at=wx_fetched_at,
        wx_pending=wx_pending,
        wx_no_data=wx_no_data,
        wx_archive_error=wx_archive_error,
        wx_error=wx_error,
        score=rating["score"],
        score_components=rating["components"],
        visible_targets=target_list,
        mw_summary=mw_summary,
        active_showers=active_showers,
        aurora=aurora_info,
        sat_passes=sat_pass_list,
        sat_stale=_sat_stale,
        sat_future_stale=sat_future_stale,
        sat_future_warn=sat_future_warn,
        sat_tle_stale=sat_tle_stale,
        sat_network_error=sat_network_error,
        starlink_trains=starlink_train_list,
        sat_starlink_unavailable=sat_starlink_unavailable,
    )
