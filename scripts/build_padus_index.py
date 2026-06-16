"""
Build a compact H3 spatial index of public lands from the USGS PAD-US 4.1 Geodatabase.

The output (cache/darkhours_padus_h3.parquet, ~3.5 MB) is used by the DarkHours Lambda
as a fast pre-filter before calling Overpass: if a GPS coordinate falls inside a known
public land cell the park name is returned directly; if it lands on a blacklisted cell
(military, tribal, private, no-access) the candidate is eliminated without an API call.

--- GETTING THE SOURCE DATA ---

1. Download the PAD-US 4.1 Combined Feature Class Geodatabase from USGS ScienceBase:
   https://www.sciencebase.gov/catalog/item/652d4fc5d34e44db0e2ee45e
   File: PADUS4_1Geodatabase.zip  (~700 MB)

2. Unzip into the project Temp/ directory so the structure is:
   Temp/
   └── PADUS4_1Geodatabase/
       └── PADUS4_1Geodatabase.gdb/   ← the actual FileGDB

3. The Temp/ folder is gitignored — do not commit the raw geodatabase.

--- DEPENDENCIES ---

    pip install -r requirements-build.txt
    # or individually: geopandas pyarrow fiona h3

--- USAGE ---

    python scripts/build_padus_index.py

Output: cache/darkhours_padus_h3.parquet — committed via a .gitignore exception
(the Docker images copy it). h3_cell is stored as sorted uint64 so the runtime loader
binary-searches it instead of building a ~1.4M-entry dict; see docs/PADUS_INDEX.md.

--- RUNTIME LOOKUP ---

    Loaded once per process and queried via darksky._load_padus_h3_index /
    _padus_h3_lookup (columnar np.searchsorted). See docs/PADUS_INDEX.md.
"""

import os
import sys

import geopandas as gpd
import h3
import pandas as pd
from shapely.geometry import mapping, MultiPolygon, Polygon

GDB_PATH   = os.path.join("Temp", "PADUS4_1Geodatabase", "PADUS4_1Geodatabase.gdb")
LAYER      = "PADUS4_1Fee"
OUT_DIR    = "cache"
OUT_FILE   = os.path.join(OUT_DIR, "darkhours_padus_h3.parquet")
RESOLUTION = 7   # ~5 km hex — fits comfortably in a Lambda Layer


_BLACKLISTED_MANAGERS = {
    "DOD",   # Dept. of Defense
    "DOE",   # Dept. of Energy
    "BIA",   # Bureau of Indian Affairs
    "BOP",   # Bureau of Prisons
    "ARS",   # Agricultural Research Service
    "NASA",  # NASA
    "USCG",  # Coast Guard
    "NGO",   # Non-Governmental Org (land trusts — often gated/private)
    "NRCS",  # USDA farm conservation — private agricultural land
    "UNK",   # Unknown Manager
    "UNKL",  # Unknown Local Manager
}

_BLACKLISTED_DES_TP = {
    "Conservation Easement",
    "MIL",    # Military
    "TRIBL",  # Tribal lands
    "CONE",   # Conservation Easement (PADUS code form)
}


def _blacklisted(row) -> bool:
    return (
        row.get("Pub_Access") == "XA"
        or row.get("Mang_Name") in _BLACKLISTED_MANAGERS
        or row.get("Mang_Type") in ("TRIB", "PVT")
        or row.get("Des_Tp") in _BLACKLISTED_DES_TP
    )


def _polyfill(geom) -> set[str]:
    """Return H3 cells covering a shapely geometry at RESOLUTION."""
    cells: set[str] = set()

    if geom is None or geom.is_empty:
        return cells

    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]

    for poly in polys:
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        poly_cells: set[str] = set()
        try:
            # h3 4.x: geo_to_cells takes a GeoJSON geometry dict (lng, lat order)
            poly_cells = set(h3.geo_to_cells(mapping(poly), RESOLUTION))
        except Exception:
            pass

        if not poly_cells:
            # Polygon smaller than one hex — anchor to centroid
            c = poly.centroid
            poly_cells = {h3.latlng_to_cell(c.y, c.x, RESOLUTION)}

        cells.update(poly_cells)

    return cells


def build_index() -> None:
    print(f"Reading layer '{LAYER}' from {GDB_PATH} …")
    gdf = gpd.read_file(GDB_PATH, layer=LAYER)
    total = len(gdf)
    print(f"  Loaded {total:,} features")

    # --- Flag blacklisted features (keep all rows) ---
    gdf["is_blacklisted"] = gdf.apply(_blacklisted, axis=1)
    n_blacklisted = int(gdf["is_blacklisted"].sum())
    print(f"  Flagged {n_blacklisted:,} blacklisted features, {total - n_blacklisted:,} viable")

    # --- Reproject to WGS84 (required for H3) ---
    print("  Reprojecting to EPSG:4326 …")
    gdf = gdf.to_crs(epsg=4326)

    # --- H3 rasterize ---
    print(f"  Rasterizing to H3 resolution {RESOLUTION} (this takes a few minutes) …")
    records: list[dict] = []
    for _, row in gdf.iterrows():
        cells = _polyfill(row.geometry)
        bl = bool(row["is_blacklisted"])
        nm = row.get("Unit_Nm") or ""
        mn = row.get("Mang_Name") or ""
        for cell in cells:
            records.append({"h3_cell": cell, "Unit_Nm": nm, "Mang_Name": mn, "is_blacklisted": bl})

    df = pd.DataFrame(records, columns=["h3_cell", "Unit_Nm", "Mang_Name", "is_blacklisted"])
    print(f"  Exploded to {len(df):,} cell rows before deduplication")

    # --- Deduplicate: blacklisted wins if a cell has conflicting sources ---
    df = df.sort_values("is_blacklisted", ascending=False)  # True first
    df = df.drop_duplicates(subset="h3_cell", keep="first").reset_index(drop=True)
    print(f"  {len(df):,} unique H3 cells after deduplication")
    print(f"  Blacklisted cells: {int(df['is_blacklisted'].sum()):,}  |  Viable: {int((~df['is_blacklisted']).sum()):,}")

    # --- Encode H3 cells as sorted uint64 ---
    # The runtime loader (darksky._load_padus_h3_index) reads h3_cell as a numpy
    # uint64 array and binary-searches it, so store cells as uint64 sorted ascending
    # — avoids a ~1.4M-object Python dict build on every Lambda cold start.
    df["h3_cell"] = df["h3_cell"].map(h3.str_to_int).astype("uint64")
    df = df.sort_values("h3_cell").reset_index(drop=True)

    # --- Write output ---
    os.makedirs(OUT_DIR, exist_ok=True)
    df.to_parquet(OUT_FILE, engine="pyarrow", compression="zstd", index=False)
    size_mb = os.path.getsize(OUT_FILE) / (1024 * 1024)
    print(f"\n  Saved → {OUT_FILE}  ({size_mb:.1f} MB)")
    print(f"  Columns: {df.columns.tolist()}")


if __name__ == "__main__":
    build_index()
