#!/usr/bin/env python3
"""
Aurora forecast — NOAA SWPC Kp products + a location-aware visibility model.

Two global (not per-location) SWPC products feed this module:

  * 3-day Kp forecast (JSON, 3-hour bins) — drives the nightly alert/card.
  * 27-day outlook (legacy text, daily largest Kp) — drives the calendar icon
    for nights beyond the Kp-forecast horizon.

Both are fetched once and cached under global keys, so one fetch serves every
location in a trip fan-out (see the single-flight lock below).

Visibility model
----------------
Geomagnetic latitude uses the centered-dipole approximation against the
IGRF-14 epoch-2025 north geomagnetic pole (80.8°N, 72.7°W). A corrected
geomagnetic (CGM) transform would be ±2-3° better in places like Europe, but
needs coefficient tables; the dipole is the right zero-dependency tradeoff for
tier margins of 3-9°.

The equatorward viewline of overhead aurora is modelled as
``66.5 − 2.05·Kp`` geomagnetic degrees, a linear fit that matches SWPC's
tabulated Kp → geomagnetic-latitude mapping to ~1°.

Tiers at ``margin = viewline − |maglat|``:
  * ``overhead``     margin ≤ 0   — oval reaches this latitude
  * ``naked_eye``    margin ≤ 3°  — visible low on the poleward horizon
  * ``photographic`` margin ≤ 9°  — a camera catches the glow on the horizon
    (aurora tops ~300 km ⇒ geometric horizon limit ~17°; 9° is the practical
    photographic rule)
  * anything wider → no aurora reported at all.

Public API:
    fetch_kp_forecast()        → (rows, stale)
    fetch_27day_outlook()      → ({iso_date: largest_kp}, stale)
    aurora_for_night(...)      → dict | None  (NightReport.aurora contract;
                                  Kp bins when they span the night, else outlook)
    nightly_aurora(...)        → dict | None  (3-day Kp product only)
    outlook_nightly_aurora(...) → dict | None (27-day outlook only)
"""

import json
import logging
import math
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import cache as _cache
from . import _http
from . import light_dome as _ld
from . import moonlight as _ml
from . import provider_health as _ph

log = logging.getLogger(__name__)

KP_URL      = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
OUTLOOK_URL = "https://services.swpc.noaa.gov/text/27-day-outlook.txt"

KP_TTL      = 1800    # 30 min — the 3-day product is refreshed several times a day
OUTLOOK_TTL = 21600   # 6 h — the 27-day outlook is issued weekly

KP_CACHE_KEY      = "swpc|kp3day"   # global keys — one fetch serves every location
OUTLOOK_CACHE_KEY = "swpc|27day"

KP_FORECAST_HORIZON_DAYS = 2   # 3-day product covers tonight + ~2 more nights
OUTLOOK_HORIZON_DAYS     = 27  # 27-day outlook covers issue day + 26 more

_USER_AGENT = "DarkHours/1.0 (open-source astronomical observation planner)"

# IGRF-14 epoch-2025 centered-dipole geomagnetic north pole. The south dipole
# pole is its exact antipode.
GM_POLE_LAT = 80.8
GM_POLE_LON = -72.7

VIEWLINE_KP0_MAGLAT  = 66.5   # equatorward edge of overhead aurora at Kp 0
VIEWLINE_DEG_PER_KP  = 2.05   # oval expansion per Kp step
NAKED_EYE_MARGIN_DEG = 3.0
PHOTO_MARGIN_DEG     = 9.0

_CLOUD_BLOCK_PCT    = 70     # same threshold as predictor._CLOUD_BLOCK_PCT
                             # (duplicated to avoid a circular import)
_WEATHER_GAP_SECS   = 5400   # 90-min nearest-neighbour tolerance, as elsewhere
_DOME_CAUTION_SCORE = _ld.MINOR_DOME_THRESHOLD  # glow_toward ≥ this → caution
_DOME_AIM_ALT_DEG   = 10.0   # aurora sits low — evaluate the dome at ~10° up

# Moonlight: aurora is an EMISSION source — the moon raises the sky background
# it must be seen against rather than washing the aurora itself, so moonlight
# degrades (never blocks alone), and brighter tiers tolerate more of it.
# Δ mag/arcsec² sky brightening at which each tier is degraded; overhead
# storms punch through any moon and have no entry.
_MOON_DELTA_DEGRADE = {
    "photographic": _ml.KS_MODERATE_THRESH,  # 0.50 — faint-aurora photography is moon-sensitive
    "naked_eye":    1.50,                    # the K&S 'severe' threshold
}

_KP_BIN_HOURS = 3

# Single-flight the global fetches: plan_trip fans out ~20 workers that would
# otherwise all miss the same cache key at once.
_fetch_lock = threading.Lock()

_WIND16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

# Strict anchored row match for the legacy 27-day text product — column drift
# is a real failure mode, so anything that doesn't match exactly is skipped.
_OUTLOOK_ROW_RE = re.compile(
    r"^(\d{4})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{1,2})\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)


# ---------------------------------------------------------------------------
# Pure geometry / science helpers
# ---------------------------------------------------------------------------

def geomagnetic_latitude(lat: float, lon: float) -> float:
    """Signed geomagnetic latitude (degrees) via the centered-dipole formula."""
    lat_r  = math.radians(lat)
    pole_r = math.radians(GM_POLE_LAT)
    dlon_r = math.radians(lon - GM_POLE_LON)
    s = (math.sin(lat_r) * math.sin(pole_r)
         + math.cos(lat_r) * math.cos(pole_r) * math.cos(dlon_r))
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def look_bearing(lat: float, lon: float) -> float:
    """Great-circle initial bearing (° from true north) toward the dipole pole.

    Northern-hemisphere sites aim at the north dipole pole; southern sites aim
    at the south dipole pole — computed directly as a bearing to the antipode
    (−80.8°, +107.3°E), never as a flipped north-pole bearing, so a Tasmanian
    photographer is told to look ~S, not N.
    """
    if lat >= 0:
        pole_lat, pole_lon = GM_POLE_LAT, GM_POLE_LON
    else:
        pole_lat, pole_lon = -GM_POLE_LAT, GM_POLE_LON + 180.0

    lat1 = math.radians(lat)
    lat2 = math.radians(pole_lat)
    dlon = math.radians(pole_lon - lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return math.degrees(math.atan2(y, x)) % 360.0


def _wind16(bearing: float) -> str:
    """16-point compass label for a bearing in degrees."""
    return _WIND16[int((bearing % 360.0) / 22.5 + 0.5) % 16]


def kp_to_viewline(kp: float) -> float:
    """Geomagnetic latitude of the equatorward edge of overhead aurora."""
    return VIEWLINE_KP0_MAGLAT - VIEWLINE_DEG_PER_KP * kp


def visibility_tier(maglat_abs: float, kp: float) -> tuple[str, float]:
    """Classify visibility at |maglat| for the given Kp.

    Returns (tier, margin) where margin = viewline − |maglat|; boundaries are
    inclusive (margin exactly 3.0 → naked_eye, exactly 9.0 → photographic).
    """
    margin = kp_to_viewline(kp) - maglat_abs
    if margin <= 0:
        return "overhead", margin
    if margin <= NAKED_EYE_MARGIN_DEG:
        return "naked_eye", margin
    if margin <= PHOTO_MARGIN_DEG:
        return "photographic", margin
    return "none", margin


def kp_to_g_scale(kp: float) -> str | None:
    """NOAA geomagnetic-storm scale for a Kp value (G1 at Kp 5 … G5 at Kp 9)."""
    if kp < 5:
        return None
    return f"G{min(5, int(kp) - 4)}"


# ---------------------------------------------------------------------------
# SWPC fetchers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = 10) -> str:
    """GET *url* with the project User-Agent; provider-health accounting."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _http.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        _ph.record("swpc", "ok")
        return text
    except urllib.error.HTTPError as e:
        _ph.record("swpc", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
        raise RuntimeError(f"SWPC HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        _ph.record("swpc", "error", str(e.reason)[:120])
        raise RuntimeError(f"SWPC unreachable: {e.reason}") from e


def _cached_fetch(key: str, ttl: int, fetch_parse, empty):
    """Fresh cache → single-flight fetch → stale fallback → *empty*."""
    cached = _cache.get(key)
    if cached is not None:
        return cached, False
    with _fetch_lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached, False
        try:
            fresh = fetch_parse()
            _cache.set(key, fresh, ttl_seconds=ttl)
            return fresh, False
        except Exception as e:
            log.warning("SWPC fetch failed for %s: %s", key, e)
        stale = _cache.get_stale(key)
        if stale is not None:
            log.debug("Using stale SWPC data for %s", key)
            return stale, True
        return empty, False


def _parse_kp_json(text: str) -> list[dict]:
    """Keep only well-formed rows from the 3-day Kp forecast JSON."""
    data = json.loads(text)
    rows = []
    for row in data if isinstance(data, list) else []:
        try:
            rows.append({
                "time_tag":   str(row["time_tag"]),
                "kp":         float(row["kp"]),
                "observed":   str(row.get("observed") or "predicted"),
                "noaa_scale": row.get("noaa_scale"),
            })
        except (TypeError, KeyError, ValueError):
            continue
    return rows


def fetch_kp_forecast() -> tuple[list[dict], bool]:
    """3-day Kp forecast rows (3-hour bins, naive-UTC time_tags), (rows, stale)."""
    return _cached_fetch(
        KP_CACHE_KEY, KP_TTL,
        lambda: _parse_kp_json(_fetch_url(KP_URL)),
        empty=[],
    )


def _parse_27day_text(text: str) -> dict[str, float]:
    """{iso_date: largest_kp} from the legacy 27-day outlook text product."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((":", "#")):
            continue
        m = _OUTLOOK_ROW_RE.match(line)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
            out[d.isoformat()] = float(m.group(6))
        except ValueError:
            continue
    return out


def fetch_27day_outlook() -> tuple[dict[str, float], bool]:
    """Daily largest-Kp outlook, ({iso_date: kp}, stale). Empty dict on failure
    or if NOAA alters the column structure (strict per-row regex)."""
    return _cached_fetch(
        OUTLOOK_CACHE_KEY, OUTLOOK_TTL,
        lambda: _parse_27day_text(_fetch_url(OUTLOOK_URL)),
        empty={},
    )


# ---------------------------------------------------------------------------
# Night / calendar assembly
# ---------------------------------------------------------------------------

def _parse_time_tag(tag: str) -> datetime:
    """SWPC time_tags are naive UTC — attach the zone explicitly."""
    return datetime.fromisoformat(tag).replace(tzinfo=timezone.utc)


def _condition_vector(
    win_start: datetime,
    win_end: datetime,
    weather_points: "list | None",
    light_dome: "dict | None",
    bearing: float,
    tier: str = "photographic",
    moon_illum_pct: "float | None" = None,
    moon_alts: "list | None" = None,
) -> tuple[list, str, bool, bool]:
    """Photography blockers for an aurora window
    → (blockers, viability, dome_caution, moon_caution).

    Cloud: all in-window points > _CLOUD_BLOCK_PCT ⇒ blocked, some ⇒ degraded,
    no weather data ⇒ fail-open (ok), same as the target condition vectors.
    Light dome toward the look bearing is a caution — degrades, never blocks.
    Moonlight (tier-scaled, see _MOON_DELTA_DEGRADE) likewise degrades, never
    blocks; missing moon data (moon_alts=None) fails open like missing weather.
    """
    blockers: list[str] = []
    viability = "ok"
    if weather_points:
        gap = timedelta(seconds=_WEATHER_GAP_SECS)
        candidates = [p for p in weather_points
                      if win_start - gap <= p.time <= win_end + gap]
        blocked_pts = [p for p in candidates
                       if p.cloud_cover_pct is not None
                       and p.cloud_cover_pct > _CLOUD_BLOCK_PCT]
        if candidates and blocked_pts:
            blockers.append("cloud")
            viability = "blocked" if len(blocked_pts) == len(candidates) else "degraded"

    dome_caution = False
    if light_dome is not None:
        # Reconstruct the detailed-format dict glow_toward() needs from the
        # summarize_horizons() output — same as _apply_condition_vectors.
        detailed = {
            d: {
                "score": light_dome["scores"][d],
                "dome_height_deg": light_dome["dome_heights"][d],
            }
            for d in _ld.DIRS_8
        }
        if _ld.glow_toward(detailed, bearing, _DOME_AIM_ALT_DEG) >= _DOME_CAUTION_SCORE:
            dome_caution = True
            if viability == "ok":
                viability = "degraded"

    moon_caution = False
    degrade_at = _MOON_DELTA_DEGRADE.get(tier)
    if degrade_at is not None and moon_illum_pct and moon_alts:
        in_window = [alt for t, alt in moon_alts if win_start <= t <= win_end]
        moon_alt_max = max(in_window) if in_window else None
        if moon_alt_max is not None and moon_alt_max > 0:
            # 90° separation = the darkest-accessible-sky proxy geometry
            # (same convention as ks_moon_credit); aurora sits low, so the
            # background is evaluated at the dome aim altitude.
            delta = _ml.ks_delta_mag(moon_illum_pct, 90.0, moon_alt_max,
                                     target_alt_deg=_DOME_AIM_ALT_DEG)
            if delta >= degrade_at:
                moon_caution = True
                blockers.append("moonlight")
                if viability == "ok":
                    viability = "degraded"
    return blockers, viability, dome_caution, moon_caution


def _result_dict(lat, lon, kp_max, kp_source, noaa_scale, tier, margin,
                 peak_start, peak_end, blockers, viability, dome_caution,
                 moon_caution, stale) -> dict:
    """The NightReport.aurora JSON contract (ISO strings only, so the dict
    survives both the dataclasses.asdict serializer and json.dumps in the
    trip night cache)."""
    bearing = look_bearing(lat, lon)
    return {
        "kp_max":              round(kp_max, 2),
        "kp_source":           kp_source,
        "noaa_scale":          noaa_scale,
        "peak_start_utc":      peak_start.isoformat() if peak_start else None,
        "peak_end_utc":        peak_end.isoformat() if peak_end else None,
        "maglat_deg":          round(geomagnetic_latitude(lat, lon), 1),
        "viewline_maglat_deg": round(kp_to_viewline(kp_max), 1),
        "margin_deg":          round(margin, 1),
        "tier":                tier,
        "look_bearing_deg":    round(bearing, 1),
        "look_direction":      _wind16(bearing),
        "blockers":            blockers,
        "light_dome_caution":  dome_caution,
        "moonlight_caution":   moon_caution,
        "viability":           viability,
        "stale":               stale,
    }


def nightly_aurora(
    lat: float,
    lon: float,
    dark_start: "datetime | None",
    dark_end: "datetime | None",
    kp_rows: "list[dict] | None" = None,
    weather_points: "list | None" = None,
    light_dome: "dict | None" = None,
    stale: bool = False,
    moon_illum_pct: "float | None" = None,
    moon_alts: "list | None" = None,
) -> "dict | None":
    """Aurora outlook for one night from the 3-day Kp product, or None when
    there is nothing to report (no astronomical darkness, no forecast bins
    overlapping the dark window, or activity below the photographic tier).

    Callers that also want the 27-day-outlook fallback for nights the Kp
    product doesn't cover should use aurora_for_night() instead.
    """
    if dark_start is None or dark_end is None:
        return None  # no true darkness (polar summer) → no aurora, any Kp

    if kp_rows is None:
        kp_rows, stale = fetch_kp_forecast()
    if not kp_rows:
        return None

    # Bins are [t, t+3h); keep the ones overlapping the dark window.
    bins = []
    for row in kp_rows:
        try:
            t0 = _parse_time_tag(row["time_tag"])
        except ValueError:
            continue
        t1 = t0 + timedelta(hours=_KP_BIN_HOURS)
        if t0 < dark_end and t1 > dark_start:
            bins.append((t0, t1, row))
    if not bins:
        return None

    kp_max = max(b[2]["kp"] for b in bins)
    tier, margin = visibility_tier(abs(geomagnetic_latitude(lat, lon)), kp_max)
    if tier == "none":
        return None

    # Peak window: the run of contiguous max-Kp bins around the first maximum,
    # clipped to the dark window.
    peak_i = next(i for i, b in enumerate(bins) if b[2]["kp"] == kp_max)
    lo = hi = peak_i
    while lo > 0 and bins[lo - 1][2]["kp"] == kp_max and bins[lo - 1][1] == bins[lo][0]:
        lo -= 1
    while hi < len(bins) - 1 and bins[hi + 1][2]["kp"] == kp_max and bins[hi][1] == bins[hi + 1][0]:
        hi += 1
    peak_start = max(bins[lo][0], dark_start)
    peak_end   = min(bins[hi][1], dark_end)
    peak_row   = bins[peak_i][2]

    blockers, viability, dome_caution, moon_caution = _condition_vector(
        peak_start, peak_end, weather_points, light_dome, look_bearing(lat, lon),
        tier=tier, moon_illum_pct=moon_illum_pct, moon_alts=moon_alts)

    return _result_dict(
        lat, lon, kp_max, peak_row["observed"],
        peak_row.get("noaa_scale") or kp_to_g_scale(kp_max),
        tier, margin, peak_start, peak_end,
        blockers, viability, dome_caution, moon_caution, stale)


def outlook_nightly_aurora(
    lat: float,
    lon: float,
    d: date,
    dark_start: "datetime | None",
    dark_end: "datetime | None",
    outlook: "dict[str, float] | None" = None,
    weather_points: "list | None" = None,
    light_dome: "dict | None" = None,
    stale: bool = False,
    moon_illum_pct: "float | None" = None,
    moon_alts: "list | None" = None,
) -> "dict | None":
    """Outlook-grade aurora for one night from the 27-day daily-largest-Kp
    product, in the same JSON shape as nightly_aurora but with kp_source
    "outlook" and no peak window (the product has no intra-night resolution —
    the cloud check runs over the whole dark window instead). None when the
    date is uncovered or activity is below the photographic tier.
    """
    if dark_start is None or dark_end is None:
        return None

    if outlook is None:
        outlook, stale = fetch_27day_outlook()
    kp = outlook.get(d.isoformat())
    if kp is None:
        return None
    tier, margin = visibility_tier(abs(geomagnetic_latitude(lat, lon)), kp)
    if tier == "none":
        return None

    blockers, viability, dome_caution, moon_caution = _condition_vector(
        dark_start, dark_end, weather_points, light_dome, look_bearing(lat, lon),
        tier=tier, moon_illum_pct=moon_illum_pct, moon_alts=moon_alts)

    return _result_dict(
        lat, lon, kp, "outlook", kp_to_g_scale(kp),
        tier, margin, None, None,
        blockers, viability, dome_caution, moon_caution, stale)


def aurora_for_night(
    lat: float,
    lon: float,
    d: date,
    dark_start: "datetime | None",
    dark_end: "datetime | None",
    kp_rows: "list[dict] | None" = None,
    kp_stale: bool = False,
    outlook: "dict[str, float] | None" = None,
    outlook_stale: bool = False,
    weather_points: "list | None" = None,
    light_dome: "dict | None" = None,
    moon_illum_pct: "float | None" = None,
    moon_alts: "list | None" = None,
) -> "dict | None":
    """Unified nightly aurora: the 3-day Kp product when its bins cover the
    night's dark window, otherwise the 27-day outlook. This is what keeps the
    calendar icon and the full night report consistent — a night that earned
    the icon from the outlook must show the same story when opened.

    A below-tier verdict from the Kp product is authoritative (no outlook
    fallback for it) — the fallback only covers nights the Kp bins don't reach.
    """
    if dark_start is None or dark_end is None:
        return None

    if kp_rows:
        # Use the Kp bins only when the product spans the ENTIRE dark window —
        # a boundary night the product covers partially (its last bins end
        # mid-night) would otherwise get a kp_max from a truncated sample,
        # while the calendar icon was derived from the full-night outlook.
        first = _parse_time_tag(kp_rows[0]["time_tag"])
        last  = _parse_time_tag(kp_rows[-1]["time_tag"]) + timedelta(hours=_KP_BIN_HOURS)
        if first <= dark_start and last >= dark_end:
            return nightly_aurora(lat, lon, dark_start, dark_end,
                                  kp_rows=kp_rows, weather_points=weather_points,
                                  light_dome=light_dome, stale=kp_stale,
                                  moon_illum_pct=moon_illum_pct, moon_alts=moon_alts)

    return outlook_nightly_aurora(lat, lon, d, dark_start, dark_end,
                                  outlook=outlook, weather_points=weather_points,
                                  light_dome=light_dome, stale=outlook_stale,
                                  moon_illum_pct=moon_illum_pct, moon_alts=moon_alts)
