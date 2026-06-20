"""Directional horizon light-dome scoring (the "Walker kernel").

Given a candidate observing site, evaluate the light-dome threat from each of the
8 cardinal directions (N, NE, E, SE, S, SW, W, NW) using a distance-weighted 2D
spatial kernel over upward radiance. The result tells an observer which way to point
a wide-angle camera ("darkest horizon is NE") and which directions are spoiled
("major light dome to the SW").

Physics
-------
Each raster pixel of upward radiance (VIIRS, nW/cm2/sr) is treated as a point source
whose contribution to horizon sky-glow decays with distance per **Walker's Law**:

    intensity ∝ d ** (-2.5)

This is steeper than the d**-2 of pure geometric (inverse-square) spreading. Walker
(1977) measured a city's artificial zenith sky brightness falling roughly as d**-2.5
with distance; Garstang's atmospheric-scattering modelling explains why — the scattered
light traverses more of the low-altitude aerosol/extinction layer (the lower ~2-3 km of
the troposphere) on its longer slant path to a distant observer, so the glow is
attenuated faster than geometry alone predicts. Summing radiance * d**-2.5 over a 45°
azimuth sector therefore approximates the total artificial sky-glow that sector adds to
the observer's horizon.

A related consequence (not needed for the score, but it explains the bias toward near
sources): a dome's apparent height is roughly theta ≈ arctan(h / d), with h ≈ 3 km the
aerosol scattering scale height. Close sources both get the steep d**-2.5 boost *and*
throw a tall dome high into the frame; distant megacities stay low on the horizon.

Design notes (why this is not a single static pixel kernel)
-----------------------------------------------------------
The light-pollution rasters are EPSG:4326 (geographic degrees), not equal-area. A fixed
pixel offset spans ~69 mi per degree of latitude but only 69*cos(lat) mi per degree of
longitude, so a kernel precomputed once in *pixel* space would be latitude-distorted
(a ~0.7:1 ground ellipse at 45°N) and the distortion changes with the site's latitude.
To stay physically correct while keeping queries fast, the static angular-offset grid is
precomputed once at init, and the distance/azimuth/area arrays are built (and memoized)
per latitude band, applying the cos(lat) longitude correction. Distances are in ground
miles so scores are comparable across latitudes and raster resolutions.

Scoring caveat
--------------
The relative ranking of the 8 scores and the ``darkest_direction`` are trustworthy
immediately — they do not depend on any absolute constant. The Major/Minor severity
thresholds have been calibrated empirically against a reference basket of known dark-sky
and near-metro sites (see ``MAJOR_DOME_THRESHOLD`` and ``scripts/calibrate_light_dome.py``);
they are tied to the default 150-mile geometry, so re-run that calibration if the radius
changes.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

try:
    import h3 as _h3_lib
    _HAS_H3 = True
except ImportError:  # h3 is a runtime dep, but degrade gracefully if absent
    _h3_lib = None
    _HAS_H3 = False

# --- configuration ---------------------------------------------------------

DEFAULT_RADIUS_MILES = 150.0         # radius from centre; 300-mile box. Matches find_nearby's
                                     # dome_search_radius so the two tools agree — a Bortle-9
                                     # metro casts a real (low) dome 90-120+ mi away.
WALKER_EXPONENT = 2.5                # d**-2.5 sky-glow decay (Walker/Garstang); see module docstring
MILES_PER_DEG_LAT = 69.0             # ~constant; a degree of latitude is ~69 statute miles
VIIRS_RES_DEG = 1.0 / 240.0          # ~0.004167° (~400 m) — VIIRS Black Marble native grid
H_AEROSOL_KM = 3.0                   # aerosol scattering scale height; a dome's apparent height
                                     # is theta ≈ arctan(h / d), so distant domes sit low
KM_PER_MILE = 1.609344

DIRS_8: tuple[str, ...] = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Severity thresholds on the per-direction score, which has units
# (nW/cm2/sr) * mi**-2.5 * mi**2 = (nW/cm2/sr) * mi**-0.5 — i.e. an area-weighted glow
# index. CALIBRATED empirically at the default 150-mile radius WITH soft binning, against a
# reference basket (see scripts/calibrate_light_dome.py). At 150 mi darkness is a
# *continuum* — almost everywhere has some distant-metro glow — and soft binning spreads a
# straddling source across two directions, so peak per-direction scores for premier dark
# parks (~0.27, e.g. Cherry Springs) and a real-but-low distant metro dome (~0.28, Phoenix
# seen 100 mi away) are nearly equal in MAGNITUDE. They are separated instead by
# DIRECTIONALITY: PROMINENCE_RATIO requires a dome to stand well above the darkest
# direction, so a sharp dome (Phoenix ~34x the darkest) flags while diffuse skyglow
# (Cherry Springs ~3.5x) stays clean. MAJOR stays robust (Joshua Tree ~2 minor vs
# Denver/Pine Barrens ~4+). Scores scale with radius_miles and with the binning scheme,
# so re-run the calibration if either changes.
MINOR_DOME_THRESHOLD = 0.25          # a real, low directional dome (gated by prominence below)
MAJOR_DOME_THRESHOLD = 3.0           # a sky-degrading dome (close/large metro within range)
PROMINENCE_RATIO = 4.0               # a dome must be >= this * the darkest direction to count —
                                     # separates a sharp directional dome from diffuse skyglow


class LightDomeAnalyzer:
    """Score the light-dome threat in each of 8 cardinal directions around a site.

    The expensive, latitude-independent geometry (the per-pixel offset grid) is built
    once at construction. The distance-weight kernel and soft direction weights depend on
    latitude (via the cos(lat) longitude correction) and are built lazily and memoized
    per ``lat_band_deg`` band, so repeated queries near the same latitude are cheap.

    Besides the 8 directional scores, :meth:`analyze_horizons_detailed` also returns a
    glow-weighted mean distance and the resulting dome apparent height per direction, and
    the module function :func:`glow_toward` samples the glow at any (azimuth, altitude) —
    the hook for scoring how a light dome affects a specific target in the sky.

    Parameters
    ----------
    radius_miles:
        Radius of the analysis box in ground miles (default 150 → 300-mile box),
        matching find_nearby's dome_search_radius so distant metro domes are captured.
    resolution_deg:
        Pixel size in degrees of the radiance window passed to
        :meth:`analyze_destination_horizons` (default = VIIRS native ~0.004167°).
    lat_band_deg:
        Granularity of the per-latitude kernel cache. 1° bands keep the cos(lat)
        approximation error well under a percent while sharing kernels across queries.
    """

    def __init__(
        self,
        radius_miles: float = DEFAULT_RADIUS_MILES,
        resolution_deg: float = VIIRS_RES_DEG,
        lat_band_deg: float = 1.0,
    ) -> None:
        if radius_miles <= 0 or resolution_deg <= 0 or lat_band_deg <= 0:
            raise ValueError("radius_miles, resolution_deg and lat_band_deg must be positive")

        self.radius_miles = float(radius_miles)
        self.resolution_deg = float(resolution_deg)
        self.lat_band_deg = float(lat_band_deg)

        # Odd pixel count so there is a true centre pixel. ``half`` pixels reach
        # ``radius_miles`` in the latitude (constant-scale) direction.
        half = math.ceil(radius_miles / (resolution_deg * MILES_PER_DEG_LAT))
        self._half = half
        self._n = 2 * half + 1

        # Static, latitude-independent integer offsets from the centre pixel.
        # row increases downward (south), col increases rightward (east).
        offs = np.arange(-half, half + 1, dtype=np.float64)
        self._row_off, self._col_off = np.meshgrid(offs, offs, indexing="ij")

        # band-key -> (weights, dist_mi, lo_idx, hi_idx, frac, cell_area_sq_mi)
        self._cache: dict[int, tuple] = {}

    @property
    def kernel_shape(self) -> tuple[int, int]:
        """(N, N) — the exact window shape :meth:`analyze_destination_horizons` expects."""
        return (self._n, self._n)

    def _kernel_for_latitude(self, lat: float) -> tuple:
        """Build (or fetch from cache) the Walker weight kernel + soft direction weights.

        Returns ``(weights, dist_mi, lo_idx, hi_idx, frac, cell_area_sq_mi)``:

        - ``weights`` — N×N ``d**-WALKER_EXPONENT`` (centre pixel = 0, no singularity).
        - ``dist_mi`` — N×N ground distance of each pixel from centre, in miles (used for
          the glow-weighted mean-distance / dome-height per direction).
        - ``lo_idx``, ``hi_idx``, ``frac`` — *soft binning*. Each pixel's azimuth falls
          between two adjacent cardinal directions ``lo_idx`` and ``hi_idx``; its weight is
          shared ``(1 - frac)`` to ``lo_idx`` and ``frac`` to ``hi_idx`` (a tent / linear
          interpolation onto the 8 nodes). This is a partition of unity — total weight per
          pixel is conserved — so a source between two compass points (e.g. Phoenix at SSW)
          is shared proportionally rather than snapped across a hard 22.5° edge, and the 8
          scores vary smoothly with source bearing instead of flipping at sector borders.
        - ``cell_area_sq_mi`` — ground area of one pixel; makes the sum an area-weighted
          (resolution-independent) integral.
        """
        band = int(round(lat / self.lat_band_deg))
        cached = self._cache.get(band)
        if cached is not None:
            return cached

        band_center = band * self.lat_band_deg
        cos_lat = max(math.cos(math.radians(band_center)), 1e-6)  # guard the poles

        # Ground offsets in miles. Negate the row term: row increases southward, so a
        # pixel one row *above* centre (row_off = -1) lies to the NORTH (+miles north).
        deg_mi = self.resolution_deg * MILES_PER_DEG_LAT
        d_north_mi = -self._row_off * deg_mi
        d_east_mi = self._col_off * deg_mi * cos_lat

        dist_mi = np.hypot(d_north_mi, d_east_mi)

        # Walker d**-2.5 decay; centre pixel (d == 0) contributes nothing.
        with np.errstate(divide="ignore"):
            weights = (dist_mi ** (-WALKER_EXPONENT)).astype(np.float32)
        weights[self._half, self._half] = 0.0

        # Compass azimuth from centre: 0° = N, 90° = E. atan2(east, north) matches the
        # bearing convention and format_ctx.cardinal(). Tent-interpolate onto 8 nodes.
        az = np.degrees(np.arctan2(d_east_mi, d_north_mi)) % 360.0
        p = az / 45.0
        lo = np.floor(p).astype(np.int64)
        frac = (p - lo).astype(np.float32)
        lo_idx = (lo % 8).astype(np.int16)
        hi_idx = ((lo + 1) % 8).astype(np.int16)

        cell_area_sq_mi = deg_mi * (deg_mi * cos_lat)

        result = (weights, dist_mi.astype(np.float32), lo_idx, hi_idx, frac, cell_area_sq_mi)
        self._cache[band] = result
        return result

    def _compute(self, destination_lat: float, window_array: np.ndarray):
        """Core vectorised pass → (scores, mean_distance_mi, dome_height_deg) arrays of len 8.

        ``scores[k] = (Σ radiance·walker·soft_k) · cell_area`` (the area-weighted glow
        index). ``mean_distance_mi[k]`` is the same soft-weighted sum, weighted again by
        distance and normalised — the glow-weighted mean distance of the light feeding that
        direction (NaN where there is no glow). ``dome_height_deg[k] = arctan(h / d)`` from
        that distance — the apparent height the dome rises off the horizon.
        """
        window = np.asarray(window_array, dtype=np.float64)
        if window.shape != self.kernel_shape:
            raise ValueError(
                f"window_array shape {window.shape} != expected kernel shape "
                f"{self.kernel_shape}; request out_shape={self.kernel_shape} when reading "
                "the window (a boundary slice may have come back truncated)"
            )

        weights, dist_mi, lo_idx, hi_idx, frac, cell_area = self._kernel_for_latitude(destination_lat)

        wflat = (window * weights).ravel()           # radiance · walker weight, per pixel
        lo, hi, f = lo_idx.ravel(), hi_idx.ravel(), frac.ravel()
        dist = dist_mi.ravel()
        w_lo, w_hi = wflat * (1.0 - f), wflat * f

        raw = (np.bincount(lo, weights=w_lo, minlength=8)
               + np.bincount(hi, weights=w_hi, minlength=8))
        dnum = (np.bincount(lo, weights=w_lo * dist, minlength=8)
                + np.bincount(hi, weights=w_hi * dist, minlength=8))

        scores = raw * cell_area
        with np.errstate(divide="ignore", invalid="ignore"):
            mean_distance_mi = np.where(raw > 0.0, dnum / raw, np.nan)
            d_km = mean_distance_mi * KM_PER_MILE
            dome_height_deg = np.where(
                raw > 0.0, np.degrees(np.arctan2(H_AEROSOL_KM, d_km)), 0.0)
        return scores, mean_distance_mi, dome_height_deg

    def analyze_destination_horizons(
        self,
        destination_lat: float,
        destination_lon: float,  # noqa: ARG002 — symmetry of intent; lon does not affect the kernel
        window_array: np.ndarray,
    ) -> dict[str, float]:
        """Return the 8 directional light-dome scores for a centred radiance window.

        ``window_array`` must be the N×N (== :attr:`kernel_shape`) sub-array of upward
        radiance centred on the destination, oriented row 0 = north (max_lat),
        col 0 = west (min_lon) — exactly what ``raster_source.read_window`` returns.

        Each score is ``Σ(radiance · walker_weight · soft_weight) · cell_area`` over the
        window — vectorised, with soft (tent) binning across the two nearest cardinal
        directions and no Python loop over pixels. For the per-direction mean distance and
        dome apparent height, use :meth:`analyze_horizons_detailed`.
        """
        scores, _, _ = self._compute(destination_lat, window_array)
        return {DIRS_8[i]: float(scores[i]) for i in range(8)}

    def analyze_horizons_detailed(
        self,
        destination_lat: float,
        destination_lon: float,  # noqa: ARG002 — symmetry of intent; lon does not affect the kernel
        window_array: np.ndarray,
    ) -> dict[str, dict]:
        """Like :meth:`analyze_destination_horizons` but rich per-direction info::

            {"S": {"score": 0.33, "mean_distance_mi": 95.2, "dome_height_deg": 1.1}, ...}

        ``mean_distance_mi`` is the glow-weighted mean distance of the light feeding that
        direction (``None`` if there is no glow); ``dome_height_deg`` is ``arctan(h / d)``
        from that distance — how high the dome rises off the horizon. Feed this dict to
        :func:`glow_toward` to sample the glow at a target's (azimuth, altitude).
        """
        scores, mean_dist, dome_h = self._compute(destination_lat, window_array)
        out: dict[str, dict] = {}
        for i, d in enumerate(DIRS_8):
            md = float(mean_dist[i])
            out[d] = {
                "score": float(scores[i]),
                "mean_distance_mi": None if math.isnan(md) else md,
                "dome_height_deg": float(dome_h[i]),
            }
        return out

    def _read_window(self, lat: float, lon: float, backend, dataset: str) -> np.ndarray:
        """Fetch the centred N×N radiance window through the ports raster seam.

        ``out_shape`` guarantees an exact N×N grid regardless of the raster's native
        resolution (the resample assumes ~``resolution_deg`` pixel spacing — sub-pixel
        alignment only). VIIRS radiance (nW/cm2/sr) is the metric; Falchi is *luminance*
        (mcd/m2, different units) so it is intentionally not blended — pass ``"viirs"``.
        """
        if backend is None:
            from . import ports
            backend = ports.get_backend()

        cos_lat = max(math.cos(math.radians(lat)), 1e-6)
        half_lat_deg = self._half * self.resolution_deg
        half_lon_deg = half_lat_deg / cos_lat

        window = backend.raster_source.read_window(
            dataset,
            lat - half_lat_deg,
            lat + half_lat_deg,
            lon - half_lon_deg,
            lon + half_lon_deg,
            out_shape=self.kernel_shape,
        )
        if window is None:
            raise RuntimeError(f"raster_source returned no {dataset} window for ({lat}, {lon})")
        return window

    def analyze(self, lat: float, lon: float, *, backend=None, dataset: str = "viirs") -> dict[str, float]:
        """Convenience wrapper: fetch the radiance window via the ports seam, then score.

        Returns the 8 directional scores. See :meth:`analyze_detailed` for distances and
        dome heights.
        """
        return self.analyze_destination_horizons(lat, lon, self._read_window(lat, lon, backend, dataset))

    def analyze_detailed(self, lat: float, lon: float, *, backend=None, dataset: str = "viirs") -> dict[str, dict]:
        """Convenience wrapper for :meth:`analyze_horizons_detailed` (fetches the window)."""
        return self.analyze_horizons_detailed(lat, lon, self._read_window(lat, lon, backend, dataset))


def summarize_horizons(
    data: dict,
    *,
    minor_threshold: float = MINOR_DOME_THRESHOLD,
    major_threshold: float = MAJOR_DOME_THRESHOLD,
    prominence_ratio: float = PROMINENCE_RATIO,
) -> dict:
    """Normalise the 8 directional results into a user-facing summary.

    Accepts either the plain ``{dir: score}`` dict from
    :meth:`LightDomeAnalyzer.analyze_destination_horizons` *or* the rich
    ``{dir: {"score", "mean_distance_mi", "dome_height_deg"}}`` dict from
    :meth:`~LightDomeAnalyzer.analyze_horizons_detailed`. When given the rich dict, each
    flagged dome also carries ``mean_distance_mi`` and ``dome_height_deg``.

    Returns::

        {
          "sky_state": "dark" | "domed" | "urban",
          "scores": {dir: rounded_score, ...},
          "darkest_direction": "NE",
          "darkest_score": 0.007,
          "domes": [ {"direction": "S", "severity": "minor", "score": 0.33,
                      "label": "Minor light dome to the S",
                      "mean_distance_mi": 95.2, "dome_height_deg": 1.1}, ... ],  # worst-first
        }

    A direction is flagged as a dome only if it is *both* absolutely significant
    (score >= ``minor_threshold``) **and** directionally prominent
    (score >= ``prominence_ratio`` * the darkest direction's score). Requiring both
    avoids two failure modes: flagging a trivially-brighter direction at a uniformly dark
    site (pure-relative false positive), and failing to distinguish a real directional
    dome from uniform city glow (pure-absolute blind spot).

    ``sky_state`` is a site-level classification the UI should branch on. It keys on the
    *darkest* direction's absolute score — "how good is your best horizon?" — which the
    prominence-gated dome list alone cannot answer (a fully-surrounded site flags *few*
    domes because nothing stands out, which would otherwise look deceptively clean):

    - ``"dark"`` — darkest direction is genuinely dark and no domes flagged;
      ``darkest_direction`` is a real best-view horizon.
    - ``"domed"`` — directional domes exist but a darker side remains; point away from them.
    - ``"bright"`` — no single dome stands out, yet every direction carries real glow
      (``minor_threshold <= darkest < major_threshold``): a uniformly washed suburban sky,
      not a dark site and not a directional problem.
    - ``"urban"`` — even the darkest direction is itself a major dome
      (``darkest >= major_threshold``); there is no dark horizon. ``domes`` /
      ``darkest_direction`` are still returned but are not a meaningful "where to point"
      signal — the honest message is "washed out in all directions".

    ``darkest_score`` is always included so the UI can caveat the darkest direction (e.g.
    "least-bad: NW, still bright") in any state.
    """
    if not data:
        raise ValueError("data must be a non-empty dict of direction -> score (or detail)")

    detailed = all(isinstance(v, dict) for v in data.values())
    scores = {d: (v["score"] if detailed else v) for d, v in data.items()}

    darkest_direction = min(scores, key=scores.__getitem__)
    darkest_score = scores[darkest_direction]

    domes: list[dict] = []
    for direction, score in scores.items():
        significant = score >= minor_threshold
        prominent = score >= prominence_ratio * darkest_score
        if not (significant and prominent):
            continue
        severity = "major" if score >= major_threshold else "minor"
        entry = {
            "direction": direction,
            "severity": severity,
            "score": round(score, 3),
            "label": f"{severity.capitalize()} light dome to the {direction}",
        }
        if detailed:
            md = data[direction].get("mean_distance_mi")
            entry["mean_distance_mi"] = None if md is None else round(md, 1)
            entry["dome_height_deg"] = round(data[direction].get("dome_height_deg", 0.0), 1)
        domes.append(entry)

    domes.sort(key=lambda d: d["score"], reverse=True)

    # Site-level state, keyed on the darkest direction's absolute score — "is even the best
    # direction bad?" — which the prominence-gated dome list cannot answer.
    if darkest_score >= major_threshold:
        sky_state = "urban"          # no dark horizon: even the best direction is a major dome
    elif domes:
        sky_state = "domed"          # a directional dome, but a darker side remains
    elif darkest_score >= minor_threshold:
        sky_state = "bright"         # uniform glow everywhere, no single standout dome
    else:
        sky_state = "dark"           # genuinely dark

    return {
        "sky_state": sky_state,
        "scores": {d: round(s, 3) for d, s in scores.items()},
        "dome_heights": {
            d: round(data[d].get("dome_height_deg", 0.0) if detailed else 0.0, 2)
            for d in scores
        },
        "darkest_direction": darkest_direction,
        "darkest_score": round(darkest_score, 3),
        "domes": domes,
    }


def glow_toward(detailed: dict[str, dict], azimuth_deg: float, altitude_deg: float) -> float:
    """Light-dome glow at an arbitrary sky position — the hook for target scoring.

    Given the rich per-direction output of
    :meth:`LightDomeAnalyzer.analyze_horizons_detailed` and a sky position
    (``azimuth_deg`` 0=N/90=E, ``altitude_deg`` 0=horizon/90=zenith), returns the
    interpolated glow index at that point. Tent-interpolates the horizon score and the
    (distance-derived) dome apparent height across the two nearest cardinal directions,
    then applies an altitude falloff anchored on that dome height:

        glow(az, alt) = horizon_score(az) / (1 + (alt / theta(az))**2)

    so glow is full at the horizon, half at the dome's apparent height ``theta``, and
    tends to 0 well above it. A distant low dome (small ``theta``) barely reaches a target
    more than a few degrees up; a near dome reaches higher. The dome height is data-driven;
    the falloff *shape* is a heuristic.

    Intended use: pass a target's azimuth/altitude (e.g. the Milky Way core or a nebula at
    a given time) to estimate how much that direction's light dome washes it out.
    """
    p = (azimuth_deg % 360.0) / 45.0
    lo = int(math.floor(p)) % 8
    hi = (lo + 1) % 8
    f = p - math.floor(p)

    score = detailed[DIRS_8[lo]]["score"] * (1.0 - f) + detailed[DIRS_8[hi]]["score"] * f
    theta = detailed[DIRS_8[lo]]["dome_height_deg"] * (1.0 - f) + detailed[DIRS_8[hi]]["dome_height_deg"] * f

    alt = max(0.0, altitude_deg)
    if theta <= 0.0:
        return score if alt == 0.0 else 0.0
    return score / (1.0 + (alt / theta) ** 2)


# ===========================================================================
# Precomputed H3 index — DarkHours light-dome lookup for the initial page load
# ===========================================================================
# Light dome is a pure function of static VIIRS radiance + location, so it is
# precomputed once into an H3 index (scripts/build_lightdome_index.py) and served
# as an O(log n) lookup on the page-load path — no 150-mile raster read. Plumbing
# mirrors darksky._PadusIndex / _load_padus_h3_index / _padus_h3_lookup.

LIGHTDOME_H3_RESOLUTION = 6          # ~36 km^2/cell — CONUS index granularity
_LIGHTDOME_H3_FILENAME = "lightdome_h3.npz"
_LIGHTDOME_UNAVAILABLE = object()    # sentinel: tried to load, missing/unreadable
_lightdome_index_cache = None        # None = not yet attempted


def _lightdome_h3_path() -> "Path | None":
    """Resolve the index .npz path: env override → repo cache → Lambda image path."""
    env_override = os.environ.get("PYNIGHTSKY_LIGHTDOME_H3_PATH")
    if env_override:
        p = Path(env_override)
        return p if p.exists() else None
    for candidate in (
        Path(__file__).parent.parent / "cache" / _LIGHTDOME_H3_FILENAME,
        Path("/app/cache") / _LIGHTDOME_H3_FILENAME,
    ):
        if candidate.exists():
            return candidate
    return None


class LightDomeIndex:
    """Columnar light-dome H3 index: a sorted uint64 cell array plus parallel raw
    per-direction value arrays (scores, dome heights, mean distances). Lookups
    binary-search the cell array; the summary is built at lookup time so a threshold
    recalibration needs no rebuild. Read from a compressed .npz with numpy only."""

    __slots__ = ("cells", "scores", "dome_heights", "mean_distances")

    def __init__(self, cells, scores, dome_heights, mean_distances):
        self.cells = cells                    # np.ndarray[uint64], ascending (N,)
        self.scores = scores                  # np.ndarray[float] (N, 8), order = DIRS_8
        self.dome_heights = dome_heights      # np.ndarray[float] (N, 8), degrees
        self.mean_distances = mean_distances  # np.ndarray[float] (N, 8); -1.0 = no glow


def load_lightdome_index() -> "LightDomeIndex | None":
    """Lazy-load the light-dome H3 index once per process; cache the result.

    Returns None (and caches that failure) if h3/numpy is unavailable, the .npz is
    missing, or the read fails — callers degrade gracefully (no light-dome panel).
    """
    global _lightdome_index_cache
    if _lightdome_index_cache is _LIGHTDOME_UNAVAILABLE:
        return None
    if _lightdome_index_cache is not None:
        return _lightdome_index_cache  # type: ignore[return-value]

    if not _HAS_H3:
        _lightdome_index_cache = _LIGHTDOME_UNAVAILABLE
        return None

    path = _lightdome_h3_path()
    if path is None:
        _lightdome_index_cache = _LIGHTDOME_UNAVAILABLE
        return None

    try:
        with np.load(path) as npz:
            cells = npz["cells"].astype(np.uint64, copy=False)
            scores = npz["scores"].astype(np.float32, copy=False)
            dome_heights = npz["dome_heights"].astype(np.float32, copy=False)
            mean_distances = npz["mean_distances"].astype(np.float32, copy=False)
        # The .npz is written sorted by cell, so np.searchsorted is valid. Guard cheaply.
        if cells.size and not bool(np.all(cells[:-1] <= cells[1:])):
            order = np.argsort(cells, kind="stable")
            cells, scores, dome_heights, mean_distances = (
                cells[order], scores[order], dome_heights[order], mean_distances[order])
        _lightdome_index_cache = LightDomeIndex(cells, scores, dome_heights, mean_distances)
    except Exception:
        _lightdome_index_cache = _LIGHTDOME_UNAVAILABLE
        return None

    return _lightdome_index_cache  # type: ignore[return-value]


def lightdome_lookup(lat: float, lon: float, index: "LightDomeIndex | None" = None) -> "dict | None":
    """Precomputed light-dome summary for (lat, lon), or None if outside coverage.

    O(log n) binary search over the H3 index — safe on the initial page-load path (no
    raster read). Returns the same shape as :func:`summarize_horizons`. The stored
    ``-1.0`` mean-distance sentinel maps back to ``None``.
    """
    if index is None:
        index = load_lightdome_index()
    if index is None:
        return None

    cell = _h3_lib.str_to_int(_h3_lib.latlng_to_cell(lat, lon, LIGHTDOME_H3_RESOLUTION))
    cells = index.cells
    i = int(np.searchsorted(cells, np.uint64(cell)))
    if i >= cells.size or cells[i] != cell:
        return None

    sc, dh, md = index.scores[i], index.dome_heights[i], index.mean_distances[i]
    detailed = {
        DIRS_8[k]: {
            "score": float(sc[k]),
            "dome_height_deg": float(dh[k]),
            "mean_distance_mi": None if md[k] < 0.0 else float(md[k]),
        }
        for k in range(8)
    }
    return summarize_horizons(detailed)
