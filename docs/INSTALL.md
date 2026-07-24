# Install and Local Setup

The web app at [darkhours.app](https://darkhours.app) needs nothing installed. This page is for running the engine and CLI on your own machine, or hacking on the repo.

You need **Python 3.13**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # runtime dependencies
pip install -r requirements-dev.txt      # + pytest, for running the test suite
```

`requirements.txt` holds the runtime libraries and nothing else. `requirements-dev.txt` adds the test tools on top. There are four more files you can ignore for local CLI work. `requirements-api.txt`, `requirements-worker.txt`, and `requirements-security.txt` belong to the cloud deployment (API server, background worker, security-scanning CI). `requirements-build.txt` holds the offline index and grid builders. Rasterio lives there, and only there.

There's no application container. Both cloud Lambdas ship as zip packages. See [Cloud Deployment](DEPLOYMENT.md). The one Dockerfile in the repo, `Dockerfile.worker`, exists for the Trivy image scan in CI and the throwaway in-region perf test. That's it.

## First run

```bash
python darkhours.py --location "Sedona, AZ" --all
```

The big datasets download on first use and cache under `~/.darkhours/`. That's light-pollution rasters, geocoding, weather, and TLEs. The full list of sources and cache lifetimes is in [Architecture, Data Download and Caching](ARCHITECTURE.md#data-download--caching). The JPL DE421 planetary ephemeris ships inside the repo at `darkhours/de421.bsp`, so the astronomy math needs no download.

## Configuration

Drop a `darkhours/config.json` file to override the built-in defaults:

```json
{
  "targets": {
    "min_elevation_deg": 20,
    "moon_min_separation_deg": 30,
    "moon_max_illumination_pct": 50
  },
  "prime_targets": {
    "min_peak_altitude_deg": 40,
    "min_window_hours": 1.0
  }
}
```

Leave a key out and it falls back to the default shown here. The file is optional. Skip it and every default applies.

## Testing

You'll need the dev dependencies first (`pip install -r requirements-dev.txt`).

```bash
python -m pytest                  # Full suite
python -m pytest -m "not eph"     # Fast suite, no ephemeris file needed
python -m pytest -v               # Verbose output
```

A default run stays offline and deterministic. Three opt-in markers in `pytest.ini` gate the exceptions. `eph` needs the bundled `de421.bsp`. `aws` hits real AWS, so it wants `PYNIGHTSKY_BACKEND=aws` plus credentials and resource env vars. `live` hits real provider APIs and wants `PYNIGHTSKY_LIVE=1`. The full per-file inventory lives in [tests/README.md](../tests/README.md).
