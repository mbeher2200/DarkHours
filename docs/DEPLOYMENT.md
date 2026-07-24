# Cloud Deployment

The repo ships an optional cloud deployment. It puts the engine behind an HTTP JSON API with a React/TypeScript frontend. That's the DarkHours app running at [darkhours.app](https://darkhours.app).

**The stack:**

- **Compute.** FastAPI on AWS Lambda, shipped as a zip package (Python 3.13, arm64). CDK asset bundling pip-installs the dependencies at deploy time, so there's no application container. CloudFront fronts it, with WAFv2 rate limiting. Async jobs run on a second zip Lambda fed by SQS, with a dead-letter queue behind it.
- **Storage.** DynamoDB holds the cache and geocode store. S3 holds the VIIRS and Falchi rasters as tiled raw-binary grids. `gridraster.py` range-reads them in place, no GDAL at runtime. See [docs/RASTERIO_REPLACEMENT.md](RASTERIO_REPLACEMENT.md).
- **Routing and geocoding.** Amazon Location GeoRoutes (`CalculateRoutes`) computes drive time and road distance to each reachable POI in `find_nearby`, and flags ferry-only and unpaved legs. An AWS Location place index handles geocoding, suggestions, and reverse-geocoding. Each leg result caches for 24 hours.
- **Frontend.** A React/TypeScript SPA (Vite) served from S3 through CloudFront. The nearby-results view orders sites by drive time, shows road distance, and links Google Maps directions from origin to site for each one.
- **Resilience.** EventBridge keep-warm pings hit both Lambdas every 4 minutes. A separate scheduled warmer Lambda refreshes satellite TLEs every 6 hours. A provider-health Lambda probes the upstream weather and space-weather APIs every 5 minutes.
- **Observability.** Structured JSON logs, X-Ray tracing, a CloudWatch dashboard with metric alarms and SNS notification, and CloudWatch RUM on the frontend. See [docs/OBSERVABILITY.md](OBSERVABILITY.md).

**HTTP API.** Served same-origin behind CloudFront. Long computations return `202` and a job id:

| Endpoint | Mode | Purpose |
|---|---|---|
| `GET /night` | sync | Full single-night report (`location` or `lat`+`lon`, `date`, optional `targets`/`satellites`; `date_only=true` refetches just the date-dependent fields) |
| `GET /suggest?q=` | sync | Place-name typeahead suggestions |
| `GET /nearby` | async (202 + job) | Dark-sky search, radius 5 to 120 mi |
| `GET /calendar` | async (202 + job) | Multi-night outlook, up to 30 days |
| `GET /jobs/{job_id}` | sync | Poll an async job (`pending` / `done` / `error`) |
| `GET /healthz` | sync | Cache round-trip plus provider health snapshot |
| `POST /warmup` | sync | Keep-warm ping target (EventBridge) |

The CLI's `--show-nearby` takes up to 150 mi. The web API caps the radius at 120 mi.

**Repository layout:**

- `cdk/` holds the Python CDK stacks. `PyNightSkyLambda` (API, worker, CloudFront, WAF, queues, alarms), `PyNightSkyCicd` (GitHub OIDC deploy role), `PyNightSkyWarmer` (TLE refresh), `PyNightSkyProviderHealth` (provider probes).
- `apps/api/` is the FastAPI application (Mangum ASGI adapter).
- `apps/worker/` is the async job worker (SQS-triggered).
- `apps/web/` is the DarkHours React SPA. See [apps/web/README.md](../apps/web/README.md).
- `Dockerfile.worker` is used only for the Trivy security scan and the throwaway perf-test recipe. It's not part of the deployment.

**CI/CD.** A push to `main` runs the test suite, assumes the deploy role through GitHub OIDC (no long-lived AWS keys), builds the SPA, and runs `cdk deploy PyNightSkyLambda`. No Docker build or registry push happens. A separate `security.yml` workflow runs Bandit, pip-audit, Semgrep, gitleaks, and Trivy on every branch.

Want your own instance? Deploy the CDK stacks into your own AWS account with `PYNIGHTSKY_BACKEND=aws`. Resource names (bucket, table, place index, queue URL) come in through environment variables and are deliberately kept out of the source.
