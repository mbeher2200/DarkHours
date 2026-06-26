#!/usr/bin/env python3
"""Night sky prediction engine — assembles a NightReport for a given location and date."""

import concurrent.futures as _futures
import dataclasses
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

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


def _wx_serialize(points: list, source: str) -> dict:
    """Serialize weather forecast results for DynamoDB caching."""
    return {
        "source": source,
        "points": [
            {**dataclasses.asdict(p), "time": p.time.isoformat()}
            for p in points
        ],
    }


def _wx_deserialize(cached: dict) -> tuple:
    """Deserialize cached weather data back to (list[WeatherPoint], source)."""
    field_names = {f.name for f in dataclasses.fields(wx.WeatherPoint)}
    points = []
    for p in cached["points"]:
        kwargs = {k: v for k, v in p.items() if k in field_names}
        kwargs["time"] = datetime.fromisoformat(kwargs["time"])
        points.append(wx.WeatherPoint(**kwargs))
    return points, cached["source"]


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

        cloud  = _bt_cloud_frac(t, weather_points) if weather_points else 0.0
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
                nearest = min(
                    weather_points,
                    key=lambda p: abs((p.time - window.best_time).total_seconds()),
                )
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
) -> NightReport:
    """
    Compute a complete NightReport for the given location and date.

    Raises ValueError if no sunset or sunrise can be found for the
    date/location (e.g. polar day/night).
    """
    def _local(dt):
        return dt.astimezone(tz)

    _t = {}  # timing checkpoints — emitted as a single log line at end
    _t0 = time.monotonic()
    _now = datetime.now(timezone.utc)

    # --- I/O kicked off immediately — independent of all Skyfield work ---
    # darksky (S3 raster) and weather (HTTP) need only lat/lon, which we have now.
    # They run concurrently with sky_events + moon + lunar_cycle on the main thread.
    _wx_cache_key = f"wx2|{lat:.2f}|{lon:.2f}|{target.isoformat()}"
    _wx_cached    = None
    _wx_exc       = None
    _wx_thread    = None

    # TLE fetches also need nothing from Skyfield — start them alongside weather.
    _tle_futures: dict[int, _futures.Future] = {}
    _sl_future:   _futures.Future | None     = None
    _sat_stale        = False
    _sat_days_offset  = (target - (datetime.now(timezone.utc).date() - timedelta(days=1))).days

    _max_workers = 3
    if fetch_satellites and _sat_days_offset >= 0:
        # 3 individual TLE fetches + 1 Starlink group fetch on top of ds + wx
        _max_workers = 8

    with _futures.ThreadPoolExecutor(max_workers=_max_workers) as _pool:
        _ds_future = _pool.submit(_ds.lookup, lat, lon)

        # Heuristic: start weather for tonight-or-future dates without waiting for
        # sunrise. "Tonight" may be yesterday in UTC when the night spans midnight
        # (e.g. 03:00 UTC — still before sunrise for a US location). Subtracting
        # 1 day catches that case; the precise _future_date check after sky_events
        # discards the thread result if the night has already fully passed.
        if fetch_weather and target >= datetime.now(timezone.utc).date() - timedelta(days=1):
            _wx_cached = _ports.get_backend().cache.get(_wx_cache_key)
            if _wx_cached is None:
                _wx_thread = _pool.submit(wx.forecast, lat, lon)

        # TLE fetches — start immediately, overlaps with ~600 ms of Skyfield work.
        if fetch_satellites:
            from . import tle_provider as _tle_mod
            _sat_stale = _sat_days_offset < 0
            if not _sat_stale:
                for _norad_id, _ in _tle_mod.TRACKED_SATELLITES:
                    _tle_futures[_norad_id] = _pool.submit(_tle_mod.get_tle, _norad_id)
                _sl_future = _pool.submit(_tle_mod.get_starlink_train_tles)

        # --- Skyfield work runs concurrently with the I/O threads above ---
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
            intervals          = se.dark_moon_intervals(events, night_start, night_end)
            dark_hours_tonight = sum((e - s).total_seconds() for s, e in intervals) / 3600
            total_astro_hours  = (night_end - night_start).total_seconds() / 3600

            # Moon score: weight moonlit fraction by K&S sky-brightening credit rather
            # than the naive (1 − illum/100) approximation.  K&S is evaluated at the
            # site-wide proxy geometry (90° sep, 30° alt) — the darkest accessible sky.
            #
            #   score = 10 × (moon_free_frac  +  moonlit_frac × ks_credit)
            #
            # Key improvements over the naive formula:
            #   50% quarter moon → credit 0.31  (was 0.50) — correctly penalised
            #   75% gibbous      → credit 0.00  (was 0.25) — correctly zeroed
            #   ≤15% crescent    → credit ~0.96 (was ~0.85) — minor difference only
            moonlit_frac = 1.0 - (dark_hours_tonight / total_astro_hours) if total_astro_hours > 0 else 0.0
            moon_score   = round(10 * ((1 - moonlit_frac) + moonlit_frac * ks_moon_credit(illumination)), 1)

            # Crescent exemption for the *displayed* Clear Dark Sky Hours:
            # illumination ≤ 20% → K&S shows Δmag < 0.25 at 90° sep regardless of altitude
            # (imperceptible-to-minor).  Report the full astronomical window as dark rather
            # than subtracting the few hours the crescent is technically above the horizon.
            # The underlying geometric intervals are preserved for weather score weighting.
            if illumination <= KS_CRESCENT_EXEMPTION_PCT and total_astro_hours > 0:
                display_dark_hours     = total_astro_hours
                display_dark_intervals = [(night_start, night_end)]
            else:
                display_dark_hours     = dark_hours_tonight
                display_dark_intervals = intervals
        else:
            # No astronomical darkness (polar summer / always dark) — timing
            # is undefined; fall back to K&S credit score only.
            intervals              = []
            dark_hours_tonight     = 0.0
            display_dark_hours     = 0.0
            display_dark_intervals = []
            moon_score             = round(10 * ks_moon_credit(illumination), 1)

        # --- Lunar cycle dark analysis ---
        _tc = time.monotonic()
        cycle      = se.lunar_cycle_dark_analysis(lat, lon, target, tz)
        dark_score = cycle["score"]
        _t["lunar_cycle_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Collect I/O (weather + darksky started at function entry) ---
        _tc = time.monotonic()
        _future_date = sunrise >= _now

        # Collect darksky (S3 raster read — almost certainly done by now)
        ds_info = _ds_future.result()

        # Light dome — precomputed H3 index lookup (O(log n), no raster read), so it's
        # safe on the initial page-load path. None when outside the index coverage.
        light_dome_info = _ld.lightdome_lookup(lat, lon)

        # Collect weather future (started at entry; may still be running if
        # Open-Meteo is slower than the Skyfield work above)
        if _wx_thread is not None:
            if _future_date:
                try:
                    _wx_fetched = _wx_thread.result()
                    try:
                        _ports.get_backend().cache.set(
                            _wx_cache_key, _wx_serialize(*_wx_fetched), ttl_seconds=_WX_CACHE_TTL
                        )
                    except Exception as _ce:
                        log.debug("Weather cache write failed (non-fatal): %s", _ce)
                except RuntimeError as _e:
                    _wx_exc     = _e
                    _wx_fetched = None
            else:
                _wx_fetched = None   # night already past; thread runs to completion in background
        else:
            _wx_fetched = None

        _t["io_wait_ms"] = round((time.monotonic() - _tc) * 1000)

        # --- Phase 2: satellite passes + visible_targets in parallel ---
        # TLE futures started at function entry are almost certainly done by now
        # (Skyfield took ~500–700 ms; Celestrak takes ~300–500 ms per TLE).
        # sunset + sunrise are now available, so we can submit the Skyfield pass
        # computation and visible_targets concurrently.
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
            _site_sqm = ds_info["sqm"] if ds_info and ds_info.get("sqm") is not None else None
            _vt_future = _pool.submit(
                _tgt.visible_targets, lat, lon, sunset, sunrise, illumination,
                night_start=night_start, night_end=night_end, sky_sqm=_site_sqm,
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

    mw_summary = None   # populated after weather — see below

    # executor exits — all futures are resolved

    bortle_score = (
        round(max(0.0, (10 - ds_info["bortle_class"]) / 9 * 10), 1)
        if ds_info and ds_info["bortle_class"] is not None
        else None
    )

    # --- Weather ---
    night_points     = []
    weather_score    = None
    wx_source        = None
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
                            wx_source = provider.name
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
                    points, wx_source = _wx_deserialize(_wx_cached)
                elif _wx_fetched is not None:
                    points, wx_source = _wx_fetched
                else:
                    # Defensive: _future_date=True but thread wasn't started.
                    # Fetch synchronously (rare: only if heuristic gap widens).
                    points, wx_source = wx.forecast(lat, lon)

                before  = [p for p in points if sunset - timedelta(hours=6) <= p.time <= sunset]
                during  = [p for p in points if sunset < p.time < sunrise]
                after   = [p for p in points if sunrise <= p.time <= sunrise + timedelta(hours=12)]
                night_points = (before[-1:] if before else []) + during + (after[:1] if after else [])

                if during or after:
                    if any(p.cloud_cover_pct is not None for p in night_points):
                        weather_score = scoring.weighted_weather_score(
                            night_points, night_start, night_end, wx.rate_conditions
                        )
                    else:
                        wx_no_data   = True
                        wx_source    = None
                        night_points = []
                else:
                    wx_pending   = True
                    wx_source    = None
                    night_points = []
        except RuntimeError as e:
            wx_error = str(e)

    # --- Active meteor showers (always computed — fast date check only) ---
    active_showers = _tgt.active_meteor_showers(target)

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
        wx_pending=wx_pending,
        wx_no_data=wx_no_data,
        wx_archive_error=wx_archive_error,
        wx_error=wx_error,
        score=rating["score"],
        score_components=rating["components"],
        visible_targets=target_list,
        mw_summary=mw_summary,
        active_showers=active_showers,
        sat_passes=sat_pass_list,
        sat_stale=_sat_stale,
        sat_future_stale=sat_future_stale,
        sat_future_warn=sat_future_warn,
        sat_tle_stale=sat_tle_stale,
        sat_network_error=sat_network_error,
        starlink_trains=starlink_train_list,
        sat_starlink_unavailable=sat_starlink_unavailable,
    )
