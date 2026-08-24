"""Single choke point for outbound HTTP.

All external fetches (weather, TLE, light-pollution downloads) go through here so
the scheme is validated — urllib's urlopen otherwise accepts ``file://`` and other
schemes, which would be a local-file-read risk if a URL were ever attacker-shaped
(CWE-22).

Two transports sit behind the one function:

* ``urllib.request`` — the default. A fresh TCP + TLS handshake on every call.
* a ``urllib3`` connection pool, selected by the ``http_pool`` feature flag —
  TLS sessions survive across calls inside a warm Lambda container. Handshake is
  ~54% of a typical Open-Meteo call (137 ms TCP + 287 ms TLS out of 795 ms), and
  ``weather.forecast()`` runs three such calls concurrently, so the pool takes
  roughly the handshake off the whole fetch rather than off one leg.

A pool shipped once before and was reverted (7adbe2d → 522fe9a) after
Lambda-only failures that were never root-caused. Every difference from that
version is deliberate and annotated at the site that fixes it. Two things make
this attempt recoverable where that one was not: selection is per call behind an
operator flag that defaults to off (so falling back to stdlib takes ~30 s, the
feature-flags cache TTL, and no deploy), and the urllib3 exception class now
survives into the message ``provider_health`` records.
"""
import io
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import _env
from . import feature_flags as _ff

log = logging.getLogger(__name__)

# The code default the DynamoDB flag falls back to, off unless set. Lets a
# throwaway in-region test function A/B the transport without writing to the
# shared flags table, and gives the deploy-time counterpart to the existing
# PYNIGHTSKY_FEATURE_HTTP_POOL_DISABLE hard override. An operator flag in the
# table still wins over this; PYNIGHTSKY_FEATURE_HTTP_POOL_DISABLE beats both.
_POOL_DEFAULT = _env.flag("PYNIGHTSKY_HTTP_POOL", "0")

_ALLOWED_SCHEMES = ("https://", "http://")

# Sent only when a caller passed a bare URL string (weather, 7Timer, WAQI).
# Callers that build a urllib.request.Request already set their own and keep it.
# Without this the pooled path would silently switch those providers from
# stdlib's "Python-urllib/3.x" to "python-urllib3/2.x"; Celestrak already serves
# 403 to the wrong User-Agent, so provider identity is not cosmetic here.
_DEFAULT_USER_AGENT = "DarkHours/1.0 (open-source astronomical observation planner)"

_pool = None
_pool_lock = threading.Lock()


# Hosts that must never see a request replayed underneath the circuit breaker.
# circuit_breaker.py gives celestrak the override (1 failure, 300 s cooldown)
# because "Celestrak's anti-abuse policy punishes exactly the concentrated-retry
# pattern that emerges at cache expiry". A urllib3 retry sits *below* the
# breaker, so the default policy would turn one call into up to three requests
# that the breaker never counts — reinstating the pattern that override exists
# to prevent. These hosts keep connect retries (those never reach the server)
# and give up read retries, so they lose the stale-connection recovery: cheap
# here, since get_tle()'s global cache has a stale fallback and one success
# serves every user for 6 h.
_NO_REPLAY_HOSTS = ("celestrak.org",)


def _retry_policy(host: str):
    import urllib3

    common = dict(connect=2, redirect=3, backoff_factor=0.2,
                  status_forcelist=[], raise_on_status=False)
    if any(host == h or host.endswith("." + h) for h in _NO_REPLAY_HOSTS):
        return urllib3.Retry(total=2, read=0, **common)
    return urllib3.Retry(total=2, read=2, **common)


def _new_pool():
    """Build the shared PoolManager. Every kwarg here fixes a defect in 7adbe2d."""
    import urllib3

    return urllib3.PoolManager(
        # num_pools counts *hosts*; maxsize is the per-host connection count and
        # defaults to 1. 7adbe2d set only num_pools, so with predictor.py's
        # 9-thread fan-out (3 TLE + Starlink to celestrak.org, plus the three
        # weather calls) every connection past the first was discarded on return
        # instead of parked. block=False means that degrades reuse and emits
        # "connection pool is full" rather than starving callers, so it was a
        # throughput defect, not the failure that forced the revert. maxsize=8
        # covers the widest fan-out, so the pool holds what it opens.
        num_pools=8,
        maxsize=8,
        block=False,
        # 7adbe2d used retries=False, which also switches off redirect following
        # and leaves nothing to recover a pooled connection that went stale while
        # the container was frozen. Every request through this module is a GET,
        # so replaying is safe in HTTP terms — but see _NO_REPLAY_HOSTS for where
        # it is not safe in provider-policy terms. Overridden per request by host;
        # this is the fallback for a pool built without going through urlopen().
        retries=_retry_policy(""),
    )


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _new_pool()
    return _pool


def _pool_enabled() -> bool:
    """Transport selection, isolated so tests can force either path."""
    return _ff.enabled("http_pool", default=_POOL_DEFAULT)


def _pooled_get(original, full: str, timeout):
    """GET *full* through the shared pool, in urllib.request's exception language."""
    import urllib3
    import urllib3.exceptions

    headers: dict[str, str] = {}
    if isinstance(original, urllib.request.Request):
        # Request.add_header capitalises keys, so a caller's {"User-Agent": ...}
        # is stored as "User-agent". HTTP header names are case-insensitive, but
        # normalise anyway so the setdefault below can actually find it.
        headers = {k.title(): v for k, v in original.headers.items()}
    headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)

    try:
        resp = _get_pool().request(
            "GET",
            full,
            headers=headers,
            retries=_retry_policy(urllib.parse.urlsplit(full).hostname or ""),
            # stdlib's timeout is per socket operation, not a deadline for the
            # whole exchange. 7adbe2d used Timeout(total=...), which would cut
            # darksky.py's 60 s multi-MB zip download short partway through.
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
            # Callers read the body themselves, including darksky.py's chunked
            # read(1 << 20). preload_content=True consumed it first and left
            # every resp.read() empty — the first bug 7adbe2d shipped.
            preload_content=False,
        )
    except urllib3.exceptions.HTTPError as exc:
        # Keep the contract callers rely on (URLError, an OSError) while carrying
        # the concrete urllib3 class through. provider_health records
        # str(e)[:120]; 7adbe2d's flat URLError(str(exc)) is the specific reason
        # its Lambda-only failures were never attributable to a cause.
        #
        # Lead with the underlying reason, not the wrapper: retries mean almost
        # everything surfaces as MaxRetryError, which names no cause and would
        # be all that survives the 120-char truncation. .reason holds the real
        # one (NewConnectionError, ProtocolError, SSLError, ReadTimeoutError).
        #
        # Never interpolate the urllib3 message itself. It embeds the full request
        # URL, and some of ours carry a credential in the query string (aqicn's
        # ?token=...). aqicn logs str(e) untruncated to CloudWatch and
        # provider_health surfaces e.reason on the public, unauthenticated
        # /healthz, so a URL here reaches both. stdlib never exposed it. The class
        # name is the part 7adbe2d was missing; the URL was never the useful bit.
        cause = getattr(exc, "reason", None)
        name = type(cause).__name__ if isinstance(cause, Exception) else type(exc).__name__
        host = urllib.parse.urlsplit(full).hostname or "unknown host"
        raise urllib.error.URLError(f"{name} for {host}") from exc

    if resp.status >= 400:
        body = resp.read()      # drains the response, returning the conn to the pool
        resp.release_conn()
        raise urllib.error.HTTPError(
            full, resp.status, resp.reason or str(resp.status),
            resp.headers, io.BytesIO(body),
        )
    return resp


def urlopen(url, *args, **kwargs):
    """``urllib.request.urlopen`` restricted to http(s) URLs/Requests.

    Accepts the same arguments as ``urllib.request.urlopen`` (a URL string or a
    ``Request``) and returns a response supporting ``read()``, ``read(n)``,
    ``headers.get()`` and use as a context manager. Raises ``ValueError`` for
    any non-HTTP(S) scheme, ``urllib.error.HTTPError`` (carrying ``.code``) on
    4xx/5xx, and an ``OSError`` on network failure. ``tests/test_http.py`` pins
    that contract against both transports.

    The pooled path reads ``timeout`` from keyword arguments, which is how all
    12 call sites pass it.
    """
    full = url.full_url if isinstance(url, urllib.request.Request) else url
    if not str(full).lower().startswith(_ALLOWED_SCHEMES):
        raise ValueError(f"Refusing to open non-HTTP(S) URL: {full!r}")
    if not args and _pool_enabled():
        return _pooled_get(url, str(full), kwargs.get("timeout"))
    # Scheme validated above; this is the one audited urlopen in the codebase.
    return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310  # nosemgrep: dynamic-urllib-use-detected
