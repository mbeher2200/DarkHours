"""TLE warmer handler (M6.2) — hermetic (tle_provider mocked, no network/AWS).

The warmer is the *only* thing that populates the TLE cache keys: the request path
reads them and never fetches. So these tests are less about "did the fetch work" and
more about "can this job ever report success while the cache stays empty" — which is
exactly what it did in production for months.
"""
import json
import time
from datetime import date

import pytest

from apps.warmer import handler as h
from darkhours import tle_provider as tle


class _FakeCache:
    """In-memory stand-in for the cache port, with real TTL semantics."""

    def __init__(self, accept_writes=True):
        self.d = {}
        self.accept_writes = accept_writes
        self.writes = []

    def get(self, key):
        entry = self.d.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and time.time() > expires:
            return None
        return value

    def get_stale(self, key):
        entry = self.d.get(key)
        return None if entry is None else entry[0]

    def set(self, key, value, ttl_seconds=None):
        self.writes.append((key, ttl_seconds))
        if not self.accept_writes:
            return False
        self.d[key] = (value, time.time() + ttl_seconds if ttl_seconds else None)
        return True


@pytest.fixture
def fake_cache(monkeypatch):
    c = _FakeCache()
    monkeypatch.setattr(tle, "_cache", c)
    return c


def _ok_launch_dates(monkeypatch, cache):
    """Wire the SATCAT leg to succeed and populate *cache*, as the real one does.

    warm_cache warms the launch-date map under its own key: the train filter cannot
    run without it, so a SATCAT failure has to show up as itself rather than as an
    unexplained empty train list.
    """
    dates = {"2026-040": date(2026, 8, 12)}

    def fake_fetch_dates(timeout=None):
        cache.set(tle._STARLINK_LAUNCH_DATES_CACHE_KEY,
                  {d: v.isoformat() for d, v in dates.items()},
                  ttl_seconds=tle.TLE_TTL)
        return dict(dates)

    monkeypatch.setattr(tle, "_fetch_starlink_launch_dates", fake_fetch_dates)
    return dates


def _ok_refreshers(monkeypatch, cache, trains=1):
    """Wire refresh_* to succeed and actually populate *cache*, as the real ones do."""
    seen = {}
    _ok_launch_dates(monkeypatch, cache)

    def fake_refresh_tle(norad, timeout=None):
        seen["tle"] = timeout
        cache.set(tle._tle_key(norad), "NAME\nline1\nline2", ttl_seconds=tle.TLE_TTL)
        return tle.TLEResult(lines=("NAME", "line1", "line2"), stale=False, error=None)

    def fake_refresh_group(timeout=None, launch_dates=None):
        seen["group"] = timeout
        value = [["S", "1 x", "2 x", "2026-08-12"]] * trains
        cache.set(tle._STARLINK_TRAINS_CACHE_KEY, value, ttl_seconds=tle.TLE_TTL)
        return value, False, None

    monkeypatch.setattr(tle, "refresh_tle", fake_refresh_tle)
    monkeypatch.setattr(tle, "refresh_starlink_trains", fake_refresh_group)
    return seen


def test_warm_all_ok(monkeypatch, fake_cache):
    _ok_refreshers(monkeypatch, fake_cache)
    out = h.handler({}, None)
    assert out["ok"] is True
    assert out["results"]["ISS"]["status"] == "ok"
    assert out["results"]["ISS"]["verified"] is True
    assert out["results"]["ISS"]["bytes"] > 0
    assert "1 trains" in out["results"]["starlink"]["status"]


def test_warm_reports_failures(monkeypatch, fake_cache):
    monkeypatch.setattr(tle, "refresh_tle",
                        lambda n, timeout=None: tle.TLEResult(lines=None, stale=False, error="HTTP 503"))
    _ok_launch_dates(monkeypatch, fake_cache)
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None, launch_dates=None: ([], True, "timed out"))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "FAIL" in out["results"]["ISS"]["status"]


def test_warm_stale_is_not_ok(monkeypatch, fake_cache):
    # stale data served (fetch failed but cache had an old entry) → ok=False
    monkeypatch.setattr(
        tle, "refresh_tle",
        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=True, error="HTTP 500"))
    _ok_launch_dates(monkeypatch, fake_cache)
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None, launch_dates=None: ([("a", "b", "c")], False, None))
    out = h.handler({}, None)
    assert out["ok"] is False
    assert "stale" in out["results"]["ISS"]["status"]


def test_warm_is_not_ok_when_the_write_is_silently_dropped(monkeypatch):
    """The production failure, in one test.

    Every fetch succeeds. Every refresh reports success. The cache accepts nothing —
    which is exactly what DynamoDB did with the 1.8 MB Starlink blob for months. The
    old handler derived ok from (stale, error) and reported ok=True through all of
    it, 58 runs over 14 days, while the row did not exist. Reading the value back is
    what makes that impossible to report as success.
    """
    dead = _FakeCache(accept_writes=False)
    monkeypatch.setattr(tle, "_cache", dead)
    monkeypatch.setattr(
        tle, "refresh_tle",
        lambda n, timeout=None: tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))
    _ok_launch_dates(monkeypatch, dead)
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None, launch_dates=None: ([("a", "b", "c")], False, None))

    out = h.handler({}, None)

    assert out["ok"] is False
    for label, result in out["results"].items():
        assert result["verified"] is False, f"{label} claimed a verified write"
        assert "NOT CACHED" in result["status"]


def test_warmer_forces_a_refresh_even_when_the_cache_is_fresh(monkeypatch, fake_cache):
    """The warmer must refresh unconditionally, not only repair an expired entry.

    It previously called a function that returned early on a fresh cache hit, making
    it a no-op whenever the cache was healthy. It could then only act *after* an
    entry had already expired — so the refresh always landed on whichever user asked
    first. Observed live: three tle| rows written 22.1h ago against a 24h TTL, with
    four warmer runs in between that touched none of them.
    """
    # Pre-populate every key with a fresh entry.
    for norad, _ in tle.TRACKED_SATELLITES:
        fake_cache.set(tle._tle_key(norad), "NAME\nl1\nl2", ttl_seconds=tle.TLE_TTL)
    fake_cache.set(tle._STARLINK_TRAINS_CACHE_KEY, [], ttl_seconds=tle.TLE_TTL)
    _ok_launch_dates(monkeypatch, fake_cache)

    calls = []
    monkeypatch.setattr(tle, "refresh_tle", lambda n, timeout=None: (
        calls.append(n),
        tle.TLEResult(lines=("a", "b", "c"), stale=False, error=None))[1])
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None, launch_dates=None: (
                            calls.append("group"), ([], False, None))[1])

    h.handler({}, None)

    assert [n for n, _ in tle.TRACKED_SATELLITES] + ["group"] == calls, \
        "a fresh cache entry must not skip the refresh — that is what lets it expire"


def test_cli_warm_skips_refresh_when_already_fresh(monkeypatch, fake_cache):
    """force=False is the CLI's mode: fill a cold cache, leave a warm one alone.

    The CLI has no warmer behind it, so it must fetch on a cold cache — but a warm
    local cache should not cost the operator Celestrak's 2s pacing per call.
    """
    for norad, _ in tle.TRACKED_SATELLITES:
        fake_cache.set(tle._tle_key(norad), "NAME\nl1\nl2", ttl_seconds=tle.TLE_TTL)
    fake_cache.set(tle._STARLINK_TRAINS_CACHE_KEY, [], ttl_seconds=tle.TLE_TTL)

    calls = []
    fake_cache.set(tle._STARLINK_LAUNCH_DATES_CACHE_KEY, {"2026-040": "2026-08-12"},
                   ttl_seconds=tle.TLE_TTL)
    monkeypatch.setattr(tle, "refresh_tle", lambda n, timeout=None: calls.append(n))
    monkeypatch.setattr(tle, "refresh_starlink_trains",
                        lambda timeout=None, launch_dates=None: calls.append("g"))
    monkeypatch.setattr(tle, "_fetch_starlink_launch_dates",
                        lambda timeout=None: calls.append("dates"))

    summary = tle.warm_cache(force=False)

    assert calls == []
    assert summary.ok is True


def test_warmer_uses_the_long_timeout_not_the_request_path_one(monkeypatch, fake_cache):
    """The warmer must opt into the patient timeout.

    _FETCH_TIMEOUT is the interactive budget (the CLI, where a person is waiting).
    The warmer has nobody waiting and its success is what stops anyone else from
    needing to fetch at all, so it gets the longer budget. If it silently inherited
    the short default, the job most likely to succeed would be the one most likely
    to give up.
    """
    seen = _ok_refreshers(monkeypatch, fake_cache)
    h.handler({}, None)

    assert seen["tle"] == tle._WARM_FETCH_TIMEOUT
    assert seen["group"] == tle._WARM_FETCH_TIMEOUT
    assert seen["tle"] > tle._FETCH_TIMEOUT


def test_emits_one_failure_metric_and_a_per_key_success_metric(monkeypatch, fake_cache, capsys):
    """EMF is how this becomes an alarm rather than a log nobody reads."""
    _ok_refreshers(monkeypatch, fake_cache)
    h.handler({}, None)

    emitted = [json.loads(line) for line in capsys.readouterr().out.splitlines()
               if line.startswith("{")]
    per_key = [e for e in emitted if "TleWarmSuccess" in e]
    failures = [e for e in emitted if "TleWarmFailure" in e]

    assert {e["Key"] for e in per_key} == {"ISS", "Hubble Telescope", "Tiangong",
                                           "starlink", "starlink-launch-dates"}
    assert all(e["TleWarmSuccess"] == 1 for e in per_key)
    assert all(e["TleCachedBytes"] > 0 for e in per_key)
    assert len(failures) == 1 and failures[0]["TleWarmFailure"] == 0

    # A verified write of an empty list is a success by every other measure here,
    # and for the train key an empty list is also the normal state between launches
    # — which is how a filter that could never match anything stayed invisible.
    by_key = {e["Key"]: e for e in per_key}
    assert by_key["starlink"]["TleWarmItems"] == 1
    assert by_key["starlink-launch-dates"]["TleWarmItems"] == 1


def test_a_permanently_empty_train_list_is_still_visible_as_a_count(monkeypatch, fake_cache, capsys):
    """The state that hid the broken filter: every key verified, ok=True, no trains.

    Nothing here is a failure — an empty train list is the normal state between
    launches — so the count is the only thing that separates "nothing tonight" from
    "nothing, for weeks".
    """
    _ok_refreshers(monkeypatch, fake_cache, trains=0)
    out = h.handler({}, None)

    assert out["ok"] is True
    assert out["results"]["starlink"]["count"] == 0

    emitted = [json.loads(line) for line in capsys.readouterr().out.splitlines()
               if line.startswith("{")]
    starlink = next(e for e in emitted if e.get("Key") == "starlink")
    assert starlink["TleWarmSuccess"] == 1, "a verified write of [] is still a write"
    assert starlink["TleWarmItems"] == 0


# ---------------------------------------------------------------------------
# Deployment bundle — the warmer ships source plus a pinned dependency list
# ---------------------------------------------------------------------------

def test_every_third_party_import_is_bundled_or_runtime_provided():
    """The warmer Lambda is not built from requirements.txt.

    cdk/warmer_stack.py stages the source tree and pip-installs exactly
    _WARMER_PIP_DEPS; boto3/botocore come from the Lambda runtime. Anything else the
    warmer path imports is simply absent in production, and the first sign is a
    ModuleNotFoundError at refresh time — which is how a working local change shipped
    broken once already.

    Run in a subprocess so the shared pytest session's imports do not pollute the
    answer, and the group filter is exercised rather than merely imported: sgp4 is
    imported lazily inside the conversion, so importing the handler alone reports
    nothing at all.
    """
    import json
    import pathlib
    import subprocess
    import sys

    import ast

    repo = pathlib.Path(__file__).resolve().parents[1]
    # Read the literal out of the source rather than importing warmer_stack: that
    # module imports aws_cdk, which lives in cdk/requirements.txt and is absent from
    # the test environment, so importing it here fails CI while passing on any
    # machine that happens to have the CDK installed.
    stack_src = (repo / "cdk" / "warmer_stack.py").read_text()
    deps_node = next(
        (n.value for n in ast.parse(stack_src).body
         if isinstance(n, ast.Assign)
         and any(getattr(tgt, "id", None) == "_WARMER_PIP_DEPS" for tgt in n.targets)),
        None,
    )
    assert deps_node is not None, "_WARMER_PIP_DEPS not found in cdk/warmer_stack.py"
    _WARMER_PIP_DEPS = ast.literal_eval(deps_node)

    probe = r"""
import json, pathlib, sys, sysconfig
from datetime import date, timedelta
import apps.warmer.handler                      # noqa: F401
from darkhours import tle_provider as tle

today = date.today()
cols = ("OBJECT_NAME,OBJECT_ID,EPOCH,MEAN_MOTION,ECCENTRICITY,INCLINATION,"
        "RA_OF_ASC_NODE,ARG_OF_PERICENTER,MEAN_ANOMALY,EPHEMERIS_TYPE,"
        "CLASSIFICATION_TYPE,NORAD_CAT_ID,ELEMENT_SET_NO,REV_AT_EPOCH,BSTAR,"
        "MEAN_MOTION_DOT,MEAN_MOTION_DDOT")
row = ("STARLINK-1,2026-160A,2026-08-25T12:00:00.000000,15.90000000,.0001000,"
       "53.0000,100.0000,90.0000,270.0000,0,U,100001,999,100,.0001000,.00000000,0")
# Exercises the lazy sgp4 import inside _omm_row_to_tle.
out = tle._filter_train_tles(cols + chr(10) + row, {"2026-160": today})
assert len(out) == 1, out

site = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
ext = set()
for name, mod in list(sys.modules.items()):
    if "." in name or mod is None:
        continue
    f = getattr(mod, "__file__", None)
    if not f:
        continue
    try:
        pth = pathlib.Path(f).resolve()
    except (OSError, ValueError):
        continue
    if site in pth.parents:
        ext.add(name.lower())
print(json.dumps(sorted(ext)))
"""
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=str(repo),
                         env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo)})
    assert res.returncode == 0, f"probe failed: {res.stderr[-600:]}"
    imported = set(json.loads(res.stdout))

    bundled = {d.split("==")[0].replace("-", "_").lower() for d in _WARMER_PIP_DEPS}
    runtime_provided = {"boto3", "botocore", "s3transfer", "jmespath", "dateutil",
                        "urllib3", "six"}

    unaccounted = imported - bundled - runtime_provided
    assert not unaccounted, (
        f"the warmer path imports {sorted(unaccounted)} but cdk/warmer_stack.py "
        f"bundles only {sorted(bundled)} — these would be missing in Lambda"
    )
    assert "sgp4" in imported, (
        "expected the filter to import sgp4; if the conversion moved, this test is "
        "no longer exercising the dependency it exists to guard"
    )
