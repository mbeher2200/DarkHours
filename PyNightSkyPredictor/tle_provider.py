#!/usr/bin/env python3
"""
TLE (Two-Line Element) acquisition for satellite pass prediction.

Separates fetch/cache concerns from orbit computation so the CLI and a
future webapp can each manage TLE lifecycle independently.  The webapp
would skip get_tle() entirely and supply its own TLE (fetched by a
background scheduler and stored in Redis/DB); the CLI calls get_tle()
which handles fetch, 6-hour cache, and stale-data fallback.

Public API:
    get_tle(norad_id)              → TLEResult
    get_starlink_train_tles()      → (list[tuple], stale, error)
    ISS_NORAD_ID                   → 25544
    TLE_TTL                        → 21600   (seconds — 6 hours)
    TRACKED_SATELLITES             → [(norad_id, display_name), ...]
"""

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import cache as _cache
from . import _http
from . import provider_health as _ph

log = logging.getLogger(__name__)

ISS_NORAD_ID      = 25544
HUBBLE_NORAD_ID   = 20580
TIANGONG_NORAD_ID = 48274

TLE_TTL     = 6 * 3600   # exactly 6 h — Celestrak rate-limit compliance
_USER_AGENT = "DarkHours/1.0 (open-source astronomical observation planner)"

# Satellites tracked by --satellites, in display-priority order.
# The display_name overrides whatever the TLE name line says.
TRACKED_SATELLITES: list[tuple[int, str]] = [
    (ISS_NORAD_ID,      "ISS"),
    (HUBBLE_NORAD_ID,   "Hubble Telescope"),
    (TIANGONG_NORAD_ID, "Tiangong"),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TLEResult:
    """Outcome of a TLE acquisition attempt."""
    lines: tuple[str, str, str] | None  # (name, line1, line2); None = complete failure
    stale: bool                          # True → using expired cache data (fetch failed)
    error: str | None                    # human-readable fetch error, or None on success


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_tle_raw(norad_id: int) -> str:
    """
    Fetch the raw 3-line TLE text from Celestrak for *norad_id*.
    Raises RuntimeError on any network or format failure.
    """
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _http.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 3:
            raise RuntimeError(
                f"Celestrak returned fewer than 3 TLE lines for NORAD {norad_id}"
            )
        log.debug("Fetched fresh TLE for NORAD %d (%d bytes)", norad_id, len(text))
        _ph.record("celestrak", "ok")
        return text
    except urllib.error.HTTPError as e:
        _ph.record("celestrak", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
        raise RuntimeError(f"Celestrak HTTP {e.code} for NORAD {norad_id}") from e
    except urllib.error.URLError as e:
        _ph.record("celestrak", "error", str(e.reason)[:120])
        raise RuntimeError(f"Celestrak unreachable: {e.reason}") from e


def _parse_tle(raw: str) -> tuple[str, str, str] | None:
    """Parse a raw TLE string into (name, line1, line2). Returns None if malformed."""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) >= 3:
        return lines[0], lines[1], lines[2]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tle(norad_id: int) -> TLEResult:
    """
    Return a TLEResult for *norad_id*.

    Acquisition strategy:
      1. Fresh cache hit   → return immediately (stale=False).
      2. Cache miss/expired → fetch from Celestrak, cache for TLE_TTL.
      3. Fetch fails        → fall back to the expired cache entry (stale=True)
                              and include the error message as a warning.
      4. No cache at all    → return lines=None with the error message.
    """
    key = f"tle|{norad_id}"

    # 1. Fresh cache hit
    cached = _cache.get(key)
    if cached is not None:
        parsed = _parse_tle(cached)
        if parsed:
            log.debug("TLE cache hit for NORAD %d", norad_id)
            return TLEResult(lines=parsed, stale=False, error=None)

    # 2. Attempt fresh fetch
    err_msg: str | None = None
    try:
        raw    = _fetch_tle_raw(norad_id)
        _cache.set(key, raw, ttl_seconds=TLE_TTL)
        parsed = _parse_tle(raw)
        if parsed is None:
            raise RuntimeError(f"Malformed TLE for NORAD {norad_id}: {raw!r}")
        return TLEResult(lines=parsed, stale=False, error=None)
    except Exception as e:
        err_msg = str(e)
        log.warning("TLE fetch failed for NORAD %d: %s", norad_id, err_msg)

    # 3. Stale fallback — read the expired entry without deleting it
    stale_raw = _cache.get_stale(key)
    if stale_raw is not None:
        parsed = _parse_tle(stale_raw)
        if parsed:
            log.debug("Using stale TLE for NORAD %d — Celestrak unreachable", norad_id)
            return TLEResult(lines=parsed, stale=True, error=err_msg)

    # 4. Complete failure — no cached data at all
    log.error("Celestrak TLE unavailable with no fallback for NORAD %d: %s",
              norad_id, err_msg, extra={"service": "celestrak"})
    return TLEResult(lines=None, stale=False, error=err_msg)


# ---------------------------------------------------------------------------
# Starlink train TLE acquisition
# ---------------------------------------------------------------------------

_STARLINK_GROUP_URL       = ("https://celestrak.org/NORAD/elements/gp.php"
                             "?GROUP=starlink&FORMAT=TLE")
_STARLINK_GROUP_CACHE_KEY = "tle|group|starlink"
_STARLINK_TRAIN_MM_MIN    = 15.5   # rev/day → altitude ≲ 430 km → raising phase
                                   # (operational Starlink sits at ~550 km / ~15.1 rev/day)
_STARLINK_RECENT_DAYS     = 21     # only launches within this window can form a visible train


def _parse_mean_motion(line2: str) -> float | None:
    """Extract mean motion (rev/day) from TLE line 2, fixed columns 52-63."""
    try:
        return float(line2[52:63])
    except (ValueError, IndexError):
        return None


def _parse_launch_date(line1: str):
    """
    Parse the launch date from the TLE line 1 International Designator (cols 9-16).

    Format: YYLAUNCHDAY_OF_YEAR + PIECE, e.g. "24191G" = 2024, day 191, piece G.
    Returns a date object or None if the field is absent / malformed.
    """
    from datetime import date, timedelta
    try:
        intl = line1[9:17].strip()
        if len(intl) < 5 or not intl[:2].isdigit() or not intl[2:5].isdigit():
            return None
        year_2d = int(intl[:2])
        year    = 2000 + year_2d if year_2d < 57 else 1900 + year_2d
        doy     = int(intl[2:5])
        return date(year, 1, 1) + timedelta(days=doy - 1)
    except (ValueError, IndexError):
        return None


def _filter_train_tles(raw: str) -> list[tuple[str, str, str]]:
    """
    Parse a multi-TLE block and return only raising-phase Starlinks from recent launches.

    Two-part filter:
      1. Mean motion ≥ _STARLINK_TRAIN_MM_MIN — satellite is below operational altitude
      2. Launch date within _STARLINK_RECENT_DAYS — satellite is from a recent deployment;
         older batches have spread out and no longer form a visible train even if they
         haven't yet reached full operational altitude
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=_STARLINK_RECENT_DAYS)

    lines  = [l.strip() for l in raw.splitlines() if l.strip()]
    result = []
    i = 0
    while i + 2 <= len(lines) - 1:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            mm          = _parse_mean_motion(l2)
            launch_date = _parse_launch_date(l1)
            is_raising  = mm is not None and mm >= _STARLINK_TRAIN_MM_MIN
            # Include if launch date is unknown (un-catalogued) OR within the cutoff
            is_recent   = launch_date is None or launch_date >= cutoff
            if is_raising and is_recent:
                result.append((name, l1, l2))
            i += 3
        else:
            i += 1
    log.debug("Filtered %d Starlink train candidates from group TLE", len(result))
    return result


def get_starlink_train_tles() -> tuple[list[tuple[str, str, str]], bool, str | None]:
    """
    Return (tles, stale, error) for Starlink satellites currently in raising phase.

    tles:  filtered list of (name, line1, line2) — only satellites with mean
           motion ≥ _STARLINK_TRAIN_MM_MIN (proxy for below-operational altitude)
    stale: True → using expired cache data after a failed refresh
    error: human-readable error if the fetch failed and no stale data existed

    The GROUP=starlink response is ~1-2 MB; a 30-second timeout is used.
    Same 6-hour TTL and stale-fallback strategy as individual TLE fetches.
    """
    key      = _STARLINK_GROUP_CACHE_KEY
    err_msg: str | None = None

    # 1. Fresh cache hit
    raw = _cache.get(key)

    if raw is None:
        # 2. Fetch fresh
        req = urllib.request.Request(_STARLINK_GROUP_URL, headers={"User-Agent": _USER_AGENT})
        try:
            with _http.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8").strip()
            _cache.set(key, raw, ttl_seconds=TLE_TTL)
            log.debug("Fetched Starlink group TLE (%d bytes)", len(raw))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # Celestrak's one-download-per-update policy: data hasn't changed
                # since our last successful fetch. Stale cache is still current.
                log.info("Celestrak Starlink group 403 — data unchanged since last fetch, using cache")
            else:
                err_msg = f"Celestrak HTTP {e.code} for Starlink group"
                log.warning("%s", err_msg)
        except urllib.error.URLError as e:
            err_msg = f"Celestrak unreachable (Starlink group): {e.reason}"
            log.warning("%s", err_msg)

    if raw is None:
        # 3. Stale fallback — always try this; on 403 the stale data IS current
        raw = _cache.get_stale(key)
        if raw:
            log.debug("Using stale Starlink group TLE")
            return _filter_train_tles(raw), True, None
        # No cache at all — silently skip trains rather than surfacing an error
        return [], False, None

    return _filter_train_tles(raw), False, None
