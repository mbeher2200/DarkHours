"""Async job lifecycle (M6.3) — hermetic (in-memory cache, run_job/SQS mocked)."""
import json

import pytest
from fastapi.testclient import TestClient

from apps import jobs
import apps.api.main as main_mod
from apps.worker import handler as worker_handler


@pytest.fixture
def mem_cache(monkeypatch):
    """Back the cache port with an in-memory dict and force the inline (no-queue) path."""
    store: dict = {}
    monkeypatch.setattr(jobs._cache, "set", lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    monkeypatch.setattr(jobs._cache, "get", lambda k: store.get(k))
    monkeypatch.delenv("PYNIGHTSKY_JOBS_QUEUE_URL", raising=False)
    return store


def test_submit_inline_runs_and_stores(monkeypatch, mem_cache):
    monkeypatch.setattr(jobs, "run_job", lambda p: {"nights": [], "echo": p["start"]})
    jid = jobs.submit({"locs": [{}], "start": "2026-06-01", "end": "2026-06-02", "weather": False})
    rec = jobs.get(jid)
    assert rec["status"] == "done"
    assert rec["result"]["echo"] == "2026-06-01"


def test_submit_inline_records_error(monkeypatch, mem_cache):
    def boom(_p):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(jobs, "run_job", boom)
    rec = jobs.get(jobs.submit({"locs": [], "start": "2026-06-01", "end": "2026-06-02"}))
    assert rec["status"] == "error" and "kaboom" in rec["error"]


def test_get_unknown_job_is_none(mem_cache):
    assert jobs.get("nope") is None


def test_submit_enqueues_when_queue_configured(monkeypatch, mem_cache):
    monkeypatch.setenv("PYNIGHTSKY_JOBS_QUEUE_URL", "https://sqs.example/q")
    sent = {}

    class FakeSQS:
        def send_message(self, QueueUrl, MessageBody):
            sent.update(url=QueueUrl, body=MessageBody)

    monkeypatch.setattr(jobs, "_sqs", lambda: FakeSQS())
    monkeypatch.setattr(jobs, "run_job",
                        lambda p: (_ for _ in ()).throw(AssertionError("must not run inline")))
    jid = jobs.submit({"locs": [], "start": "2026-06-01", "end": "2026-06-02"})
    assert jobs.get(jid)["status"] == "pending"
    assert sent["url"].endswith("/q") and jid in sent["body"]


def test_worker_handler_processes_records(monkeypatch, mem_cache):
    monkeypatch.setattr(jobs, "run_job", lambda p: {"ok": True})
    event = {"Records": [{"body": json.dumps(
        {"job_id": "abc", "params": {"locs": [], "start": "2026-06-01", "end": "2026-06-02"}})}]}
    assert worker_handler.handler(event, None) == {"processed": 1}
    assert jobs.get("abc") == {"status": "done", "result": {"ok": True}}


def test_worker_handler_warmup_ping(monkeypatch):
    """A non-SQS event (no Records) is a scheduled warmup: it runs prewarm and
    returns without touching the job pipeline."""
    calls = {"prewarm": 0, "process": 0}
    monkeypatch.setattr(worker_handler, "_prewarm", lambda: calls.__setitem__("prewarm", calls["prewarm"] + 1))
    monkeypatch.setattr(jobs, "process", lambda *a, **kw: calls.__setitem__("process", calls["process"] + 1))
    assert worker_handler.handler({"warmup": True}, None) == {"warmed": True}
    assert calls == {"prewarm": 1, "process": 0}


def test_trip_endpoint_returns_202_then_done(monkeypatch, mem_cache):
    monkeypatch.setattr(main_mod._loc, "resolve",
                        lambda name: (40.0, -105.0, "Boulder", "America/Denver"))
    monkeypatch.setattr(jobs, "run_job", lambda p: {"nights": [], "n": len(p["locs"])})
    client = TestClient(main_mod.app)
    r = client.get("/trip", params={"locations": "Boulder", "start": "2026-06-01", "end": "2026-06-03"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    poll = client.get(f"/jobs/{jid}")
    assert poll.status_code == 200 and poll.json()["status"] == "done"
    assert poll.json()["result"]["n"] == 1


def test_jobs_endpoint_unknown_returns_404(mem_cache):
    assert TestClient(main_mod.app).get("/jobs/doesnotexist").status_code == 404


# ── nearby job dispatch ────────────────────────────────────────────────────────

def test_run_job_nearby_dispatches(monkeypatch):
    """run_job routes type='nearby' to find_nearby, not plan_trip."""
    monkeypatch.setattr(jobs, "_find_nearby",
                        lambda lat, lon, radius_miles: {
                            "origin_bortle": 7, "origin_sqm": 19.5,
                            "radius_miles": radius_miles,
                            "results": [], "light_domes": [],
                            "has_dark_sky": False, "best_available": None,
                        })
    result = jobs.run_job({"type": "nearby", "lat": 35.2, "lon": -111.6, "radius_miles": 60})
    assert result["origin_bortle"] == 7
    assert result["radius_miles"] == 60


def test_run_job_nearby_raises_when_none(monkeypatch):
    """run_job wraps find_nearby returning None as RuntimeError."""
    monkeypatch.setattr(jobs, "_find_nearby", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        jobs.run_job({"type": "nearby", "lat": 0.0, "lon": 0.0})


def test_run_job_defaults_to_trip_when_type_absent(monkeypatch):
    """Existing calendar/trip jobs (no 'type' key) still reach plan_trip."""
    called = {}

    class _FakeReport:
        date_start = __import__("datetime").date(2026, 6, 1)
        date_end   = __import__("datetime").date(2026, 6, 2)
        locations  = []
        nights     = []
        ranked     = []

    def fake_plan_trip(locs, start, end, fetch_weather):
        called["ok"] = True
        return _FakeReport()

    monkeypatch.setattr(jobs._trip, "plan_trip", fake_plan_trip)
    try:
        jobs.run_job({"locs": [], "start": "2026-06-01", "end": "2026-06-02"})
    except Exception:
        pass   # serializer may fail on the fake report — what matters is plan_trip was called
    assert called.get("ok"), "plan_trip was not called for a job without 'type'"
