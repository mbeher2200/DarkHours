#!/usr/bin/env python3
"""
TLE (Two-Line Element) acquisition for satellite pass prediction.

The public API is split in two on purpose, and the split is load-bearing:

  * ``cached_tle`` / ``cached_starlink_trains`` — read the cache and nothing else.
    These contain no network code at all. **This is what a user request calls.**
  * ``refresh_tle`` / ``refresh_starlink_trains`` / ``warm_cache`` — fetch from
    Celestrak and write the cache. Only the scheduled warmer and the CLI call these.

TLE retrieval must never happen on the user's request path. That was previously a
convention enforced by a cache TTL and a warmer, and it failed silently for months:
the Starlink group entry could never be written (see ``_STARLINK_TRAINS_CACHE_KEY``),
so every ``/night?satellites=true`` re-downloaded 1.8 MB from Celestrak, and the
individually-tracked TLEs were refreshed by whichever user happened to ask first
after they expired. Making it structural — the request path calls functions that
have no ``urlopen`` in them — is what makes the guarantee hold, because there is no
flag to misconfigure and no new call site that can quietly reintroduce a fetch.

Public API:
    cached_tle(norad_id)                 → TLEResult              (request path)
    cached_starlink_trains()             → (list[tuple], stale, error)
    refresh_tle(norad_id, timeout=…)     → TLEResult              (warmer/CLI)
    refresh_starlink_trains(timeout=…)   → (list[tuple], stale, error)
    warm_cache(timeout=…, force=True)    → WarmSummary            (warmer/CLI)
    get_tle(norad_id, timeout=…)         → TLEResult              (CLI convenience)
    get_starlink_train_tles(timeout=…)   → (list[tuple], stale, error)
    ISS_NORAD_ID                         → 25544
    TLE_TTL                              → 86400   (seconds — 24 hours)
    TRACKED_SATELLITES                   → [(norad_id, display_name), ...]
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import cache as _cache
from . import circuit_breaker as _cb
from . import _http
from . import provider_health as _ph
from . import rate_limiter as _rl

log = logging.getLogger(__name__)

ISS_NORAD_ID      = 25544
HUBBLE_NORAD_ID   = 20580
TIANGONG_NORAD_ID = 48274

# Retention, not freshness. The warmer revalidates every 6 h (see warmer_stack.py);
# this is the point at which DynamoDB may *delete* the row, and it is deliberately
# four refresh cycles wide so three consecutive warm failures still leave a usable
# copy. Keeping the two equal is what stranded us before: the row was deleted the
# moment it went stale, get_stale() then had nothing to serve, and Celestrak's
# "unchanged since your last download" 403 became unrecoverable — every request
# re-asked and got the same 403. Orbital elements degrade gracefully; a day-old TLE
# still predicts passes usefully, and is infinitely better than none.
#
# Note the warmer now *extends* this window on every run rather than only repairing
# an entry that already expired (see warm_cache), so in steady state the row's age
# never exceeds one refresh interval and the retention margin is never drawn down.
TLE_TTL     = 24 * 3600  # 24 h retention; revalidated every 6 h
_USER_AGENT = "DarkHours/1.0 (open-source astronomical observation planner)"

# urllib's timeout bounds a single socket operation, not the whole transfer, so
# these cap how long one stalled read may block — not how long a legitimate
# download may take. For the whole-transfer bound the group fetch needs, see
# _http.read_capped and _STARLINK_TRANSFER_DEADLINE below.
#
# The split is deliberate. _FETCH_TIMEOUT is the interactive budget: the CLI (which
# has no warmer behind it) fetches on a cold cache while a person waits, so it fails
# fast and lets the breaker take over. The warmer has nobody waiting, and its success
# is precisely what stops anyone else from ever needing to fetch, so it gets room to
# finish on a slow day.
_FETCH_TIMEOUT      = 5    # interactive (CLI) — fail fast, degrade to no satellites
_WARM_FETCH_TIMEOUT = 30   # background warmer — patience is free here

# Per-resource locks (mirrors weather.py's/aqicn.py's lock_for). Only the refresh
# path takes these — the cached_* readers need no serialization. The CLI can still
# fan out several refreshes concurrently (warm_cache runs them in sequence, but
# get_tle/get_starlink_train_tles are reachable from a concurrent caller), so the
# lock keys off the same string already used as the cache key: only one thread ever
# actually fetches a given NORAD id or the Starlink group at a time.
_tle_fetch_locks: dict[str, threading.Lock] = {}
_tle_fetch_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _tle_fetch_locks_guard:
        lock = _tle_fetch_locks.get(key)
        if lock is None:
            lock = _tle_fetch_locks[key] = threading.Lock()
        return lock

# Satellites tracked by --satellites, in display-priority order.
# The display_name overrides whatever the TLE name line says.
TRACKED_SATELLITES: list[tuple[int, str]] = [
    (ISS_NORAD_ID,      "ISS"),
    (HUBBLE_NORAD_ID,   "Hubble Telescope"),
    (TIANGONG_NORAD_ID, "Tiangong"),
]


def _tle_key(norad_id: int) -> str:
    return f"tle|{norad_id}"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TLEResult:
    """Outcome of a TLE acquisition attempt."""
    lines: tuple[str, str, str] | None  # (name, line1, line2); None = complete failure
    stale: bool                          # True → using expired cache data (fetch failed)
    error: str | None                    # human-readable fetch error, or None on success


@dataclass
class WarmKeyResult:
    """What warm_cache managed to do for one cache key."""
    key: str
    label: str
    status: str            # human-readable, for the log line
    verified: bool         # the value was read back out of the cache afterwards
    bytes: int = 0         # serialized size of the verified value
    error: str | None = None


@dataclass
class WarmSummary:
    ok: bool = True
    keys: list[WarmKeyResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "results": {
                k.label: {"status": k.status, "verified": k.verified, "bytes": k.bytes}
                for k in self.keys
            },
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _NotModified(Exception):
    """Celestrak answered 403: our cached copy is still the current one.

    Their one-download-per-update policy returns 403 — *"GP data has not updated
    since your last successful download"* — rather than 304. It is a successful
    revalidation, not a failure, and the correct response is to extend the cached
    entry's lifetime rather than to retry.
    """


def _fetch_tle_raw(norad_id: int, timeout: float = _WARM_FETCH_TIMEOUT) -> str:
    """
    Fetch the raw 3-line TLE text from Celestrak for *norad_id*.

    Raises _NotModified when Celestrak reports our copy is still current, and
    RuntimeError on any other network or format failure.
    """
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if not _cb.allow("celestrak"):
        raise _cb.unavailable("celestrak")
    try:
        with _rl.acquire("celestrak"), _http.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 3:
            # Content-level failure: Celestrak was reached, so this does not
            # count toward the breaker.
            raise RuntimeError(
                f"Celestrak returned fewer than 3 TLE lines for NORAD {norad_id}"
            )
        log.debug("Fetched fresh TLE for NORAD %d (%d bytes)", norad_id, len(text))
        _ph.record("celestrak", "ok")
        _cb.on_success("celestrak")
        return text
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # Not a failure: our copy is current. The caller revalidates.
            _ph.record("celestrak", "ok")
            _cb.on_success("celestrak")
            raise _NotModified(f"NORAD {norad_id} unchanged since last fetch") from e
        _ph.record("celestrak", "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
        _cb.on_failure("celestrak")
        raise RuntimeError(f"Celestrak HTTP {e.code} for NORAD {norad_id}") from e
    except urllib.error.URLError as e:
        _ph.record("celestrak", "error", str(e.reason)[:120])
        _cb.on_failure("celestrak")
        raise RuntimeError(f"Celestrak unreachable: {e.reason}") from e


def _parse_tle(raw: str) -> tuple[str, str, str] | None:
    """Parse a raw TLE string into (name, line1, line2). Returns None if malformed."""
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) >= 3:
        return lines[0], lines[1], lines[2]
    return None


# ---------------------------------------------------------------------------
# Request path — cache reads only. NOTHING here may touch the network.
# ---------------------------------------------------------------------------

def cached_tle(norad_id: int) -> TLEResult:
    """Return the cached TLE for *norad_id*, or a degraded result. Never fetches.

    Three outcomes:
      1. Fresh entry            → stale=False, error=None
      2. Expired entry only     → stale=True  (a day-old TLE still predicts passes)
      3. Nothing cached at all  → lines=None, logged at ERROR

    Case 3 is a warmer failure, not a user-visible upstream failure, but it is
    logged at ERROR under service=celestrak so it reaches UpstreamErrorAlarm: an
    empty cache here means satellites are silently missing from every request until
    someone notices, which is exactly the class of outage this module already had.
    """
    key = _tle_key(norad_id)

    cached = _cache.get(key)
    if cached is not None:
        parsed = _parse_tle(cached)
        if parsed:
            log.debug("TLE cache hit for NORAD %d", norad_id)
            return TLEResult(lines=parsed, stale=False, error=None)

    stale_raw = _cache.get_stale(key)
    if stale_raw is not None:
        parsed = _parse_tle(stale_raw)
        if parsed:
            log.warning(
                "Serving expired TLE for NORAD %d — the warmer has not refreshed it",
                norad_id,
            )
            return TLEResult(lines=parsed, stale=True,
                             error=f"cached TLE for NORAD {norad_id} is past its refresh window")

    msg = f"no cached TLE for NORAD {norad_id} — the warmer has not populated it"
    log.error("%s", msg, extra={"service": "celestrak"})
    return TLEResult(lines=None, stale=False, error=msg)


def cached_starlink_trains() -> tuple[list[tuple[str, str, str]], bool, str | None]:
    """Return the cached Starlink train TLEs, or a degraded result. Never fetches.

    An empty list is a legitimate answer — there is frequently no launch in the
    raising phase — so every check here is ``is not None``, not truthiness. Reading
    an empty cached list as "nothing cached" would send us back to fetching on the
    request path for the most common case.
    """
    key = _STARLINK_TRAINS_CACHE_KEY

    trains = _cache.get(key)
    if trains is not None:
        return _as_tle_tuples(trains), False, None

    trains = _cache.get_stale(key)
    if trains is not None:
        log.warning("Serving expired Starlink train TLEs — the warmer has not refreshed them")
        return _as_tle_tuples(trains), True, None

    msg = "no cached Starlink train TLEs — the warmer has not populated them"
    log.error("%s", msg, extra={"service": "celestrak"})
    return [], False, msg


def _as_tle_tuples(value) -> list[tuple[str, str, str]]:
    """Coerce the cached JSON (lists of 3 strings) back to the tuples callers expect."""
    out: list[tuple[str, str, str]] = []
    for entry in value or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 3:
            out.append((entry[0], entry[1], entry[2]))
    return out


# ---------------------------------------------------------------------------
# Starlink train TLE acquisition
# ---------------------------------------------------------------------------

_STARLINK_GROUP_URL       = ("https://celestrak.org/NORAD/elements/gp.php"
                             "?GROUP=starlink&FORMAT=TLE")

# The cache holds the *filtered* train list, never the raw group response.
#
# This is the whole bug. The previous key cached the raw GROUP=starlink body, which
# is ~1.8 MB — 4.5x DynamoDB's 400 KB item limit. put_item could never succeed, the
# rejection was swallowed at DEBUG, and set() returned nothing, so the row simply
# never existed and every single request re-downloaded the group. Filtering first
# takes the stored value from ~1.8 MB to a few KB. The key is new so a stale raw
# blob under the old name (local caches only — the DynamoDB row never existed) is
# never read back in the wrong shape.
_STARLINK_TRAINS_CACHE_KEY = "tle|starlink|trains"

_STARLINK_TRAIN_MM_MIN    = 15.5   # rev/day → altitude ≲ 430 km → raising phase
                                   # (operational Starlink sits at ~550 km / ~15.1 rev/day)
_STARLINK_RECENT_DAYS     = 21     # only launches within this window can form a visible train

# Ceiling on what we will store. The observed raising-phase population is ~880
# satellites (~150 KB serialized) before the recency filter narrows it; capping the
# stored list keeps the item comfortably small even if a filter change ever widens
# the match. Far more than enough trains to render.
_STARLINK_MAX_TRAINS = 400

# Whole-transfer bounds for the group fetch, which urllib's socket timeout does not
# provide (see _http.read_capped). The body is ~1.8 MB today; the ceiling leaves room
# for constellation growth while refusing a response that is clearly not a TLE file.
_STARLINK_MAX_BYTES          = 32 * 1024 * 1024
_STARLINK_TRANSFER_DEADLINE  = 45.0


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

    The recency cutoff is evaluated here, at filter time, and the result is what gets
    cached — so it is frozen for up to one refresh interval (6 h) against a 21-day
    window. That drift is immaterial; caching the raw block instead is what was not.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_STARLINK_RECENT_DAYS)

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


# ---------------------------------------------------------------------------
# Refresh path — the only code in this module that reaches Celestrak.
# ---------------------------------------------------------------------------

def refresh_tle(norad_id: int, timeout: float = _WARM_FETCH_TIMEOUT) -> TLEResult:
    """Fetch *norad_id* from Celestrak and cache it. Always attempts the network.

    Unconditional by design. The warmer used to call a function that returned early
    on a fresh cache hit, which made it a no-op whenever the cache was healthy — so
    it could only ever repair an entry that had *already* expired, and the refresh
    always landed on whichever user asked first. Refreshing every run pushes the
    retention window out from now, so the row never reaches expiry at all.
    """
    key = _tle_key(norad_id)
    err_msg: str | None = None

    with _lock_for(key):
        try:
            raw    = _fetch_tle_raw(norad_id, timeout)
            parsed = _parse_tle(raw)
            if parsed is None:
                raise RuntimeError(f"Malformed TLE for NORAD {norad_id}: {raw!r}")
            _cache.set(key, raw, ttl_seconds=TLE_TTL)
            return TLEResult(lines=parsed, stale=False, error=None)
        except _NotModified:
            # Revalidated: Celestrak confirms our copy is current, so push the
            # retention window out from now and serve it as fresh.
            revalidated = _cache.get_stale(key)
            parsed = _parse_tle(revalidated) if revalidated is not None else None
            if parsed:
                _cache.set(key, revalidated, ttl_seconds=TLE_TTL)
                log.debug("NORAD %d revalidated (403) — TTL extended", norad_id)
                return TLEResult(lines=parsed, stale=False, error=None)
            # 403 but nothing to revalidate. Re-asking returns the same 403, so
            # this must count as a failure or we spin on every request.
            err_msg = f"Celestrak 403 for NORAD {norad_id} with no cached copy"
            log.warning("%s", err_msg)
            _ph.record("celestrak", "error", "403, empty cache")
            _cb.on_failure("celestrak")
        except Exception as e:
            err_msg = str(e)
            log.warning("TLE fetch failed for NORAD %d: %s", norad_id, err_msg)

    # Stale fallback — read the expired entry without deleting it
    stale_raw = _cache.get_stale(key)
    if stale_raw is not None:
        parsed = _parse_tle(stale_raw)
        if parsed:
            log.debug("Using stale TLE for NORAD %d — Celestrak unreachable", norad_id)
            return TLEResult(lines=parsed, stale=True, error=err_msg)

    # Complete failure — no cached data at all
    log.error("Celestrak TLE unavailable with no fallback for NORAD %d: %s",
              norad_id, err_msg, extra={"service": "celestrak"})
    return TLEResult(lines=None, stale=False, error=err_msg)


def refresh_starlink_trains(
    timeout: float = _WARM_FETCH_TIMEOUT,
) -> tuple[list[tuple[str, str, str]], bool, str | None]:
    """Fetch the Starlink group, filter it to raising-phase trains, and cache that.

    Returns (tles, stale, error). Never raises: a read timeout mid-response arrives
    as a bare TimeoutError rather than a urllib.error.URLError (urllib only wraps
    connect-phase failures), and an uncaught exception here previously crashed the
    entire /night response for callers that do not expect this to raise.

    The filtered list is what reaches the cache — see _STARLINK_TRAINS_CACHE_KEY for
    why caching the raw response could never work.
    """
    key = _STARLINK_TRAINS_CACHE_KEY
    err_msg: str | None = None

    with _lock_for(key):
        if not _cb.allow("celestrak"):
            log.debug("Starlink group fetch skipped — celestrak circuit open")
        else:
            req = urllib.request.Request(_STARLINK_GROUP_URL,
                                         headers={"User-Agent": _USER_AGENT})
            try:
                with _rl.acquire("celestrak"), _http.urlopen(req, timeout=timeout) as resp:
                    body = _http.read_capped(
                        resp, _STARLINK_MAX_BYTES, _STARLINK_TRANSFER_DEADLINE
                    )
                raw    = body.decode("utf-8").strip()
                trains = _filter_train_tles(raw)[:_STARLINK_MAX_TRAINS]
                _cache.set(key, trains, ttl_seconds=TLE_TTL)
                log.debug("Fetched Starlink group TLE (%d bytes → %d trains)",
                          len(raw), len(trains))
                _ph.record("celestrak", "ok")
                _cb.on_success("celestrak")
                return trains, False, None
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    # One-download-per-update: 403 means our copy is still current.
                    # That is a successful revalidation, so extend the retention
                    # window from now — the entry must never expire while Celestrak
                    # keeps confirming it.
                    cached = _cache.get_stale(key)
                    if cached is not None:
                        _cache.set(key, cached, ttl_seconds=TLE_TTL)
                        log.info("Celestrak Starlink group 403 — revalidated, "
                                 "TTL extended to %d h", TLE_TTL // 3600)
                        _ph.record("celestrak", "ok")
                        _cb.on_success("celestrak")
                        return _as_tle_tuples(cached), False, None
                    # 403 with nothing cached: re-asking returns the same 403, so
                    # treat it as a failure and let the breaker open rather than
                    # retrying on every request.
                    err_msg = "Celestrak 403 for Starlink group with no cached copy"
                    log.warning("%s", err_msg)
                    _ph.record("celestrak", "error", "403, empty cache")
                    _cb.on_failure("celestrak")
                else:
                    err_msg = f"Celestrak HTTP {e.code} for Starlink group"
                    log.warning("%s", err_msg)
                    _ph.record("celestrak",
                               "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
                    _cb.on_failure("celestrak")
            except urllib.error.URLError as e:
                err_msg = f"Celestrak unreachable (Starlink group): {e.reason}"
                log.warning("%s", err_msg)
                _ph.record("celestrak", "error", str(e.reason)[:120])
                _cb.on_failure("celestrak")
            except Exception as e:
                err_msg = f"Celestrak Starlink group fetch failed: {e}"
                log.warning("%s", err_msg)
                _ph.record("celestrak", "error", str(e)[:120])
                _cb.on_failure("celestrak")

    # Stale fallback — always try this; on 403 the stale data IS current
    cached = _cache.get_stale(key)
    if cached is not None:
        log.debug("Using stale Starlink train TLEs")
        return _as_tle_tuples(cached), True, None
    # No cache at all — no trains to show, but still surface why
    return [], False, err_msg


# ---------------------------------------------------------------------------
# Warming — used by the scheduled warmer Lambda and by the CLI
# ---------------------------------------------------------------------------

def _verify(key: str) -> tuple[bool, int]:
    """Read *key* back out of the cache. Returns (present_and_fresh, serialized bytes).

    A write that returned without raising is not evidence that anything was stored:
    DynamoDB rejects an oversized item, the adapter catches it, and the caller sees
    nothing. Reading the value back is the only check that covers every failure mode
    rather than the ones we remembered to catch (same reasoning as the /healthz cache
    probe).
    """
    value = _cache.get(key)
    if value is None:
        return False, 0
    return True, len(json.dumps(value).encode("utf-8"))


def warm_cache(timeout: float = _WARM_FETCH_TIMEOUT, force: bool = True) -> WarmSummary:
    """Refresh every TLE the app needs into the cache, and verify each one landed.

    *force* controls whether an already-fresh entry is refetched. The scheduled
    warmer passes True: revalidating on every run is what keeps the entry's age
    below one refresh interval, so it never expires under a user. The CLI passes
    False — it has no warmer behind it, so it only needs to fill a cold cache, and
    refetching a warm one would just cost the operator Celestrak's 2 s pacing per
    call for no benefit.

    ``ok`` is true only if every key was read back out of the cache afterwards. The
    warmer previously reported ok from (stale, error) alone, which is blind to
    whether anything was actually stored — and it reported ok for months while the
    Starlink row did not exist.
    """
    summary = WarmSummary()

    def _record(key: str, label: str, status: str, error: str | None = None) -> None:
        verified, size = _verify(key)
        if not verified:
            status = f"{status} but NOT CACHED"
            summary.ok = False
        if error is not None:
            summary.ok = False
        summary.keys.append(WarmKeyResult(key=key, label=label, status=status,
                                          verified=verified, bytes=size, error=error))

    for norad, label in TRACKED_SATELLITES:
        key = _tle_key(norad)
        if not force and _cache.get(key) is not None:
            _record(key, label, "fresh (skipped)")
            continue
        r = refresh_tle(norad, timeout=timeout)
        if r.lines is None:
            _record(key, label, "FAIL", error=r.error or "no TLE")
        elif r.stale:
            _record(key, label, "stale", error=r.error or "stale")
        else:
            _record(key, label, "ok")

    key = _STARLINK_TRAINS_CACHE_KEY
    if not force and _cache.get(key) is not None:
        _record(key, "starlink", "fresh (skipped)")
    else:
        trains, stale, err = refresh_starlink_trains(timeout=timeout)
        if err is not None:
            _record(key, "starlink", "FAIL", error=err)
        elif stale:
            _record(key, "starlink", "stale", error="stale")
        else:
            _record(key, "starlink", f"ok ({len(trains)} trains)")

    return summary


# ---------------------------------------------------------------------------
# CLI convenience — cache first, fetch on a miss.
#
# These are what the CLI uses: it runs against the local backend with no warmer
# behind it, so filling a cold cache in-process is correct there. Nothing on the
# request path may call them — see this module's docstring.
# ---------------------------------------------------------------------------

def get_tle(norad_id: int, timeout: float = _FETCH_TIMEOUT) -> TLEResult:
    """Return a TLEResult for *norad_id*, fetching if the cache has no fresh entry.

    Acquisition strategy:
      1. Fresh cache hit    → return immediately (stale=False).
      2. Cache miss/expired → fetch from Celestrak, cache for TLE_TTL.
      3. Fetch fails        → fall back to the expired cache entry (stale=True)
                              and include the error message as a warning.
      4. No cache at all    → return lines=None with the error message.
    """
    cached = _cache.get(_tle_key(norad_id))
    if cached is not None:
        parsed = _parse_tle(cached)
        if parsed:
            log.debug("TLE cache hit for NORAD %d", norad_id)
            return TLEResult(lines=parsed, stale=False, error=None)
    return refresh_tle(norad_id, timeout=timeout)


def get_starlink_train_tles(
    timeout: float = _FETCH_TIMEOUT,
) -> tuple[list[tuple[str, str, str]], bool, str | None]:
    """Return (tles, stale, error) for Starlink satellites currently in raising phase,
    fetching if the cache has no fresh entry."""
    cached = _cache.get(_STARLINK_TRAINS_CACHE_KEY)
    if cached is not None:
        return _as_tle_tuples(cached), False, None
    return refresh_starlink_trains(timeout=timeout)
