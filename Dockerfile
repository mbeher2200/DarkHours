# PyNightSky API container — Amazon Linux 2023 base.
# AWS-native (App Runner's own runtimes are AL2023), glibc (manylinux wheels work),
# and currently 0 CVEs. The image is GDAL-free: light-pollution rasters are read as
# tiled raw-binary grids with numpy + boto3 (rasterio is build-only, not installed here).
# Pinned by digest for reproducible builds; Dependabot (docker ecosystem) bumps it
# when AWS republishes the tag, which is how we pick up base-OS security patches.
FROM amazonlinux:2023@sha256:d09f1353c3c0844852cff7bf103498a205532f5bca444e1ed6406e3c5402bb26

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Python 3.13 from the AL2023 repos, then an isolated venv.
RUN dnf update -y \
    dnf install -y python3.13 python3.13-pip \
    && dnf clean all \
    && rm -rf /var/cache/dnf
RUN python3.13 -m venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# Dependencies first for layer caching. requirements-api.txt pulls in
# requirements.txt (engine runtime) plus FastAPI/uvicorn.
COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements-api.txt

# Application code — includes the bundled de421.bsp ephemeris + config/targets JSON.
COPY PyNightSkyPredictor/ ./PyNightSkyPredictor/
COPY apps/ ./apps/

# Drop root: run as an unprivileged numeric UID. AL2023's base has no shadow-utils
# (no useradd), and we don't need one — the aws backend reads rasters from S3 and
# caches in DynamoDB, so nothing is written to the local filesystem. A writable
# HOME is provided in case a library looks for one.
ENV HOME=/home/appuser
RUN mkdir -p /home/appuser && chown 10001:10001 /home/appuser
USER 10001:10001

EXPOSE 8080
# Backend + cache table + raster bucket are injected as env at runtime (App Runner).
ENV PYNIGHTSKY_BACKEND=local
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
