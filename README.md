# PyNightSkyPredictor

This tool provides extensive night sky trip planning for astrophotographers looking for help to decide when and where to observe. Give it a location and date and it tells you everything you need to decide.

Most tools treat moonrise as a binary. Moon up, night ruined. A 5% crescent above the horizon produces 0.06 Δmag of sky brightening at your target — imperceptible. A 75% gibbous produces 1.73 Δmag — severe.

PyNightSkyPredictor uses the Krisciunas & Schaefer (1991) photometric model to compute sky brightening at every target's position throughout the night, and clips each imaging window at the point where scattered moonlight exceeds the contrast threshold for that object type.

The Night Quality Score (1–10) combines:
* Lunar interference — K&S sky-brightening credit, not raw illumination percentage
* Seeing forecast — Cn² profile integration via 7Timer ASTRO/GFS (3 days out)
* Total clear dark sky hours — moon-corrected and cloud-adjusted
* Bortle scale — VIIRS 2025 satellite data with Falchi 2016 radiative-transfer fallback for genuinely dark sites

Beyond the score:
* Per-target imaging windows clipped by K&S moonlight interference
* Nearest dark sky areas named from OpenStreetMap, plus light domes on the horizon
* Monthly night scoring calendar
* Multi-location trip comparison across a date range
* Historical weather analysis back to 1940 via ERA5 reanalysis

Built on open data: NOAA, Open-Meteo, NASA/VIIRS, Falchi, 7Timer, OpenStreetMap, and Celestrak.

The two primary CLI scripts are:

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

`--show-nearby` adds a table of named darker sky areas and light domes within the search radius.

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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Architecture

Three layers — engine, formatting, and rendering — with two CLI shells on top. The engine has no print statements and returns only dataclasses, so it can be called directly from a web backend.

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
| `PyNightSkyPredictor/config.py` | Configuration loader (`config.json`) |
| `PyNightSkyPredictor/darksky.py` | Light pollution lookup (VIIRS + Falchi); `find_nearby()` dark-sky search |
| `PyNightSkyPredictor/weather.py` | Weather forecast — NOAA/NWS, Open-Meteo, 7Timer ASTRO |
| `PyNightSkyPredictor/location.py` | Geocoding and timezone resolution |
| `PyNightSkyPredictor/satellites.py` | Satellite pass prediction — Skyfield SGP4 propagation, Moon proximity |
| `PyNightSkyPredictor/tle_provider.py` | TLE acquisition — Celestrak fetch, 6-hour cache, stale-data fallback |
| `PyNightSkyPredictor/trip.py` | Trip planning engine |
| `PyNightSkyPredictor/cache.py` | Disk-backed JSON cache with per-entry TTL |

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

---

## Testing

```bash
python -m pytest                  # Full suite — 105 tests
python -m pytest -m "not eph"     # Fast suite — 94 tests, no ephemeris file needed
python -m pytest -v               # Verbose output
```

| Test file | Coverage |
|-----------|----------|
| `test_moonlight.py` | `ks_delta_mag` (including distance correction), `ks_moon_credit`, `moon_wash_severity` |
| `test_scoring.py` | `rate_night` geometric mean formula, weight redistribution, weather score |
| `test_milky_way.py` | `gal_to_radec` IAU matrix, `mw_max_visible`, core geometry |
| `test_moon_events.py` | `classify_full_moon` thresholds, eclipse integration against known 2026 events |
| `test_sky_events.py` | `dark_moon_intervals`, moon phase, sunset timing (ephemeris) |
| `test_mw_geometry.py` | Five-location Milky Way geometry regression (Whitehorse → Ushuaia) |

Tests marked `@pytest.mark.eph` require the bundled `de421.bsp`; skipped by `-m "not eph"`. All other tests are pure math with no network or file dependencies.

---

## License

MIT — see [LICENSE](LICENSE).

Development assisted by GitHub Copilot and Claude.
