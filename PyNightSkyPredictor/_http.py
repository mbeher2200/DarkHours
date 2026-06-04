"""Single choke point for outbound HTTP.

All external fetches (weather, TLE, light-pollution downloads) go through here so
the scheme is validated — urllib's urlopen otherwise accepts ``file://`` and other
schemes, which would be a local-file-read risk if a URL were ever attacker-shaped
(CWE-22). Uses a urllib3 PoolManager so warm-container requests reuse existing TLS
sessions, saving ~50–100ms per call to the same host.
"""
import urllib.error
import urllib.request
import urllib3
import urllib3.exceptions

_ALLOWED_SCHEMES = ("https://", "http://")

# Single pool shared for the lifetime of the Lambda container. urllib3 keeps
# connections alive (HTTP keep-alive), so the second request to api.open-meteo.com
# or celestrak.org skips the TCP+TLS handshake entirely.
_pool = urllib3.PoolManager(
    num_pools=4,
    retries=False,  # callers handle errors; no silent retries
)


def urlopen(url, *args, timeout=10, **kwargs):
    """urllib.request.urlopen-compatible wrapper backed by a urllib3 PoolManager.

    Accepts a URL string or urllib.request.Request. Raises urllib.error.HTTPError
    on 4xx/5xx and urllib.error.URLError on network/timeout failures. Returns a
    context-manager-compatible urllib3 HTTPResponse with a working .read().
    """
    if isinstance(url, urllib.request.Request):
        full    = url.full_url
        headers = dict(url.headers)
    else:
        full    = str(url)
        headers = {}

    if not full.lower().startswith(_ALLOWED_SCHEMES):
        raise ValueError(f"Refusing to open non-HTTP(S) URL: {full!r}")

    try:
        resp = _pool.request(
            "GET", full,
            headers=headers,
            timeout=urllib3.Timeout(total=timeout),
            preload_content=False,
        )
    except urllib3.exceptions.TimeoutError as exc:
        raise urllib.error.URLError("timed out") from exc
    except urllib3.exceptions.HTTPError as exc:
        raise urllib.error.URLError(str(exc)) from exc

    if resp.status >= 400:
        raise urllib.error.HTTPError(
            full, resp.status, resp.reason or str(resp.status), resp.headers, None
        )
    return resp
