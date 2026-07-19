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
