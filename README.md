# DarkHours

**DarkHours tells you whether tonight is worth the drive — and if not, when the next good night is.**

[![Try it live](https://img.shields.io/badge/darkhours.app-live-brightgreen)](https://darkhours.app)
[![Deploy](https://github.com/mbeher2200/DarkHours/actions/workflows/deploy.yml/badge.svg)](https://github.com/mbeher2200/DarkHours/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/release/python-3130/)

![DarkHours night report — Knolls, UT, Bortle 1, with the horizon light-dome panel showing a real light dome from Salt Lake City](docs/images/hero.png)

DarkHours gives astrophotographers the information they need to decide when and where to shoot. A single query returns a composite Night Quality Score built from real astronomical models — moonlight interference (not just phase), seeing and cloud cover, clear dark-sky hours, and Bortle-class light pollution. Beyond the score: per-target imaging windows with honest viability verdicts, nearby darker-sky search with drive times, a simulated 360° sky dome, horizon light-dome analysis, aurora and meteor-shower forecasts, satellite pass tables, and a 30-day outlook. Free, no account, no ads.

### See it in action

![360° simulated sky — drag to pan, scrub through the night](docs/images/skydome.gif)

![Red night-vision mode — one tap, the whole UI flips](docs/images/redmode.gif)

![Find darker sky nearby — search, compare, click through](docs/images/nearby.gif)

---

## Under the hood

Most tools treat moonrise as a binary. Moon up, night ruined. A 5% crescent above the horizon produces 0.06 Δmag of sky brightening at your target — imperceptible. A 75% gibbous produces 1.73 Δmag — severe.

DarkHours computes sky brightening at every target's position throughout the night with a hybrid moonlight model — Krisciunas & Schaefer (1991) lunar photometry driven through a Winkler (2022) single-scatter kernel (two-component Rayleigh + Henyey–Greenstein phase function) with **live aerosol optical depth** from Open-Meteo CAMS and an inverse-square Earth–Moon distance correction — and clips each imaging window at the point where scattered moonlight exceeds the contrast threshold for that object type. Details: [docs/CLI.md](docs/CLI.md).

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
* Live haze cross-check — a real-time ground-station PM2.5/PM10 reading (WAQI), shown on
  tonight's live "Now" marker when it crosses this app's own haze threshold, catching
  fast-moving smoke/haze events the forecast hasn't caught up to yet
* Aurora visibility forecast — NOAA SWPC Kp-index forecast run through a dipole
  geomagnetic-latitude viewline model, tiered overhead/naked-eye/photographic by margin

Built on open data: NOAA (SWPC space weather, NWS), Open-Meteo, NASA/VIIRS, Falchi, 7Timer, OpenStreetMap, Celestrak, WAQI, and USGS PAD-US.

The engine ships with two surfaces:

* **Two CLI scripts** — `darkhours.py` (single-night reports, monthly calendars, nearby dark-sky search) and `tripbuilder.py` (multi-location score matrix and ranked best nights across a date range).
* **DarkHours**, a React web app ([darkhours.app](https://darkhours.app)) serving the same engine through a FastAPI/Lambda backend, with features the terminal can't render — a 360° simulated sky dome, an all-sky light-dome panel, a 30-day outlook heatmap, and a red night-vision mode. See [apps/web/README.md](apps/web/README.md) and the full feature list in [docs/FEATURES.md](docs/FEATURES.md).

## darkhours.py

Single-night reports, monthly calendars, and nearby dark-sky search for a single location.

Full documentation: [CLI.md](docs/CLI.md)

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
- **Meteor Showers** — active showers with peak note and ZHR (always shown, no flag needed); the engine also models day-decayed, radiant-altitude-corrected local rates — see [docs/TARGETS.md](docs/TARGETS.md#meteor-shower-zhr-decay-model)
- **Clear Dark Sky Hours** — effective dark time, cloud-adjusted and moon-corrected; lunar-cycle average alongside for context

`--weather` adds an hourly conditions table: cloud cover, seeing, transparency, wind (speed + direction), dew point, feels-like, humidity, precipitation — each hour rated 1–10 for astrophotography.

`--targets` adds prime targets by type (Milky Way, clusters, planets, nebulae, galaxies, meteor showers) with visibility windows and moon-interference clipping.

`--satellites` adds a unified pass table for ISS, Hubble Telescope, Tiangong, and any currently raising Starlink trains. Each row shows rise, peak, and set times with altitude, azimuth, pass duration, and moon separation. Twilight passes are flagged `†`; passes ending in Earth's shadow are flagged `*`.

`--show-nearby` adds a table of named darker sky areas and light domes within the search radius. The search is **POI-first**: when the routable OSM POI index is present (see [Offline Spatial Index (OSM POIs)](#offline-spatial-index-osm-pois)), it surfaces named, reachable destinations (trailhead parking, viewpoints, campsites, observatories, …) that sit on a dark pixel, rather than raw off-road coordinates; areas with no routable POI fall back to a plain coordinate, flagged as remote. It returns sky at least one Bortle class darker than the origin (capped at Bortle 3), so already-dark origins (e.g. a Bortle 3 site) still surface the reachable Bortle 2 areas nearby. Drive times and road distances are computed only on the cloud (AWS) deployment — see [Cloud Deployment](#cloud-deployment).

`--all` is shorthand for `--weather --targets --satellites --show-nearby` in one flag.

`--calendar` replaces the single-night report with a full-month score grid.

### Example — single night with targets, weather, and nearby search

```bash
python darkhours.py --location "Sedona, AZ" --date 2018-08-12 --targets --weather --show-nearby
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
python darkhours.py --location "Sedona, AZ" --satellites
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
python darkhours.py --location "Sedona, AZ" --calendar --date 2026-06
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

`requirements.txt` holds only the runtime libraries; `requirements-dev.txt` layers the test toolchain on top. The additional files `requirements-api.txt`, `requirements-worker.txt`, and `requirements-security.txt` are used by the cloud deployment (API server, background worker, and security-scanning CI respectively), and `requirements-build.txt` holds the offline index/grid builders (rasterio lives there, and only there) — none of these are needed for local CLI use.

There is no application container: both cloud Lambdas deploy as zip packages (see [Cloud Deployment](#cloud-deployment)). The only Dockerfile in the repo, `Dockerfile.worker`, exists for the Trivy image scan in CI and the throwaway in-region perf-test recipe.

---

## Architecture

Three layers — engine, formatting, and rendering — with two CLI shells on top. The engine has no print statements and returns only dataclasses, so it can be called directly from a web backend.

External I/O — caching, the saved-location store, and the light-pollution rasters — is reached through three narrow interfaces in `ports.py`, with the concrete implementation selected once from the `PYNIGHTSKY_BACKEND` environment variable (default `local`). The same engine can therefore run against local files (the CLI) or, in the future, cloud services without changing any call sites.

**Engine** (pure functions, no I/O):

| Module | Role |
|--------|------|
| `darkhours/predictor.py` | Assembles `NightReport` from all data sources |
| `darkhours/scoring.py` | Night and weather score calculations |
| `darkhours/sky_events.py` | Sun/moon events, dark intervals, moon phase |
| `darkhours/moonlight.py` | Scattered-moonlight model — K&S (1991) × Winkler (2022) hybrid with live AOD |
| `darkhours/moon_events.py` | Lunar distance, eclipse detection, supermoon/micromoon |
| `darkhours/milky_way.py` | Galactic coordinate helpers, Milky Way arch synthesis |
| `darkhours/targets.py` | Visible targets engine — K&S interference, photo window clipping |
| `darkhours/targets.json` | Curated target catalog |
| `darkhours/config.py` | Configuration loader — merges `darkhours/config.json` over built-in defaults (see [Configuration](#configuration) below) |
| `darkhours/darksky.py` | Light pollution lookup (VIIRS + Falchi); POI-first `find_nearby()` dark-sky search (routable OSM POI index + drive times); `LocalRasterSource`/`S3RasterSource` adapters |
| `darkhours/light_dome.py` | Directional horizon light-dome analysis (Walker d^-2.5 kernel) + precomputed H3 index |
| `darkhours/gridraster.py` | Pure-numpy tiled raster grid reader (local memmap / S3 byte-range) — no GDAL |
| `darkhours/gridbuild.py` | Build-time GeoTIFF → grid converter (the package's only rasterio import) |
| `darkhours/weather.py` | Weather forecast — NOAA/NWS, Open-Meteo, 7Timer ASTRO |
| `darkhours/aqicn.py` | Live haze cross-check — WAQI real-time station PM2.5/PM10, ±1 day window, distance-filtered against far-away "nearest" stations |
| `darkhours/aurora.py` | Aurora visibility forecast — NOAA SWPC 3-day Kp forecast + 27-day outlook, dipole geomagnetic-latitude viewline model |
| `darkhours/location.py` | Geocoding and timezone resolution; `LocalGeocodeStore` adapter |
| `darkhours/satellites.py` | Satellite pass prediction — Skyfield SGP4 propagation, Moon proximity |
| `darkhours/tle_provider.py` | TLE acquisition — Celestrak fetch, 6-hour cache, stale-data fallback |
| `darkhours/trip.py` | Trip planning engine |
| `darkhours/cache.py` | Disk-backed JSON cache with per-entry TTL; `LocalFileCache`/`DynamoCache` adapters |
| `darkhours/provider_health.py` | In-process registry of observed third-party provider health (feeds `/healthz`) |
| `darkhours/ports.py` | I/O backend interfaces (`Cache`, `GeocodeStore`, `RasterSource`) + `PYNIGHTSKY_BACKEND` selector |
| `darkhours/_http.py` | Security-restricted HTTP wrapper — all outbound fetches go through here; blocks non-HTTP(S) schemes (guards against CWE-22 file:// injection) |

**Formatting** — `darkhours/format_ctx.py`: timezone/unit conversion, locale detection.

**Rendering** — `darkhours/render_report.py`, `darkhours/render_calendar.py`, `darkhours/render_trip.py`: terminal output only, each receives a dataclass and prints to stdout.

**CLI shells** — `darkhours.py`, `tripbuilder.py`.

Direct engine usage:

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

### Data Download & Caching

External datasets are downloaded on first use and stored in `~/.darkhours/`:

| Data | Source | TTL |
|------|--------|-----|
| VIIRS Black Marble 2025 | NASA/NOAA satellite | Permanent (static dataset) |
| Falchi World Atlas 2016 | GFZ Potsdam | Permanent (static dataset) |
| Nominatim geocoding | OpenStreetMap | 90 days |
| Overpass API (area names for `--show-nearby`) | OpenStreetMap | 90 days |
| Weather forecasts | NOAA / Open-Meteo / 7Timer | Hours–days |
| Live haze cross-check (PM2.5/PM10) | WAQI (World Air Quality Index Project) | 30 minutes |
| Aurora Kp forecast (3-day) / outlook (27-day) | NOAA SWPC | 30 minutes / 6 hours |
| Satellite TLEs (ISS, Hubble, Tiangong, Starlink) | Celestrak | 6 hours |

The file `darkhours/de421.bsp` (JPL DE421 planetary ephemeris, 1900–2050) is bundled in the repository — no download needed for astronomical computations.

All data remains under its original open license. See [ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md) for full attribution.

### Offline Spatial Index (PADUS)

`find_nearby()` uses a pre-built H3 spatial index of USGS PAD-US public lands as a fast first-pass filter. The runtime artifact is `cache/darkhours_padus_h3.npz` (~3.1 MB, columnar sorted-uint64, **committed** — nothing to build for normal use); the intermediate `cache/darkhours_padus_h3.parquet` is also committed. Regenerating from scratch is only needed when the PAD-US source data changes:

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

Output: the `cache/darkhours_padus_h3.*` pair. Format, blacklist rules, and the uint64-sorted invariant: [docs/PADUS_INDEX.md](docs/PADUS_INDEX.md). `Temp/` is gitignored; the built indexes under `cache/` are committed.

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

Drop a `darkhours/config.json` to override the built-in defaults:

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

The repository includes an optional cloud-native deployment that exposes the engine as an HTTP JSON API with a React/TypeScript web frontend (the DarkHours app).

**Stack:**
- **Compute** — FastAPI on AWS Lambda, deployed as a **zip package** (Python 3.13, arm64; dependencies pip-installed by CDK asset bundling at deploy time — no application container), fronted by CloudFront with WAFv2 rate limiting. Async jobs run on a second zip Lambda fed by SQS (with a dead-letter queue)
- **Storage** — DynamoDB for cache and geocode store; S3 for the VIIRS/Falchi rasters as tiled raw-binary grids, range-read in place by `gridraster.py` (no GDAL at runtime — see [docs/RASTERIO_REPLACEMENT.md](docs/RASTERIO_REPLACEMENT.md))
- **Routing & geocoding** — Amazon Location **GeoRoutes** (`CalculateRoutes`) computes drive time + road distance to each reachable POI in `find_nearby`, and flags ferry-only/unpaved-road legs; an AWS Location place index handles geocoding, suggestions, and reverse-geocoding. Per-leg results are cached for 24h
- **Frontend** — React/TypeScript SPA (Vite) served from S3 via CloudFront. The nearby-results view orders sites by drive time, shows road distance, and links Google Maps driving directions (origin → site) for each
- **Resilience** — EventBridge keep-warm pings (every 4 min) for both Lambdas; a separate scheduled warmer Lambda refreshes satellite TLEs every 6h; a provider-health Lambda probes the upstream weather/space-weather APIs every 5 min
- **Observability** — structured JSON logs, X-Ray tracing, CloudWatch dashboard + metric alarms with SNS notification, CloudWatch RUM on the frontend (see [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md))

**HTTP API** (served same-origin behind CloudFront; long computations return `202` + a job id):

| Endpoint | Mode | Purpose |
|---|---|---|
| `GET /night` | sync | Full single-night report (`location` or `lat`+`lon`, `date`, optional `targets`/`satellites`; `date_only=true` refetches just the date-dependent fields) |
| `GET /suggest?q=` | sync | Place-name typeahead suggestions |
| `GET /nearby` | async (202 + job) | Dark-sky search, radius 5–120 mi |
| `GET /calendar` | async (202 + job) | Multi-night outlook, up to 30 days |
| `GET /jobs/{job_id}` | sync | Poll an async job (`pending` / `done` / `error`) |
| `GET /healthz` | sync | Cache round-trip + provider health snapshot |
| `POST /warmup` | sync | Keep-warm ping target (EventBridge) |

The CLI's `--show-nearby` accepts up to 150 mi; the web API caps the radius at 120 mi.

**Repository layout:**
- `cdk/` — Python CDK stacks: `PyNightSkyLambda` (API + worker + CloudFront + WAF + queues + alarms), `PyNightSkyCicd` (GitHub OIDC deploy role), `PyNightSkyWarmer` (TLE refresh), `PyNightSkyProviderHealth` (provider probes)
- `apps/api/` — FastAPI application (Mangum ASGI adapter)
- `apps/worker/` — async job worker (SQS-triggered)
- `apps/web/` — the DarkHours React SPA ([apps/web/README.md](apps/web/README.md))
- `Dockerfile.worker` — used only for the Trivy security scan and the throwaway perf-test recipe; not part of the deployment

**CI/CD** — push to `main` runs the test suite, assumes the deploy role via GitHub OIDC (no long-lived AWS keys), builds the SPA, and runs `cdk deploy PyNightSkyLambda`. No Docker build or registry push is involved. A separate `security.yml` workflow runs Bandit, pip-audit, Semgrep, gitleaks, and Trivy on every branch.

To run your own instance, deploy the CDK stacks against your own AWS account with `PYNIGHTSKY_BACKEND=aws`. Resource names (bucket, table, place index, queue URL) are injected via environment variables and are deliberately absent from the source.

---

## Testing

Requires the dev dependencies (`pip install -r requirements-dev.txt`).

```bash
python -m pytest                  # Full suite — 803 tests across 36 files
python -m pytest -m "not eph"     # Fast suite — no ephemeris file needed
python -m pytest -v               # Verbose output
```

A default run stays offline and deterministic. Three opt-in markers (`pytest.ini`) gate the exceptions: `eph` (needs the bundled `de421.bsp`), `aws` (real AWS integration — needs `PYNIGHTSKY_BACKEND=aws` + credentials + resource env vars), and `live` (real provider APIs — needs `PYNIGHTSKY_LIVE=1`). The full per-file inventory lives in [tests/README.md](tests/README.md).

---

## License

MIT — see [LICENSE](LICENSE).

This is a personal, non-commercial project. All third-party data sources are used within their respective free-tier terms for personal, non-commercial use.

Development assisted by GitHub Copilot and Claude.
