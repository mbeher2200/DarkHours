"""
Contract tests for _http.urlopen — the single choke point every outbound fetch
goes through.

These pin down exactly what the 12 call sites rely on, so the transport
underneath can be swapped (stdlib urllib.request <-> a pooled urllib3
PoolManager) without silently changing behaviour. A urllib3 pool was shipped
once and reverted (7adbe2d -> 522fe9a) after Lambda-only failures that were
never root-caused; nothing in the suite would have caught the breakage.

The contract, grepped from every caller:

  resp.read()                     all JSON/text providers
  resp.read(n)                    darksky.py:165 streams a multi-MB zip in 1 MB chunks
  resp.headers.get(...)           darksky.py:160 reads Content-Length
  with ... as resp:               every call site
  HTTPError.code                  tle_provider 403 -> _NotModified, 429 -> degraded;
                                  weather.py:201/372 429 -> degraded
  OSError on network failure      callers fall through to `except Exception`
  ValueError on a bad scheme      the CWE-22 guard this module exists for
  Request(headers=...)            tle_provider/aurora/darksky send a User-Agent

Hermetic: a throwaway HTTP server on 127.0.0.1, no network, no sleeping beyond
one short timeout probe.
"""
import http.server
import json
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.request

import pytest

from darkhours import _http


BODY = b'{"hello": "world"}'
BIG = b"x" * (256 * 1024)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so a pooled transport can reuse

    def log_message(self, *args):
        pass

    def _send(self, code, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ok":
            self._send(200, BODY)
        elif path == "/big":
            self._send(200, BIG)
        elif path == "/echo-headers":
            seen = {k.lower(): v for k, v in self.headers.items()}
            self._send(200, json.dumps(seen).encode())
        elif path.startswith("/status/"):
            self._send(int(path.rsplit("/", 1)[1]), b'{"err": true}')
        elif path == "/slow":
            time.sleep(2.0)
            self._send(200, BODY)
        else:
            self._send(404, b'{"err": "no route"}')


@pytest.fixture(scope="module")
def base_url():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="module")
def dead_port():
    """A port with nothing listening, for connection-refused behaviour."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Reading the body
# ---------------------------------------------------------------------------

def test_read_returns_full_body(base_url):
    with _http.urlopen(f"{base_url}/ok", timeout=5) as resp:
        assert resp.read() == BODY


def test_read_is_usable_after_the_response_is_returned(base_url):
    """The bug that broke the first pooled attempt: preload_content=True consumed
    the body, so every caller's resp.read() came back empty (fixed in 9d89c17)."""
    resp = _http.urlopen(f"{base_url}/ok", timeout=5)
    assert resp.read() == BODY


def test_chunked_read_reassembles_the_body(base_url):
    """darksky.py:165 streams a multi-MB zip with resp.read(1 << 20) until empty."""
    chunks = []
    with _http.urlopen(f"{base_url}/big", timeout=5) as resp:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    assert b"".join(chunks) == BIG
    assert len(chunks) > 1, "expected the body to arrive in more than one chunk"


def test_content_length_header_is_readable(base_url):
    """darksky.py:160 uses it to drive the download progress meter."""
    with _http.urlopen(f"{base_url}/big", timeout=5) as resp:
        assert int(resp.headers.get("Content-Length", 0)) == len(BIG)


def test_response_is_a_context_manager(base_url):
    """Every call site uses `with _http.urlopen(...) as resp:`."""
    with _http.urlopen(f"{base_url}/ok", timeout=5) as resp:
        assert resp.read() == BODY


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [403, 404, 429, 500, 503])
def test_http_error_carries_the_status_code(base_url, code):
    """tle_provider branches 403 -> _NotModified and 429 -> degraded; weather.py
    branches 429 -> degraded. The code must survive the transport exactly."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        _http.urlopen(f"{base_url}/status/{code}", timeout=5)
    assert exc.value.code == code


def test_connection_failure_raises_oserror(dead_port):
    """Callers catch `except Exception` after HTTPError; asserting OSError keeps
    the contract honest without over-specifying (stdlib gives URLError on connect,
    TimeoutError on read — both OSError)."""
    with pytest.raises(OSError) as exc:
        _http.urlopen(f"http://127.0.0.1:{dead_port}/ok", timeout=5)
    assert not isinstance(exc.value, urllib.error.HTTPError)


def test_read_timeout_raises_oserror(base_url):
    with pytest.raises(OSError):
        _http.urlopen(f"{base_url}/slow", timeout=0.3)


# ---------------------------------------------------------------------------
# Scheme guard — the reason this module exists (CWE-22)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/x",
    "FILE:///etc/passwd",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(ValueError):
        _http.urlopen(url)


def test_scheme_guard_applies_to_request_objects():
    req = urllib.request.Request("file:///etc/passwd")
    with pytest.raises(ValueError):
        _http.urlopen(req)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_schemes_pass_the_guard(scheme, dead_port):
    """Reaching a connection error (not ValueError) proves the guard let it through."""
    with pytest.raises(OSError) as exc:
        _http.urlopen(f"{scheme}://127.0.0.1:{dead_port}/ok", timeout=5)
    assert not isinstance(exc.value, ValueError)


# ---------------------------------------------------------------------------
# Request objects with headers
# ---------------------------------------------------------------------------

def test_request_headers_reach_the_server(base_url):
    """tle_provider.py:126, aurora.py:197 and darksky.py:1490 all send a
    User-Agent this way; Celestrak serves 403 to the wrong one."""
    ua = "DarkHours/1.0 (test)"
    req = urllib.request.Request(f"{base_url}/echo-headers", headers={"User-Agent": ua})
    with _http.urlopen(req, timeout=5) as resp:
        seen = json.loads(resp.read())
    assert seen["user-agent"] == ua


def test_plain_url_still_sends_some_user_agent(base_url):
    """weather.py and aqicn.py pass bare URL strings. Whatever the transport, a
    provider must not see a request with no User-Agent at all."""
    with _http.urlopen(f"{base_url}/echo-headers", timeout=5) as resp:
        seen = json.loads(resp.read())
    assert seen.get("user-agent", "").strip()


# ---------------------------------------------------------------------------
# Concurrency — the app's real fan-out pattern
# ---------------------------------------------------------------------------

def test_concurrent_requests_to_one_host_all_succeed(base_url):
    """predictor.py fans out up to 9 threads: 3 TLE + Starlink to celestrak.org
    plus weather. The first pooled attempt left maxsize at its default of 1,
    which starves exactly this pattern."""
    import concurrent.futures as futures

    def fetch(_):
        with _http.urlopen(f"{base_url}/ok", timeout=5) as resp:
            return resp.read()

    with futures.ThreadPoolExecutor(max_workers=9) as pool:
        results = list(pool.map(fetch, range(18)))
    assert results == [BODY] * 18


# ---------------------------------------------------------------------------
# read_capped — bounding the whole transfer, not one socket read
# ---------------------------------------------------------------------------
# urllib's `timeout` bounds a single socket operation. A response that trickles in
# just fast enough to keep every individual read under the limit can take
# arbitrarily long overall — which is what let a 1.8 MB Celestrak group fetch run
# for 30s against a "5s timeout". The latency histogram was smooth from 1-30s with
# no pile-up at 5s, which is the signature of a bound that was never binding.

class _SlowResponse:
    """Yields fixed-size chunks, sleeping between them."""

    def __init__(self, chunks, delay=0.0):
        self._chunks = list(chunks)
        self._delay = delay

    def read(self, _n=None):
        if self._delay:
            time.sleep(self._delay)
        return self._chunks.pop(0) if self._chunks else b""


def test_read_capped_returns_the_whole_body_when_within_bounds():
    resp = _SlowResponse([b"abc", b"def", b""])
    assert _http.read_capped(resp, max_bytes=1024, deadline_s=5) == b"abcdef"


def test_read_capped_aborts_past_the_byte_ceiling():
    resp = _SlowResponse([b"x" * 100] * 50)
    with pytest.raises(_http.TransferLimitExceeded) as e:
        _http.read_capped(resp, max_bytes=256, deadline_s=5)
    assert "256" in str(e.value)


def test_read_capped_aborts_past_the_transfer_deadline():
    """The bound urllib's timeout does not give: each read is fast, the transfer
    is not."""
    resp = _SlowResponse([b"x" * 10] * 100, delay=0.02)
    started = time.monotonic()
    with pytest.raises(_http.TransferLimitExceeded) as e:
        _http.read_capped(resp, max_bytes=10 * 1024, deadline_s=0.05)
    assert "deadline" in str(e.value)
    assert time.monotonic() - started < 2.0, "must abort, not run to completion"


def test_read_capped_bounds_the_starlink_group_fetch():
    """The only caller that matters: the warmer's group fetch holds a Lambda
    timeout open, and its success is what keeps TLEs off the request path."""
    from darkhours import tle_provider as tle
    assert tle._STARLINK_TRANSFER_DEADLINE < tle._WARM_FETCH_TIMEOUT * 2
    assert tle._STARLINK_MAX_BYTES > 2 * 1024 * 1024, "must fit the real ~1.8 MB body"
