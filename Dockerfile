# PyNightSky API container.
# The rasterio wheel bundles GDAL (incl. /vsis3 + curl for S3 reads), so no
# system GDAL package is needed — slim base is enough.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# The rasterio wheel bundles GDAL but still links libexpat, which python:3.13-slim
# omits — without it `import rasterio` fails at runtime. ca-certificates is already
# present in slim (needed for GDAL /vsis3 HTTPS to S3).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first for layer caching. requirements-api.txt pulls in
# requirements.txt (the engine runtime) plus FastAPI/uvicorn.
COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements-api.txt

# Application code — includes the bundled de421.bsp ephemeris + config/targets JSON.
COPY PyNightSkyPredictor/ ./PyNightSkyPredictor/
COPY apps/ ./apps/

# Drop root: run the service as an unprivileged user. The aws backend reads rasters
# from S3 and caches in DynamoDB, so no local filesystem writes are needed.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8080
# Backend + cache table + raster bucket are injected as env at runtime (App Runner).
# Default to the local backend so a bare `docker run` still starts.
ENV PYNIGHTSKY_BACKEND=local
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
