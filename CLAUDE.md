# CLAUDE.md

Guidance for AI assistants working in this repo. Read this first, then the relevant
`docs/` file for the area you're touching.

## What this is

DarkHours: an astronomy/dark-sky planner. Predicts observing conditions
(weather, moon, milky way, satellites, light pollution) and finds nearby dark-sky sites.
The repo was originally named PyNightSkyPredictor; the package/CLI/docs have been renamed
to match the DarkHours product brand, but the CDK/AWS infra layer (stack ids, log groups,
IAM role, ECR repo, the `PYNIGHTSKY_*` env var convention) is intentionally **not yet
renamed** — deferred to the next infra overhaul, since those renames imply real resource
replacement/trust-policy risk. Don't "fix" that layer's naming without checking first.

- **CLI:** `darkhours.py` (the parity oracle — behavior here is the reference).
- **Web API:** `apps/api` (FastAPI). Long computes run async via SQS → `apps/worker`.
- **Engine:** `darkhours/` — `darksky.py` (light pollution + `find_nearby`),
  `weather.py`, `moon_events.py`, `milky_way.py`, `satellites.py`, `tle_provider.py`,
  `location.py`, `scoring.py`, `trip.py`.

## Backends (ports & adapters)

One backend is selected per process via `PYNIGHTSKY_BACKEND` (`local` default, or `aws`),
through `ports.py`. The same engine runs against local files (CLI) or cloud services
(prod). Don't bypass the seam:
- cache → `LocalFileCache` | `DynamoCache`
- geocode store → local json | DynamoDB
- rasters → local tiled grid (memmap) | S3 tiled grid via `gridraster.py` byte-range reads
  (no GDAL at runtime — see `docs/RASTERIO_REPLACEMENT.md`)
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
- **Persist context.** Update the relevant `docs/` file as you go;
  that's how the next session inherits this one.

## Tests

`python -m pytest -q`. Markers (see `pytest.ini`), all skipped by default:
- `eph` — needs the de421.bsp ephemeris.
- `aws` — hits real AWS; runs only with `PYNIGHTSKY_BACKEND=aws` + cache/raster env + creds.
- `live` — hits real provider APIs; runs only with `PYNIGHTSKY_LIVE=1`
  (`tests/test_provider_smoke.py` covers Open-Meteo, 7Timer, Celestrak, Nominatim, AWS Location).

A default run stays offline and deterministic (803 tests collected as of 2026-07-18;
aws/live auto-skip). Per-file inventory: `tests/README.md`.

## Ship flow (CI/CD)

- **Branch → PR → squash-merge to `main` = deploy.** `.github/workflows/deploy.yml` fires
  on push to `main`: test gate (`pytest -q`) → OIDC → `cdk deploy PyNightSkyLambda`.
  Both the API and worker are **zip Lambdas** — CDK asset bundling pip-installs deps on
  `linux/arm64` inline during deploy; no Docker build or ECR push in CI.
- `PyNightSkyProviderHealth` (like `PyNightSkyWarmer`/`PyNightSkyCicd`) is deployed manually,
  once — `deploy.yml` only ever targets `PyNightSkyLambda`. Redeploy it by hand
  (`cdk deploy PyNightSkyProviderHealth`) if its code changes.
- **`security.yml`** runs on every branch/PR (scan-only, does not gate deploy): pytest +
  Bandit, pip-audit, Semgrep, gitleaks, Trivy (image CVEs against `Dockerfile.worker`)
  via `scripts/security_scan.sh`.
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

To measure a worker change on real infra without touching the deployed worker
(note: the *deployed* Lambdas are zip packages — this container exists only for
test/scan use, which is also why `Dockerfile.worker` is in the repo at all):
1. `docker build -f Dockerfile.worker --platform linux/arm64 --provenance=false -t <repo>/pynightsky-worker:proftest .`
   (single-platform manifest — Lambda rejects buildx attestation lists; arm64 matches deployed arch).
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

## Key docs

- `docs/AUDIT_2026-07-18.md` — docs-vs-code audit: what was reconciled and where.
- `docs/PERF_FINDNEARBY.md` — `find_nearby` performance: current state + investigation log.
- `docs/RASTERIO_REPLACEMENT.md` — the rasterio-free tiled raster grid pipeline.
- `docs/PADUS_INDEX.md` — PAD-US H3 index: format, build, blacklist rules, regeneration.
- `docs/OSM_POI_INDEX.md` — routable OSM POI H3 index for POI-first `find_nearby`.
- `docs/TARGETS.md` — target catalog schema + meteor-shower ZHR decay model.
- `docs/OBSERVABILITY.md` — CloudWatch dashboard, alarms + SNS notification wiring, log groups,
  X-Ray scope, and the Application Insights shadow-alarm gap left open on purpose.
- `docs/FEATURES.md` — user-facing feature list (validated against code).
- `apps/web/README.md` — the DarkHours SPA: dev setup, architecture, red-mode rules.
- `docs/CLI.md`, `docs/TRIPBUILDER.md`, `PRODUCT.md`, `README.md` — product/engine overview.
