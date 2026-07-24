# Architecture

Three layers sit under two CLI shells: engine, formatting, and rendering. The engine has no print statements. It returns only dataclasses. That's what lets a web backend call it directly.

All the outside I/O runs through three narrow interfaces in `ports.py`. That covers caching, the saved-location store, and the light-pollution rasters. One environment variable, `PYNIGHTSKY_BACKEND` (default `local`), picks the concrete implementation once at startup. So the same engine runs against local files for the CLI, or cloud services for the deployed app, and no call site changes.

**Engine** (pure functions, no I/O):

| Module | Role |
|--------|------|
| `darkhours/predictor.py` | Assembles `NightReport` from every data source |
| `darkhours/scoring.py` | Night and weather score math |
| `darkhours/sky_events.py` | Sun and moon events, dark intervals, moon phase |
| `darkhours/moonlight.py` | Scattered-moonlight model. K&S (1991) times Winkler (2022) hybrid with live AOD |
| `darkhours/moon_events.py` | Lunar distance, eclipse detection, supermoon and micromoon |
| `darkhours/milky_way.py` | Galactic coordinate helpers, Milky Way arch synthesis |
| `darkhours/targets.py` | Visible targets engine. K&S interference, photo window clipping |
| `darkhours/targets.json` | Curated target catalog |
| `darkhours/config.py` | Config loader. Merges `darkhours/config.json` over built-in defaults (see [Configuration](INSTALL.md#configuration)) |
| `darkhours/darksky.py` | Light pollution lookup (VIIRS + Falchi), POI-first `find_nearby()` search (routable OSM POI index + drive times), `LocalRasterSource` and `S3RasterSource` adapters |
| `darkhours/light_dome.py` | Directional horizon light-dome analysis (Walker d^-2.5 kernel) plus precomputed H3 index |
| `darkhours/gridraster.py` | Pure-numpy tiled raster grid reader (local memmap or S3 byte-range). No GDAL |
| `darkhours/gridbuild.py` | Build-time GeoTIFF to grid converter. The package's only rasterio import |
| `darkhours/weather.py` | Weather forecast. NOAA/NWS, Open-Meteo, 7Timer ASTRO |
| `darkhours/aqicn.py` | Live haze cross-check. WAQI real-time station PM2.5/PM10, plus-or-minus 1 day window, distance-filtered against far-away "nearest" stations |
| `darkhours/aurora.py` | Aurora forecast. NOAA SWPC 3-day Kp forecast + 27-day outlook, dipole geomagnetic-latitude viewline model |
| `darkhours/location.py` | Geocoding and timezone resolution, `LocalGeocodeStore` adapter |
| `darkhours/satellites.py` | Satellite pass prediction. Skyfield SGP4 propagation, moon proximity |
| `darkhours/tle_provider.py` | TLE acquisition. Celestrak fetch, 6-hour cache, stale-data fallback |
| `darkhours/trip.py` | Trip planning engine |
| `darkhours/cache.py` | Disk-backed JSON cache with per-entry TTL, `LocalFileCache` and `DynamoCache` adapters |
| `darkhours/provider_health.py` | In-process registry of observed third-party provider health (feeds `/healthz`) |
| `darkhours/ports.py` | I/O backend interfaces (`Cache`, `GeocodeStore`, `RasterSource`) plus the `PYNIGHTSKY_BACKEND` selector |
| `darkhours/_http.py` | Security-restricted HTTP wrapper. Every outbound fetch goes through here, and non-HTTP(S) schemes are blocked (guards against CWE-22 file:// injection) |

**Formatting.** `darkhours/format_ctx.py` handles timezone and unit conversion and locale detection.

**Rendering.** `darkhours/render_report.py`, `darkhours/render_calendar.py`, and `darkhours/render_trip.py` do terminal output only. Each one takes a dataclass and prints to stdout.

**CLI shells.** `darkhours.py` and `tripbuilder.py`.

## Direct engine usage

```python
from darkhours.predictor import assemble_night
from datetime import date
from zoneinfo import ZoneInfo

report = assemble_night(
    lat=36.4229, lon=-116.9137,
    target=date.today(),
    tz=ZoneInfo("America/Los_Angeles"),
    display_name="Death Valley",
)
print(report.score)           # 0–10
print(report.dark_hours)      # clear dark sky hours tonight
print(report.active_showers)  # active meteor showers
```

## Data Download & Caching

The engine downloads external datasets on first use and stores them in `~/.darkhours/`:

| Data | Source | TTL |
|------|--------|-----|
| VIIRS Black Marble 2025 | NASA/NOAA satellite | Permanent (static dataset) |
| Falchi World Atlas 2016 | GFZ Potsdam | Permanent (static dataset) |
| Nominatim geocoding | OpenStreetMap | 90 days |
| Overpass API (area names for `--show-nearby`) | OpenStreetMap | 90 days |
| Weather forecasts | NOAA / Open-Meteo / 7Timer | Hours to days |
| Live haze cross-check (PM2.5/PM10) | WAQI (World Air Quality Index Project) | 30 minutes |
| Aurora Kp forecast (3-day) / outlook (27-day) | NOAA SWPC | 30 minutes / 6 hours |
| Satellite TLEs (ISS, Hubble, Tiangong, Starlink) | Celestrak | 6 hours |

The JPL DE421 planetary ephemeris (1900 to 2050) ships in the repo at `darkhours/de421.bsp`. The astronomy math needs no download.

Every dataset stays under its original open license. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for full credit.

## Offline Spatial Index (PADUS)

`find_nearby()` runs a pre-built H3 spatial index of USGS PAD-US public lands as a fast first pass. The runtime file is `cache/darkhours_padus_h3.npz`, about 3.1 MB, columnar sorted-uint64, and committed to the repo. Nothing to build for normal use. The intermediate `cache/darkhours_padus_h3.parquet` is committed too. You only rebuild from scratch when the PAD-US source data changes.

**1. Download the source geodatabase**

Grab the PAD-US 4.1 Combined Feature Class Geodatabase from USGS ScienceBase, about 700 MB:

> https://www.sciencebase.gov/catalog/item/652d4fc5d34e44db0e2ee45e

Unzip it into the project `Temp/` directory:

```
Temp/
└── PADUS4_1Geodatabase/
    └── PADUS4_1Geodatabase.gdb/
```

**2. Install build dependencies**

```bash
pip install -r requirements-build.txt
```

**3. Run the build script**

```bash
python scripts/build_padus_index.py
```

Out comes the `cache/darkhours_padus_h3.*` pair. Format, blacklist rules, and the uint64-sorted invariant live in [docs/PADUS_INDEX.md](PADUS_INDEX.md). `Temp/` is gitignored. The built indexes under `cache/` are committed.

## Offline Spatial Index (OSM POIs)

`find_nearby()` is POI-first. Dark-sky areas come back as named, routable OpenStreetMap POIs (parking, viewpoints, campsites, rest areas, observatories, lighthouses, and more) instead of raw off-road pixels. So results are reachable, already named (no reverse-geocode), and carry a real coordinate to route to. A compact H3 index drives this, `cache/osm_pois.npz`, about 0.7 MB, committed. To rebuild it:

```bash
pip install -r requirements-build.txt          # provides osmium (pyosmium) + h3
scripts/update_pois.sh                          # download latest US extract, build, clean up
# or, against a .pbf you already have in Temp/:
python scripts/osm_poi_builder.py Temp/us-260608.osm.pbf
```

The builder filters a Geofabrik US extract down to named POIs of the indexed types, drops closed and junk entries, and keeps only the ones in dark (Bortle ≤ 4) areas so the index stays small. Source data is © OpenStreetMap contributors, licensed **ODbL**. Keep that attribution visible wherever the data shows up. Format and rules: [docs/OSM_POI_INDEX.md](OSM_POI_INDEX.md).
