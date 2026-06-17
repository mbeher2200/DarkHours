# Routable OSM POI Index

A pre-built H3 index of named, routable OpenStreetMap POIs used by `find_nearby` to
surface **reachable** dark sky. When a POI (parking lot, viewpoint, campsite, rest area)
sits on a dark pixel, the search returns *the POI* — which is reachable, already named,
and carries a real coordinate to route — instead of a raw, often-unreachable wilderness
pixel.

- **Source of truth:** OpenStreetMap (a Geofabrik US `.osm.pbf` extract).
- **Artifact:** `cache/osm_pois.npz` — **committed** (a `.gitignore` exception; the Docker
  images copy it). ~0.6 MB, ~39k POIs.
- **Builder:** `scripts/osm_poi_builder.py` (offline; needs `requirements-build.txt`).
- **Refresh:** `scripts/update_pois.sh` (download → build → clean up the `.pbf`).
- **Runtime:** `darksky._load_poi_h3_index` / `_extract_poi_candidates`.

## License / attribution (ODbL — required)

The source is © OpenStreetMap contributors, licensed under the **ODbL**. `cache/osm_pois.npz`
is a *derivative database* and is redistributed under ODbL. Keep the attribution
**“© OpenStreetMap contributors”** visible wherever the data is used — it is in the web app
colophon (`apps/web/src/App.tsx`). Do not remove it.

## Why it exists

The darkest pixels are often unreachable backcountry, which both answers the wrong question
("where is it dark" vs. "where can I drive to") and makes the AWS route matrix snap a
wilderness coordinate to a far-off road. A POI is, by construction, an established place
with a road, a name, and a coordinate. So when a POI falls inside a dark area:
- **Surface the POI** (`is_poi=True`) — reachable + pre-named (no reverse-geocode).
- **Route only POIs** — raw fallbacks are never sent to the route matrix.
- **Fallback to raw pixels** (`is_poi=False`) only when *no* POI intersects the dark area;
  the frontend marks these "Remote" and links the raw coordinate to a map.

## POI types

Extracted from nodes **and** areas (a name tag is required — an established, displayable
destination). `poi_type` is the index into `_POI_TYPE_LABELS` (18 types), drawn from the
`amenity / tourism / highway / man_made / leisure / historic` keys, e.g. `tourism=viewpoint`/
`camp_site`/`caravan_site`/`picnic_site`/`information`/`attraction`, `man_made=observatory`/
`lighthouse`/`pier`/`tower` (the last **only** for `tower:type=observation` — broadcast masts
are excluded), `amenity=parking`/`ranger_station`, `highway=rest_area`,
`leisure=summer_camp`/`firepit`/`beach_resort`, and `historic=*` (folded into one `historic`
type; `historic=tower` → `tower`). Urban-recreation tags (playground, pitch, sports_centre,
…) are intentionally **not** indexed — they're never dark-sky destinations.
`POI_TYPE_LABELS` is **append-only** — existing codes are a stored contract in the committed
`.npz`; add new types only at the end. See `scripts/osm_poi_builder.POI_TYPE_LABELS` /
`darksky._POI_TYPE_LABELS` / `apps/web/src/types.ts PoiType` for the canonical list (kept in
sync). One POI per H3 res-7 cell; on collision the better astro destination wins
(`_POI_PRIORITY`: observatory/viewpoint/lighthouse/… first, generic/urban + bare parking last).

## Build-time filters (keep the index lean despite a broad tag set)

The query-time dark-mask drops bright POIs, but storing them still bloats the committed file.
So the builder filters up front:
- **`_is_usable_name`** — rejects junk names (pure digits, `<2` chars, `unknown`/`unnamed`,
  and `closed` — kills the OSM `"9"` and `"… (Closed)"` cases).
- **`_is_open`** — drops `access=no/private` and `disused`/`abandoned` POIs (lifecycle-prefixed
  tags like `disused:amenity=…` never match the exact-key `KeyFilter` in the first place).
- **Dark prefilter (`DARK_KEEP_BORTLE = 4`)** — `find_nearby`'s dark threshold is always
  Bortle ≤3, so a POI in a brighter area can *never* be a result. The builder samples the
  engine's own `darksky.lookup` and drops anything above Bortle 4 (+1 margin). This needs the
  **local light-pollution grids**; if they're absent the prefilter is skipped (and logged) so
  the build still works (just larger). Because of this, broad/urban tags (playground, pitch,
  …) cost almost nothing — their bright instances are dropped at build time.

## File format (`np.savez_compressed`)

| Array | Type | Notes |
|---|---|---|
| `cells` | **uint64, sorted ascending** | H3 res-7 cell id (build-time dedup key + parity with PAD-US) |
| `lats` | float32 | the POI's true latitude — the **routing destination** |
| `lons` | float32 | the POI's true longitude |
| `name_codes` | uint32 | dictionary code per POI, index into the names list |
| `names_blob` | uint8 | utf-8 of `"\x00".join(unique_names)`; reader splits on `\x00` |
| `poi_types` | uint8 | index into `_POI_TYPE_LABELS` |

> **Why store coordinates** (unlike PAD-US, which is cell→name only): the POI *is* the
> routing destination, so we keep its real lat/lon. A res-7 cell center can be ~3 km off
> the actual lot — useless for routing. float32 ≈ 1.4 m precision, ample for a road snap.

## Runtime path (`PyNightSkyPredictor/darksky.py`)

- `_load_poi_h3_index()` — lazy-loads once per process into a `_PoiIndex`; cached in the
  `_poi_h3_cache` module global; returns `None` (cached) if h3/numpy is missing or the file
  can't be read → `find_nearby` degrades to raw-pixel extraction. Prewarmed on the worker
  (`apps/worker/handler.py` `_prewarm`).
- `_extract_dark_sky_candidates(..., poi_index=...)` — after the dark mask is built, calls
  `_extract_poi_candidates`; if any POI hits a dark pixel, returns POIs, else raw pixels.
- `_extract_poi_candidates(...)` — bbox-prefilters the index to the search window, projects
  each POI to its raster pixel (inverse of the lat/lon `linspace` grids), keeps those whose
  pixel is dark, and emits candidate dicts (`is_poi=True`, `name`, `poi_type`, true coord).
- `_offline_tier_name` short-circuits `is_poi` candidates to their OSM name (still discards
  on a PAD-US blacklist cell — never route onto military/tribal land).
- `_aws_drive_times` routes only `is_poi` candidates.
- File resolution order (`_poi_h3_path`): `PYNIGHTSKY_POI_H3_PATH` env override →
  `<repo>/cache/osm_pois.npz` → `/app/cache/osm_pois.npz` (the Lambda image path).

## Rebuilding from source

Automated:

```
scripts/update_pois.sh          # downloads the latest US extract, builds, cleans up
```

Manual (when you already have a `.pbf`):

1. Drop a Geofabrik US extract in the gitignored `Temp/` (e.g. `Temp/us-260608.osm.pbf`).
2. `pip install -r requirements-build.txt` (provides `osmium` + `h3`).
3. `python scripts/osm_poi_builder.py Temp/us-260608.osm.pbf` → `cache/osm_pois.npz`.
   - **Pass 1** streams the multi-GB file through a C++ `KeyFilter`, writing matched POIs
     **plus their referenced nodes** (`ForwardReferenceWriter`) to a tiny intermediate pbf —
     this keeps memory bounded (a full-US node-location cache will OOM a 16 GB machine).
   - **Pass 2** assembles areas + node locations on the small file and indexes to H3.
4. Commit the regenerated `cache/osm_pois.npz` (a tracked `.gitignore` exception).

## Related

- Performance context (POI-first is primarily a *relevance* change; the latency win is
  first-visit-only): [PERF_FINDNEARBY.md](PERF_FINDNEARBY.md) and `memory/find_nearby_perf`.
- The naming tiers it feeds: `_jit_geocode_candidates` / `_offline_tier_name`; PAD-US is the
  US public-lands tier ([PADUS_INDEX.md](PADUS_INDEX.md)).
