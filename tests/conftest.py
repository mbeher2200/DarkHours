"""
Shared pytest configuration.

Markers are registered in pytest.ini; session-scoped fixtures live here.
"""
import pytest

# Test modules that exercise the SWPC fetchers themselves and must NOT have
# them stubbed (they mock the HTTP/cache layer underneath instead).
_AURORA_OPT_OUT = {"test_aurora_provider", "test_provider_smoke"}


@pytest.fixture(autouse=True)
def _fresh_circuit_breaker():
    """Reset circuit-breaker state around every test.

    Breaker state is module-global; without this, a test that simulates
    provider failures could trip a breaker and short-circuit provider calls
    in unrelated later tests (celestrak trips on a single failure).
    """
    from darkhours import circuit_breaker as _cb
    _cb.reset()
    yield
    _cb.reset()


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    """Reset rate-limiter state around every test, mirroring _fresh_circuit_breaker.

    Pacing state (last-call clocks) and semaphores are module-global; without
    this, a pace-configured provider's real wall-clock timestamp could persist
    across tests and cost a real sleep if two tests happen to exercise the same
    provider within its interval of real test-runner time.
    """
    from darkhours import rate_limiter as _rl
    _rl.reset()
    yield
    _rl.reset()


@pytest.fixture(autouse=True)
def _offline_aurora(request, monkeypatch):
    """Keep the default test run offline: assemble_night()/fetch_night() gained a
    default-on SWPC fetch whose date gate doesn't protect tests that use today's
    date. Stub both fetchers to empty results (aurora → None) everywhere except
    the provider tests, which re-mock the layers they need.
    """
    if request.module.__name__.rpartition(".")[2] in _AURORA_OPT_OUT:
        yield
        return
    from darkhours import aurora as _aurora
    monkeypatch.setattr(_aurora, "fetch_kp_forecast", lambda: ([], False))
    monkeypatch.setattr(_aurora, "fetch_27day_outlook", lambda: ({}, False))
    yield
