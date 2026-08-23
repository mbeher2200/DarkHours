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


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_ports = []  # one entry per request, for the reuse assertions
        self._ports_lock = threading.Lock()

    def handle_error(self, request, client_address):
        """Stay quiet when a client walks away mid-response (the timeout test)."""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so a pooled transport can reuse

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        with self.server._ports_lock:
            self.server.client_ports.append(self.client_address[1])
        super().handle_one_request()

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
        elif path == "/hold":
            # Long enough that concurrent callers genuinely overlap. Over
            # loopback a request finishes so fast that threads never contend,
            # and a one-connection pool looks indistinguishable from a healthy
            # one — which is how 7adbe2d's maxsize=1 could go unnoticed.
            time.sleep(0.08)
            self._send(200, BODY)
        else:
            self._send(404, b'{"err": "no route"}')


@pytest.fixture(scope="module")
def server():
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def base_url(server):
    server.client_ports.clear()
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def dead_port():
    """A port with nothing listening, for connection-refused behaviour."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(params=["stdlib", "pooled"], autouse=True)
def transport(request, monkeypatch):
    """Run every test in this module against both transports.

    The whole point of the contract is that swapping the transport changes
    nothing observable, so asserting it on only one of them would miss exactly
    the class of bug that reverted the pool the first time.
    """
    monkeypatch.setattr(_http, "_pool_enabled", lambda: request.param == "pooled")
    return request.param


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
# Connection reuse — the reason the pooled transport exists
# ---------------------------------------------------------------------------

def _distinct_client_ports(server):
    """One source port per TCP connection, so this counts handshakes."""
    return len(set(server.client_ports))


def test_sequential_requests_reuse_one_connection_when_pooled(base_url, server, transport):
    for _ in range(4):
        with _http.urlopen(f"{base_url}/ok", timeout=5) as resp:
            resp.read()

    if transport == "pooled":
        assert _distinct_client_ports(server) == 1, "pooled transport re-handshook"
    else:
        assert _distinct_client_ports(server) == 4, "stdlib unexpectedly reused a connection"


def test_reuse_holds_under_the_concurrent_fan_out(base_url, server, transport):
    """Overlapping callers must still reuse, not re-handshake per request.

    /hold forces the workers to genuinely overlap; over loopback a request
    finishes so fast that threads never contend and any pool looks healthy.
    """
    import concurrent.futures as futures

    def fetch(_):
        with _http.urlopen(f"{base_url}/hold", timeout=5) as resp:
            return resp.read()

    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(fetch, range(24))) == [BODY] * 24

    ports = _distinct_client_ports(server)
    if transport == "pooled":
        assert ports <= 6, f"expected <=6 connections for 24 requests, got {ports}"
    else:
        assert ports >= 24, "stdlib should handshake per request"


def test_pool_holds_the_whole_fan_out():
    """7adbe2d configured num_pools (a count of hosts) and left maxsize at its
    default of 1, so connections past the first were discarded rather than
    parked. Asserted directly: with block=False the symptom is lost reuse, not
    a failure, so no connection-count test reliably catches it.
    """
    pool = _http._new_pool()
    host_pool = pool.connection_from_url("https://celestrak.org/")
    assert host_pool.pool.maxsize >= 8, "must hold predictor.py's widest fan-out"
    assert host_pool.block is False, "callers must never block waiting for a connection"


def test_pooled_failures_name_the_underlying_cause(dead_port, transport):
    """provider_health records str(e)[:120]. 7adbe2d mapped every urllib3 error
    to a flat URLError, so the cause never reached the log — the reason its
    Lambda-only failures were never diagnosed. Retries mean the wrapper is
    almost always MaxRetryError, which names nothing, so .reason must lead.
    """
    if transport != "pooled":
        pytest.skip("stdlib raises its own errors; nothing is being mapped")
    with pytest.raises(urllib.error.URLError) as exc:
        _http.urlopen(f"http://127.0.0.1:{dead_port}/ok", timeout=5)
    detail = str(exc.value)[:120]
    assert "MaxRetryError" not in detail.split(":")[0], "wrapper must not mask the cause"
    assert "Error" in detail.split(":")[0], f"no urllib3 class in {detail!r}"


def test_pool_retries_are_enabled_for_idempotent_gets():
    """retries=False, 7adbe2d's setting, also switches off redirect following and
    leaves nothing to recover a connection that went stale during a container
    freeze. Every request through this module is a GET.
    """
    retries = _http._retry_policy("api.open-meteo.com")
    assert retries.total >= 1 and retries.read >= 1 and retries.redirect >= 1
    assert retries.raise_on_status is False, "4xx/5xx must reach the HTTPError mapping"


@pytest.mark.parametrize("host", ["celestrak.org", "gp.celestrak.org"])
def test_celestrak_requests_are_never_replayed(host):
    """circuit_breaker.py trips celestrak on the *first* failure with a 300 s
    cooldown because its anti-abuse policy punishes concentrated retries. A
    urllib3 retry sits below the breaker, so a replay there would never be
    counted — the pattern that override exists to prevent.
    """
    assert _http._retry_policy(host).read == 0
    assert _http._retry_policy("api.open-meteo.com").read > 0, "only celestrak is exempt"


def test_read_retry_policy_controls_actual_replays(base_url, server, transport):
    """The counters above are only meaningful if urllib3 honours them."""
    if transport != "pooled":
        pytest.skip("retry policy applies to the pooled transport only")
    import urllib3

    seen = []

    class _Dropper(_Handler):
        def do_GET(self):
            seen.append(1)
            self.close_connection = True  # drop mid-exchange -> a read error

    drop_srv = _Server(("127.0.0.1", 0), _Dropper)
    threading.Thread(target=drop_srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{drop_srv.server_address[1]}/x"
    try:
        for host, expected in (("api.open-meteo.com", 3), ("celestrak.org", 1)):
            seen.clear()
            pool = urllib3.PoolManager(num_pools=2, maxsize=2,
                                       retries=_http._retry_policy(host))
            with pytest.raises(urllib3.exceptions.HTTPError):
                pool.request("GET", url, timeout=urllib3.Timeout(connect=3, read=3),
                             preload_content=False)
            assert len(seen) == expected, f"{host}: {len(seen)} requests, expected {expected}"
    finally:
        drop_srv.shutdown()
        drop_srv.server_close()
