"""Single choke point for outbound HTTP.

All external fetches (weather, TLE, light-pollution downloads) go through here so
the scheme is validated — urllib's urlopen otherwise accepts ``file://`` and other
schemes, which would be a local-file-read risk if a URL were ever attacker-shaped
(CWE-22).
"""
import time
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


class TransferLimitExceeded(Exception):
    """A response exceeded its byte ceiling or its total-transfer deadline.

    Distinct from a socket timeout on purpose: urllib's ``timeout`` bounds a single
    socket operation, so a response that trickles in just fast enough to keep each
    read under the limit can take arbitrarily long overall. Callers whose own
    deadline matters (the TLE warmer holds a Lambda timeout open) need a bound on
    the *whole* transfer, which is what ``read_capped`` adds.
    """


def read_capped(resp, max_bytes: int, deadline_s: float, chunk_size: int = 65536) -> bytes:
    """Read *resp* in chunks, aborting past *max_bytes* or *deadline_s* seconds.

    Returns the body as bytes. Raises ``TransferLimitExceeded`` if either bound is
    crossed — a partial TLE file is not usable data, so there is nothing to salvage
    by returning what arrived.

    ``deadline_s`` is wall-clock from the first read, not per-socket-operation: it
    is precisely the bound urllib's ``timeout`` does not give.
    """
    deadline = time.monotonic() + deadline_s
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise TransferLimitExceeded(
                f"response exceeded {max_bytes} bytes (read {total} so far)"
            )
        if time.monotonic() > deadline:
            raise TransferLimitExceeded(
                f"response exceeded its {deadline_s:g}s transfer deadline "
                f"(read {total} bytes)"
            )
        chunks.append(chunk)
    return b"".join(chunks)
