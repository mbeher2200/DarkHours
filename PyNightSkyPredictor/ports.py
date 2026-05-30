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
    """Resolves a light-pollution dataset name to an rasterio-openable path.

    Returns a local filesystem path today; a GDAL VSI URI (``/vsis3/...``) once
    the rasters move to S3 in M2. ``dataset`` is ``"viirs"`` or ``"falchi"``.
    """

    def path_for(self, dataset: str, *, show_progress: bool = True): ...


class Backend:
    """Bundle of the three adapters chosen for the active environment."""

    def __init__(self, cache: Cache, geocode_store: GeocodeStore,
                 raster_source: RasterSource):
        self.cache = cache
        self.geocode_store = geocode_store
        self.raster_source = raster_source


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
    if name == "local":
        # Lazy imports: these modules import this one, so importing them at module
        # load would be circular. By call time they are fully initialised.
        from .cache import LocalFileCache
        from .location import LocalGeocodeStore
        from .darksky import LocalRasterSource
        return Backend(
            cache=LocalFileCache(),
            geocode_store=LocalGeocodeStore(),
            raster_source=LocalRasterSource(),
        )
    raise ValueError(
        f"Unknown PYNIGHTSKY_BACKEND={name!r} "
        f"(expected 'local'; cloud backends arrive in M2/M3)."
    )
