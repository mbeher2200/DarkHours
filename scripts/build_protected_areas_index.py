#!/usr/bin/env python3
"""One-time build script: download PADUS from the USGS ArcGIS REST API and
produce PyNightSkyPredictor/data/protected_areas.json.gz.

Run once, commit the output, re-run annually when PADUS updates:

    python scripts/build_protected_areas_index.py

Output format matches what _overpass_natural_areas_in_radius returns so that
_best_area_name_for_cluster works unchanged:
    [{"name": str, "priority": int, "minlat": f, "maxlat": f, "minlon": f, "maxlon": f}, ...]

Priority tiers:
    0 = wilderness / wilderness study area
    1 = national monument / national recreation area / national scenic area
    2 = national park / national forest / BLM / NWR / grassland
    3 = state park / state forest / other
"""

import gzip
import json
import sys
import time
from pathlib import Path

import requests

# PADUS 4.1 combined Management Areas layer (Fee + Designation merged).
# Public — no API key required.
_FEE_URL = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services"
    "/PADUS_Management_Areas/FeatureServer/0/query"
)

# Minimum size to include (acres). Filters out tiny urban parks and trail corridors.
_MIN_ACRES = 500  # ~0.78 sq mi; field name in PADUS 4.1 is GIS_AcreD (double)

# Des_Tp values to skip entirely (private, easements, tribal, trail corridors).
_SKIP_DES_TP = {
    "AGRE", "CONE", "FORE", "OTHE", "PAGR", "PCON", "PFOR", "PHCA",
    "POTH", "PPRK", "PRAN", "PREC", "RANE", "RECE", "UNKE",
    "TRIBL",               # Tribal lands — sovereign, don't label
    "NT", "WSR",           # Trail/river corridors — linear, not dark-sky sites
    "PROC",                # Proclamation boundary (planning overlay, not managed)
    "UNK", "LOTH", "FOTH", "SOTH",  # unknowns
}

# designation type code -> priority (lower = better label for dark-sky purposes)
_DES_PRIORITY = {
    # 0 = wilderness-grade (most specific, darkest intent)
    "WA":   0,  "WSA":  0,  "SW":   0,
    # 1 = national monuments / special designations
    "NM":   1,  "NRA":  1,  "NLS":  1,  "NCA":  1,
    "NSBV": 1,  "PUB":  1,  "SDA":  1,
    # 2 = national / major public lands
    "NP":   2,  "NF":   2,  "NG":   2,  "NWR":  2,
    "IRA":  2,  "REA":  2,  "RNA":  2,  "ACEC": 2,
    "MIL":  2,  "HCA":  2,  "RMA":  2,  "WPA":  2,
    # 3 = state / local public lands
    "SP":   3,  "SCA":  3,  "SREC": 3,  "SRMA": 3,
    "SHCA": 3,  "LP":   3,  "LCA":  3,  "LREC": 3,
    "REC":  3,  "LRMA": 3,  "MIT":  3,  "ND":   3,
    "LHCA": 3,
}
_DEFAULT_PRIORITY = 3


def _priority(des_tp: str) -> int:
    return _DES_PRIORITY.get((des_tp or "").strip().upper(), _DEFAULT_PRIORITY)


def _bbox(geom: dict) -> tuple | None:
    """Return (minlon, minlat, maxlon, maxlat) from a GeoJSON geometry."""
    coords_flat = []
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])

    if gtype == "Point":
        return None
    elif gtype == "Polygon":
        coords_flat = raw[0] if raw else []
    elif gtype == "MultiPolygon":
        for poly in raw:
            if poly:
                coords_flat.extend(poly[0])
    else:
        return None

    if not coords_flat:
        return None

    lons = [c[0] for c in coords_flat]
    lats = [c[1] for c in coords_flat]
    return min(lons), min(lats), max(lons), max(lats)


def _fetch_features_by_ids(url: str, oids: list[int]) -> list[dict]:
    """Fetch a batch of features by object ID. Returns raw GeoJSON feature list."""
    params = {
        "objectIds":         ",".join(str(i) for i in oids),
        "outFields":         "Unit_Nm,Des_Tp,Mang_Name,GIS_AcreD",
        "returnGeometry":    "true",
        "outSR":             "4326",
        "geometryPrecision": "4",
        "f":                 "geojson",
    }
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, timeout=120)
            resp.raise_for_status()
            return resp.json().get("features", [])
        except (requests.RequestException, ValueError) as e:
            wait = 5 * (attempt + 1)
            print(f"\n  retry {attempt+1}/3: {e} — waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    return []


def _fetch_layer(url: str, label: str) -> list[dict]:
    """Fetch all matching features via OID-based chunking. Returns list of area dicts.

    resultOffset-based pagination hits a service-level cap (~10k records).
    Fetching all OIDs first (no geometry = no cap) then requesting features in
    small batches bypasses this limit entirely.
    """
    print(f"Fetching {label}...")

    # Step 1: get all matching object IDs (no geometry, no pagination cap)
    id_resp = None
    for attempt in range(4):
        try:
            r = requests.get(url, params={
                "where":         f"GIS_AcreD>{_MIN_ACRES}",
                "returnIdsOnly": "true",
                "f":             "json",
            }, timeout=60)
            r.raise_for_status()
            id_resp = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            time.sleep(5 * (attempt + 1))
    if id_resp is None:
        print("ERROR: could not fetch object IDs", file=sys.stderr)
        return []

    all_oids = id_resp.get("objectIds") or []
    print(f"  {len(all_oids):,} matching object IDs")

    # Step 2: fetch features in chunks of 100
    chunk = 100
    areas: list[dict] = []
    fetched = 0

    for i in range(0, len(all_oids), chunk):
        batch = all_oids[i: i + chunk]
        features = _fetch_features_by_ids(url, batch)
        fetched += len(features)

        for feat in features:
            props = feat.get("properties") or feat.get("attributes") or {}
            des_tp = (props.get("Des_Tp") or "").strip().upper()
            if des_tp in _SKIP_DES_TP:
                continue
            name = (props.get("Unit_Nm") or "").strip()
            if not name:
                continue
            geom = feat.get("geometry")
            if not geom:
                continue
            bb = _bbox(geom)
            if bb is None:
                continue
            minlon, minlat, maxlon, maxlat = bb
            if minlat == maxlat or minlon == maxlon:
                continue  # degenerate bbox

            areas.append({
                "name":     name,
                "priority": _priority(des_tp),
                "minlat":   round(minlat, 4),
                "maxlat":   round(maxlat, 4),
                "minlon":   round(minlon, 4),
                "maxlon":   round(maxlon, 4),
            })

        print(f"  {fetched:,}/{len(all_oids):,} fetched, {len(areas):,} kept...", end="\r")
        time.sleep(0.2)

    print(f"  {len(areas):,} named areas from {label}                    ")
    return areas


def _dedup(areas: list[dict]) -> list[dict]:
    """Remove exact-duplicate (name, bbox) entries."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for a in areas:
        key = (a["name"], a["minlat"], a["maxlat"], a["minlon"], a["maxlon"])
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def main() -> None:
    out_path = Path(__file__).parent.parent / "PyNightSkyPredictor" / "data" / "protected_areas.json.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_areas = _dedup(_fetch_layer(_FEE_URL, "PADUS 4.1 Management Areas"))
    all_areas.sort(key=lambda a: (a["priority"], a["name"]))

    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(all_areas, f, separators=(",", ":"))

    size_kb = out_path.stat().st_size / 1024
    print(f"\nWrote {len(all_areas):,} areas -> {out_path}  ({size_kb:.0f} KB compressed)")


if __name__ == "__main__":
    main()
