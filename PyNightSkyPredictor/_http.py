"""Single choke point for outbound HTTP.

All external fetches (weather, TLE, light-pollution downloads) go through here so
the scheme is validated — urllib's urlopen otherwise accepts ``file://`` and other
schemes, which would be a local-file-read risk if a URL were ever attacker-shaped
(CWE-22).
"""
import urllib.request

_ALLOWED_SCHEMES = ("https://", "http://")


def urlopen(url, *args, **kwargs):
    """``urllib.request.urlopen`` restricted to http(s) URLs/Requests.

    Accepts the same arguments as ``urllib.request.urlopen`` (a URL string or a
    ``Request``) and returns the same response object. Raises ``ValueError`` for
    any non-HTTP(S) scheme.
    """
    full = url.full_url if isinstance(url, urllib.request.Request) else url
    if not str(full).lower().startswith(_ALLOWED_SCHEMES):
        raise ValueError(f"Refusing to open non-HTTP(S) URL: {full!r}")
    return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310  # nosemgrep: dynamic-urllib-use-detected
