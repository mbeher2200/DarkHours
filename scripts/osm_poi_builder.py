"""
Build a compact H3 index of *routable* OpenStreetMap POIs for the DarkHours "nearby"
search. The output (cache/osm_pois.npz, a few MB) lets find_nearby surface dark sky that
sits on an established, reachable place — a parking lot, viewpoint, campsite, or rest area —
instead of a raw, often-unreachable wilderness pixel. Each POI carries its true coordinate
(so AWS routing snaps to a real road) and an offline name (so no reverse-geocode is needed).

POI types extracted (node OR area), name tag required. See POI_TYPE_LABELS for the full
set — parking/viewpoint/camp_site/rest_area/caravan_site/picnic_site/ranger_station/
observatory/attraction/information/tourism/pier/lighthouse/tower/summer_camp/sports_centre/
stadium/track/playground/pitch/firepit/dog_park/disc_golf_course/bandstand/beach_resort/
amusement_arcade/historic — across the amenity/tourism/highway/man_made/leisure/historic keys.

Two build-time filters keep the index lean despite the broad tag set:
  - junk-name / closed (`_is_usable_name`, `_is_open`) drop unusable or decommissioned POIs;
  - a DARK PREFILTER drops any POI in a Bortle > 4 area (it could never be a result), using
    the local light-pollution grids — so bright urban POIs cost nothing.

--- GETTING THE SOURCE DATA ---

A Geofabrik US extract (.osm.pbf). Either drop one in the gitignored Temp/ directory, or
let scripts/update_pois.sh download the latest. Used at BUILD TIME ONLY.

--- DEPENDENCIES ---

    pip install -r requirements-build.txt   # provides osmium (pyosmium) + h3

--- USAGE ---

    python scripts/osm_poi_builder.py Temp/us-260608.osm.pbf
    # output → cache/osm_pois.npz  (committed via a .gitignore exception; images COPY it)

--- LICENSE / ATTRIBUTION ---

Source data © OpenStreetMap contributors, licensed ODbL. The derived index in
cache/osm_pois.npz is a derivative database under ODbL — keep the attribution
"© OpenStreetMap contributors" visible in the app. See docs/OSM_POI_INDEX.md.

--- RUNTIME LOOKUP ---

Loaded once per process via darksky._load_poi_h3_index; intersected with the dark-sky
mask in _extract_dark_sky_candidates (bbox prefilter → raster-pixel test). See
docs/OSM_POI_INDEX.md.
"""

import argparse
import os
import sys
from pathlib import Path

import h3
import numpy as np
import osmium

RESOLUTION = 7   # ~5 km hex — matches the PAD-US index; used for build-time dedup
OUT_DIR    = "cache"
OUT_FILE   = os.path.join(OUT_DIR, "osm_pois.npz")

# Keys whose objects we inspect (C++ pre-filter; exact-key match excludes lifecycle
# prefixes like disused:amenity).
_KEYFILTER_KEYS = ("amenity", "tourism", "highway", "man_made", "leisure", "historic")

# Build-time dark prefilter: find_nearby's dark threshold is always Bortle <= 3, so a POI
# in a brighter area can NEVER surface. Drop those at build time (with a +1 margin) so the
# committed index holds only usable, dark-located POIs regardless of how broad the tag set
# is. Needs the local light-pollution grids; if they're unavailable we keep everything.
DARK_KEEP_BORTLE = 4

# poi_type code = index into this tuple (stored as uint8). Existing indices are a stored
# contract (the .npz, darksky._POI_TYPE_LABELS, and the frontend all mirror this order);
# changing it requires rebuilding the .npz and re-syncing those three.
POI_TYPE_LABELS = (
    "parking", "viewpoint", "camp_site", "rest_area",                       # 0-3
    "caravan_site", "picnic_site", "ranger_station", "observatory", "attraction",  # 4-8
    "information", "tourism", "pier", "lighthouse", "tower",                # 9-13
    "summer_camp", "firepit", "beach_resort", "historic",                  # 14-17
)

# Dedup priority when several POIs share one H3 cell: keep the better astro destination.
# Lower rank wins; the generic catch-alls and bare parking sit last.
_POI_PRIORITY = {
    "observatory": 0, "viewpoint": 1, "lighthouse": 2, "tower": 3, "camp_site": 4,
    "caravan_site": 5, "summer_camp": 6, "picnic_site": 7, "firepit": 8, "rest_area": 9,
    "ranger_station": 10, "beach_resort": 11, "pier": 12, "information": 13, "historic": 14,
    "attraction": 15, "tourism": 16, "parking": 17,
}

# historic=* values folded into a single "historic" type (historic=tower → "tower" above).
_HISTORIC_VALUES = {
    "yes", "temple", "tank", "stone", "ruins", "monument",
    "memorial", "house", "district", "castle", "building",
}


def _classify(tags) -> "str | None":
    """Return the POI type for an object's tags, or None if it is not one we index.

    Checked high-signal first so an object carrying several tags (e.g. a viewpoint also
    tagged tourism=attraction) is classified as the more specific destination; generic
    catch-alls (attraction, tourism=yes, historic) are checked last. man_made=tower is
    kept ONLY for tower:type=observation — broadcast/comms masts aren't destinations.
    """
    mm = tags.get("man_made")
    if mm == "observatory":
        return "observatory"
    if mm == "lighthouse":
        return "lighthouse"
    if mm == "pier":
        return "pier"
    if (mm == "tower" and tags.get("tower:type") == "observation") \
            or tags.get("historic") == "tower":
        return "tower"

    t = tags.get("tourism")
    if t == "viewpoint":
        return "viewpoint"
    if t == "camp_site":
        return "camp_site"
    if t == "caravan_site":
        return "caravan_site"
    if t == "picnic_site":
        return "picnic_site"
    if t == "information":
        return "information"

    if tags.get("highway") == "rest_area":
        return "rest_area"

    a = tags.get("amenity")
    if a == "ranger_station":
        return "ranger_station"

    le = tags.get("leisure")
    if le in ("summer_camp", "firepit", "beach_resort"):
        return le

    if a == "parking":
        return "parking"

    # Generic catch-alls last.
    if t == "attraction":
        return "attraction"
    if t == "yes":
        return "tourism"
    if tags.get("historic") in _HISTORIC_VALUES:
        return "historic"
    return None


def _is_usable_name(name: str) -> bool:
    """Reject junk names that aren't a useful display label (the OSM '9' / '(Closed)' cases)."""
    nm = name.strip()
    if len(nm) < 2 or nm.isdigit():
        return False
    low = nm.lower()
    return not ("unknown" in low or "unnamed" in low or "closed" in low)


def _is_open(tags) -> bool:
    """Drop access-restricted or decommissioned POIs (lifecycle-prefixed tags like
    `disused:amenity=…` are already excluded by the KeyFilter, which matches exact keys)."""
    if tags.get("access") in ("no", "private"):
        return False
    return not (tags.get("disused") or tags.get("abandoned"))


_lookup_fn = None   # cached darksky.lookup (None until first use / if rasters absent)
_dark_lookup_ok = True


def _bortle_at(lat: float, lon: float) -> "int | None":
    """Bortle class at (lat, lon) via the engine's own lookup, or None if the local
    light-pollution grids aren't available (then the caller keeps the POI)."""
    global _lookup_fn, _dark_lookup_ok
    if not _dark_lookup_ok:
        return None
    if _lookup_fn is None:
        try:
            # Running as a script puts scripts/ on sys.path, not the repo root — add it so
            # `PyNightSkyPredictor` imports. lookup() reads the local grids relative to cwd.
            _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _repo not in sys.path:
                sys.path.insert(0, _repo)
            from PyNightSkyPredictor import darksky as _ds
            _lookup_fn = _ds.lookup
            if _ds.lookup(0.0, 0.0) is None and _ds.lookup(39.0, -105.0) is None:
                # Grids genuinely unavailable (both ocean and a known-land point return None).
                _dark_lookup_ok = False
                return None
        except Exception:
            _dark_lookup_ok = False
            return None
    try:
        info = _lookup_fn(lat, lon)
    except Exception:
        return None
    return None if info is None else info.get("bortle_class")


def _area_center(area) -> "tuple[float, float] | None":
    """Representative center of an area = mean of its outer-ring vertices.

    A simple vertex mean is enough for a routable anchor (we snap to a road anyway) and
    avoids a shapely dependency. Returns None if no usable ring geometry is present.
    """
    sum_lat = sum_lon = 0.0
    n = 0
    for ring in area.outer_rings():
        for node_ref in ring:
            try:
                sum_lat += node_ref.lat
                sum_lon += node_ref.lon
                n += 1
            except osmium.InvalidLocationError:
                continue
    if n == 0:
        return None
    return sum_lat / n, sum_lon / n


def encode_poi_npz(out_path, cells, lats, lons, names, poi_types) -> dict:
    """Write the columnar .npz. Inputs are parallel sequences (one entry per cell):
    `cells` uint64-able, `lats`/`lons` floats, `names` strings, `poi_types` uint8 codes.
    Sorts ascending by cell (the reader binary-search-guards) and dictionary-encodes names
    (mirrors scripts/convert_padus_parquet_to_npz.encode_padus_npz). Returns a stats dict.
    """
    cells = np.asarray(cells, dtype=np.uint64)
    lats = np.asarray(lats, dtype=np.float32)
    lons = np.asarray(lons, dtype=np.float32)
    poi_types = np.asarray(poi_types, dtype=np.uint8)
    names = ["" if n is None else str(n) for n in names]

    # Sort all columns ascending by cell so the runtime loader can np.searchsorted.
    if cells.size and not bool(np.all(cells[:-1] <= cells[1:])):
        order = np.argsort(cells, kind="stable")
        cells = cells[order]
        lats = lats[order]
        lons = lons[order]
        poi_types = poi_types[order]
        names = [names[i] for i in order]

    # Dictionary-encode names: sorted unique + a uint32 code per cell; reader splits blob.
    uniq = sorted(set(names))
    code_of = {n: i for i, n in enumerate(uniq)}
    name_codes = np.fromiter((code_of[n] for n in names), dtype=np.uint32, count=len(names))
    names_blob = np.frombuffer("\x00".join(uniq).encode("utf-8"), dtype=np.uint8)

    np.savez_compressed(
        out_path, cells=cells, lats=lats, lons=lons,
        name_codes=name_codes, names_blob=names_blob, poi_types=poi_types,
    )
    return {"cells": int(cells.size), "unique_names": len(uniq)}


def _filter_to_small_pbf(pbf_path: str, small_path: str) -> int:
    """Pass 1: stream the (multi-GB) source, keep only named POIs of our types, and write
    them — plus the child nodes their geometry needs (ForwardReferenceWriter's default
    back-references) — to a tiny intermediate .pbf. No node-location cache here, so this
    stays memory-bounded on the full US extract. Returns the count of POI objects written.
    """
    n_written = 0
    with osmium.ForwardReferenceWriter(
        small_path, ref_src=pbf_path, overwrite=True, forward_relation_depth=0,
    ) as writer:
        fp = osmium.FileProcessor(pbf_path).with_filter(
            osmium.filter.KeyFilter(*_KEYFILTER_KEYS))
        for obj in fp:
            t = obj.tags
            name = t.get("name")
            if (_classify(t) is None or not name
                    or not _is_usable_name(name) or not _is_open(t)):
                continue
            writer.add(obj)
            n_written += 1
            if n_written % 100_000 == 0:
                print(f"    … {n_written:,} POIs filtered", flush=True)
    return n_written


def build_index(pbf_path: str) -> None:
    small_path = os.path.join("Temp", "osm_pois_filtered.osm.pbf")
    os.makedirs("Temp", exist_ok=True)
    print(f"Pass 1: filtering POIs from {pbf_path} → {small_path} …", flush=True)
    n_written = _filter_to_small_pbf(pbf_path, small_path)
    print(f"  Wrote {n_written:,} POI objects (+ referenced nodes) to the intermediate pbf",
          flush=True)

    # Pass 2: the small file fits in memory, so assemble areas + resolve node locations here.
    print("Pass 2: assembling geometry and indexing to H3 …", flush=True)
    fp = (
        osmium.FileProcessor(small_path)
        .with_locations()
        .with_areas()
        .with_filter(osmium.filter.KeyFilter(*_KEYFILTER_KEYS))
    )

    # cell → (priority, lat, lon, name, type_code). One POI per H3 cell (best priority).
    best: dict[int, tuple] = {}
    n_seen = 0
    n_named = 0
    n_bright = 0
    for obj in fp:
        # Skip the raw ways that with_areas() re-emits as areas (avoid double counting);
        # standalone tagged nodes and assembled areas carry the tags we need.
        if obj.is_way():
            continue
        ptype = _classify(obj.tags)
        if ptype is None:
            continue
        n_seen += 1
        name = obj.tags.get("name")
        if not name or not _is_usable_name(name) or not _is_open(obj.tags):
            continue
        if obj.is_node():
            lat, lon = obj.lat, obj.lon
        elif obj.is_area():
            center = _area_center(obj)
            if center is None:
                continue
            lat, lon = center
        else:
            continue

        # Dark prefilter: a POI in a Bortle > DARK_KEEP_BORTLE area can never be a result.
        bortle = _bortle_at(lat, lon)
        if bortle is not None and bortle > DARK_KEEP_BORTLE:
            n_bright += 1
            continue
        n_named += 1

        cell = h3.str_to_int(h3.latlng_to_cell(lat, lon, RESOLUTION))
        rank = _POI_PRIORITY[ptype]
        prev = best.get(cell)
        if prev is None or rank < prev[0]:
            best[cell] = (rank, lat, lon, name, POI_TYPE_LABELS.index(ptype))

    try:
        os.remove(small_path)   # the intermediate is disposable
    except OSError:
        pass

    _dark_note = ("(dark grids unavailable — no dark prefilter applied)"
                  if not _dark_lookup_ok else f"dropped {n_bright:,} too-bright")
    print(f"  Matched {n_seen:,} POI-typed objects; kept {n_named:,} dark+named "
          f"[{_dark_note}]; {len(best):,} unique H3 cells after dedup")
    if not best:
        print("  No POIs found — aborting (is this a valid .osm.pbf with the expected tags?)")
        sys.exit(1)

    cells, lats, lons, names, types = [], [], [], [], []
    for cell, (_rank, lat, lon, name, type_code) in best.items():
        cells.append(cell)
        lats.append(lat)
        lons.append(lon)
        names.append(name)
        types.append(type_code)

    os.makedirs(OUT_DIR, exist_ok=True)
    stats = encode_poi_npz(OUT_FILE, cells, lats, lons, names, types)
    size_mb = os.path.getsize(OUT_FILE) / (1024 * 1024)

    # Per-type tally for a sanity check.
    type_counts = {}
    for _c, (_r, _la, _lo, _nm, tc) in best.items():
        lbl = POI_TYPE_LABELS[tc]
        type_counts[lbl] = type_counts.get(lbl, 0) + 1
    print(f"\n  Saved → {OUT_FILE}  ({size_mb:.1f} MB)  "
          f"{stats['cells']:,} cells, {stats['unique_names']:,} unique names")
    print(f"  By type: {type_counts}")


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", type=Path, help="input Geofabrik .osm.pbf (US extract)")
    args = ap.parse_args(argv)
    if not args.pbf.exists():
        ap.error(f"input file not found: {args.pbf}")
    build_index(str(args.pbf))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
