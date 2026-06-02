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
