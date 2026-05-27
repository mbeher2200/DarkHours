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
import json
import logging
import math
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import cache

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


# ---------------------------------------------------------------------------
# Nearby dark-sky search
# ---------------------------------------------------------------------------

_DIRS_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

_SAMPLE_RINGS_MILES = [5, 10, 15, 20, 30, 45, 60, 80, 100, 120]
_SAMPLE_BEARINGS    = [i * 22.5 for i in range(16)]

_OVERPASS_URL    = "https://overpass-api.de/api/interpreter"
_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/reverse"
_GEO_CACHE_TTL   = 90 * 24 * 3600   # 90 days
_OVERPASS_SLEEP  = 3.0               # minimum seconds between Overpass requests
_NOMINATIM_SLEEP = 1.1               # minimum seconds between Nominatim requests

# Module-level rate-limit trackers (updated only on actual network calls)
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


def _grid_sample_points(lat: float, lon: float, radius_miles: float) -> list:
    """
    Generate sample coordinates at concentric rings of bearings within radius_miles.
    Returns list of (lat, lon) tuples.
    """
    rings = [d for d in _SAMPLE_RINGS_MILES if d <= radius_miles]
    if not rings:
        rings = [int(radius_miles)]
    elif rings[-1] < radius_miles:
        rings.append(int(radius_miles))

    lat_per_mi = 1.0 / 69.0
    # Guard against cos(90°) = 0 at poles
    lon_per_mi = 1.0 / max(69.0 * math.cos(math.radians(lat)), 0.01)

    points = []
    for dist_mi in rings:
        for bearing_deg in _SAMPLE_BEARINGS:
            brad = math.radians(bearing_deg)
            plat = lat + math.cos(brad) * dist_mi * lat_per_mi
            plon = lon + math.sin(brad) * dist_mi * lon_per_mi
            points.append((plat, plon))
    return points


def _bulk_bortle_lookup(coords: list) -> list:
    """
    Sample Bortle class for every (lat, lon) in coords in a single TIF open per source.
    Returns list of {'bortle_class': int, 'sqm': float} or None, parallel to coords.
    """
    try:
        import rasterio
        from rasterio.warp import transform as warp_transform
    except ImportError:
        log.warning("rasterio not installed; cannot sample dark-sky grid")
        return [None] * len(coords)

    _download_viirs(show_progress=False)
    _download_falchi(show_progress=False)

    results       = [None] * len(coords)
    falchi_needed = []

    # VIIRS pass — covers all measurably-lit points
    try:
        with rasterio.open(_VIIRS_TIF) as ds:
            lons = [c[1] for c in coords]
            lats = [c[0] for c in coords]
            if ds.crs and ds.crs.to_epsg() != 4326:
                xs, ys = warp_transform("EPSG:4326", ds.crs, lons, lats)
            else:
                xs, ys = lons, lats

            nodata  = ds.nodata
            sampled = list(ds.sample(zip(xs, ys)))
            for i, pixel in enumerate(sampled):
                val = max(float(pixel[0]), 0.0)
                if nodata is not None and abs(float(pixel[0]) - nodata) < 1.0:
                    val = 0.0
                if val > 0:
                    sqm = radiance_to_sqm(val)
                    cls, _ = sqm_to_bortle(sqm)
                    results[i] = {"bortle_class": cls, "sqm": sqm}
                else:
                    falchi_needed.append(i)
    except Exception as e:
        log.warning("VIIRS bulk lookup failed: %s", e)
        falchi_needed = list(range(len(coords)))

    # Falchi pass — fills in genuinely dark (VIIRS-zero) points
    if falchi_needed:
        try:
            fc = [coords[i] for i in falchi_needed]
            with rasterio.open(_FALCHI_TIF) as ds:
                lons = [c[1] for c in fc]
                lats = [c[0] for c in fc]
                if ds.crs and ds.crs.to_epsg() != 4326:
                    xs, ys = warp_transform("EPSG:4326", ds.crs, lons, lats)
                else:
                    xs, ys = lons, lats

                nodata  = ds.nodata
                sampled = list(ds.sample(zip(xs, ys)))
                for idx, pixel in zip(falchi_needed, sampled):
                    val = max(float(pixel[0]), 0.0)
                    if nodata is not None and abs(float(pixel[0]) - nodata) < 1.0:
                        val = 0.0
                    if val == 0.0:
                        # Below Falchi detection — classify as pristine (Bortle 1)
                        results[idx] = {"bortle_class": 1, "sqm": _SQM_NATURAL}
                    else:
                        scaled = val * _FALCHI_SCALE
                        sqm    = luminance_to_sqm(scaled)
                        cls, _ = sqm_to_bortle(sqm)
                        results[idx] = {"bortle_class": cls, "sqm": sqm}
        except Exception as e:
            log.warning("Falchi bulk lookup failed: %s", e)

    return results


def _cluster_points(points: list, merge_miles: float = 8.0) -> list:
    """
    Greedy de-duplication: drop points within merge_miles of a darker/nearer one.
    Input points must have 'lat', 'lon', 'bortle_class', 'distance_miles'.
    Returns a reduced list of cluster representatives.
    """
    sorted_pts = sorted(points, key=lambda p: (p["bortle_class"], p["distance_miles"]))
    used       = set()
    clusters   = []
    for i, pt in enumerate(sorted_pts):
        if i in used:
            continue
        clusters.append(pt)
        for j, other in enumerate(sorted_pts):
            if j != i and j not in used:
                if _haversine_miles(pt["lat"], pt["lon"],
                                    other["lat"], other["lon"]) <= merge_miles:
                    used.add(j)
        used.add(i)
    return clusters


def _overpass_area_name(lat: float, lon: float) -> str | None:
    """
    Find the most prominent named natural/protected area containing (lat, lon)
    using the Overpass API.  Results cached for 90 days.
    Returns None if no matching area found or request fails.
    """
    cache_key = f"overpass_area|{lat:.2f}|{lon:.2f}"
    cached    = cache.get(cache_key)
    if cached is not None:
        return cached or None   # "" stored when no area found

    query = (
        f"[out:json][timeout:20];\n"
        f"is_in({lat:.4f},{lon:.4f})->.a;\n"
        f"(\n"
        f'  relation(pivot.a)["name"]["boundary"="national_park"];\n'
        f'  relation(pivot.a)["name"]["boundary"="protected_area"];\n'
        f'  relation(pivot.a)["name"]["boundary"="national_forest"];\n'
        f'  relation(pivot.a)["name"]["leisure"="nature_reserve"];\n'
        f'  relation(pivot.a)["name"]["landuse"="forest"];\n'
        f'  way(pivot.a)["name"]["boundary"="national_park"];\n'
        f'  way(pivot.a)["name"]["leisure"="nature_reserve"];\n'
        f");\n"
        f"out tags;"
    )
    # Rate-limit: enforce minimum gap between actual network calls
    global _last_overpass_call
    wait = _OVERPASS_SLEEP - (time.time() - _last_overpass_call)
    if wait > 0:
        time.sleep(wait)

    params = urllib.parse.urlencode({"data": query})
    url    = f"{_OVERPASS_URL}?{params}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PyNightSkyPredictor/1.0 (light-pollution-research)"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read())
        _last_overpass_call = time.time()
    except Exception as e:
        _last_overpass_call = time.time()
        log.debug("Overpass area lookup failed for (%.4f, %.4f): %s", lat, lon, e)
        return None

    elements = data.get("elements", [])
    best_name, best_priority = None, 999

    for el in elements:
        tags    = el.get("tags", {})
        name    = tags.get("name")
        if not name:
            continue
        boundary = tags.get("boundary", "")
        landuse  = tags.get("landuse", "")
        leisure  = tags.get("leisure", "")

        if "wilderness" in name.lower():
            priority = _AREA_PRIORITY["wilderness"]
        elif boundary == "national_park":
            priority = _AREA_PRIORITY["national_park"]
        elif boundary == "protected_area":
            priority = _AREA_PRIORITY["protected_area"]
        elif boundary == "national_forest":
            priority = _AREA_PRIORITY["national_forest"]
        elif leisure == "nature_reserve":
            priority = _AREA_PRIORITY["nature_reserve"]
        elif landuse == "forest":
            priority = _AREA_PRIORITY["forest"]
        else:
            priority = 10

        if priority < best_priority:
            best_priority = priority
            best_name     = name

    cache.set(cache_key, best_name or "", ttl_seconds=_GEO_CACHE_TTL)
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

    # Rate-limit: enforce minimum gap between actual network calls
    global _last_nominatim_call
    wait = _NOMINATIM_SLEEP - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)

    params = urllib.parse.urlencode({
        "lat": f"{lat:.4f}", "lon": f"{lon:.4f}",
        "format": "json", "zoom": "10", "addressdetails": "1",
    })
    url = f"{_NOMINATIM_URL}?{params}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PyNightSkyPredictor/1.0 (light-pollution-research)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        _last_nominatim_call = time.time()
    except Exception as e:
        _last_nominatim_call = time.time()
        log.debug("Nominatim lookup failed for (%.4f, %.4f): %s", lat, lon, e)
        return None

    address    = data.get("address", {})
    name       = address.get("city") or address.get("town") or address.get("village")
    state_code = address.get("ISO3166-2-lvl4", "")   # e.g. "US-AZ"
    state_abbr = state_code.split("-")[-1] if "-" in state_code else ""
    result     = f"{name}, {state_abbr}" if (name and state_abbr) else name or ""

    cache.set(cache_key, result, ttl_seconds=_GEO_CACHE_TTL)
    return result or None


def find_darker_nearby(lat: float, lon: float, radius_miles: int = 60) -> dict | None:
    """
    Search for areas with significantly darker skies within radius_miles of (lat, lon).

    Samples Bortle class on a ring grid, clusters nearby dark spots, then names
    each cluster via Overpass (natural/protected areas) or Nominatim (settlements).

    Returns dict with keys:
      origin_bortle   int
      origin_sqm      float | None
      radius_miles    int
      results         list[dict]  — dark clusters meeting the threshold
      light_domes     list[dict]  — bright city glows (only when origin <= Bortle 5)
      has_dark_sky    bool
      best_available  dict | None — nearest/darkest point when threshold not met

    Each result/dome/best_available entry has:
      name, bortle_class, sqm, distance_miles, direction

    Returns None if light pollution data is unavailable (rasterio not installed).
    """
    origin_info = lookup(lat, lon)
    if origin_info is None:
        return None

    origin_bortle = origin_info.get("bortle_class") or 5
    origin_sqm    = origin_info.get("sqm")

    # Already optimal — nothing to search for
    if origin_bortle <= 1:
        return {
            "origin_bortle": origin_bortle,
            "origin_sqm":    origin_sqm,
            "radius_miles":  radius_miles,
            "results":       [],
            "light_domes":   [],
            "has_dark_sky":  True,
            "best_available": None,
        }

    # Dark threshold: need to be meaningfully darker than origin
    if origin_bortle <= 2:
        dark_threshold = 1   # Bortle 2 → require Bortle 1
    else:
        dark_threshold = min(origin_bortle - 2, 5)

    # Sample the grid
    sample_coords = _grid_sample_points(lat, lon, radius_miles)
    bulk          = _bulk_bortle_lookup(sample_coords)

    all_darker      = []   # any point darker than origin (for best_available fallback)
    dark_candidates = []   # meets dark_threshold
    dome_candidates = []   # light dome candidates (Bortle 7+, only for dark origins)

    for (plat, plon), info in zip(sample_coords, bulk):
        if info is None:
            continue
        bortle = info["bortle_class"]
        sqm    = info["sqm"]
        dist   = _haversine_miles(lat, lon, plat, plon)
        direc  = _bearing_label(lat, lon, plat, plon)
        pt = {
            "lat": plat, "lon": plon,
            "bortle_class": bortle, "sqm": sqm,
            "distance_miles": round(dist, 1),
            "direction": direc,
            "name": None,
        }
        if bortle < origin_bortle:
            all_darker.append(pt)
        if bortle <= dark_threshold:
            dark_candidates.append(pt)
        # Only report light domes when the observer is at a dark site
        if origin_bortle <= 5 and bortle >= 7 and dist >= 15:
            dome_candidates.append(pt)

    _MAX_RESULTS = 10   # max dark-sky areas to name and display
    _MAX_DOMES   = 4    # max light domes to name and display

    # Sort and cap before naming — every returned entry will be named
    dark_clusters = _cluster_points(dark_candidates)          if dark_candidates else []
    dome_clusters = _cluster_points(dome_candidates, merge_miles=20) if dome_candidates else []

    # Nearest-first within Bortle tiers — ensures closest dark sky is always shown
    dark_clusters = sorted(dark_clusters, key=lambda p: (p["distance_miles"], p["bortle_class"]))[:_MAX_RESULTS]
    dome_clusters = sorted(dome_clusters, key=lambda p: (p["distance_miles"], p["bortle_class"]))[:_MAX_DOMES]

    # Best-available fallback when threshold not met
    best_available = None
    if not dark_clusters and all_darker:
        best = sorted(all_darker, key=lambda p: (p["bortle_class"], p["distance_miles"]))[0]
        name = _overpass_area_name(best["lat"], best["lon"])
        if not name:
            name = _nominatim_settlement(best["lat"], best["lon"])
        best["name"] = name or f"{best['lat']:.2f}°, {best['lon']:.2f}°"
        best_available = best

    # Name dark clusters — Overpass first (natural areas), Nominatim as fallback
    for cluster in dark_clusters:
        name = _overpass_area_name(cluster["lat"], cluster["lon"])
        if not name:
            name = _nominatim_settlement(cluster["lat"], cluster["lon"])
        cluster["name"] = name or f"{cluster['lat']:.2f}°, {cluster['lon']:.2f}°"

    # Name light domes — Nominatim first (city names), Overpass as fallback
    for dome in dome_clusters:
        name = _nominatim_settlement(dome["lat"], dome["lon"])
        if not name:
            name = _overpass_area_name(dome["lat"], dome["lon"])
        dome["name"] = name or f"{dome['lat']:.2f}°, {dome['lon']:.2f}°"

    return {
        "origin_bortle":  origin_bortle,
        "origin_sqm":     origin_sqm,
        "radius_miles":   radius_miles,
        "results":        dark_clusters,
        "light_domes":    dome_clusters,
        "has_dark_sky":   any(c["bortle_class"] <= 3 for c in dark_clusters),
        "best_available": best_available,
    }
