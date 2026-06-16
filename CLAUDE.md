# CLAUDE.md

Guidance for AI assistants working in this repo. Read this first, then the relevant
`memory/` notes and `docs/` for the area you're touching.

## What this is

PyNightSkyPredictor: an astronomy/dark-sky planner. Predicts observing conditions
(weather, moon, milky way, satellites, light pollution) and finds nearby dark-sky sites.

- **CLI:** `pynightsky.py` (the parity oracle — behavior here is the reference).
- **Web API:** `apps/api` (FastAPI). Long computes run async via SQS → `apps/worker`.
- **Engine:** `PyNightSkyPredictor/` — `darksky.py` (light pollution + `find_nearby`),
  `weather.py`, `moon_events.py`, `milky_way.py`, `satellites.py`, `tle_provider.py`,
  `location.py`, `scoring.py`, `trip.py`.

## Backends (ports & adapters)

One backend is selected per process via `PYNIGHTSKY_BACKEND` (`local` default, or `aws`),
through `ports.py`. The same engine runs against local files (CLI) or cloud services
(prod). Don't bypass the seam:
- cache → `LocalFileCache` | `DynamoCache`
- geocode store → local json | DynamoDB
- rasters → local GeoTIFF | S3 COG via GDAL `/vsis3`
- reverse-geocode/routing → Nominatim+Overpass (local) | AWS Location (aws)

## Ways of working (these produced the good results — keep them)

- **Profile before optimizing.** Never guess a bottleneck. `PYNIGHTSKY_PROFILE=1` turns
  on per-phase timing + cache hit/miss in `find_nearby`; `cache.stats` counts lookups.
- **One variable at a time, and benchmark it.** Capture a baseline first, change one
  thing, measure again, record the before/after. See `scripts/bench_*` / `profile_*`.
- **Verify before shipping.** Tests must be green; for perf/infra claims, confirm on real
  infra (see the test-worker recipe) — don't ship on a local number alone.
- **Clean up experiments.** Tear down any throwaway AWS resources and temp files you create.
- **Surface caveats, don't bury them.** "the bump won't help", "first-container image
  tax", correctness gates — call these out explicitly.
- **Persist context.** Update the relevant `memory/` note and `docs/` file as you go;
  that's how the next session inherits this one.

## Tests

`python -m pytest -q`. Markers (see `pytest.ini`), all skipped by default:
- `eph` — needs the de421.bsp ephemeris.
- `aws` — hits real AWS; runs only with `PYNIGHTSKY_BACKEND=aws` + cache/raster env + creds.
- `live` — hits real provider APIs; runs only with `PYNIGHTSKY_LIVE=1`
  (`tests/test_provider_smoke.py` covers Open-Meteo, 7Timer, Celestrak, Nominatim, AWS Location).

A default run stays offline and deterministic (currently 390 passed, ~7 skipped).

## Ship flow (CI/CD)

- **Branch → PR → squash-merge to `main` = deploy.** `.github/workflows/deploy.yml` fires
  on push to `main`: test gate (`pytest -q`) → OIDC → build+push **both** `pynightsky-api`
  and `pynightsky-worker` images (tagged `:<sha>`) → `cdk deploy ... -c imageTag=<sha>`.
  Both the API and the worker (where `find_nearby` runs) get the new code.
- **`security.yml`** runs on every branch/PR (scan-only, does not gate deploy): pytest +
  Bandit, pip-audit, Semgrep, gitleaks, Trivy (image CVEs) via `scripts/security_scan.sh`.
- We're on `main` by default — **branch before committing**. Commit trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR body trailer:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

### Security gate notes
- Trivy image CVEs are gated HIGH/CRITICAL. Accepted, **time-boxed** suppressions live in
  `.trivyignore` with a justification + removal trigger (currently 4 `libsolv` CVEs from
  the AL2023 base — remove when AWS ships the patched package).
- This is a **public repo**: never commit the S3 bucket name, DynamoDB table name, AWS
  account id, or role ARNs. They're injected via env/secrets and discovered at runtime —
  see below. `scripts/profile_aws.sh` reads them from the environment for this reason.

## Running against real AWS (local)

Needs an authenticated session and the resource names (kept out of source). Discover them
rather than hardcoding:
- Resource names: synthesized templates under `cdk/cdk.out/*.template.json`, or
  `aws lambda list-functions` / `aws ecr describe-repositories`.
- Then: `scripts/profile_aws.sh` (set `PYNIGHTSKY_RASTER_BUCKET` + `PYNIGHTSKY_CACHE_TABLE`
  first) runs one `find_nearby` against the aws backend with profiling.

### In-region perf validation (throwaway test worker)

To measure a worker change on real infra without touching the deployed worker:
1. `docker build -f Dockerfile.worker --platform linux/amd64 --provenance=false -t <repo>/pynightsky-worker:proftest .`
   (single-platform manifest — Lambda rejects buildx attestation lists).
2. ECR login, push the `:proftest` tag.
3. `aws lambda create-function` a throwaway fn from that image, **reusing the existing
   worker IAM role**, with the resource env vars + `PYNIGHTSKY_PROFILE=1`.
4. Invoke with a synthetic SQS event:
   `{"Records":[{"body":"{\"job_id\":\"x\",\"params\":{\"type\":\"nearby\",\"lat\":..,\"lon\":..,\"radius_miles\":60}}"}]}`
   (use `AWS_MAX_ATTEMPTS=1 --cli-read-timeout 0` to avoid a double-invoke; force a cold
   container between samples with a dummy env bump).
5. Read `[profile]` lines from the function's CloudWatch log group; results in the cache
   under `job|<job_id>`.
6. **Tear down:** delete the function, the `:proftest` ECR image, and the log group.

## Key docs & memory

- `docs/PERF_FINDNEARBY.md` — `find_nearby` profiling results, fixes, and benchmark log.
- `docs/PADUS_INDEX.md` — PAD-US H3 index: format, build, blacklist rules, regeneration.
- `memory/` — running project notes (cloud migration, find_nearby perf, PAD-US, etc.).
- `docs/PYNIGHTSKY.md`, `PRODUCT.md`, `README.md` — product/engine overview.
