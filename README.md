# PyNightSkyPredictor

This tool provides extensive night sky trip planning for astrophotographers looking for help to decide when and where to observe. Give it a location and date and it tells you everything you need to decide.

Most tools treat moonrise as a binary. Moon up, night ruined. A 5% crescent above the horizon produces 0.06 Δmag of sky brightening at your target — imperceptible. A 75% gibbous produces 1.73 Δmag — severe.

PyNightSkyPredictor uses the Krisciunas & Schaefer (1991) photometric model to compute sky brightening at every target's position throughout the night, and clips each imaging window at the point where scattered moonlight exceeds the contrast threshold for that object type.

The Night Quality Score (1–10) combines:
* Lunar interference (25%) — K&S sky-brightening credit, not raw illumination percentage
* Seeing / cloud cover (40%) — Cn² profile integration via 7Timer ASTRO/GFS, cloud-adjusted
* Total clear dark sky hours (25%) — moon-corrected
* Bortle scale (10%) — VIIRS 2025 satellite data with Falchi 2016 radiative-transfer fallback for genuinely dark sites

Beyond the score:
* Per-target imaging windows clipped by K&S moonlight interference
* Nearest dark sky areas — pre-filtered against USGS PAD-US public lands, named from OpenStreetMap, plus light domes on the horizon
* Monthly night scoring calendar
* Multi-location trip comparison across a date range
* Historical weather analysis back to 1940 via ERA5 reanalysis

Built on open data: NOAA, Open-Meteo, NASA/VIIRS, Falchi, 7Timer, OpenStreetMap, Celestrak, and USGS PAD-US.

The two CLI scripts are:

* `pynightsky.py` — Single-night reports, monthly calendars, and nearby dark-sky search.
* `tripbuilder.py` — Multi-location score matrix and ranked best nights across a date range.

## pynightsky.py

Single-night reports, monthly calendars, and nearby dark-sky search for a single location.

Full documentation: [PYNIGHTSKY.md](docs/PYNIGHTSKY.md)

### Options

| Flag | Short | Description |
|------|-------|-------------|
| `--location NAME` | `-l` | Location name or city (geocoded and cached) |
| `--coords LAT LON` | `-c` | Decimal-degree coordinates, e.g. `-c 40.7128 -74.0060` |
| `--date DATE` | `-d` | Date (YYYY-MM-DD, default: today); YYYY-MM format accepted with `--calendar` |
| `--weather` | `-w` | Include hourly weather forecast |
| `--targets` | `-t` | Show prime targets (peak ≥ 40°, window ≥ 1h, no moon wash) |
| `--satellites` | `-s` | Show ISS, Hubble Telescope, Tiangong, and Starlink train pass times with moon separation |
| `--show-nearby [MILES]` | | Darker sky areas and light domes within radius (default 60 mi, max 150 mi) |
| `--all` | `-a` | Enable `--weather`, `--targets`, `--satellites`, and `--show-nearby 60` in one flag |
| `--calendar` | | Month-view night score grid |
| `--save-location NAME` | | Save `--coords` under a name for future use |
| `--list-locations` | | Show all saved/cached locations and exit |
| `--units imperial\|si` | | Temperature/wind units (default: auto-detect from locale) |
| `--verbose` | `-v` | Debug output to stderr |

One of `--location` or `--coords` is required.

### Output

Every run produces a single-night report:

- **Night Quality Score** (1–10) — composite of lunar interference, dark hours, weather, and light pollution
- **Night Timeline** — sunset, astronomical night begin/end, moonrise/set, sunrise
- **Light Pollution** — SQM, Bortle class, djlorenz zone for the coordinates
- **Moon** — phase, illumination, distance; supermoon/micromoon flags; eclipse type and magnitude when applicable
- **Meteor Showers** — active showers with peak note and ZHR (always shown, no flag needed)
- **Clear Dark Sky Hours** — effective dark time, cloud-adjusted and moon-corrected; lunar-cycle average alongside for context

`--weather` adds an hourly conditions table: cloud cover, seeing, transparency, wind (speed + direction), dew point, feels-like, humidity, precipitation — each hour rated 1–10 for astrophotography.

`--targets` adds prime targets by type (Milky Way, clusters, planets, nebulae, galaxies, meteor showers) with visibility windows and moon-interference clipping.

`--satellites` adds a unified pass table for ISS, Hubble Telescope, Tiangong, and any currently raising Starlink trains. Each row shows rise, peak, and set times with altitude, azimuth, pass duration, and moon separation. Twilight passes are flagged `†`; passes ending in Earth's shadow are flagged `*`.

`--show-nearby` adds a table of named darker sky areas and light domes within the search radius. The search is **POI-first**: when the routable OSM POI index is present (see [Offline Spatial Index (OSM POIs)](#offline-spatial-index-osm-pois)), it surfaces named, reachable destinations (trailhead parking, viewpoints, campsites, observatories, …) that sit on a dark pixel, rather than raw off-road coordinates; areas with no routable POI fall back to a plain coordinate, flagged as remote. It returns sky at least one Bortle class darker than the origin (capped at Bortle 3), so already-dark origins (e.g. a Bortle 3 site) still surface the reachable Bortle 2 areas nearby. Drive times and road distances are computed only on the cloud (AWS) deployment — see [Cloud Deployment](#cloud-deployment).

`--all` is shorthand for `--weather --targets --satellites --show-nearby` in one flag.

`--calendar` replaces the single-night report with a full-month score grid.

### Example — single night with targets, weather, and nearby search

```bash
python pynightsky.py --location "Sedona, AZ" --date 2018-08-12 --targets --weather --show-nearby
```

```
Date:               2018-08-12
Location:           Sedona, Coconino County, Arizona, United States  (34.8689°, -111.7614°)
Light Pollution:    SQM 18.7  ·  Zone 7a  ·  Bortle 7  (Suburban/urban transition)  [VIIRS 2025]
Moon:               New Moon  |  4.2% illuminated  |  363,111 km
Meteor Showers:     Perseids · Peak night · ZHR 100
Clear Dark Sky Hours:  6h 12m  ( 9:00 PM – 10:00 PM,  11:00 PM –  4:12 AM MST)  ·  avg 3.4h  ±2.7h over lunar cycle
Night Quality Score:  8.3/10  (Lunar 10.0 · Dark Hours 10.0 · Weather 8.2 · Bortle 3.3)

Night Timeline:

  Time (MST)        Event
  ----------------  -------------------------
  Aug 12,  7:08 AM  Moonrise
  Aug 12,  7:18 PM  Sunset
  Aug 12,  8:32 PM  Moonset
  Aug 12,  8:51 PM  Astronomical night begins
  Aug 13,  4:12 AM  Astronomical night ends
  Aug 13,  5:45 AM  Sunrise

Weather  [Open-Meteo Historical]:

  Time (MST)        Wx Rating  Cloud Cover  Temp  Dew Pt  Feels  Humidity      Wind  Precip
  ----------------  ---------  -----------  ----  ------  -----  --------  --------  ------
  Aug 12,  7:00 PM       5/10          46%  86°F    49°F   82°F       28%    8mph S  None
  Aug 12,  8:00 PM       4/10          54%  83°F    55°F   80°F       38%   11mph S  None
  Aug 12,  9:00 PM       8/10          12%  79°F    58°F   78°F       49%   9mph SE  None
  Aug 12, 10:00 PM       4/10          62%  79°F    58°F   78°F       49%    6mph E  None
  Aug 12, 11:00 PM       8/10          17%  78°F    57°F   80°F       48%   2mph SE  None
  Aug 13, 12:00 AM       9/10           2%  78°F    58°F   80°F       50%   1mph SE  None
  Aug 13,  1:00 AM      10/10           1%  75°F    58°F   77°F       55%   1mph NE  None
  Aug 13,  2:00 AM      10/10           0%  73°F    58°F   75°F       59%   2mph NE  None
  Aug 13,  3:00 AM      10/10           0%  71°F    58°F   72°F       64%   2mph NE  None
  Aug 13,  4:00 AM      10/10           0%  71°F    58°F   72°F       64%   2mph NE  None

Prime Targets  ( 7:18 PM –  5:45 AM MST):

  Milky Way: 6.7/10  (Altitude 10.0/10  ·  Waypoints 1.2/10  ·  Window 6.6/10)
  Visible   8:51 PM – 12:08 AM  ·  3h 17m  ·  Core 26°/26°  ·  1 of 8 waypoints visible
  Best time      8:51 PM  —  core 26° S

  Target                  Best Viewing                                  Sky       Astro Window
  ----------------------  --------------------------------------------  --------  -------------------------------
  Galactic Core            8:51 PM @ 26°  181°(S)  arch 49° (moderate)  Dark sky   8:51 PM @ 26° – 12:08 AM @ 10°

  Meteor Showers
  Perseids Meteor Shower   4:12 AM @ 60°  30°(NE)                       Dark sky  10:58 PM @ 21° –  4:12 AM @ 60°

  Clusters
  Double Cluster           4:12 AM @ 65°  22°(N)                        Dark sky  10:08 PM @ 20° –  4:12 AM @ 65°
  Pleiades                 4:12 AM @ 55°  97°(E)                        Dark sky   1:28 AM @ 21° –  4:12 AM @ 55°

  Nebulae
  Eagle Nebula             9:18 PM @ 41°  179°(S)                       Dark sky   8:51 PM @ 41° – 12:48 AM @ 21°
  Ring Nebula              9:58 PM @ 88°  202°(S)                       Dark sky   8:51 PM @ 77° –  3:38 AM @ 21°
  Dumbbell Nebula         10:58 PM @ 78°  177°(S)                       Dark sky   8:51 PM @ 59° –  4:12 AM @ 22°

  Galaxies
  Pinwheel Galaxy          8:51 PM @ 47°  315°(NW)                      Dark sky   8:51 PM @ 47° – 11:58 PM @ 21°
  Andromeda Galaxy         3:48 AM @ 83°  352°(N)                       Dark sky   9:38 PM @ 21° –  4:12 AM @ 81°
  Triangulum Galaxy        4:12 AM @ 84°  130°(SE)                      Dark sky  10:58 PM @ 21° –  4:12 AM @ 84°
  Whirlpool Galaxy         8:51 PM @ 41°  305°(NW)                      Dark sky   8:51 PM @ 41° – 10:58 PM @ 21°

Nearby Skies  (60 mi radius):

  Nearest:  Bortle 1  ·  15 mi ENE  (Coconino, AZ)

  Area                                 Bortle   SQM  Distance  Direction
  -----------------------------------  ------  ----  --------  ---------
  Coconino, AZ                              1  22.0     15 mi        ENE
  Red Rock-Secret Mountain Wilderness       1  22.0     15 mi         NW
  Wet Beaver Wilderness                     1  22.0     20 mi         SE
  Sycamore Canyon Wilderness                1  22.0     20 mi        WNW
```

### Example — satellite passes

```bash
python pynightsky.py --location "Sedona, AZ" --satellites
```

```
Date:               2026-05-30
Location:           Sedona, Coconino County, Arizona, United States  (34.8689°, -111.7614°)
Light Pollution:    SQM 18.7  ·  Zone 7a  ·  Bortle 7  (Suburban/urban transition)  [VIIRS 2025]
Moon:               Waxing Gibbous  |  99.9% illuminated  |  405,972 km  ·  *** Micromoon ***
Clear Dark Sky Hours:  None (moon up all night)  ·  avg 2.8h  ±2.1h over lunar cycle
Night Quality Score:  0.0/10  (Lunar 0.0 · Dark Hours 0.0 · Weather 9.0 · Bortle 3.3)

Night Timeline:

  Time (MST)        Event
  ----------------  -------------------------
  May 30,  7:31 PM  Moonrise
  May 30,  7:34 PM  Sunset
  May 30,  9:18 PM  Astronomical night begins
  May 31,  3:31 AM  Astronomical night ends
  May 31,  5:02 AM  Moonset
  May 31,  5:14 AM  Sunrise

Satellite Passes  ( 7:34 PM –  5:14 AM MST):

                    Rise                     |  Peak                     |  Set
  Satellite         Time      Alt  Az        |      Time  Alt  Az        |      Time   Alt  Az        Dur  Moon Sep
  ----------------  --------  ---  --------  |  --------  ---  --------  |  --------  ----  --------  ---  --------
  ISS †              7:53 PM  10°  292°(W)   |   7:56 PM  30°  229°(SW)  |   7:59 PM   10°  165°(S)    6m  98.5°
  Tiangong           8:56 PM  10°  292°(W)   |   8:59 PM  73°  207°(SW)  |   9:00 PM  41°*  134°(SE)   4m  72.1°
  Hubble Telescope   4:17 AM  16°  232°(SW)  |   4:19 AM  23°  191°(S)   |   4:22 AM   10°  137°(SE)   5m  40.8°

  * Set alt > 10° — satellite entered Earth's shadow before geometric set
  † Pass during civil twilight — sky too bright to observe
  +3 passes in Earth's shadow (not visible)
```

### Example — monthly calendar

```bash
python pynightsky.py --location "Sedona, AZ" --calendar --date 2026-06
```

```
Calendar — Sedona, Coconino County, Arizona, United States
Light Pollution:    SQM 18.7  ·  Zone 7a  ·  Bortle 7  (Suburban/urban transition)  [VIIRS 2025]  ·  Score 3.3/10
June 2026

  Date        Night Quality Score  Clear Dark Hours  Weather  Moon
  ----------  -------------------  ----------------  -------  ----
  2026-06-01               0.0/10            0h 00m        —  0.0
  2026-06-02               1.4/10            0h 44m        —  1.2
  2026-06-03               2.4/10            1h 23m        —  2.3
  2026-06-04               3.2/10            1h 56m        —  3.2
  2026-06-05               3.9/10            2h 25m        —  4.0
  2026-06-06               4.6/10            2h 52m        —  5.1
  2026-06-07               5.4/10            3h 17m        —  6.6
  2026-06-08               6.1/10            3h 42m        —  7.8
  2026-06-09               6.8/10            4h 09m        —  8.8
  2026-06-10               7.3/10            4h 39m        —  9.5
  2026-06-11               7.8/10            6h 00m        —  9.9
  2026-06-12               8.3/10            5h 58m        —  10.0
  2026-06-13               8.3/10            5h 58m        —  10.0
  2026-06-14               8.3/10            5h 58m        —  10.0
  2026-06-15               8.3/10            5h 57m        —  10.0
  2026-06-16               8.1/10            5h 57m        —  10.0
  2026-06-17               7.6/10            5h 57m        —  9.8
  2026-06-18               7.1/10            4h 22m        —  9.4
  2026-06-19               6.6/10            3h 52m        —  8.7
  2026-06-20               5.9/10            3h 26m        —  7.7
  2026-06-21               5.2/10            3h 01m        —  6.4
  2026-06-22               4.4/10            2h 36m        —  4.9
  2026-06-23               3.5/10            2h 09m        —  3.6
  2026-06-24               2.9/10            1h 40m        —  2.8
  2026-06-25               2.1/10            1h 07m        —  1.9
  2026-06-26               1.0/10            0h 28m        —  0.8
  2026-06-27               0.0/10            0h 00m        —  0.0
  2026-06-28               0.0/10            0h 00m        —  0.0  ·  *** Micromoon ***
  2026-06-29               0.0/10            0h 00m        —  0.0  ·  *** Micromoon ***
  2026-06-30               0.0/10            0h 00m        —  0.0  ·  *** Micromoon ***

  Best nights:  Jun 12 (8.3/10)  ·  Jun 13 (8.3/10)  ·  Jun 14 (8.3/10)
```

---

## tripbuilder.py

Compare multiple dark-sky sites across a date range — score matrix, ranked best nights, and weather-adjusted totals.

Full documentation: [TRIPBUILDER.md](docs/TRIPBUILDER.md)

### Options

| Flag | Short | Description |
|------|-------|-------------|
| `--locations NAME [NAME ...]` | `-l` | One or more location names to compare (required) |
| `--date-range START END` | `-d` | Date range as YYYY-MM-DD YYYY-MM-DD (required) |
| `--top N` | `-n` | Number of nights in the ranked list (default: 10) |
| `--no-weather` | | Astronomical factors only — skip weather fetch |
| `--units imperial\|si` | | Temperature/wind units (default: auto-detect) |
| `--verbose` | `-v` | Debug output to stderr |

### Output

- **Score matrix** — location × date grid with Night Quality Score per cell
- **Site summary** — average and best score per location; best-location callout
- **Top Nights** — ranked list across all locations with Lunar / Dark / Bortle / Weather breakdown

Weather is included for dates within the 16-day forecast window; score weights redistribute automatically for dates beyond it, so near-future and far-future nights are directly comparable.

### Example

```bash
python tripbuilder.py \
  --locations "Death Valley" "Sedona, AZ" "Grand Canyon Village, AZ" \
  --date-range 2026-06-01 2026-06-14
```

```
Trip Plan: Jun 1 – Jun 14, 2026

              Death Valley                Sedona    Grand Canyon Vill…
──────────────────────────────────────────────────────────────────────────
Jun  1                0.2                   0.1                   0.2
Jun  2                0.4                   0.3                   0.4
...
Jun 13                9.3                   3.8                   9.3
Jun 14                9.3                   3.9                   9.4
──────────────────────────────────────────────────────────────────────────
Average                 4.8                   2.3                   4.8
Best                   9.3                   3.9                   9.4

  → Best location: Grand Canyon Vill…  (avg 4.8/10)

Top Nights:

  Rank  Date    Location             Score  Lunar  Dark  Bortle  Weather
  ────  ──────  ──────────────────  ──────  ─────  ────  ──────  ───────
     1  Jun 14  Grand Canyon Vill…  9.4/10   10.0   9.3    10.0        —
     2  Jun 13  Death Valley        9.3/10    9.8   9.3    10.0        —
     3  Jun 14  Death Valley        9.3/10   10.0   9.2    10.0        —
```

---

## Installation

Requires **Python 3.13**.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # runtime dependencies
pip install -r requirements-dev.txt      # + pytest, for running the test suite
```

`requirements.txt` holds only the runtime libraries; `requirements-dev.txt` layers the test toolchain on top. The additional files `requirements-api.txt`, `requirements-worker.txt`, and `requirements-security.txt` are used by the cloud deployment (API server, background worker, and security-scanning CI respectively) and are not needed for local CLI use.

Container builds: see `Dockerfile`, `Dockerfile.lambda`, and `Dockerfile.worker` — cloud deployment documentation is in progress.

---

## Architecture

Three layers — engine, formatting, and rendering — with two CLI shells on top. The engine has no print statements and returns only dataclasses, so it can be called directly from a web backend.

External I/O — caching, the saved-location store, and the light-pollution rasters — is reached through three narrow interfaces in `ports.py`, with the concrete implementation selected once from the `PYNIGHTSKY_BACKEND` environment variable (default `local`). The same engine can therefore run against local files (the CLI) or, in the future, cloud services without changing any call sites.

**Engine** (pure functions, no I/O):

| Module | Role |
|--------|------|
| `PyNightSkyPredictor/predictor.py` | Assembles `NightReport` from all data sources |
| `PyNightSkyPredictor/scoring.py` | Night and weather score calculations |
| `PyNightSkyPredictor/sky_events.py` | Sun/moon events, dark intervals, moon phase |
| `PyNightSkyPredictor/moonlight.py` | Krisciunas & Schaefer (1991) moonlight model |
| `PyNightSkyPredictor/moon_events.py` | Lunar distance, eclipse detection, supermoon/micromoon |
| `PyNightSkyPredictor/milky_way.py` | Galactic coordinate helpers, Milky Way arch synthesis |
| `PyNightSkyPredictor/targets.py` | Visible targets engine — K&S interference, photo window clipping |
| `PyNightSkyPredictor/targets.json` | Curated target catalog |
| `PyNightSkyPredictor/config.py` | Configuration loader — merges `PyNightSkyPredictor/config.json` over built-in defaults (see [Configuration](#configuration) below) |
| `PyNightSkyPredictor/darksky.py` | Light pollution lookup (VIIRS + Falchi); POI-first `find_nearby()` dark-sky search (routable OSM POI index + drive times); `LocalRasterSource` adapter |
| `PyNightSkyPredictor/weather.py` | Weather forecast — NOAA/NWS, Open-Meteo, 7Timer ASTRO |
| `PyNightSkyPredictor/location.py` | Geocoding and timezone resolution; `LocalGeocodeStore` adapter |
| `PyNightSkyPredictor/satellites.py` | Satellite pass prediction — Skyfield SGP4 propagation, Moon proximity |
| `PyNightSkyPredictor/tle_provider.py` | TLE acquisition — Celestrak fetch, 6-hour cache, stale-data fallback |
| `PyNightSkyPredictor/trip.py` | Trip planning engine |
| `PyNightSkyPredictor/cache.py` | Disk-backed JSON cache with per-entry TTL; `LocalFileCache` adapter |
| `PyNightSkyPredictor/ports.py` | I/O backend interfaces (`Cache`, `GeocodeStore`, `RasterSource`) + `PYNIGHTSKY_BACKEND` selector |
| `PyNightSkyPredictor/_http.py` | Security-restricted HTTP wrapper — all outbound fetches go through here; blocks non-HTTP(S) schemes (guards against CWE-22 file:// injection) |

**Formatting** — `PyNightSkyPredictor/format_ctx.py`: timezone/unit conversion, locale detection.

**Rendering** — `PyNightSkyPredictor/render_report.py`, `PyNightSkyPredictor/render_calendar.py`, `PyNightSkyPredictor/render_trip.py`: terminal output only, each receives a dataclass and prints to stdout.

**CLI shells** — `pynightsky.py`, `tripbuilder.py`.

Direct engine usage:

```python
from PyNightSkyPredictor.predictor import assemble_night
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

### Data Download & Caching

External datasets are downloaded on first use and stored in `~/.pynightsky-predictor/`:

| Data | Source | TTL |
|------|--------|-----|
| VIIRS Black Marble 2025 | NASA/NOAA satellite | Permanent (static dataset) |
| Falchi World Atlas 2016 | GFZ Potsdam | Permanent (static dataset) |
| Nominatim geocoding | OpenStreetMap | 90 days |
| Overpass API (area names for `--show-nearby`) | OpenStreetMap | 90 days |
| Weather forecasts | NOAA / Open-Meteo / 7Timer | Hours–days |
| Satellite TLEs (ISS, Hubble, Tiangong, Starlink) | Celestrak | 6 hours |

The file `PyNightSkyPredictor/de421.bsp` (JPL DE421 planetary ephemeris, 1900–2050) is bundled in the repository — no download needed for astronomical computations.

All data remains under its original open license. See [ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) for full attribution.

### Offline Spatial Index (PADUS)

The DarkHours Lambda uses a pre-built H3 spatial index (`cache/darkhours_padus_h3.parquet`) as a fast first-pass filter before calling Overpass. This file is **not distributed** in the repository — it must be generated once locally.

**1. Download the source geodatabase**

Download the PAD-US 4.1 Combined Feature Class Geodatabase from USGS ScienceBase (~700 MB):

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

Output: `cache/darkhours_padus_h3.parquet` (~10 MB). Both `Temp/` and `cache/` are gitignored.

### Offline Spatial Index (OSM POIs)

`find_nearby()` is **POI-first**: dark-sky areas are surfaced as named, routable OpenStreetMap
POIs (parking, viewpoints, campsites, rest areas, observatories, lighthouses, and more) rather
than raw off-road pixels — so results are reachable, pre-named (no reverse-geocode), and carry a
real coordinate to route to. This is driven by a compact H3 index, `cache/osm_pois.npz` (~0.7 MB,
committed). To regenerate it:

```bash
pip install -r requirements-build.txt          # provides osmium (pyosmium) + h3
scripts/update_pois.sh                          # download latest US extract, build, clean up
# — or, against a .pbf you already have in Temp/:
python scripts/osm_poi_builder.py Temp/us-260608.osm.pbf
```

The builder filters a Geofabrik US extract to named POIs of the indexed types, drops closed/junk
entries, and keeps only those in dark (Bortle ≤ 4) areas so the index stays small. Source data is
© OpenStreetMap contributors, licensed **ODbL** — keep that attribution visible wherever the data
is used. Format and rules: [docs/OSM_POI_INDEX.md](docs/OSM_POI_INDEX.md).

### Configuration

Drop a `PyNightSkyPredictor/config.json` to override the built-in defaults:

```json
{
  "targets": {
    "min_elevation_deg": 20,
    "moon_min_separation_deg": 30,
    "moon_max_illumination_pct": 50
  },
  "prime_targets": {
    "min_peak_altitude_deg": 40,
    "min_window_hours": 1.0
  }
}
```

Any key you omit falls back to the default shown above. The file is optional — without it, all defaults apply.

---

## Cloud Deployment

The repository includes an optional cloud-native deployment that exposes the engine as an HTTP JSON API with a React/TypeScript web frontend.

**Stack:**
- **Compute** — FastAPI on AWS Lambda (container image) fronted by CloudFront with WAFv2 rate limiting
- **Storage** — DynamoDB for cache and geocode store; S3 for the VIIRS/Falchi rasters as Cloud-Optimized GeoTIFFs
- **Routing & geocoding** — Amazon Location **GeoRoutes** (`CalculateRouteMatrix`, traffic-aware via `DepartNow`) computes drive time + road distance to each reachable POI in `find_nearby`; AWS Location place index handles reverse-geocoding. Per-leg results are cached briefly (live-traffic ETAs)
- **Frontend** — React/TypeScript SPA (Vite) served from S3 via CloudFront. The nearby-results view orders sites by drive time, shows road distance, and links Google Maps driving directions (origin → site) for each
- **Resilience** — scheduled Lambda warmer for satellite TLEs; SQS + worker Lambda for async calendar/trip jobs (the worker also runs `find_nearby` and ships the PAD-US + OSM POI indexes)
- **Observability** — structured JSON logs, X-Ray tracing, CloudWatch metric alarms

**Repository layout:**
- `cdk/` — Python CDK stacks (Lambda API, CloudFront distribution, WAF, warmer, CI/CD)
- `apps/api/` — FastAPI application
- `apps/worker/` — async job worker
- `apps/web/` — React SPA
- `Dockerfile.lambda`, `Dockerfile.worker` — container images

**CI/CD** — GitHub Actions deploys via OIDC (no long-lived AWS keys); the pipeline builds and pushes container images to ECR, then runs `cdk deploy`.

To run your own instance, deploy the CDK stacks against your own AWS account with `PYNIGHTSKY_BACKEND=aws`.

---

## Testing

Requires the dev dependencies (`pip install -r requirements-dev.txt`).

```bash
python -m pytest                  # Full suite — 423 tests
python -m pytest -m "not eph"     # Fast suite — no ephemeris file needed
python -m pytest -v               # Verbose output
```

**Core engine tests** (pure math, no network, no ephemeris unless noted):

| Test file | Coverage |
|-----------|----------|
| `test_moonlight.py` | `ks_delta_mag` (including distance correction), `ks_moon_credit`, `moon_wash_severity` |
| `test_scoring.py` | `rate_night` formula, weight redistribution, weather score |
| `test_milky_way.py` | `gal_to_radec` IAU matrix, `mw_max_visible`, core geometry |
| `test_moon_events.py` | `classify_full_moon` thresholds, eclipse integration against known 2026 events |
| `test_sky_events.py` | `dark_moon_intervals`, moon phase, sunset timing (ephemeris) |
| `test_mw_geometry.py` | Five-location Milky Way geometry regression (Whitehorse → Ushuaia) |
| `test_predictor_formulas.py` | Moon score, Bortle conversion, crescent exemption — pure math from `predictor.py` |
| `test_darksky_formulas.py` | SQM-from-radiance conversions, Bortle classification boundaries |
| `test_targets_helpers.py` | Coordinate parsing, visibility window detection — pure helpers from `targets.py` |
| `test_tle_provider.py` | TLE parsing, Starlink filter, `get_tle()` state machine — fully hermetic |
| `test_weather_conditions.py` | `rate_conditions()`, Open-Meteo parser, 7Timer merge — pure logic |
| `test_weather_fallback.py` | Provider fallback behaviour — network stubbed |

**Adapter & cloud tests** (require moto / FastAPI TestClient; AWS smoke tests need real credentials):

| Test file | Coverage |
|-----------|----------|
| `test_adapters.py` | `LocalFileCache` vs `DynamoCache`, `LocalGeocodeStore` vs `DynamoGeocodeStore` contract parity (DynamoDB mocked via moto) |
| `test_api.py` | HTTP API endpoints via FastAPI TestClient — healthz, error paths, `/night` success |
| `test_aws_location.py` | AWS Location geocoding + GeoRoutes drive-time matrix (DepartNow, per-leg cache) — all boto3 calls mocked |
| `test_poi_index.py` | POI-first `find_nearby` — OSM POI index loader/encoder round-trip, dark-mask intersection, naming/drive-time gates, dark-threshold |
| `test_aws_smoke.py` | Opt-in integration smoke against real AWS (skipped unless `PYNIGHTSKY_BACKEND=aws` + credentials set) |
| `test_warmer.py` | TLE warmer Lambda handler — `tle_provider` mocked, no network |
| `test_jobs.py` | Async job lifecycle — in-memory cache, `run_job` and SQS mocked |

Tests marked `@pytest.mark.eph` require the bundled `de421.bsp`; skipped by `-m "not eph"`. All other core tests are pure math with no network or file dependencies.

---

## License

MIT — see [LICENSE](LICENSE).

This is a personal, non-commercial project. All third-party data sources are used within their respective free-tier terms for personal, non-commercial use.

Development assisted by GitHub Copilot and Claude.
