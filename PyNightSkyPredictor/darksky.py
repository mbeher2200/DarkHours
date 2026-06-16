#!/usr/bin/env python3
"""
Light pollution lookup using a two-tier hybrid data strategy.

Primary   — VIIRS Black Marble 2025 (lightpollutionmap.info)
  Raw satellite radiance in nW/cm²/sr.  Current data (2025) picks up
  post-2016 light growth.  Used whenever the pixel has a measurable signal.

Fallback  — Falchi New World Atlas 2016 (GFZ Potsdam)
  Radiative-transfer / Mie-scattering model of artificial sky luminance
  in mcd/m².  Used when VIIRS returns 0 (below the ~0.2 nW/cm²/sr
  detection floor), i.e. for genuinely dark rural sites.  The physical
  model propagates city-glow from all surrounding sources, so dark sites
  get non-zero, distinguishable values — Bortle 1 / 2 / 3 can be told
  apart, unlike with raw VIIRS.

Rationale: light pollution only increases over time.  If VIIRS 2025
shows a measurable signal, the site is bright now and VIIRS is the most
current reading.  If VIIRS shows zero, the site is still dark as of 2023
and Falchi's physical model gives the best available classification.

SQM conversions
  VIIRS  : SQM ≈ 21.7 − 2.5 × log10(L + 0.6)       (L in nW/cm²/sr)
  Falchi : SQM = 22.08 − 2.5 × log10((La+0.252)/0.252)  (La in mcd/m²)
"""

import contextlib
import io
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import cache
from . import ports
from . import _http

log = logging.getLogger(__name__)

try:
    from global_land_mask import globe as _glm
    _HAS_GLM = True
except ImportError:
    log.warning(
        "global-land-mask not installed; water pre-filtering disabled. "
        "Run `pip install global-land-mask` to enable early water coordinate filtering."
    )
    _HAS_GLM = False

_HAS_H3 = False
try:
    import h3 as _h3_lib
    _HAS_H3 = True
except ImportError:
    log.warning("h3 not installed; PAD-US Tier-1 spatial filter disabled.")

# ---------------------------------------------------------------------------
# Data-source constants
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".pynightsky-predictor"

# VIIRS Black Marble 2025
_VIIRS_ZIP_URL = "https://www2.lightpollutionmap.info/data/v2/viirs_2025_raw.zip"
_VIIRS_TIF     = _CACHE_DIR / "viirs_2025_raw.tif"

# Falchi World Atlas 2016
_FALCHI_ZIP_URL = ("https://datapub.gfz-potsdam.de/download/"
                   "10.5880.GFZ.1.4.2016.001/World_Atlas_2015.zip")
_FALCHI_TIF     = _CACHE_DIR / "world_atlas_2016.tif"

# Tiled raw-binary grids (built once from the GeoTIFFs above by gridbuild; read at
# runtime by gridraster with numpy+boto3 only — no rasterio/GDAL). The local backend
# builds these on first use; the aws backend reads the same-named pair from S3.
_GRID_DIR    = _CACHE_DIR / "grid"
_VIIRS_GRID  = _GRID_DIR / "viirs_2025"
_FALCHI_GRID = _GRID_DIR / "world_atlas_2016"

# Falchi natural-sky reference (airglow + zodiacal light + integrated starlight)
_L_NATURAL   = 0.252   # mcd/m²
_SQM_NATURAL = 22.08   # mag/arcsec²

# Correction factor applied to Falchi luminance values before computing SQM.
# The Falchi 2016 atlas is built on DMSP-OLS data (~2014), which reads 2–5×
# lower than VIIRS for the same sites (Kyba et al. 2017).  At dark-sky sites
# (La < 0.1 mcd/m²) the model also underestimates long-range city-glow
# propagation.  A factor of 3 brings calibration-site results in line with
# reported observer SQM measurements and IDA dark-sky park classifications.
# Only applied in the Falchi fallback path; VIIRS readings are unaffected.
_FALCHI_SCALE = 3.0

# Bortle class boundaries (minimum SQM, darkest first).
# Thresholds aligned with the djlorenz zone system and commonly cited
# SQM equivalents (Bortle 2001; Cinzano et al. 2001; Sky & Telescope):
#   Class 1 requires SQM ≥ 22.0 — zodiacal light casts shadows, M33 naked-eye.
#   Class 2 requires SQM ≥ 21.7 — airglow faintly visible, IDA Dark Sky Parks.
_BORTLE = [
    (22.0, 1, "Exceptional dark sky"),
    (21.7, 2, "Truly dark sky"),
    (21.3, 3, "Rural sky"),
    (20.8, 4, "Rural/suburban transition"),
    (20.0, 5, "Suburban sky"),
    (19.1, 6, "Bright suburban sky"),
    (18.0, 7, "Suburban/urban transition"),
    (17.0, 8, "City sky"),
    ( 0.0, 9, "Inner city sky"),
]

# djlorenz Light Pollution Index zones (minimum SQM to reach this zone, darkest first).
# Zone 0 = essentially natural sky (SQM > 21.99).  Each half-zone step = √3 × more
# artificial light, starting from LPI = 1.0 at the 3b/4a boundary (SQM 21.25).
# Reference: https://djlorenz.github.io/astronomy/lp/bortle.html
_LORENZ_ZONES = [
    (21.99, "1a"),
    (21.93, "1b"),
    (21.89, "2a"),
    (21.81, "2b"),
    (21.69, "3a"),
    (21.51, "3b"),
    (21.25, "4a"),
    (20.91, "4b"),
    (20.50, "5a"),
    (20.02, "5b"),
    (19.50, "6a"),
    (18.95, "6b"),
    (18.38, "7a"),
    (17.80, "7b"),
]


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(label: str, zip_url: str, tif_path: Path,
              show_progress: bool = True) -> None:
    """Download and extract a GeoTIFF zip if not already cached."""
    if tif_path.exists():
        return

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if show_progress:
        print(f"Downloading {label} …")
        print(f"  Source: {zip_url}")

    try:
        with _http.urlopen(zip_url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            buf   = io.BytesIO()
            downloaded = 0
            chunk_size = 1 << 20
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                buf.write(chunk)
                downloaded += len(chunk)
                if show_progress and total:
                    pct = downloaded / total * 100
                    print(f"\r  {downloaded >> 20} / {total >> 20} MB  ({pct:.0f}%)",
                          end="", flush=True)
    except Exception as e:
        raise RuntimeError(f"{label} download failed: {e}") from e

    if show_progress:
        print()

    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
        if not tif_names:
            raise RuntimeError(f"No .tif in {label} archive.")
        log.debug("Extracting %s → %s", tif_names[0], tif_path)
        with zf.open(tif_names[0]) as src, open(tif_path, "wb") as dst:
            dst.write(src.read())

    if show_progress:
        print(f"  Saved: {tif_path}")


def _download_viirs(show_progress: bool = True) -> None:
    _download("VIIRS 2025 light pollution data",
              _VIIRS_ZIP_URL, _VIIRS_TIF, show_progress)


def _download_falchi(show_progress: bool = True) -> None:
    _download("Falchi World Atlas 2016",
              _FALCHI_ZIP_URL, _FALCHI_TIF, show_progress)


class _GridRasterSource:
    """Shared ``sample``/``read_window`` over a per-dataset tiled raw-binary grid.

    Subclasses provide ``_grid(dataset) -> gridraster.GridArray`` (cached). Both
    operations preserve the legacy contract: ``sample`` returns nodata/out-of-bounds
    → 0.0, negatives → 0, ``None`` on read error; ``read_window`` returns a float64
    array (row 0 = max_lat, col 0 = min_lon), boundless-filled 0.0, nodata/neg
    clamped, with an optional bilinear ``out_shape`` resample.
    """

    def __init__(self):
        self._grids: dict[str, object] = {}

    def _grid(self, dataset: str):                     # pragma: no cover - abstract
        raise NotImplementedError

    def sample(self, dataset: str, lat: float, lon: float) -> float | None:
        g = self._grid(dataset)
        return g.sample(lat, lon)

    def read_window(self, dataset: str, min_lat: float, max_lat: float,
                    min_lon: float, max_lon: float,
                    out_shape: "tuple[int, int] | None" = None) -> "np.ndarray | None":
        g = self._grid(dataset)
        return g.read_window(min_lat, max_lat, min_lon, max_lon, out_shape=out_shape)


class LocalRasterSource(_GridRasterSource):
    """Local RasterSource: download the raw GeoTIFF then build the tiled grid on
    first use, and read it with ``gridraster`` (memmap).

    ``rasterio`` is required only for the one-time build (a local/build-only
    dependency — ``pip install -r requirements-build.txt``); it is NOT needed for
    runtime reads and is absent from the Lambda image.
    """

    _DATASETS = {
        "viirs":  (_download_viirs,  _VIIRS_TIF,  _VIIRS_GRID),
        "falchi": (_download_falchi, _FALCHI_TIF, _FALCHI_GRID),
    }

    def _grid(self, dataset: str):
        cached = self._grids.get(dataset)
        if cached is not None:
            return cached
        try:
            downloader, tif_path, grid_prefix = self._DATASETS[dataset]
        except KeyError:
            raise ValueError(f"Unknown raster dataset: {dataset!r}")

        from . import gridraster
        if not (grid_prefix.with_suffix(".bin").exists()
                and grid_prefix.with_suffix(".json").exists()):
            downloader(show_progress=True)             # ensure the raw GeoTIFF is present
            from . import gridbuild
            try:
                gridbuild.build(tif_path, grid_prefix, dataset)
            except ImportError as e:
                raise RuntimeError(
                    "Building the light-pollution grid requires rasterio "
                    "(local/build-only). Install it with: "
                    "pip install -r requirements-build.txt"
                ) from e

        g = gridraster.open_local(grid_prefix)
        self._grids[dataset] = g
        return g


class S3RasterSource(_GridRasterSource):
    """AWS RasterSource: range-read the tiled grid (``{key}.bin``/``{key}.json``)
    from S3 in place via boto3 — nothing is downloaded.

    The bucket comes from ``PYNIGHTSKY_RASTER_BUCKET`` (kept out of source so the
    public repo carries no bucket name); credentials/region resolve from the
    standard AWS environment (task role in the cloud, ``AWS_PROFILE`` locally).
    """

    _KEYS = {
        "viirs":  "viirs_2025",
        "falchi": "world_atlas_2016",
    }

    def __init__(self, bucket: str | None = None):
        super().__init__()
        self._bucket = bucket
        self._client = None

    @property
    def bucket(self) -> str:
        # Resolved lazily (like DynamoCache's table) so constructing the backend
        # for a cache-only operation doesn't require the raster bucket env var.
        b = self._bucket or os.environ.get("PYNIGHTSKY_RASTER_BUCKET")
        if not b:
            raise RuntimeError(
                "PYNIGHTSKY_RASTER_BUCKET is not set — required for the 'aws' "
                "raster backend (the S3 bucket holding the grids)."
            )
        return b

    def _s3(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    def _grid(self, dataset: str):
        cached = self._grids.get(dataset)
        if cached is not None:
            return cached
        try:
            key_prefix = self._KEYS[dataset]
        except KeyError:
            raise ValueError(f"Unknown raster dataset: {dataset!r}")
        from . import gridraster
        g = gridraster.open_s3(self.bucket, key_prefix, client=self._s3())
        self._grids[dataset] = g
        return g


# ---------------------------------------------------------------------------
# Raster sampling
# ---------------------------------------------------------------------------

def _viirs_radiance(lat: float, lon: float) -> float | None:
    """Return VIIRS 2025 radiance (nW/cm²/sr); builds/opens the grid on first use."""
    try:
        value = ports.get_backend().raster_source.sample("viirs", lat, lon)
    except Exception as e:
        log.warning("VIIRS lookup failed: %s", e)
        return None
    if value is not None:
        log.debug("VIIRS radiance at (%.4f, %.4f): %.3f nW/cm²/sr", lat, lon, value)
    return value


def _falchi_luminance(lat: float, lon: float) -> float | None:
    """Return Falchi 2016 artificial luminance (mcd/m²); builds/opens on first use."""
    try:
        value = ports.get_backend().raster_source.sample("falchi", lat, lon)
    except Exception as e:
        log.warning("Falchi lookup failed: %s", e)
        return None
    if value is not None:
        log.debug("Falchi luminance at (%.4f, %.4f): %.4f mcd/m²", lat, lon, value)
    return value


# ---------------------------------------------------------------------------
# SQM / Bortle conversions
# ---------------------------------------------------------------------------

def radiance_to_sqm(radiance_nw: float) -> float:
    """
    VIIRS empirical regression (nW/cm²/sr → mag/arcsec²).
        SQM ≈ 21.7 − 2.5 × log10(L + 0.6)
    The 0.6 offset represents natural airglow in the VIIRS band.
    Accuracy: ±0.5–1.0 mag/arcsec² (one Bortle class).
    """
    return round(21.7 - 2.5 * math.log10(radiance_nw + 0.6), 1)


def luminance_to_sqm(la_mcd_m2: float) -> float:
    """
    Falchi physical model (mcd/m² artificial luminance → mag/arcsec²).
        SQM = 22.08 − 2.5 × log10((La + 0.252) / 0.252)
    0.252 mcd/m² is the Falchi (2016) natural sky reference.
    At La = 0: SQM = 22.08; at La = 0.252: SQM ≈ 21.3 (Bortle 3).
    """
    if la_mcd_m2 <= 0.0:
        return _SQM_NATURAL
    return round(_SQM_NATURAL - 2.5 * math.log10(
        (la_mcd_m2 + _L_NATURAL) / _L_NATURAL), 1)


def sqm_to_bortle(sqm: float) -> tuple[int, str]:
    """Return (class_number, description) for a given SQM value."""
    for threshold, cls, desc in _BORTLE:
        if sqm >= threshold:
            return cls, desc
    return 9, "Inner city sky"


def sqm_to_zone(sqm: float) -> str:
    """Return the djlorenz light pollution zone label (e.g. '0', '1a', '3b')."""
    if sqm > 21.99:
        return "0"
    for min_sqm, label in _LORENZ_ZONES:
        if sqm >= min_sqm:
            return label
    return "7b"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

# In-process cache: Bortle data is static (raster pixels don't change).
# Keyed by (lat, lon) rounded to 2 decimal places (~1 km resolution).
_bortle_mem_cache: dict[tuple, dict | None] = {}


def lookup(lat: float, lon: float) -> dict | None:
    """
    Return light pollution info for a location, using the best available
    source:

      1. VIIRS 2025  — used if radiance > 0 (measurable signal)
      2. Falchi 2016 — fallback for dark sites where VIIRS = 0

    Return dict keys:
      sqm            float | None
      bortle_class   int | None
      bortle_desc    str | None
      lp_zone        str | None  — djlorenz zone ("0", "1a" … "7b")
      below_detection bool  — True only if both sources return 0/None
      source         str   — "VIIRS 2025" or "Falchi 2016"

    Returns None if the raster grids are unavailable or both reads fail.
    """
    _cache_key = (round(lat, 2), round(lon, 2))
    if _cache_key in _bortle_mem_cache:
        return _bortle_mem_cache[_cache_key]

    # --- Primary: VIIRS 2025 ---
    radiance = _viirs_radiance(lat, lon)
    if radiance is None:
        # grid unreadable or read error; try Falchi anyway
        log.debug("VIIRS unavailable, falling back to Falchi")
    elif radiance > 0:
        sqm = radiance_to_sqm(radiance)
        bortle_cls, bortle_d = sqm_to_bortle(sqm)
        log.debug("Using VIIRS 2025: radiance=%.3f  SQM=%.1f  Bortle=%d",
                  radiance, sqm, bortle_cls)
        result = {
            "sqm":            sqm,
            "bortle_class":   bortle_cls,
            "bortle_desc":    bortle_d,
            "lp_zone":        sqm_to_zone(sqm),
            "below_detection": False,
            "source":         "VIIRS 2025",
        }
        _bortle_mem_cache[_cache_key] = result
        return result
    else:
        log.debug("VIIRS below detection floor, falling back to Falchi")

    # --- Fallback: Falchi 2016 ---
    luminance = _falchi_luminance(lat, lon)
    if luminance is None:
        return None   # both sources failed — don't cache (may be a transient error)

    # luminance == 0.0 means below the sensor detection floor (natural sky),
    # not missing data. luminance_to_sqm handles 0.0 → _SQM_NATURAL correctly.
    scaled = luminance * _FALCHI_SCALE
    sqm = luminance_to_sqm(scaled)
    bortle_cls, bortle_d = sqm_to_bortle(sqm)
    log.debug("Using Falchi 2016: luminance=%.4f  scaled=%.4f  SQM=%.1f  Bortle=%d",
              luminance, scaled, sqm, bortle_cls)
    result = {
        "sqm":            sqm,
        "bortle_class":   bortle_cls,
        "bortle_desc":    bortle_d,
        "lp_zone":        sqm_to_zone(sqm),
        "below_detection": False,
        "source":         "Falchi 2016",
    }
    _bortle_mem_cache[_cache_key] = result
    return result



# ---------------------------------------------------------------------------
# Nearby dark-sky search
# ---------------------------------------------------------------------------

_DIRS_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

_MAX_SEARCH_RADIUS  = 150   # beyond this the Overpass query grows unreliable and driving isn't practical

# Tier-3 (Nominatim) spatial pre-dedup radius. A dark candidate within this many
# miles of an already-named result is skipped without a network probe, because
# adjacent dark pixels reverse-geocode to the same settlement. Matches the
# long-standing _cluster_points default so it never collapses sites further apart
# than the clustering stage already treats as distinct.
_NAME_DEDUP_MILES = 8.0

# Main public Overpass instance. The overpass.private.coffee mirror was tried but
# is unreachable (connections hang to timeout); other community mirrors
# (kumi.systems, openstreetmap.ru, mail.ru) also failed to respond, while
# overpass-api.de answers the areas-in-radius query reliably (~7-8 s). Respect its
# usage policy via the self-throttle below.
_OVERPASS_URL    = "https://overpass-api.de/api/interpreter"
_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/reverse"
_OVER_WATER      = "__water__"   # sentinel: Nominatim found no state/county — ocean or large water body
_GEO_CACHE_TTL   = 90 * 24 * 3600   # 90 days
_DRIVE_NO_ROUTE  = -1            # sentinel: route matrix returned no duration for this leg

# PAD-US H3 spatial index — module-level lazy-load cache
_PADUS_H3_FILENAME = "darkhours_padus_h3.npz"
_PADUS_UNAVAILABLE = object()   # singleton: tried to load, file missing or unreadable
_padus_h3_cache: "dict | object | None" = None  # None = not yet attempted
_OVERPASS_SLEEP  = 1.0               # minimum seconds between Overpass requests (overpass-api.de policy)
_NOMINATIM_SLEEP = 1.1               # minimum seconds between Nominatim requests

# Thread-safe rate-limit state
_overpass_lock       = threading.Lock()
_nominatim_lock      = threading.Lock()
_last_overpass_call  = 0.0
_last_nominatim_call = 0.0

_AREA_PRIORITY = {
    "wilderness":      0,
    "national_park":   1,
    "protected_area":  2,
    "national_forest": 3,
    "nature_reserve":  4,
    "forest":          5,
}


# Reverse-geocode fan-out width for backends with no per-second policy (AWS Location).
# Each find_nearby on the aws backend issues at most this many concurrent geocode
# calls; keep it modest so aggregate TPS across concurrent worker Lambdas stays under
# the SearchPlaceIndexForPosition service quota (default ~50 req/s, raisable).
_GEOCODE_MAX_WORKERS = int(os.environ.get("PYNIGHTSKY_GEOCODE_WORKERS", "8"))

# Shared boto3 'location' client (built once per process, reused across calls and
# threads). Rebuilding it per call reloads the service model and discards the HTTP
# connection pool — paying a fresh TLS handshake every request. botocore low-level
# clients are thread-safe for calls, so a single pooled client backs the parallel
# reverse-geocode fan-out. Double-checked locking guards the one-time construction.
_location_client = None
_location_client_lock = threading.Lock()


def _location():
    """Return the process-wide AWS Location client, building it lazily once."""
    global _location_client
    if _location_client is None:
        with _location_client_lock:
            if _location_client is None:
                import boto3
                from botocore.config import Config
                region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
                _location_client = boto3.client(
                    "location",
                    region_name=region,
                    # Pool ≥ fan-out width so parallel calls don't queue on connections;
                    # adaptive retries absorb ThrottlingException near the TPS quota.
                    config=Config(
                        max_pool_connections=max(_GEOCODE_MAX_WORKERS + 2, 10),
                        retries={"max_attempts": 5, "mode": "adaptive"},
                    ),
                )
    return _location_client


def _reset_location_client() -> None:
    """Drop the cached AWS Location client (used by tests, mirrors ports.reset_backend)."""
    global _location_client
    _location_client = None


def _drive_cache_key(olat: float, olon: float, dlat: float, dlon: float) -> str:
    """Per-leg drive-time cache key (origin→destination, rounded to ~111 m)."""
    return f"route_drive|{olat:.3f}|{olon:.3f}|{dlat:.3f}|{dlon:.3f}"


def _aws_drive_times(origin_lat: float, origin_lon: float, clusters: list) -> None:
    """Annotate each cluster dict with drive_minutes (int | None) via AWS Location route matrix.

    AWS-backend only. Mutates in-place. Silently sets None on any API failure so the
    caller always has the field, just without a value.

    Per-leg cached (origin→destination, rounded, 90-day TTL): dark-sky sites for a given
    area are stable, so repeat searches skip the route-matrix call entirely; only the
    cache-missing legs are sent to AWS. The matrix phase was ~2 s and previously uncached —
    the dominant warm cost (see docs/PERF_FINDNEARBY.md).
    """
    if not clusters:
        return
    # 1. Serve cached legs from a single batched route-matrix call's worth of history;
    #    collect the misses. A cached _DRIVE_NO_ROUTE means "computed, no route" (≠ miss).
    misses = []
    for c in clusters:
        cached = cache.get(_drive_cache_key(origin_lat, origin_lon, c["lat"], c["lon"]))
        if cached is not None:
            c["drive_minutes"] = None if cached == _DRIVE_NO_ROUTE else cached
        else:
            c["drive_minutes"] = None      # default until filled by the API below
            misses.append(c)
    if not misses:
        return
    # 2. One route-matrix call for just the uncached legs; cache each result. On failure
    #    leave drive_minutes=None and DON'T cache, so a transient error isn't sticky.
    calc_name = os.environ.get("PYNIGHTSKY_ROUTE_CALCULATOR", "pynightsky-route-calculator")
    try:
        client = _location()
        resp = client.calculate_route_matrix(
            CalculatorName=calc_name,
            DeparturePositions=[[origin_lon, origin_lat]],
            DestinationPositions=[[c["lon"], c["lat"]] for c in misses],
            TravelMode="Car",
        )
        row = resp.get("RouteMatrix", [[]])[0]
        for i, c in enumerate(misses):
            entry = row[i] if i < len(row) else {}
            secs  = entry.get("DurationSeconds") if entry else None
            mins  = round(secs / 60) if secs is not None else None
            c["drive_minutes"] = mins
            cache.set(_drive_cache_key(origin_lat, origin_lon, c["lat"], c["lon"]),
                      mins if mins is not None else _DRIVE_NO_ROUTE,
                      ttl_seconds=_GEO_CACHE_TTL)
    except Exception as e:
        log.debug("AWS route matrix failed: %s", e)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R    = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a    = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """16-point compass bearing from point 1 to point 2."""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon  = math.radians(lon2 - lon1)
    x     = math.sin(dlon) * math.cos(lat2r)
    y     = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    az    = math.degrees(math.atan2(x, y)) % 360
    return _DIRS_16[round(az / 22.5) % 16]



def _sqm_to_bortle_array(sqm_arr: "np.ndarray") -> "np.ndarray":
    """
    Vectorized sqm_to_bortle over a 2-D array.
    Returns int8 array; 0 where sqm_arr is NaN (ocean / nodata sentinel).
    """
    import numpy as np
    thresholds = np.array([t for t, _, _ in reversed(_BORTLE)], dtype=np.float64)
    classes    = np.array([c for _, c, _ in reversed(_BORTLE)], dtype=np.int8)
    valid = ~np.isnan(sqm_arr)
    out   = np.zeros(sqm_arr.shape, dtype=np.int8)
    idx   = np.searchsorted(thresholds, sqm_arr[valid], side="right") - 1
    out[valid] = classes[np.clip(idx, 0, len(classes) - 1)]
    return out


def _load_raster_window(
    source_key: str,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    out_shape: "tuple[int, int] | None" = None,
) -> "np.ndarray | None":
    """
    Read a bounding-box sub-window from a named raster source into a float64 ndarray.

    Row 0 = max_lat (north edge); last row = min_lat (south edge).
    Col 0 = min_lon (west edge); last col = max_lon (east edge).
    Nodata and negative pixels are clamped to 0.0.

    out_shape: optional (rows, cols) to resample the window on read — use this to
    align a coarser raster (e.g. Falchi) to the VIIRS pixel grid so both arrays
    are shape-identical before any arithmetic.

    Delegates to the active RasterSource's grid reader (tiled raw-binary; numpy +
    boto3, no GDAL). Both datasets are EPSG:4326 so there is no runtime reproject.
    Returns None on any error (backend unavailable, read failure).
    """
    try:
        return ports.get_backend().raster_source.read_window(
            source_key, min_lat, max_lat, min_lon, max_lon, out_shape=out_shape,
        )
    except Exception as exc:
        log.warning("_load_raster_window(%r, %.3f, %.3f, %.3f, %.3f) failed: %s",
                    source_key, min_lat, max_lat, min_lon, max_lon, exc)
        return None


def _connected_components_8(mask: "np.ndarray") -> "tuple[np.ndarray, int]":
    """Label 8-connected components of a 2-D boolean ``mask``.

    Pure-numpy drop-in for ``scipy.ndimage.label(mask, structure=np.ones((3, 3)))``:
    returns ``(labeled, n_features)`` with background = 0 and components numbered
    1..n in raster-scan order of first appearance — byte-identical to scipy's output
    (verified over random masks), so scipy is not a runtime dependency.

    Vectorised union-find by min-label propagation: build the 8-connectivity edges
    (4 shift directions cover all neighbour pairs), then iteratively push the minimum
    flat index across each edge with pointer-jumping until stable. Each component's
    representative is its minimum flat index, so sorting the unique representatives
    reproduces scipy's first-appearance numbering.
    """
    import numpy as np

    rows, cols = mask.shape
    n = rows * cols
    labeled = np.zeros((rows, cols), dtype=np.int64)
    if n == 0 or not mask.any():
        return labeled, 0

    grid = np.arange(n).reshape(rows, cols)
    a_parts, b_parts = [], []

    def _add(sel, ga, gb):
        if sel.any():
            a_parts.append(ga[sel])
            b_parts.append(gb[sel])

    _add(mask[:, :-1] & mask[:, 1:],   grid[:, :-1],  grid[:, 1:])    # right
    _add(mask[:-1, :] & mask[1:, :],   grid[:-1, :],  grid[1:, :])    # down
    _add(mask[:-1, :-1] & mask[1:, 1:], grid[:-1, :-1], grid[1:, 1:])  # down-right
    _add(mask[:-1, 1:] & mask[1:, :-1], grid[:-1, 1:],  grid[1:, :-1])  # down-left

    labels = np.arange(n)
    if a_parts:
        a = np.concatenate(a_parts)
        b = np.concatenate(b_parts)
        while True:
            prev = labels
            cand = np.minimum(labels[a], labels[b])
            nxt = labels.copy()
            np.minimum.at(nxt, a, cand)
            np.minimum.at(nxt, b, cand)
            nxt = nxt[nxt]                        # pointer-jump for fast convergence
            if np.array_equal(nxt, prev):
                break
            labels = nxt

    fg = mask.ravel()
    reps = labels[fg]
    uniq = np.unique(reps)                        # ascending = first-appearance order
    remap = np.zeros(n, dtype=np.int64)
    remap[uniq] = np.arange(1, uniq.size + 1)
    out = np.zeros(n, dtype=np.int64)
    out[fg] = remap[reps]
    return out.reshape(rows, cols), int(uniq.size)


def _find_light_domes_from_array(
    viirs_array: "np.ndarray",
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    tier_min_bortle: int = 8,
    min_blob_pixels: int = 4,
) -> list:
    """
    Pure numpy function — no I/O, no scipy, testable with synthetic arrays.

    Identifies contiguous regions of pixels whose Bortle class is >= tier_min_bortle
    and returns each blob's radiance-weighted centroid and peak Bortle class.

    Parameters
    ----------
    viirs_array : ndarray, shape (rows, cols), float64
        VIIRS radiance in nW/cm²/sr, non-negative (nodata clamped to 0).
        Row 0 = max_lat (north); last row = min_lat (south).
        Col 0 = min_lon (west); last col = max_lon (east).
    min_lat, max_lat, min_lon, max_lon : float
        Bounding box of the array in WGS-84 degrees.
    tier_min_bortle : int
        Minimum Bortle class to enter the tier mask (default 8 = city sky).
    min_blob_pixels : int
        Blobs smaller than this are discarded as noise (default 4).

    Returns
    -------
    list of (lat: float, lon: float, max_bortle: int) tuples.
    Returns [] if the array is empty or no blobs qualify.
    """
    import numpy as np

    rows, cols = viirs_array.shape
    if rows == 0 or cols == 0:
        return []

    # ── Coordinate grids ──────────────────────────────────────────────────────
    # indexing="ij" ensures lat_grid[r, c] and lon_grid[r, c] correspond to the
    # same pixel (r, c) in viirs_array.  Default "xy" would transpose lat/lon.
    lat_vals = np.linspace(max_lat, min_lat, rows)   # row 0 = north = max_lat
    lon_vals = np.linspace(min_lon, max_lon, cols)   # col 0 = west  = min_lon
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    # ── Ocean mask ────────────────────────────────────────────────────────────
    arr = viirs_array.copy()
    if _HAS_GLM:
        arr = np.where(_glm.is_land(lat_grid, lon_grid), arr, np.nan)

    # ── SQM + Bortle arrays ───────────────────────────────────────────────────
    # Do not round — rounding before thresholding causes systematic Bortle
    # misclassification near SQM boundaries.
    sqm_arr = np.where(arr > 0, 21.7 - 2.5 * np.log10(arr + 0.6), np.nan)
    bortle_arr = _sqm_to_bortle_array(sqm_arr)

    # ── Tier mask ─────────────────────────────────────────────────────────────
    tier_mask = (bortle_arr >= tier_min_bortle) & (bortle_arr != 0)
    if not tier_mask.any():
        return []

    # ── Connected-component labeling (8-connectivity) ─────────────────────────
    # 8-connectivity merges diagonally adjacent pixels, matching how city skyglow
    # blobs appear in the raster (not strictly axis-aligned). _connected_components_8
    # is a pure-numpy drop-in for scipy.ndimage.label (same partition AND label
    # numbering), so scipy is no longer a runtime dependency.
    labeled, n_features = _connected_components_8(tier_mask)

    # ── Per-blob reductions (radiance-weighted centroid + peak Bortle, vectorised) ─
    # viirs_array (not tier_mask) is the centroid weighting array so the centroid
    # gravitates toward the brightest core rather than the geometric centre — for a
    # crescent metro the geometric centroid can land in a bay or dark suburb.
    # Every label is reduced in a few full-array bincount/maximum.at passes (no
    # per-blob Python loop, no scipy index= APIs); output is identical.
    sizes = np.bincount(labeled.ravel())          # pixel count per label (index 0 = bg)
    keep = np.nonzero(sizes >= min_blob_pixels)[0]
    keep = keep[keep != 0]
    if keep.size == 0:
        return []

    n_lab   = sizes.size                          # n_features + 1 (incl. background)
    lab_flat = labeled.ravel()
    weights  = viirs_array.ravel().astype(np.float64)
    row_idx  = np.repeat(np.arange(rows, dtype=np.float64), cols)
    col_idx  = np.tile(np.arange(cols, dtype=np.float64), rows)
    # Radiance-weighted centroid: sum(w*coord)/sum(w) per label (matches
    # scipy.ndimage.center_of_mass(viirs_array, labeled)); zero total weight → NaN.
    wsum = np.bincount(lab_flat, weights=weights,           minlength=n_lab)
    rsum = np.bincount(lab_flat, weights=weights * row_idx, minlength=n_lab)
    csum = np.bincount(lab_flat, weights=weights * col_idx, minlength=n_lab)
    with np.errstate(invalid="ignore", divide="ignore"):
        cen_row = rsum[keep] / wsum[keep]
        cen_col = csum[keep] / wsum[keep]
    # Peak Bortle per label (matches scipy.ndimage.maximum(bortle_arr, labeled)).
    maxb = np.zeros(n_lab, dtype=np.int64)
    np.maximum.at(maxb, lab_flat, bortle_arr.ravel().astype(np.int64))
    max_bortle = maxb[keep]

    results = []
    for row_f, col_f, mb in zip(cen_row, cen_col, max_bortle):
        # A degenerate blob (zero total weight) yields a NaN centroid — skip it.
        if math.isnan(row_f) or math.isnan(col_f):
            continue
        row_i = min(int(round(row_f)), rows - 1)
        col_i = min(int(round(col_f)), cols - 1)
        results.append((
            float(lat_grid[row_i, col_i]),
            float(lon_grid[row_i, col_i]),
            int(mb),
        ))

    return results


def _extract_dark_sky_candidates(
    viirs_array: "np.ndarray",
    falchi_array: "np.ndarray | None",
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    origin_lat: float,
    origin_lon: float,
    radius_miles: float,
    dark_threshold: int,
) -> list:
    """
    Pure numpy function — no I/O, no scipy dependency.

    Builds a composite Bortle array (VIIRS primary, Falchi fills VIIRS-zero pixels),
    applies a vectorised haversine radius mask, and returns candidate dicts
    compatible with the existing _cluster_points() input format.

    Pre-sorts by distance and caps at _MAX_ARRAY_EXTRACT candidates before
    materialising any Python objects, bounding _cluster_points to at most
    _MAX_ARRAY_EXTRACT² haversine calls.

    Returns [] if viirs_array is None or has zero pixels.
    """
    import numpy as np

    if viirs_array is None:
        return []
    rows, cols = viirs_array.shape
    if rows == 0 or cols == 0:
        return []

    # ── Coordinate grids ──────────────────────────────────────────────────────
    lat_vals = np.linspace(max_lat, min_lat, rows)
    lon_vals = np.linspace(min_lon, max_lon, cols)
    lat_grid, lon_grid = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    # ── Land mask (compute once, reused in dark_mask below) ───────────────────
    land_mask = None
    if _HAS_GLM:
        land_mask = _glm.is_land(lat_grid, lon_grid)

    # ── Composite Bortle array ────────────────────────────────────────────────
    # VIIRS primary: any pixel with measurable radiance
    sqm_viirs = np.where(
        viirs_array > 0,
        21.7 - 2.5 * np.log10(viirs_array + 0.6),
        np.nan,
    )
    bortle_arr = _sqm_to_bortle_array(sqm_viirs)

    # Falchi fills VIIRS-zero pixels (dark sites below VIIRS detection floor)
    if falchi_array is not None:
        viirs_zero  = viirs_array == 0
        falchi_scaled = falchi_array * _FALCHI_SCALE
        sqm_falchi = np.where(
            falchi_scaled > 0,
            _SQM_NATURAL - 2.5 * np.log10(
                (falchi_scaled + _L_NATURAL) / _L_NATURAL
            ),
            np.nan,
        )
        falchi_bortle = _sqm_to_bortle_array(sqm_falchi)
        # VIIRS-zero AND Falchi-zero → pristine sky (Bortle 1)
        falchi_bortle = np.where(
            np.isnan(sqm_falchi) & viirs_zero, 1, falchi_bortle
        )
        bortle_arr = np.where(
            viirs_zero & (bortle_arr == 0), falchi_bortle, bortle_arr
        )

    # Combined SQM array: mirrors bortle_arr source selection.
    # VIIRS-measured pixels use sqm_viirs; Falchi-filled pixels use sqm_falchi;
    # pristine sky pixels (both sources zero) have NaN → stored as None.
    if falchi_array is not None:
        sqm_arr = np.where(viirs_array > 0, sqm_viirs, sqm_falchi)
    else:
        sqm_arr = sqm_viirs

    # ── Vectorised haversine distance ─────────────────────────────────────────
    dlat = np.radians(lat_grid - origin_lat)
    dlon = np.radians(lon_grid - origin_lon)
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(np.radians(origin_lat)) * np.cos(np.radians(lat_grid))
        * np.sin(dlon / 2) ** 2
    )
    dist_array = 2 * 3958.8 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    radius_mask = dist_array <= radius_miles

    # ── Dark pixel mask ───────────────────────────────────────────────────────
    # IMPORTANT: do NOT check ~np.isnan(viirs_array) here — viirs_array is raw
    # unmutated input, so it never contains NaN.  The Falchi fallback above also
    # assigns bortle_class=1 to ocean VIIRS-zero pixels, so bortle_arr > 0 alone
    # is insufficient.  _glm.is_land() is the authoritative ocean exclusion.
    dark_mask = (bortle_arr > 0) & (bortle_arr <= dark_threshold) & radius_mask
    if land_mask is not None:
        dark_mask &= land_mask

    candidate_rows, candidate_cols = np.where(dark_mask)
    if candidate_rows.size == 0:
        return []

    # ── Pre-sort by distance and cap ─────────────────────────────────────────
    # _cluster_points is O(N²) in haversine calls; a rural 150-mile search can
    # yield 250 000+ dark pixels.  Sorting and slicing here keeps _cluster_points
    # bounded at _MAX_ARRAY_EXTRACT² calls regardless of search area darkness.
    _MAX_ARRAY_EXTRACT = 500
    _N_BANDS           = 6          # distance bands across the search radius
    _PER_BAND          = (_MAX_ARRAY_EXTRACT + _N_BANDS - 1) // _N_BANDS  # ≈83

    valid_distances = dist_array[candidate_rows, candidate_cols]
    valid_bortles   = bortle_arr[candidate_rows, candidate_cols]

    # Stratified sampling: take up to _PER_BAND candidates per distance band,
    # darkest-first within each band.  Without this, when an entire search radius
    # is Bortle 1 the nearest-first cap fills all 500 slots with pixels from the
    # first 20 miles, and sites 80+ miles away (Grand Canyon, etc.) never appear.
    band_width = radius_miles / _N_BANDS
    selected: list[np.ndarray] = []
    for band in range(_N_BANDS):
        lo, hi = band * band_width, (band + 1) * band_width
        in_band = np.where((valid_distances >= lo) & (valid_distances < hi))[0]
        if in_band.size == 0:
            continue
        band_key = valid_bortles[in_band] * 1e6 + valid_distances[in_band]
        best = in_band[np.argsort(band_key)[:_PER_BAND]]
        selected.append(best)

    sorted_idx = np.concatenate(selected) if selected else np.array([], dtype=int)
    best_rows   = candidate_rows[sorted_idx]
    best_cols   = candidate_cols[sorted_idx]

    candidates = []
    for r, c in zip(best_rows, best_cols):
        _sqm = float(sqm_arr[r, c])
        candidates.append({
            "lat":            float(lat_grid[r, c]),
            "lon":            float(lon_grid[r, c]),
            "bortle_class":   int(bortle_arr[r, c]),
            "sqm":            None if np.isnan(_sqm) else _sqm,
            "distance_miles": round(float(dist_array[r, c]), 1),
            "direction":      _bearing_label(
                                  origin_lat, origin_lon,
                                  float(lat_grid[r, c]), float(lon_grid[r, c])),
            "name":           None,
        })
    return candidates


def _cluster_points(points: list, merge_miles: float = 8.0) -> list:
    """
    Greedy de-duplication: drop points within merge_miles of a darker/nearer one.
    Input points must have 'lat', 'lon', 'bortle_class', 'distance_miles'.
    Returns a reduced list of cluster representatives.
    """
    # Sort: Darkest skies first, then closest distance
    sorted_pts = sorted(points, key=lambda p: (p["bortle_class"], p["distance_miles"]))

    used = set()
    clusters = []

    for i, pt in enumerate(sorted_pts):
        # If this point was absorbed by a better, nearby point, skip it
        if i in used:
            continue

        # Keep this point as the best representative for its area
        clusters.append(pt)

        # Only check remaining points (j > i) to see if they fall within the merge radius
        for j in range(i + 1, len(sorted_pts)):
            if j not in used:
                other = sorted_pts[j]

                # If a lesser point is too close to our cluster center, mark it as used
                if _haversine_miles(pt["lat"], pt["lon"],
                                    other["lat"], other["lon"]) <= merge_miles:
                    used.add(j)

    return clusters

def _tags_to_priority(tags: dict) -> int:
    """Return _AREA_PRIORITY int from an OSM element's tags dict."""
    name     = tags.get("name", "")
    boundary = tags.get("boundary", "")
    landuse  = tags.get("landuse",  "")
    leisure  = tags.get("leisure",  "")
    if "wilderness" in name.lower():
        return _AREA_PRIORITY["wilderness"]
    if boundary == "national_park":
        return _AREA_PRIORITY["national_park"]
    if boundary in ("protected_area", "national_forest"):
        return _AREA_PRIORITY.get(boundary, 3)
    if leisure == "nature_reserve":
        return _AREA_PRIORITY["nature_reserve"]
    if landuse == "forest":
        return _AREA_PRIORITY["forest"]
    return 10


def _overpass_natural_areas_in_radius(
    lat: float, lon: float, radius_miles: int
) -> list[dict]:
    """
    Fetch all named protected/natural areas whose boundary intersects a circle
    around (lat, lon).  One HTTP call covers the entire search area.

    Returns list of dicts: {name, minlat, minlon, maxlat, maxlon, priority}
    Bounding-box corners come from "out bb tags" — used in _best_area_name_for_cluster
    to test containment rather than mere proximity to centre.
    Cached per origin coordinate + radius for 90 days.
    """
    cache_key = f"overpass_areas2|{lat:.2f}|{lon:.2f}|{radius_miles}"
    cached    = cache.get(cache_key)
    if cached is not None:
        return cached

    radius_m = int(radius_miles * 1609.344 * 1.15)   # +15 % margin for edge-overlap
    # Single around: lookup with an (if:) tag filter — ~3× faster than 5 separate
    # around: sub-queries because Overpass performs only one spatial index scan.
    # "out bb tags" returns the bounding-box corners so we can test containment.
    query = (
        f"[out:json][timeout:30];\n"
        f'relation(around:{radius_m},{lat:.4f},{lon:.4f})["name"]'
        f'(if: t["boundary"]=="national_park" || t["boundary"]=="national_forest" || '
        f't["boundary"]=="protected_area" || t["leisure"]=="nature_reserve" || '
        f't["landuse"]=="forest");\n'
        f"out bb tags;"
    )

    global _last_overpass_call
    with _overpass_lock:
        wait = _OVERPASS_SLEEP - (time.time() - _last_overpass_call)
        if wait > 0:
            time.sleep(wait)
        _last_overpass_call = time.time()

    params = urllib.parse.urlencode({"data": query})
    url    = f"{_OVERPASS_URL}?{params}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DarkHours/1.0 (light-pollution-research)"},
        )
        with _http.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.debug("Overpass areas-in-radius failed for (%.4f, %.4f): %s", lat, lon, e)
        cache.set(cache_key, [], ttl_seconds=300)   # short cache on failure
        return []

    areas = []
    for el in data.get("elements", []):
        tags   = el.get("tags", {})
        name   = tags.get("name")
        bounds = el.get("bounds", {})
        if not name or not bounds:
            continue
        # OSM sometimes packs multiple names with ";" — keep the longest segment
        # e.g. "Abbotts Bridge Unit;CRNRA - Abbotts Bridge Unit" → "CRNRA - Abbotts Bridge Unit"
        if ";" in name:
            name = max(name.split(";"), key=len).strip()
        areas.append({
            "name":     name,
            "minlat":   bounds["minlat"],
            "maxlat":   bounds["maxlat"],
            "minlon":   bounds["minlon"],
            "maxlon":   bounds["maxlon"],
            "priority": _tags_to_priority(tags),
        })

    log.debug("Overpass areas-in-radius: %d areas found for (%.4f, %.4f)", len(areas), lat, lon)
    cache.set(cache_key, areas, ttl_seconds=_GEO_CACHE_TTL)
    return areas


# National forests have bboxes 60–135 miles wide — far too coarse for
# containment matching (the rectangle includes towns, private land, gaps).
# Wilderness areas and monuments are 5–20 miles wide and are reliable.
# Only trust bbox containment when the bbox is compact enough.
_MAX_BBOX_MILES = 45.0


def _bbox_width_miles(area: dict) -> float:
    """Return the larger of the two bbox dimensions in miles."""
    dlat = (area["maxlat"] - area["minlat"]) * 69.0
    mid_lat = (area["minlat"] + area["maxlat"]) / 2
    dlon = (area["maxlon"] - area["minlon"]) * 69.0 * math.cos(math.radians(mid_lat))
    return max(dlat, dlon)


def _best_area_name_for_cluster(
    cluster_lat: float,
    cluster_lon: float,
    areas: list[dict],
) -> str | None:
    """
    CPU-only: find the best named area for a dark-sky cluster.

    Uses bounding-box containment (from "out bb tags").  Among all containing
    areas, picks the highest-priority one; breaks ties by the smallest bbox
    (more specific area wins).  Priority order: 0=wilderness, 1=monument,
    2=national park/forest/preserve/other.
    """
    best_name, best_priority, best_bbox_area = None, 999, float("inf")

    for area in areas:
        # Bounding-box containment check
        if not (area["minlat"] <= cluster_lat <= area["maxlat"] and
                area["minlon"] <= cluster_lon <= area["maxlon"]):
            continue

        p = area["priority"]
        # Prefer higher priority; break ties by smaller bbox (more specific area)
        bbox_area = ((area["maxlat"] - area["minlat"]) *
                     (area["maxlon"] - area["minlon"]))
        if p < best_priority or (p == best_priority and bbox_area < best_bbox_area):
            best_priority, best_name, best_bbox_area = p, area["name"], bbox_area

    return best_name


def _nominatim_settlement(lat: float, lon: float) -> str | None:
    """
    Reverse-geocode (lat, lon) via Nominatim to a city/town name.
    Returns "City, ST" for US locations, "City" elsewhere, or None for rural areas.
    Results cached for 90 days.
    """
    cache_key = f"nominatim_rev|{lat:.3f}|{lon:.3f}"
    cached    = cache.get(cache_key)
    if cached is not None:
        return cached or None

    # Thread-safe rate limiting
    global _last_nominatim_call
    with _nominatim_lock:
        wait = _NOMINATIM_SLEEP - (time.time() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.time()

    params = urllib.parse.urlencode({
        "lat": f"{lat:.4f}", "lon": f"{lon:.4f}",
        "format": "json", "zoom": "16", "addressdetails": "1",
    })
    url = f"{_NOMINATIM_URL}?{params}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DarkHours/1.0 (light-pollution-research)"},
        )
        with _http.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.debug("Nominatim lookup failed for (%.4f, %.4f): %s", lat, lon, e)
        return None

    address    = data.get("address", {})
    # Check progressively less specific place types before giving up
    name = (address.get("city") or address.get("town") or address.get("village")
            or address.get("hamlet") or address.get("suburb") or address.get("municipality"))
    state_code = address.get("ISO3166-2-lvl4", "")   # e.g. "US-AZ"
    state_abbr = state_code.split("-")[-1] if "-" in state_code else ""
    county_raw = address.get("county", "")

    if not name:
        # Last resort: strip "County"/"Parish" suffix and use that
        name = (county_raw.replace(" County", "").replace(" Parish", "").strip()) or None

    result = f"{name}, {state_abbr}" if (name and state_abbr) else name or ""

    if not result:
        # No state AND no county → ocean, large lake, or international waters.
        # Grid sample points over water are dark simply because no one lives there;
        # they're useless as observing sites and should be excluded from results.
        if not county_raw and not address.get("state"):
            cache.set(cache_key, _OVER_WATER, ttl_seconds=_GEO_CACHE_TTL)
            return _OVER_WATER
        # Has state/country context but no settlement — cache briefly and return None
        cache.set(cache_key, "", ttl_seconds=86400)
        return None

    cache.set(cache_key, result, ttl_seconds=_GEO_CACHE_TTL)
    # Side-cache county-derived city name so find_nearby() can broaden origin exclusions
    # without a second API call.  e.g. origin=Culver City → also exclude "Los Angeles, CA".
    if county_raw and state_abbr and name:
        _cc = county_raw.replace(" County", "").replace(" Parish", "").strip()
        if _cc and _cc != name:
            cache.set(f"nominatim_county|{lat:.3f}|{lon:.3f}",
                      f"{_cc}, {state_abbr}", ttl_seconds=_GEO_CACHE_TTL)
    return result


def _aws_location_settlement(lat: float, lon: float) -> str | None:
    """
    Reverse-geocode (lat, lon) via AWS Location Service (aws backend only).

    Same contract as _nominatim_settlement: returns "City, ST" / "City" /
    _OVER_WATER sentinel / None.  Results are cached identically so the
    two implementations are interchangeable.
    """
    cache_key = f"nominatim_rev|{lat:.3f}|{lon:.3f}"   # reuse same cache namespace
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None

    index_name = os.environ.get("PYNIGHTSKY_PLACE_INDEX", "pynightsky-place-index")
    try:
        client = _location()
        resp = client.search_place_index_for_position(
            IndexName=index_name,
            Position=[lon, lat],        # AWS expects [lon, lat]
            MaxResults=1,
        )
    except Exception as e:
        log.debug("AWS Location reverse geocode failed for (%.4f, %.4f): %s", lat, lon, e)
        return None

    results = resp.get("Results", [])
    if not results:
        cache.set(cache_key, _OVER_WATER, ttl_seconds=_GEO_CACHE_TTL)
        return _OVER_WATER

    place = results[0]["Place"]
    label = place.get("Label", "")
    municipality = place.get("Municipality", "")
    sub_region = place.get("SubRegion", "")
    region = place.get("Region", "")

    # Walk label backwards to find the 2-char US state code; the element immediately
    # before it is the authoritative city name.  This avoids neighbourhood artifacts
    # where Municipality can be a district ("Brickell") rather than the city ("Miami").
    # label forms: "City, ST, USA" / "District, City, ST, USA" / "Street, City, ST, USA"
    parts = [p.strip() for p in label.split(",")]
    state_abbr = ""
    city_from_label = ""
    for i in range(len(parts) - 1, 0, -1):
        part = parts[i]
        if len(part) == 2 and part.isalpha() and part.isupper():
            state_abbr = part
            candidate = parts[i - 1]
            # Skip if street number (starts with digit); fall back to municipality/sub_region
            if candidate and not candidate[0].isdigit():
                city_from_label = candidate
            break

    # Preference: city extracted from label > Municipality > SubRegion (strip county suffix)
    name = city_from_label or municipality
    if not name and sub_region:
        name = sub_region.replace(" County", "").replace(" Parish", "").strip()

    result = f"{name}, {state_abbr}" if (name and state_abbr) else name or ""

    if not result:
        if not region:
            cache.set(cache_key, _OVER_WATER, ttl_seconds=_GEO_CACHE_TTL)
            return _OVER_WATER
        cache.set(cache_key, "", ttl_seconds=86400)
        return None

    cache.set(cache_key, result, ttl_seconds=_GEO_CACHE_TTL)
    # Side-cache sub_region-derived city for origin exclusion (same key as Nominatim
    # county cache) so _get_nominatim_county_city() works on both backends.
    if sub_region and state_abbr and name:
        _sr = sub_region.replace(" County", "").replace(" Parish", "").strip()
        if _sr and _sr != name:
            cache.set(f"nominatim_county|{lat:.3f}|{lon:.3f}",
                      f"{_sr}, {state_abbr}", ttl_seconds=_GEO_CACHE_TTL)
    return result


def _settlement(lat: float, lon: float) -> str | None:
    """Dispatch to AWS Location or Nominatim based on the active backend."""
    if ports.get_backend()._name == "aws":
        return _aws_location_settlement(lat, lon)
    return _nominatim_settlement(lat, lon)


def _get_nominatim_county_city(lat: float, lon: float) -> "str | None":
    """Return the county-derived city name cached by _nominatim_settlement(), or None."""
    return cache.get(f"nominatim_county|{lat:.3f}|{lon:.3f}") or None


# ---------------------------------------------------------------------------
# PAD-US H3 spatial index helpers
# ---------------------------------------------------------------------------

def _padus_h3_path() -> "Path | None":
    """Resolve the H3 index (.npz) path, trying env override → repo layout → Lambda layout."""
    env_override = os.environ.get("PYNIGHTSKY_PADUS_H3_PATH")
    if env_override:
        p = Path(env_override)
        return p if p.exists() else None
    for candidate in (
        Path(__file__).parent.parent / "cache" / _PADUS_H3_FILENAME,
        Path("/app/cache") / _PADUS_H3_FILENAME,
    ):
        if candidate.exists():
            return candidate
    return None


class _PadusIndex:
    """Columnar PAD-US H3 index: a sorted uint64 cell array plus parallel blacklist
    and dictionary-encoded name arrays. Replaces a ~1.4M-entry Python dict — the dict
    build dominated worker cold starts (~1.8 s locally, 5-24 s on Lambda's throttled
    vCPU). Lookups binary-search the cell array (see _padus_h3_lookup); the name is
    materialised only on a hit via ``names[name_codes[i]]``. Read from a compressed
    .npz with numpy only (no pyarrow)."""

    __slots__ = ("cells", "name_codes", "names", "blacklist")

    def __init__(self, cells, name_codes, names, blacklist):
        self.cells = cells              # np.ndarray[uint64], ascending
        self.name_codes = name_codes    # np.ndarray[uint32], index into names, aligned to cells
        self.names = names              # list[str], unique names (dictionary values)
        self.blacklist = blacklist      # np.ndarray[bool], aligned to cells


def _load_padus_h3_index() -> "_PadusIndex | None":
    """Lazy-load the PAD-US H3 index once per process; return the cached index after.

    The index is columnar (see _PadusIndex), so loading is a numpy .npz read rather
    than a ~1.4M-object Python dict build. Returns None (and caches that failure) if
    h3/numpy is unavailable, the .npz is missing, or the read fails — callers degrade
    gracefully to Tier 2/3.
    """
    global _padus_h3_cache
    if _padus_h3_cache is _PADUS_UNAVAILABLE:
        return None
    if _padus_h3_cache is not None:
        return _padus_h3_cache  # type: ignore[return-value]

    if not _HAS_H3:
        _padus_h3_cache = _PADUS_UNAVAILABLE
        return None

    path = _padus_h3_path()
    if path is None:
        log.debug("PAD-US H3 index (.npz) not found; Tier-1 spatial filter disabled.")
        _padus_h3_cache = _PADUS_UNAVAILABLE
        return None

    try:
        import numpy as np
        with np.load(path) as npz:
            cells      = npz["cells"].astype(np.uint64, copy=False)
            name_codes = npz["name_codes"].astype(np.uint32, copy=False)
            blacklist  = npz["blacklist"].astype(bool, copy=False)
            names_blob = npz["names_blob"].tobytes()
        # Dictionary values: unique names joined on NUL (see convert_padus_parquet_to_npz).
        names = names_blob.decode("utf-8").split("\x00") if names_blob else []
        # The .npz is written sorted by cell, so np.searchsorted is valid. Guard
        # cheaply (~ms) and sort all three together if it ever isn't.
        if cells.size and not bool(np.all(cells[:-1] <= cells[1:])):
            order      = np.argsort(cells, kind="stable")
            cells      = cells[order]
            name_codes = name_codes[order]
            blacklist  = blacklist[order]
        _padus_h3_cache = _PadusIndex(cells, name_codes, names, blacklist)
        log.debug("PAD-US H3 index loaded: %d cells (numpy .npz).", cells.size)
    except Exception as exc:
        log.debug("PAD-US H3 index load failed: %s", exc)
        _padus_h3_cache = _PADUS_UNAVAILABLE
        return None

    return _padus_h3_cache  # type: ignore[return-value]


def _padus_h3_lookup(
    lat: float,
    lon: float,
    index: "_PadusIndex",
) -> "tuple[str, bool] | None":
    """Return (Unit_Nm, is_blacklisted) for the H3 cell at (lat, lon), or None.

    Binary-searches the sorted uint64 cell array; the name is materialised from the
    Arrow array only on a hit.
    """
    import numpy as np
    cell  = _h3_lib.str_to_int(_h3_lib.latlng_to_cell(lat, lon, 7))
    cells = index.cells
    i = int(np.searchsorted(cells, np.uint64(cell)))
    if i < cells.size and cells[i] == cell:
        return (index.names[int(index.name_codes[i])], bool(index.blacklist[i]))
    return None


def _is_good_padus_name(unit_nm: "str | None") -> bool:
    """Return True if unit_nm is a meaningful display name for a PAD-US unit.

    Rejects None/empty strings, raw short codes (< 5 chars), names containing
    'unknown', and pure numeric legacy IDs.
    """
    if not unit_nm or not unit_nm.strip():
        return False
    nm = unit_nm.strip()
    if len(nm) < 5:
        return False
    nml = nm.lower()
    if "unknown" in nml:
        return False
    if "unnamed" in nml:
        return False
    if "office" in nml:
        return False
    if nm.isdigit():
        return False
    return True


def _is_in_us(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) falls within the US bounding box (incl. AK, HI)."""
    return (18.0 <= lat <= 72.0) and (-180.0 <= lon <= -66.0)


def _offline_tier_name(
    c: dict,
    padus_index: "dict | None",
    natural_areas: "list | None",
) -> "tuple[str, str | None]":
    """Resolve the offline naming tiers (1 PAD-US, 2 Overpass) for one candidate.

    Returns one of:
      ("discard", None) — PAD-US blacklisted cell (military/tribal/restricted land).
      ("name", str)     — named by PAD-US (good Unit_Nm) or an Overpass area match.
      ("tier3", None)   — needs the network reverse-geocoder (Tier 3).

    No I/O beyond the in-memory PAD-US index and Overpass area list, so it is cheap
    to call twice (prefetch planning + the main loop).
    """
    lat, lon = c["lat"], c["lon"]
    padus_verified = False
    if padus_index is not None:
        try:
            hit = _padus_h3_lookup(lat, lon, padus_index)
            if hit is not None:
                unit_nm, is_blacklisted = hit
                if is_blacklisted:
                    return ("discard", None)  # hard stop: restricted land
                padus_verified = True
                if _is_good_padus_name(unit_nm):
                    return ("name", unit_nm)  # optimal hit: no network needed
        except Exception as exc:
            log.debug("PAD-US H3 lookup failed (%.4f, %.4f): %s", lat, lon, exc)
            # treat as miss → fall through to Tier 2
    # Tier 2 only when PAD-US had no polygon hit; never discard on an Overpass miss.
    if not padus_verified and natural_areas:
        nm = _best_area_name_for_cluster(lat, lon, natural_areas)
        if nm:
            return ("name", nm)
    return ("tier3", None)


def _parallel_prefetch_settlements(
    candidates: list,
    padus_index: "dict | None",
    natural_areas: "list | None",
) -> dict:
    """Concurrently reverse-geocode the spatially-distinct Tier-3 candidates.

    Used only on backends with no per-second policy (AWS Location). Candidates that
    Tiers 1-2 already name or discard are excluded; the remainder are clustered at
    _NAME_DEDUP_MILES so only one representative per ~8 mi area is fetched (mirroring
    the serial path's spatial dedup), and those reps are geocoded in parallel.

    Returns {(round(lat,3), round(lon,3)): settlement_result}, where the value is
    whatever _settlement returned (a name / "" / None / _OVER_WATER). The main loop
    reads names from this map instead of issuing the calls serially.
    """
    need = [c for c in candidates
            if _offline_tier_name(c, padus_index, natural_areas)[0] == "tier3"]
    if not need:
        return {}
    reps = _cluster_points(need, merge_miles=_NAME_DEDUP_MILES)
    out: dict = {}
    workers = min(_GEOCODE_MAX_WORKERS, len(reps))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_settlement, c["lat"], c["lon"]): c for c in reps}
        for fut in futures:
            c = futures[fut]
            try:
                out[(round(c["lat"], 3), round(c["lon"], 3))] = fut.result()
            except Exception as exc:
                log.debug("parallel settlement failed (%.4f, %.4f): %s",
                          c["lat"], c["lon"], exc)
    return out


def _jit_geocode_candidates(
    candidates: list,
    max_results: int,
    natural_areas: list | None = None,
    *,
    padus_index: "dict | None" = None,
    exclude: "set[str] | None" = None,
) -> list:
    """Reverse-geocode candidates with a three-tier naming strategy.

    Tier 1 — PAD-US H3 spatial index (when padus_index is not None):
      Blacklisted cell      → candidate discarded (military/tribal/restricted).
      Non-blacklisted + good Unit_Nm → name used; Overpass and _settlement() skipped.
      Non-blacklisted + junk Unit_Nm → PAD-US-verified; falls to Tier 3 for naming.
      No PAD-US cell hit    → falls to Tier 2.

    Tier 2 — Overpass natural areas (naming only, not gating):
      Reached only when PAD-US had no polygon hit. Match → use the area name;
      no match → fall through to Tier 3 (never discard on an Overpass miss).

    Tier 3 — Reverse geocoder (_settlement):
      Reached when Tiers 1-2 produced no name. _OVER_WATER → candidate discarded;
      None → coordinate fallback used.

    Concurrency: the public Nominatim instance (local backend) forbids parallel/bulk
    access, so it keeps the lazy serial path. On the aws backend (AWS Location, no
    per-second policy) the Tier-3 calls are prefetched in parallel up front and the
    loop reads names from memory — see _parallel_prefetch_settlements.

    Dedup: each unique name appears at most once. Tier-3 candidates also get a spatial
    pre-dedup (see _NAME_DEDUP_MILES) that skips a candidate adjacent to an
    already-named result. max_results cap unchanged.
    """
    seen_keys: set[str] = set(exclude) if exclude else set()
    dark_clusters: list = []
    kept_coords: list[tuple[float, float]] = []   # (lat, lon) of accepted results

    # On backends with no per-second policy (AWS Location), fetch the Tier-3 names
    # concurrently so the loop below resolves them from memory instead of one serial
    # network round-trip per candidate. Empty on the local/Nominatim backend.
    prefetch = (_parallel_prefetch_settlements(candidates, padus_index, natural_areas)
                if ports.get_backend()._name == "aws" else {})

    for c in candidates:
        lat, lon = c["lat"], c["lon"]
        kind, name = _offline_tier_name(c, padus_index, natural_areas)
        if kind == "discard":
            continue  # PAD-US blacklist: military/tribal/restricted land

        if kind == "tier3":
            # Spatial pre-dedup: skip when within _NAME_DEDUP_MILES of an already-kept
            # result — adjacent dark pixels reverse-geocode to the same settlement, so
            # probing them only yields a duplicate name. Profiling a Phoenix search
            # found 36 of 43 probes were such duplicates (median 5.3 mi from their kept
            # twin), the single largest contributor to find_nearby latency.
            if any(_haversine_miles(lat, lon, klat, klon) <= _NAME_DEDUP_MILES
                   for klat, klon in kept_coords):
                continue
            key = (round(lat, 3), round(lon, 3))
            # Prefetched on aws; otherwise (local, or a non-representative pixel whose
            # rep was dropped) fall back to a single direct call.
            name = prefetch[key] if key in prefetch else _settlement(lat, lon)
            if name == _OVER_WATER:
                continue
            if not name:
                name = f"{lat:.2f}°, {lon:.2f}°"

        # ── Dedup and accumulate ──────────────────────────────────────────────
        if name in seen_keys:
            continue
        seen_keys.add(name)
        c["name"] = name
        dark_clusters.append(c)
        kept_coords.append((lat, lon))
        if len(dark_clusters) == max_results:
            break
    return dark_clusters


# ---------------------------------------------------------------------------
# find_nearby profiling (opt-in via PYNIGHTSKY_PROFILE=1)
# ---------------------------------------------------------------------------
# find_nearby has three very different cost centres — disk-bound raster window
# reads, CPU-bound numpy/scipy passes, and network-bound reverse-geocoding whose
# cost is dominated by cache misses (each Nominatim miss waits _NOMINATIM_SLEEP).
# The profiler attributes wall-clock time to each phase and pairs it with the
# cache hit/miss delta so a slow run can be diagnosed as "cold cache" vs "slow
# I/O" vs "CPU". Disabled by default → the phase() context manager is a no-op.

_PROFILE = os.environ.get("PYNIGHTSKY_PROFILE", "").strip().lower() in ("1", "true", "yes", "on")


class _Profiler:
    """Accumulate per-phase wall-clock timings. Near-zero cost when disabled."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.timings: list[tuple[str, float]] = []
        self._cache_start = (0, 0)
        if enabled:
            self._cache_start = cache.stats.snapshot()

    @contextlib.contextmanager
    def phase(self, name: str):
        if not self.enabled:
            yield
            return
        h0, m0 = cache.stats.snapshot()
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            h1, m1 = cache.stats.snapshot()
            self.timings.append((name, dt))
            log.info("[profile] %-26s %8.1f ms  (cache +%dh/+%dm)",
                     name, dt, h1 - h0, m1 - m0)

    def report(self) -> None:
        if not self.enabled:
            return
        total = sum(dt for _, dt in self.timings)
        h, m = cache.stats.snapshot()
        dh, dm = h - self._cache_start[0], m - self._cache_start[1]
        log.info("[profile] %-26s %8.1f ms  (cache %dh/%dm this call)",
                 "TOTAL (sum of phases)", total, dh, dm)


def find_nearby(lat: float, lon: float, radius_miles:int) -> dict | None:
    """
    Search for darker sky areas and nearby light domes within radius_miles of (lat, lon).

    Samples Bortle class on a ring grid, clusters nearby dark spots, then names
    each cluster via Overpass (natural/protected areas) or Nominatim (settlements).

    Returns dict with keys:
      origin_bortle   int
      origin_sqm      float | None
      radius_miles    int
      results         list[dict]  — dark clusters meeting the threshold
      light_domes     list[dict]  — bright city glows visible from the origin
      has_dark_sky    bool
      best_available  dict | None — nearest/darkest point when threshold not met

    Each result/dome/best_available entry has:
      name, bortle_class, sqm, distance_miles, direction

    Returns None if light pollution data is unavailable (raster grid unreadable).
    """
    prof = _Profiler(_PROFILE)
    _funnel: dict = {}   # candidate counts per stage (logged at end when profiling)
    with prof.phase("origin lookup"):
        origin_info = lookup(lat, lon)
    if origin_info is None:
        return None

    origin_bortle = origin_info.get("bortle_class") or 5
    origin_sqm    = origin_info.get("sqm")

    # Dark threshold: need to be meaningfully darker than origin
    if origin_bortle <= 2:
        dark_threshold = 1   # Bortle 2 → require Bortle 1
    else:
        dark_threshold = min(origin_bortle - 2, 3) # Bortle 3 is the darkest we can reliably surface from a suburban origin (Bortle 5+)

    # Force the bounding box out to 150 miles to catch distant megacities regardless
    # of the user's requested driving radius.  Both dark-sky and dome detection read
    # from the same two window reads — exactly one per raster dataset.
    dome_search_radius = max(radius_miles, 150)
    _deg_lat = dome_search_radius / 69.0
    _deg_lon = dome_search_radius / max(69.0 * math.cos(math.radians(lat)), 0.01)
    _min_lat, _max_lat = lat - _deg_lat, lat + _deg_lat
    _min_lon, _max_lon = lon - _deg_lon, lon + _deg_lon

    # ── Single window read per raster dataset ─────────────────────────────────
    with prof.phase("viirs window read"):
        viirs_arr  = _load_raster_window("viirs",  _min_lat, _max_lat, _min_lon, _max_lon)
    with prof.phase("falchi window read"):
        falchi_arr = _load_raster_window(
            "falchi", _min_lat, _max_lat, _min_lon, _max_lon,
            out_shape=viirs_arr.shape if viirs_arr is not None else None,
        )

    # ── Dark-sky candidates (pure numpy, no scipy required) ───────────────────
    with prof.phase("extract dark candidates"):
        dark_candidates = _extract_dark_sky_candidates(
            viirs_arr, falchi_arr,
            _min_lat, _max_lat, _min_lon, _max_lon,
            lat, lon, radius_miles, dark_threshold,
        )
    _funnel["extract_raw"] = len(dark_candidates)

    # all_darker: any pixel darker than origin — used only for best_available
    # when no proper dark clusters are found.  Computed lazily to avoid
    # the second numpy pass on the common case where dark_candidates is non-empty.
    all_darker: list = []

    _MAX_DARK_CANDIDATES = 60    # post-cluster cap (total, spread across distance bands)
    _MAX_RESULTS = 10    # max dark-sky areas to name and display
    _MAX_DOMES   = 10    # max light domes to name and display
    _N_BANDS     = 6     # distance bands used in both extract and cluster selection

    # Calculate priority: Bortle 1/2 are "MUST HAVES" (boosted), 3+ are distance-based
    with prof.phase("cluster + band select"):
        for pt in dark_candidates:
            if pt["bortle_class"] <= 2:
                pt["priority_score"] = pt["distance_miles"] * (radius_miles * 0.25)
            else:
                pt["priority_score"] = pt["distance_miles"]

        dark_candidates.sort(key=lambda p: p["priority_score"])

        # Cluster spatially (pure CPU) to collapse adjacent pixels into geographic areas.
        # Then select clusters via stratified distance sampling: take up to _PER_BAND
        # clusters per distance band (darkest/nearest first within each band).
        # Without stratification, a nearest-first cap silently drops all distant
        # dark areas when the entire search radius is one Bortle class (e.g. Bortle 1).
        _per_band    = max(1, _MAX_DARK_CANDIDATES // _N_BANDS)
        _band_width  = radius_miles / _N_BANDS
        all_clusters = _cluster_points(dark_candidates, merge_miles=1) if dark_candidates else []
        _funnel["clusters"] = len(all_clusters)
        selected: list = []
        for _band in range(_N_BANDS):
            _lo, _hi = _band * _band_width, (_band + 1) * _band_width
            _band_clusters = sorted(
                [c for c in all_clusters if _lo <= c["distance_miles"] < _hi],
                key=lambda c: (c["bortle_class"], c["distance_miles"]),
            )[:_per_band]
            selected.extend(_band_clusters)
        dark_candidates = sorted(selected, key=lambda c: (c["bortle_class"], c["distance_miles"]))
        _funnel["band_selected"] = len(dark_candidates)

    # ── Light dome candidates (numpy blob detection on the same viirs_arr) ─────
    # A dome must be brighter than the origin by >=2 Bortle classes (dbortle >
    # origin_bortle and >= min(origin_bortle+2, 10)). The brightest possible blob is
    # Bortle 9, so for an origin already at Bortle 8-9 no dome can ever qualify —
    # skip detection AND naming entirely (output is unchanged: it was always empty).
    dome_clusters = []
    _funnel["domes_raw"] = 0
    _dome_search = origin_bortle <= 7
    with prof.phase("light dome detection"):
        if viirs_arr is not None and _dome_search:
            raw_domes = _find_light_domes_from_array(
                viirs_arr, _min_lat, _max_lat, _min_lon, _max_lon,
                tier_min_bortle=8,
            )
            _funnel["domes_raw"] = len(raw_domes)
            for dlat, dlon, dbortle in raw_domes:
                dist  = _haversine_miles(lat, lon, dlat, dlon)
                if dist < 5:
                    continue
                if dbortle > origin_bortle and dbortle >= min(origin_bortle + 2, 10):
                    dome_clusters.append({
                        "lat":            dlat,
                        "lon":            dlon,
                        "bortle_class":   dbortle,
                        "sqm":            None,
                        "distance_miles": round(dist, 1),
                        "direction":      _bearing_label(lat, lon, dlat, dlon),
                        "name":           None,
                    })

    _funnel["domes_pass_filter"] = len(dome_clusters)
    # Take 2× the display limit so dedup has buffer to fill _MAX_DOMES unique names.
    dome_clusters = sorted(dome_clusters, key=lambda p: (-p["bortle_class"], p["distance_miles"]))[:_MAX_DOMES * 2]

    # ── Naming ─────────────────────────────────────────────────────────────
    _OVERPASS_JOIN_TIMEOUT_S = 15.0
    _use_overpass = (ports.get_backend()._name != "aws")

    best_available = None
    best_candidate = None
    with prof.phase("best-available numpy pass"):
        if not dark_candidates and origin_bortle > 1:
            # Lazy: only run the second numpy pass when there are no proper dark clusters.
            # Uses a looser threshold (anything darker than origin) to find a fallback.
            all_darker = _extract_dark_sky_candidates(
                viirs_arr, falchi_arr,
                _min_lat, _max_lat, _min_lon, _max_lon,
                lat, lon, radius_miles, dark_threshold=origin_bortle - 1,
            )
        if all_darker:
            best_candidate = sorted(
                all_darker, key=lambda p: (p["bortle_class"], p["distance_miles"])
            )[0]

    # Resolve the origin's settlement name so it can be excluded from results —
    # there is no point surfacing a dark candidate named after the city you're in.
    # Also exclude the county's principal city (e.g. "Los Angeles, CA" when searching
    # from Culver City), populated as a free side-effect of _nominatim_settlement().
    with prof.phase("origin settlement"):
        _origin_settlement = _settlement(lat, lon)
        _exclude: set[str] = set()
        if _origin_settlement and _origin_settlement != _OVER_WATER:
            _exclude.add(_origin_settlement)
            # Add sub-region/county-derived city to catch mismatches where the origin
            # reverse-geocodes to a different granularity than nearby candidates.
            # _settlement() side-populates nominatim_county|... for both backends.
            _county_city = _get_nominatim_county_city(lat, lon)
            if _county_city:
                _exclude.add(_county_city)

    # Load PAD-US H3 index once per search (US searches only — PAD-US is US-only data).
    # Candidates are already land-filtered by _extract_dark_sky_candidates(_glm.is_land()),
    # so the PAD-US check here is the first spatial tier, not a water filter.
    with prof.phase("padus index load"):
        _padus_index = _load_padus_h3_index() if _is_in_us(lat, lon) else None

    # Fetch Overpass natural areas in background while dome naming runs.
    # One network call covers the entire search radius; all cluster matching
    # is then done locally with no further API calls for areas it covers.
    natural_areas: list = []
    if _use_overpass:
        def _fetch_areas():
            natural_areas.extend(
                _overpass_natural_areas_in_radius(lat, lon, radius_miles)
            )
        areas_thread = threading.Thread(target=_fetch_areas, daemon=True)
        areas_thread.start()

    # Name light domes. AWS Location has no per-second policy, so geocode them
    # concurrently (like _parallel_prefetch_settlements); public Nominatim (local
    # backend) stays serial per its usage policy. Names/order are unchanged.
    with prof.phase("dome naming (geocode)"):
        if dome_clusters:
            if ports.get_backend()._name == "aws" and len(dome_clusters) > 1:
                workers = min(_GEOCODE_MAX_WORKERS, len(dome_clusters))
                with ThreadPoolExecutor(max_workers=workers) as _ex:
                    _names = list(_ex.map(
                        lambda d: _settlement(d["lat"], d["lon"]), dome_clusters))
            else:
                _names = [_settlement(d["lat"], d["lon"]) for d in dome_clusters]
            for dome, _nm in zip(dome_clusters, _names):
                dome["name"] = _nm or f"{dome['lat']:.2f}°, {dome['lon']:.2f}°"

        # Deduplicate by name; list is sorted by (bortle desc, distance asc) so we keep
        # the nearest occurrence of each city name.
        _seen_dome_names: set[str] = set()
        _deduped_domes: list = []
        for dome in dome_clusters:
            if dome["name"] not in _seen_dome_names:
                _seen_dome_names.add(dome["name"])
                _deduped_domes.append(dome)
        dome_clusters = _deduped_domes[:_MAX_DOMES]
        _funnel["domes_final"] = len(dome_clusters)

    if _use_overpass:
        with prof.phase("overpass join (net)"):
            areas_thread.join(timeout=_OVERPASS_JOIN_TIMEOUT_S)
            if areas_thread.is_alive():
                log.warning("Overpass join timed out after %ss; falling back to geocoding only",
                            _OVERPASS_JOIN_TIMEOUT_S)

    # 1. Name the best_candidate separately if it exists
    if best_candidate:
        with prof.phase("best-candidate naming"):
            name = _best_area_name_for_cluster(best_candidate["lat"], best_candidate["lon"], natural_areas) or \
                   _settlement(best_candidate["lat"], best_candidate["lon"])
            best_candidate["name"] = None if name == _OVER_WATER else (name or f"{best_candidate['lat']:.2f}°, {best_candidate['lon']:.2f}°")
            if best_candidate["name"] is not None:
                best_available = best_candidate

    # 2. JIT: geocode and deduplicate dark candidates, stop at _MAX_RESULTS
    # Pass natural_areas=None (not []) when Overpass is disabled so the Tier 2 discard
    # gate is bypassed — an empty list would incorrectly discard all non-PAD-US candidates
    # on the AWS backend where Overpass is not used.
    with prof.phase("jit geocode candidates"):
        dark_clusters = _jit_geocode_candidates(
            dark_candidates, _MAX_RESULTS,
            natural_areas if _use_overpass else None,
            padus_index=_padus_index,
            exclude=_exclude,
        )

    if best_candidate is not None:
        best_available = best_candidate

    # Drive-time annotation (AWS backend only)
    with prof.phase("drive times (aws)"):
        needs_drive = dark_clusters + ([best_available] if best_available else [])
        if ports.get_backend()._name == "aws":
            _aws_drive_times(lat, lon, needs_drive)

            # Sky quality first; drive time is only a tie-breaker for identical bortle_class
            needs_drive.sort(key=lambda c: (
                c["bortle_class"],
                c["drive_minutes"] if c["drive_minutes"] is not None else 999,
            ))
            # Re-assign back to dark_clusters (excluding the best_available)
            dark_clusters = needs_drive[:len(dark_clusters)]
        else:
            # Keep existing distance-based sort for local backend
            for c in needs_drive:
                c["drive_minutes"] = None

    _funnel["results_final"] = len(dark_clusters)
    _funnel["best_available"] = best_available is not None
    if prof.enabled:
        # One structured line per call: the candidate funnel + every surfaced
        # coordinate, for the profiling harness to capture and save.
        _funnel["origin"] = {"lat": round(lat, 5), "lon": round(lon, 5),
                             "radius_mi": radius_miles,
                             "bortle": origin_bortle, "sqm": origin_sqm}
        _funnel["results"] = [
            {"name": c.get("name"), "bortle": c["bortle_class"], "sqm": c.get("sqm"),
             "dist_mi": c["distance_miles"], "dir": c["direction"],
             "lat": round(c["lat"], 5), "lon": round(c["lon"], 5),
             "drive_min": c.get("drive_minutes")}
            for c in dark_clusters
        ]
        _funnel["domes"] = [
            {"name": d.get("name"), "bortle": d["bortle_class"],
             "dist_mi": d["distance_miles"], "dir": d["direction"],
             "lat": round(d["lat"], 5), "lon": round(d["lon"], 5)}
            for d in dome_clusters
        ]
        if best_available:
            _funnel["best"] = {
                "name": best_available.get("name"), "bortle": best_available["bortle_class"],
                "dist_mi": best_available["distance_miles"],
                "lat": round(best_available["lat"], 5), "lon": round(best_available["lon"], 5)}
        log.info("[funnel] %s", json.dumps(_funnel))

    prof.report()

    # --- CHANGED: has_dark_sky logic ---
    return {
        "origin_bortle":  origin_bortle,
        "origin_sqm":     origin_sqm,
        "radius_miles":   radius_miles,
        "results":        dark_clusters,
        "light_domes":    dome_clusters,
        "has_dark_sky":   origin_bortle <= 3 or any(c["bortle_class"] <= 3 for c in dark_clusters),
        "best_available": best_available,
    }
