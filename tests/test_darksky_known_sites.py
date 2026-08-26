"""
Known-site classification basket, run against the real rasters.

Marked `raster`: needs the on-disk VIIRS + Falchi grids (~15 GB), so it is opt-in and
never runs in CI. Gated on an explicit env var rather than on the grids merely being
present, so a default run stays machine-independent. Run with:

    PYNIGHTSKY_RASTER_TESTS=1 python -m pytest -q -m raster

WHAT THIS LAYER DOES AND DOES NOT COVER
---------------------------------------
Tolerance is ±1 class, matching the accuracy `radiance_to_sqm` documents (±0.5–1.0
mag/arcsec², one Bortle class). At that tolerance this file cannot detect a
single-class threshold error — a consensus-2 site reading Bortle 1 passes. Threshold
and quantisation behaviour is covered exactly, offline, in
`tests/test_darksky_classification.py`. What this file catches is directional drift
in the raster pipeline: a change of source data, scale factor, or sampling that moves
the whole basket.

BASKET SELECTION
----------------
Sites carry a DarkSky International certification or a widely published
classification; nothing is included on an unsourced estimate. Coordinates are pinned
because at a threshold the class swings a full step on pixel choice — Big Cypress
reads Bortle 2 at its visitor center and 3 at Oasis, 12 miles away.

Twelve of the 25 sites sit above Bortle 1, so a bias toward darker classifications has
somewhere to show up. A basket of only pristine parks would not: Bortle 1 is the floor
of the scale, and a darker-biased error is invisible against a floor.

Town coordinates read the immediate light source rather than a metro average — that is
what a point sample of VIIRS radiance is, and the consensus values below are chosen for
the pinned coordinate, not the wider place.
"""
import os
from pathlib import Path

import pytest

from darkhours import darksky as ds

pytestmark = pytest.mark.raster


# (name, lat, lon, consensus Bortle class, basis)
BASKET = [
    # ── Certified dark-sky sites ────────────────────────────────────────────
    ("Big Bend NP, TX",          29.2705, -103.3018, 1, "DarkSky Park, Gold Tier"),
    ("Cosmic Campground, NM",    33.4794, -108.9214, 1, "DarkSky Sanctuary"),
    ("Great Basin NP, NV",       38.9833, -114.3000, 1, "DarkSky Park"),
    ("Death Valley NP, CA",      37.0100, -117.4500, 1, "DarkSky Park, Gold Tier"),
    ("Natural Bridges NM, UT",   37.6083, -110.0137, 2, "first DarkSky Park (2007)"),
    ("Great Sand Dunes NP, CO",  37.7325, -105.5120, 2, "DarkSky Park"),
    ("Bryce Canyon NP, UT",      37.5930, -112.1871, 2, "DarkSky Park"),
    ("Chaco Culture NHP, NM",    36.0606, -107.9559, 2, "DarkSky Park"),
    ("Cherry Springs SP, PA",    41.6626,  -77.8261, 2, "DarkSky Park, Gold Tier"),
    ("Zion NP, UT",              37.2982, -113.0263, 2, "DarkSky Park"),
    ("Mont-Megantic, QC",        45.4550,  -71.1520, 2, "first DarkSky Reserve (2007)"),
    ("Joshua Tree NP, CA",       33.7400, -115.8150, 3, "DarkSky Park"),
    ("Headlands, MI",            45.7808,  -84.7736, 3, "DarkSky Park"),
    ("Acadia NP, ME",            44.3290,  -68.1830, 3, "published classification"),
    ("Cape Cod Ntl Seashore, MA", 41.9200, -69.9800, 3, "published classification"),
    ("Shenandoah NP, VA",        38.5220,  -78.4370, 4, "DarkSky Park"),
    ("Sterling Forest SP, NY",   41.1900,  -74.2500, 3, "darksky._dark_threshold docstring"),
    ("Torrey, UT",               38.2990, -111.4190, 4, "Capitol Reef DarkSky gateway town"),

    # ── Populated places: headroom at the bright end ────────────────────────
    ("Flagstaff, AZ",            35.1983, -111.6513, 6, "first DarkSky City (2001)"),
    ("Sedona, AZ",               34.8697, -111.7610, 7, "DarkSky Community"),
    ("Boulder, CO",              40.0150, -105.2705, 8, "published classification"),
    ("Tucson, AZ",               32.2226, -110.9747, 8, "published classification"),
    ("Phoenix, AZ",              33.4484, -112.0740, 9, "metro core"),
    ("Denver, CO",               39.7392, -104.9903, 9, "metro core"),
    ("Times Square, NY",         40.7580,  -73.9855, 9, "metro core"),
]

TOLERANCE = 1


def _grids_available() -> bool:
    for stem in (ds._VIIRS_GRID, ds._FALCHI_GRID):
        if not (Path(f"{stem}.bin").exists() and Path(f"{stem}.json").exists()):
            return False
    return True


@pytest.fixture(scope="module", autouse=True)
def require_local_grids():
    if os.environ.get("PYNIGHTSKY_RASTER_TESTS") != "1":
        pytest.skip("set PYNIGHTSKY_RASTER_TESTS=1 to run the known-site basket")
    if os.environ.get("PYNIGHTSKY_BACKEND", "local") != "local":
        pytest.skip("known-site basket runs against the local grids")
    if not _grids_available():
        pytest.skip(f"raster grids not built under {ds._GRID_DIR}")


@pytest.fixture(scope="module")
def classified(require_local_grids):
    """Classify the whole basket once, bypassing both cache layers."""
    real_get, real_set = ds.cache.get, ds.cache.set
    ds.cache.get = lambda k: None
    ds.cache.set = lambda k, v, ttl_seconds=None: True
    try:
        out = {}
        for name, lat, lon, consensus, basis in BASKET:
            ds._bortle_mem_cache.clear()
            result = ds.lookup(lat, lon)
            assert result is not None, f"{name}: raster read failed"
            out[name] = (result, consensus, basis)
        return out
    finally:
        ds.cache.get, ds.cache.set = real_get, real_set
        ds._bortle_mem_cache.clear()


@pytest.mark.parametrize("name,lat,lon,consensus,basis", BASKET)
def test_site_within_tolerance(classified, name, lat, lon, consensus, basis):
    result, _, _ = classified[name]
    got = result["bortle_class"]
    assert abs(got - consensus) <= TOLERANCE, (
        f"{name} ({basis}): consensus Bortle {consensus}, model {got} "
        f"(SQM {result['sqm']}, {result['source']})"
    )


def test_cherry_springs_matches_consensus_exactly(classified):
    """The reported case. Consensus is Bortle 2 and the tolerance above would accept
    1, so this pins it exactly."""
    result, consensus, _ = classified["Cherry Springs SP, PA"]
    assert result["bortle_class"] == consensus == 2
    assert result["lp_zone"] == "1b"


def test_basket_spans_the_scale(classified):
    """A basket clustered at the Bortle 1 floor cannot show a darker-biased shift."""
    classes = {r["bortle_class"] for r, _, _ in classified.values()}
    assert min(classes) == 1
    assert max(classes) >= 8
    above_floor = sum(1 for r, _, _ in classified.values() if r["bortle_class"] > 1)
    assert above_floor >= 12, "too few sites with headroom toward darker"


def test_no_site_is_off_by_more_than_one_in_either_direction(classified):
    """Reported as one list so a systematic shift reads as a pattern, not 25 failures."""
    offenders = [
        f"{name}: consensus {consensus}, model {r['bortle_class']} "
        f"(SQM {r['sqm']}, {r['source']})"
        for name, (r, consensus, _) in classified.items()
        if abs(r["bortle_class"] - consensus) > TOLERANCE
    ]
    assert not offenders, "sites outside ±1 class:\n  " + "\n  ".join(offenders)
