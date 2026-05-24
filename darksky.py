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

import io
import logging
import math
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

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
        with urllib.request.urlopen(zip_url, timeout=60) as resp:
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


# ---------------------------------------------------------------------------
# Raster sampling
# ---------------------------------------------------------------------------

def _sample_tif(tif_path: Path, lat: float, lon: float) -> float | None:
    """
    Return the pixel value at (lat, lon) from a GeoTIFF.
    Handles EPSG:4326 and other CRS via rasterio warp.
    Returns None on error; 0.0 for nodata / below-detection pixels.
    """
    try:
        import rasterio
        from rasterio.warp import transform as warp_transform
    except ImportError:
        log.warning("rasterio not installed; skipping light pollution lookup")
        return None

    try:
        with rasterio.open(tif_path) as ds:
            if ds.crs and ds.crs.to_epsg() != 4326:
                xs, ys = warp_transform("EPSG:4326", ds.crs, [lon], [lat])
            else:
                xs, ys = [lon], [lat]

            value = float(list(ds.sample([(xs[0], ys[0])]))[0][0])

            if ds.nodata is not None and abs(value - ds.nodata) < 1.0:
                log.debug("Nodata pixel at (%.4f, %.4f) in %s", lat, lon, tif_path.name)
                return 0.0

            return max(value, 0.0)
    except Exception as e:
        log.warning("Raster lookup failed (%s): %s", tif_path.name, e)
        return None


def _viirs_radiance(lat: float, lon: float) -> float | None:
    """Return VIIRS 2025 radiance (nW/cm²/sr), downloading the TIF if needed."""
    _download_viirs()
    value = _sample_tif(_VIIRS_TIF, lat, lon)
    if value is not None:
        log.debug("VIIRS radiance at (%.4f, %.4f): %.3f nW/cm²/sr", lat, lon, value)
    return value


def _falchi_luminance(lat: float, lon: float) -> float | None:
    """Return Falchi 2016 artificial luminance (mcd/m²), downloading if needed."""
    _download_falchi()
    value = _sample_tif(_FALCHI_TIF, lat, lon)
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
    if sqm >= 21.99:
        return "0"
    for min_sqm, label in _LORENZ_ZONES:
        if sqm >= min_sqm:
            return label
    return "7b"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

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

    Returns None if rasterio is unavailable or both files cannot be read.
    """
    # --- Primary: VIIRS 2025 ---
    radiance = _viirs_radiance(lat, lon)
    if radiance is None:
        # rasterio missing or file error; try Falchi anyway
        log.debug("VIIRS unavailable, falling back to Falchi")
    elif radiance > 0:
        sqm = radiance_to_sqm(radiance)
        bortle_cls, bortle_d = sqm_to_bortle(sqm)
        log.debug("Using VIIRS 2025: radiance=%.3f  SQM=%.1f  Bortle=%d",
                  radiance, sqm, bortle_cls)
        return {
            "sqm":            sqm,
            "bortle_class":   bortle_cls,
            "bortle_desc":    bortle_d,
            "lp_zone":        sqm_to_zone(sqm),
            "below_detection": False,
            "source":         "VIIRS 2025",
        }
    else:
        log.debug("VIIRS below detection floor, falling back to Falchi")

    # --- Fallback: Falchi 2016 ---
    luminance = _falchi_luminance(lat, lon)
    if luminance is None:
        return None   # both sources failed

    if luminance == 0.0:
        return {
            "sqm":            None,
            "bortle_class":   None,
            "bortle_desc":    None,
            "lp_zone":        None,
            "below_detection": True,
            "source":         "Falchi 2016",
        }

    scaled = luminance * _FALCHI_SCALE
    sqm = luminance_to_sqm(scaled)
    bortle_cls, bortle_d = sqm_to_bortle(sqm)
    log.debug("Using Falchi 2016: luminance=%.4f  scaled=%.4f  SQM=%.1f  Bortle=%d",
              luminance, scaled, sqm, bortle_cls)
    return {
        "sqm":            sqm,
        "bortle_class":   bortle_cls,
        "bortle_desc":    bortle_d,
        "lp_zone":        sqm_to_zone(sqm),
        "below_detection": False,
        "source":         "Falchi 2016",
    }


def summary_line(lat: float, lon: float) -> str | None:
    """
    Return a one-line display string, e.g.:
      'SQM 19.2  ·  Zone 6a  ·  Bortle 6  (Bright suburban sky)  [VIIRS 2025]'
    Returns None if the lookup fails entirely.
    """
    info = lookup(lat, lon)
    if info is None:
        return None
    if info["below_detection"]:
        return "Light pollution data unavailable"
    return (f"SQM {info['sqm']}  ·  Zone {info['lp_zone']}"
            f"  ·  Bortle {info['bortle_class']}"
            f"  ({info['bortle_desc']})  [{info['source']}]")
