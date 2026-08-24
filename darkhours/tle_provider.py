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
    cached_starlink_trains()             → (list[tuple], stale, error)   (request path)
    refresh_tle(norad_id, timeout=…)     → TLEResult              (warmer/CLI)
    refresh_starlink_trains(timeout=…, launch_dates=…)                   (warmer/CLI)
    warm_cache(timeout=…, force=True)    → WarmSummary            (warmer/CLI)
    get_tle(norad_id, timeout=…)         → TLEResult              (CLI convenience)
    get_starlink_train_tles(timeout=…)   → (list[tuple], stale, error)
    ISS_NORAD_ID                         → 25544
    TLE_TTL                              → 86400   (seconds — 24 hours)
    TRACKED_SATELLITES                   → [(norad_id, display_name), ...]
"""

import csv
import io
import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

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
    count: int = 0         # how many items the value holds, where that is meaningful
    error: str | None = None


@dataclass
class WarmSummary:
    ok: bool = True
    keys: list[WarmKeyResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "results": {
                k.label: {"status": k.status, "verified": k.verified,
                          "bytes": k.bytes, "count": k.count}
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


def cached_starlink_trains() -> tuple[list[tuple[str, str, str, str | None]], bool, str | None]:
    """Return the cached Starlink train TLEs, or a degraded result. Never fetches.

    An empty list is a legitimate answer — there is frequently no launch in the
    raising phase — so every check here is ``is not None``, not truthiness. Reading
    an empty cached list as "nothing cached" would send us back to fetching on the
    request path for the most common case.

    Each entry is ``(name, line1, line2, launch_date_iso)``. The launch date rides
    along because it is not derivable from the TLE — see ``_cospar_designator``.
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


def _as_tle_tuples(value) -> list[tuple[str, str, str, str | None]]:
    """Coerce the cached JSON back to the tuples callers expect.

    Entries are ``[name, line1, line2, launch_date]``. The launch date travels
    with the TLE because it cannot be recovered from one: line 1 carries the
    launch *number* within its year, not a date (see ``_cospar_designator``).
    Three-element entries written by an older build are still accepted and read
    as an unknown launch date.
    """
    out: list[tuple[str, str, str, str | None]] = []
    for entry in value or []:
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            launch = entry[3] if len(entry) > 3 else None
            out.append((entry[0], entry[1], entry[2],
                        launch if isinstance(launch, str) else None))
    return out


def _as_launch_dates(value) -> dict[str, date]:
    """Coerce the cached ``{designator: "YYYY-MM-DD"}`` map back to dates."""
    out: dict[str, date] = {}
    for designator, iso in (value or {}).items():
        try:
            out[designator] = date.fromisoformat(iso)
        except (TypeError, ValueError):
            continue
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

# Celestrak's SATCAT is the only place a launch *date* exists. GP/TLE data carries
# a launch number, not a date (see _cospar_designator), so the recency half of the
# train filter has no other source. Warmer path only, like every fetch here.
_STARLINK_SATCAT_URL      = ("https://celestrak.org/satcat/records.php"
                             "?GROUP=starlink&FORMAT=CSV")
_STARLINK_LAUNCH_DATES_CACHE_KEY = "tle|starlink|launch_dates"

# rev/day. Every operational Starlink shell sits at or below ~15.12 rev/day
# (530-560 km); above this line a satellite has not reached its shell yet. The
# previous value of 15.5 (~345 km) described only the lowest insertion orbit, and
# dropped batches deployed directly at ~465 km — three of the four most recent
# launches in the feed on 2026-08-23.
_STARLINK_TRAIN_MM_MIN    = 15.2
_STARLINK_RECENT_DAYS     = 21     # only launches within this window can form a visible train

# The cached launch-date map is pruned to this window. The filter only ever asks
# about _STARLINK_RECENT_DAYS; the wider margin keeps the item small (tens of
# entries out of ~700 Starlink launches) while leaving headroom to widen the
# train window without needing a second change here.
_STARLINK_LAUNCH_DATE_WINDOW_DAYS = 90

# Whole-transfer bounds for the SATCAT fetch — same reasoning as the group fetch
# below. The CSV is ~1 MB today.
_SATCAT_MAX_BYTES         = 16 * 1024 * 1024
_SATCAT_TRANSFER_DEADLINE = 45.0

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


def _cospar_designator(line1: str) -> str | None:
    """Return the launch's COSPAR designator from TLE line 1, e.g. ``"2026-159"``.

    Columns 10-17 hold ``YYNNNPPP``: two-digit year, launch number *within that
    year*, and piece. NNN is a sequence number, not a day of the year — 1998-067A
    (the ISS) launched on 20 November 1998, and day 67 of 1998 is 8 March.

    This identifies a launch; it cannot date one. Reading NNN as a day-of-year is
    what made the train filter match nothing: it re-dated every launch to somewhere
    in the first third of its year, so on 2026-08-23 the newest launch in the feed
    (2026-159, launched 11 July) presented as 8 June and fell outside every window
    the filter could reasonably use. Launch dates come from the SATCAT instead —
    see _fetch_starlink_launch_dates.
    """
    intl = line1[9:17].strip()
    if len(intl) < 5 or not intl[:5].isdigit():
        return None
    year_2d = int(intl[:2])
    year    = 2000 + year_2d if year_2d < 57 else 1900 + year_2d
    return f"{year}-{intl[2:5]}"


def _parse_satcat_launch_dates(csv_text: str, today: date) -> dict[str, date]:
    """Build ``{designator: launch date}`` from a SATCAT CSV, pruned to recent launches."""
    cutoff = today - timedelta(days=_STARLINK_LAUNCH_DATE_WINDOW_DAYS)
    out: dict[str, date] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        object_id = (row.get("OBJECT_ID") or "").strip()
        raw_date  = (row.get("LAUNCH_DATE") or "").strip()
        if len(object_id) < 8:
            continue
        try:
            launched = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if launched >= cutoff:
            out[object_id[:8]] = launched
    return out


def _filter_train_tles(
    raw: str, launch_dates: dict[str, date]
) -> list[tuple[str, str, str, str]]:
    """
    Parse a multi-TLE block and return only raising-phase Starlinks from recent launches.

    Two-part filter:
      1. Mean motion ≥ _STARLINK_TRAIN_MM_MIN — the satellite is still below every
         operational shell, so it has not been handed over to service yet.
      2. The launch is within _STARLINK_RECENT_DAYS, per *launch_dates* — an older
         batch has spread around its orbit and no longer crosses the sky as a train
         even while it is still raising.

    Recency is the load-bearing half, and it is why this needs the SATCAT. Mean
    motion alone is not a train signal: ~880 of the ~10,700 satellites in the group
    are above the threshold at any moment, and most are old satellites being lowered
    for re-entry rather than new ones being raised.

    An empty *launch_dates* therefore fails closed. Without dates the two populations
    are indistinguishable, and emitting the raising-phase set unfiltered would put
    hundreds of decaying satellites on screen labelled as trains.

    Ordered newest launch first so _STARLINK_MAX_TRAINS truncates the oldest batch
    rather than an arbitrary one.

    The recency cutoff is evaluated here, at filter time, and the result is what gets
    cached — so it is frozen for up to one refresh interval (6 h) against a 21-day
    window. That drift is immaterial; caching the raw block instead is what was not.
    """
    if not launch_dates:
        log.error("No Starlink launch dates available — cannot tell a train from a "
                  "decaying satellite, so no trains will be reported",
                  extra={"service": "celestrak"})
        return []

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_STARLINK_RECENT_DAYS)

    lines  = [l.strip() for l in raw.splitlines() if l.strip()]
    dated: list[tuple[date, tuple[str, str, str, str]]] = []
    raising = 0
    i = 0
    while i + 2 <= len(lines) - 1:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            mm = _parse_mean_motion(l2)
            if mm is not None and mm >= _STARLINK_TRAIN_MM_MIN:
                raising += 1
                launched = launch_dates.get(_cospar_designator(l1) or "")
                if launched is not None and launched >= cutoff:
                    dated.append((launched, (name, l1, l2, launched.isoformat())))
            i += 3
        else:
            i += 1

    dated.sort(key=lambda entry: entry[0], reverse=True)
    log.debug("Filtered %d Starlink train candidates from group TLE (%d raising)",
              len(dated), raising)
    return [entry for _, entry in dated]


def _prune_expired_trains(
    trains: list[tuple[str, str, str, str | None]],
) -> list[tuple[str, str, str, str | None]]:
    """Drop cached entries whose launch has aged out of the train window.

    Celestrak's 403 means the group has not changed, so there is nothing to
    re-filter — but the 21-day window has still moved since the list was built, and
    the raw block is deliberately not kept. Each entry carries its own launch date,
    so the cached list can be aged without refetching anything.
    """
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_STARLINK_RECENT_DAYS)
    kept: list[tuple[str, str, str, str | None]] = []
    for entry in trains:
        try:
            launched = date.fromisoformat(entry[3])
        except (TypeError, ValueError):
            continue
        if launched >= cutoff:
            kept.append(entry)
    return kept


def _fetch_starlink_launch_dates(timeout: float = _WARM_FETCH_TIMEOUT) -> dict[str, date]:
    """Fetch recent Starlink launch dates from Celestrak's SATCAT, and cache them.

    Falls back to the cached map (fresh, then expired) on any failure: a launch date
    does not change once it is set, so a stale map is as good as a new one for every
    launch it already covers, and losing the map entirely disables the train filter.
    """
    key = _STARLINK_LAUNCH_DATES_CACHE_KEY

    if not _cb.allow("celestrak"):
        log.debug("SATCAT fetch skipped — celestrak circuit open")
    else:
        req = urllib.request.Request(_STARLINK_SATCAT_URL,
                                     headers={"User-Agent": _USER_AGENT})
        try:
            with _rl.acquire("celestrak"), _http.urlopen(req, timeout=timeout) as resp:
                body = _http.read_capped(
                    resp, _SATCAT_MAX_BYTES, _SATCAT_TRANSFER_DEADLINE
                )
            dates = _parse_satcat_launch_dates(
                body.decode("utf-8"), datetime.now(timezone.utc).date()
            )
            _ph.record("celestrak", "ok")
            _cb.on_success("celestrak")
            if dates:
                _cache.set(key, {d: v.isoformat() for d, v in dates.items()},
                           ttl_seconds=TLE_TTL)
                log.debug("Fetched %d recent Starlink launch dates from SATCAT", len(dates))
                return dates
            # Reached Celestrak and it had nothing recent. Not a transport failure,
            # so it must not touch the breaker.
            log.warning("Celestrak SATCAT listed no Starlink launch in the last %d days",
                        _STARLINK_LAUNCH_DATE_WINDOW_DAYS)
        except urllib.error.HTTPError as e:
            log.warning("Celestrak SATCAT HTTP %d", e.code)
            _ph.record("celestrak",
                       "degraded" if e.code == 429 else "error", f"HTTP {e.code}")
            _cb.on_failure("celestrak")
        except urllib.error.URLError as e:
            log.warning("Celestrak SATCAT unreachable: %s", e.reason)
            _ph.record("celestrak", "error", str(e.reason)[:120])
            _cb.on_failure("celestrak")
        except Exception as e:
            log.warning("Celestrak SATCAT fetch failed: %s", e)
            _ph.record("celestrak", "error", str(e)[:120])
            _cb.on_failure("celestrak")

    cached = _cache.get(key)
    if cached is None:
        cached = _cache.get_stale(key)
    if cached:
        log.debug("Using cached Starlink launch dates")
        return _as_launch_dates(cached)
    return {}


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
    launch_dates: dict[str, date] | None = None,
) -> tuple[list[tuple[str, str, str, str | None]], bool, str | None]:
    """Fetch the Starlink group, filter it to raising-phase trains, and cache that.

    Returns (tles, stale, error). Never raises: a read timeout mid-response arrives
    as a bare TimeoutError rather than a urllib.error.URLError (urllib only wraps
    connect-phase failures), and an uncaught exception here previously crashed the
    entire /night response for callers that do not expect this to raise.

    *launch_dates* is fetched here when the caller does not supply one; warm_cache
    passes the map it already has so a warm run makes exactly one SATCAT request.

    The filtered list is what reaches the cache — see _STARLINK_TRAINS_CACHE_KEY for
    why caching the raw response could never work.
    """
    key = _STARLINK_TRAINS_CACHE_KEY
    err_msg: str | None = None

    if launch_dates is None:
        launch_dates = _fetch_starlink_launch_dates(timeout)

    if not launch_dates:
        # Nothing to gain from downloading 1.8 MB we cannot filter: without launch
        # dates the group is indistinguishable from a list of decaying satellites
        # (see _filter_train_tles). Returning here also keeps a SATCAT failure
        # reported as itself, rather than as a group fetch that quietly never ran
        # because the shared celestrak breaker had already opened underneath it.
        if not _cb.allow("celestrak"):
            # An open breaker is a deliberate skip, not a failure — same silent-skip
            # contract the group fetch below honours.
            log.debug("Starlink train refresh skipped — celestrak circuit open")
        else:
            err_msg = "no Starlink launch dates available — cannot identify trains"
            log.warning("%s", err_msg)
        cached = _cache.get_stale(key)
        if cached is not None:
            return _prune_expired_trains(_as_tle_tuples(cached)), True, err_msg
        return [], False, err_msg

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
                trains = _filter_train_tles(raw, launch_dates)[:_STARLINK_MAX_TRAINS]
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
                        trains = _prune_expired_trains(_as_tle_tuples(cached))
                        _cache.set(key, [list(t) for t in trains], ttl_seconds=TLE_TTL)
                        log.info("Celestrak Starlink group 403 — revalidated, "
                                 "%d trains still in window, TTL extended to %d h",
                                 len(trains), TLE_TTL // 3600)
                        _ph.record("celestrak", "ok")
                        _cb.on_success("celestrak")
                        return trains, False, None
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

    def _record(key: str, label: str, status: str, error: str | None = None,
                count: int = 0) -> None:
        verified, size = _verify(key)
        if not verified:
            status = f"{status} but NOT CACHED"
            summary.ok = False
        if error is not None:
            summary.ok = False
        summary.keys.append(WarmKeyResult(key=key, label=label, status=status,
                                          verified=verified, bytes=size,
                                          count=count, error=error))

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

    # Launch dates first: the train filter cannot run without them, and warming
    # them under their own key means a SATCAT failure shows up as itself rather
    # than as an unexplained empty train list.
    dates_key    = _STARLINK_LAUNCH_DATES_CACHE_KEY
    launch_dates: dict[str, date] = {}
    if not force and _cache.get(dates_key) is not None:
        launch_dates = _as_launch_dates(_cache.get(dates_key))
        _record(dates_key, "starlink-launch-dates", "fresh (skipped)",
                count=len(launch_dates))
    else:
        launch_dates = _fetch_starlink_launch_dates(timeout=timeout)
        if launch_dates:
            _record(dates_key, "starlink-launch-dates",
                    f"ok ({len(launch_dates)} launches)", count=len(launch_dates))
        else:
            _record(dates_key, "starlink-launch-dates", "FAIL",
                    error="no Starlink launch dates from SATCAT")

    key = _STARLINK_TRAINS_CACHE_KEY
    if not force and _cache.get(key) is not None:
        _record(key, "starlink", "fresh (skipped)",
                count=len(_as_tle_tuples(_cache.get(key))))
    else:
        trains, stale, err = refresh_starlink_trains(timeout=timeout,
                                                     launch_dates=launch_dates)
        if err is not None:
            _record(key, "starlink", "FAIL", error=err, count=len(trains))
        elif stale:
            _record(key, "starlink", "stale", error="stale", count=len(trains))
        else:
            # A train list is legitimately empty most of the time, so this is not a
            # failure — but it is the state that hid a broken filter for months, and
            # the count is what makes a permanent zero visible on a graph.
            newest = max((t[3] for t in trains if len(t) > 3 and t[3]), default=None)
            detail = f" (newest launch {newest})" if newest else ""
            _record(key, "starlink", f"ok ({len(trains)} trains){detail}",
                    count=len(trains))

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
) -> tuple[list[tuple[str, str, str, str | None]], bool, str | None]:
    """Return (tles, stale, error) for Starlink satellites currently in raising phase,
    fetching if the cache has no fresh entry."""
    cached = _cache.get(_STARLINK_TRAINS_CACHE_KEY)
    if cached is not None:
        return _as_tle_tuples(cached), False, None
    return refresh_starlink_trains(timeout=timeout)
