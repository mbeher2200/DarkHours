"""Thread-safe in-memory registry of 3rd-party provider health.

Provider call paths record success/failure here; the /healthz endpoint reads it.
No outbound probes — state reflects real observed traffic only.
"""
import threading
import time

_lock = threading.Lock()
_records: dict[str, dict] = {}

_STALE_AFTER = 3600  # treat a record as stale if older than this (seconds)


def record(provider: str, status: str, detail: str = "") -> None:
    """Update provider health. Called by HTTP call paths, never by health checks."""
    entry: dict = {"status": status, "as_of": time.time()}
    if detail:
        entry["detail"] = detail
    with _lock:
        _records[provider] = entry


def aws_error_detail(e: BaseException) -> str:
    """Detail string for an AWS SDK failure: the service error code (e.g.
    "ThrottlingException") for a botocore ClientError, else the exception class
    name (e.g. "EndpointConnectionError"). The full message stays in the logs."""
    resp = getattr(e, "response", None)
    err = resp.get("Error") if isinstance(resp, dict) else None
    code = err.get("Code") if isinstance(err, dict) else None
    return str(code)[:120] if code else type(e).__name__


def snapshot() -> dict[str, dict]:
    """Return provider → status dict for the health endpoint. Stale records are flagged."""
    now = time.time()
    out: dict[str, dict] = {}
    with _lock:
        for name, rec in _records.items():
            if now - rec["as_of"] > _STALE_AFTER:
                out[name] = {"status": "unknown", "stale": True}
            else:
                r: dict = {"status": rec["status"]}
                if "detail" in rec:
                    r["detail"] = rec["detail"]
                out[name] = r
    return out
