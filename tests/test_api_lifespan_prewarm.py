"""The API lifespan must pre-warm at most once per container.

Mangum builds a fresh ``LifespanCycle`` inside ``__call__`` (mangum/adapter.py), so
``lifespan()`` runs on every invocation rather than once at container init. Before the
guard, each request spawned a daemon thread whose first step is an unmemoized DynamoDB
GetItem — roughly one wasted read per request.
"""
import asyncio
import threading

import apps.api.main as main


def _run_lifespan_cycles(n: int) -> None:
    async def go():
        for _ in range(n):
            async with main.lifespan(main.app):
                pass
    asyncio.run(go())


class _FakeThread:
    """Records spawns without running the prewarm (which would hit the network)."""
    spawned: list = []

    def __init__(self, target=None, daemon=None):
        self.target = target
        type(self).spawned.append(target)

    def start(self):
        pass


def _patch(monkeypatch):
    _FakeThread.spawned = []
    monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
    monkeypatch.setattr(main, "_prewarm_started", False)
    monkeypatch.setattr(threading, "Thread", _FakeThread)


def test_prewarm_spawns_once_across_many_invocations(monkeypatch):
    _patch(monkeypatch)
    _run_lifespan_cycles(5)
    assert len(_FakeThread.spawned) == 1, (
        "the prewarm thread must spawn once per container, not once per invocation"
    )


def test_guard_is_set_before_the_thread_starts(monkeypatch):
    """Set inside the thread instead, and a burst would each spawn their own."""
    _patch(monkeypatch)
    _run_lifespan_cycles(1)
    # The fake never runs the target, so a guard set inside _prewarm would still be False.
    assert main._prewarm_started is True


def test_no_prewarm_outside_lambda(monkeypatch):
    _FakeThread.spawned = []
    monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)
    monkeypatch.setattr(main, "_prewarm_started", False)
    monkeypatch.setattr(threading, "Thread", _FakeThread)
    _run_lifespan_cycles(3)
    assert _FakeThread.spawned == []
