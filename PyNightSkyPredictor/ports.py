#!/usr/bin/env python3
"""Pluggable I/O backends (ports & adapters).

The engine reaches the outside world through three narrow interfaces — a cache,
a geocode store, and a raster source. A single backend is selected once from the
``PYNIGHTSKY_BACKEND`` environment variable (default ``"local"``), which lets the
same engine run unchanged against local files (the CLI) or, later, cloud services
(the web app). See the migration plan: cloud adapters arrive in M2 (S3 rasters)
and M3 (DynamoDB cache / geocode store).

Adapter implementations live next to the code they replace:
  * ``LocalFileCache``     in ``cache.py``
  * ``LocalGeocodeStore``  in ``location.py``
  * ``LocalRasterSource``  in ``darksky.py``
They are imported lazily in ``_build_backend`` so those modules can ``import ports``
at module load without a circular dependency.
"""

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """Key→value cache with optional per-entry TTL and a stale-read escape hatch."""

    def get(self, key: str): ...
    def get_stale(self, key: str): ...
    def set(self, key: str, value, ttl_seconds: int | None = None) -> None: ...
    def invalidate(self, key: str) -> None: ...
    def clear_expired(self) -> int: ...
    def clear_all(self) -> int: ...


@runtime_checkable
class GeocodeStore(Protocol):
    """Persistence for saved/cached named locations (the whole dict at once)."""

    def load(self) -> dict: ...
    def save(self, data: dict) -> None: ...


@runtime_checkable
class RasterSource(Protocol):
    """Reads light-pollution values from the tiled raw-binary grids (numpy + boto3,
    no GDAL). ``dataset`` is ``"viirs"`` or ``"falchi"``.

    ``sample`` returns a single pixel value (nodata/out-of-bounds → 0.0, ``None`` on
    error); ``read_window`` returns a float64 bbox sub-array (row 0 = max_lat), with
    an optional bilinear ``out_shape`` resample. Local reads a memmapped grid;
    aws range-reads it from S3.
    """

    def sample(self, dataset: str, lat: float, lon: float) -> "float | None": ...
    def read_window(self, dataset: str, min_lat: float, max_lat: float,
                    min_lon: float, max_lon: float,
                    out_shape: "tuple[int, int] | None" = None): ...


class Backend:
    """Bundle of the three adapters chosen for the active environment.

    Each adapter is built lazily on first access, so a consumer that only needs
    one of them never imports the others. The raster adapter now reads tiled
    raw-binary grids with numpy + boto3 (no rasterio/GDAL in the runtime image).
    ``name`` is validated eagerly in ``_build_backend``; the adapter modules are
    imported on demand
    (they ``import ports`` themselves, so importing them at module load would be
    circular — by access time they are fully initialised)."""

    def __init__(self, name: str):
        self._name = name
        self._cache: Cache | None = None
        self._geocode_store: GeocodeStore | None = None
        self._raster_source: RasterSource | None = None

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            if self._name == "aws":
                from .cache import DynamoCache
                self._cache = DynamoCache()
            else:
                from .cache import LocalFileCache
                self._cache = LocalFileCache()
        return self._cache

    @property
    def geocode_store(self) -> GeocodeStore:
        if self._geocode_store is None:
            if self._name == "aws":
                from .location import DynamoGeocodeStore
                self._geocode_store = DynamoGeocodeStore()
            else:
                from .location import LocalGeocodeStore
                self._geocode_store = LocalGeocodeStore()
        return self._geocode_store

    @property
    def raster_source(self) -> RasterSource:
        if self._raster_source is None:
            if self._name == "aws":
                from .darksky import S3RasterSource
                self._raster_source = S3RasterSource()
            else:
                from .darksky import LocalRasterSource
                self._raster_source = LocalRasterSource()
        return self._raster_source


_backend: Backend | None = None


def get_backend() -> Backend:
    """Return the process-wide Backend, building it on first use."""
    global _backend
    if _backend is None:
        name = os.environ.get("PYNIGHTSKY_BACKEND", "local").strip().lower()
        _backend = _build_backend(name)
    return _backend


def reset_backend() -> None:
    """Drop the cached Backend so the next get_backend() re-selects (used in tests)."""
    global _backend
    _backend = None


def _build_backend(name: str) -> Backend:
    # Validate eagerly (so a bad env var fails fast); adapters are constructed
    # lazily per-attribute on the returned Backend.
    # local → M1 file/disk adapters; aws → M2 S3 rasters + M3 DynamoDB cache/geocode.
    if name not in ("local", "aws"):
        raise ValueError(
            f"Unknown PYNIGHTSKY_BACKEND={name!r} (expected 'local' or 'aws')."
        )
    return Backend(name)
