# PyNightSkyPredictor

A night sky prediction tool for astronomy and astrophotography planning. For a given location and date, PyNightSkyPredictor predicts:

- A Night Quality Score (1-10) by taking into consideration **sun and moon rise/set times**, **total night sky availability**, **moon phase, and percent illumination**, **light pollution levels**, and **weather conditions**.

- Visible major targets, and prime target times and elevation.

Perfect for dark sky observations, astrophotography sessions, and trips.

## Output

The tool displays:
- **Night Quality Score (1-10)** — Overall night sky quality, broken down by component
- **Night Timeline** — Sunset, astronomical night begins/ends, moonrise/set, sunrise
- **Light Pollution** — SQM reading, Bortle class, and djlorenz zone for the exact location
- **Moon** — Phase and percent illumination at sunset
- **Prime Dark Sky Hours** — Total moon-free hours within astronomical darkness tonight, plus the average and standard deviation across the current 30-night lunar cycle (used to put tonight's dark time in context for scoring)
- **Weather** — Hourly cloud cover, seeing, transparency, temperature, humidity, wind, and precipitation — each hour rated 1–10 for astrophotography conditions (with `--weather`)
- **Visible Targets** — What's observable tonight, grouped by type (with `--targets` or `--prime-targets`)

Example output (`python pynightsky.py --location "Grand Canyon Village, Arizona" --prime-targets`):
```
Date:               2026-05-23
Location:           Grand Canyon Village, Coconino County, Arizona, United States  (36.0578°, -112.1282°)
Light Pollution:    SQM 21.9  ·  Zone 2a  ·  Bortle 2  (Truly dark sky)  [Falchi 2016]
Moon:               First Quarter  |  56.8% illuminated
Prime Dark Sky Hours:  1h 57m  ( 1:34 AM –  3:32 AM MST)  ·  avg 3.1h  ±2.5h over lunar cycle
Night Quality Score:  5.6/10  (Lunar 4.3 · Dark Hours 3.0 · Weather 8.5 · Bortle 8.9)

Night Timeline:

  Time (MST)        Event
  ----------------  -------------------------
  May 23, 12:33 PM  Moonrise
  May 23,  7:33 PM  Sunset
  May 23,  9:18 PM  Astronomical night begins
  May 24,  1:34 AM  Moonset
  May 24,  3:32 AM  Astronomical night ends
  May 24,  5:16 AM  Sunrise

Prime Targets  ( 7:33 PM –  5:16 AM MST):

  Target                Best Viewing                                  Sky        Window
  --------------------  --------------------------------------------  ---------  -------------------------------
  Milky Way
    5.3/10  (Altitude 10.0/10  ·  Waypoints 6.2/10  ·  Window 3.6/10  ·  moon penalty)
    Visible   1:34 AM –  3:23 AM  ·  1h 48m  ·  Core 25°/25°  ·  5 of 8 waypoints visible  ·  moon-limited
    Best time      2:03 AM  —  core 25° S, arch sweeps to Cygnus Star Cloud (76° E)
  Galactic Core          2:03 AM @ 25°  179°(S)  arch 48° (moderate)  Dark sky  10:53 PM @ 11° –  3:23 AM @ 23°
  Scutum Star Cloud      3:13 AM @ 57°  178°(S)  arch 26° (flat)      Dark sky  10:03 PM @ 11° –  3:23 AM @ 57°
  Cygnus Star Cloud      3:23 AM @ 76°  92°(E)   arch 61° (steep)     Dark sky   9:43 PM @ 11° –  3:23 AM @ 76°
  Cepheus Cloud          3:23 AM @ 47°  39°(NE)  arch 76° (steep)     Dark sky   9:43 PM @ 11° –  3:23 AM @ 47°
  Perseus/Cassiopeia     3:23 AM @ 12°  27°(NE)  arch 76° (steep)     Dark sky   3:03 AM @ 11° –  3:23 AM @ 12°

  Clusters
  Hercules Cluster       1:03 AM @ 90°  343°(N)                       Moon wash   9:23 PM @ 46° –  3:23 AM @ 62°
  Wild Duck Cluster      3:13 AM @ 48°  180°(S)                       Dark sky   11:23 PM @ 22° –  3:23 AM @ 48°

  Planets
  Jupiter                7:33 PM @ 42°  268°(W)                       Moon wash   7:33 PM @ 42° –  9:13 PM @ 22°

  Nebulae
  Eagle Nebula           2:43 AM @ 40°  181°(S)                       Dark sky   11:13 PM @ 20° –  3:23 AM @ 39°
  Ring Nebula            3:13 AM @ 87°  176°(S)                       Dark sky    9:23 PM @ 20° –  3:23 AM @ 86°
  Dumbbell Nebula        3:23 AM @ 72°  133°(SE)                      Dark sky   11:03 PM @ 21° –  3:23 AM @ 72°
  Veil Nebula            3:23 AM @ 68°  96°(E)                        Dark sky   11:23 PM @ 21° –  3:23 AM @ 68°
  North America Nebula   3:23 AM @ 66°  60°(NE)                       Dark sky   10:53 PM @ 20° –  3:23 AM @ 66°

  Galaxies
  Bode's Galaxy          9:23 PM @ 49°  337°(NW)                      Moon wash   9:23 PM @ 49° –  3:13 AM @ 20°
  Sombrero Galaxy        9:23 PM @ 42°  187°(S)                       Moon wash   9:23 PM @ 42° – 12:33 AM @ 21°
  Whirlpool Galaxy       9:53 PM @ 79°  359°(N)                       Moon wash   9:23 PM @ 78° –  3:23 AM @ 30°
  Pinwheel Galaxy       10:23 PM @ 72°  1°(N)                         Moon wash   9:23 PM @ 69° –  3:23 AM @ 37°
```

## Night Quality Score (1–10)

The tool evaluates four factors and produces a composite score:

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Weather** | 40% | Cloud cover, seeing, transparency, humidity, and precipitation |
| **Lunar Interference** | 25% | 10 = new moon, 0 = full moon |
| **Dark Sky Hours** | 25% | Based on your location's typical lunar cycle; scores relative to best conditions |
| **Light Pollution** | 10% | 10 = no pollution (Bortle 1), decreases with light-polluted skies (Bortle 9) |

Weights redistribute automatically when a factor is unavailable (e.g. no weather data).

The score uses a weighted geometric mean: every factor influences the result proportionally, and a single zero factor (complete cloud cover, full moon) will zero the overall score. A factor of 1/10 with 40% weight contributes roughly 0.25× to the product, so bad factors still drag the score down significantly without needing a separate penalty term.

**Score interpretation:**
- **9–10**: Excellent — Perfect conditions for astronomy
- **7–8**: Good — Suitable for astrophotography and observing
- **5–6**: Fair — Usable but compromised (clouds, moon, or light pollution)
- **3–4**: Poor — Challenging conditions
- **1–2**: Unusable — Heavy clouds, full moon, or bad weather

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Basic: Today at your location

```bash
python pynightsky.py --location "New York"
```

Or use coordinates:

```bash
python pynightsky.py --coords 40.7128 -74.0060
```

### With weather forecast

```bash
python pynightsky.py --location "New York" --weather
```

The `Wx Rating` column scores each hour 1–10 for astrophotography suitability. Precipitation of any kind caps the score at 1. Otherwise the score is a weighted combination of:

| Factor | Weight | Notes |
|--------|--------|-------|
| Cloud cover | 50% | Non-linear — heavy cloud penalised more steeply above 50% |
| Seeing | 20% | Atmospheric steadiness; lower arcseconds = steadier |
| Transparency | 15% | Sky clarity and extinction |
| Wind speed | 10% | Vibration, tracking error, and turbulence |
| Humidity | 5% | Dew risk; no penalty below 50%, zero above 90% |

Weights redistribute automatically when a field is not available from the provider.

### Visible targets

```bash
# All visible targets tonight, grouped by type
python pynightsky.py --location "Death Valley" --targets

# Prime targets only — no moon interference, peak ≥40°, visible window ≥1h
python pynightsky.py --location "Death Valley" --prime-targets
```

Targets are grouped as: Meteor Showers · Milky Way · Clusters · Planets · Nebulae · Galaxies. Each entry shows best viewing time, peak altitude and azimuth, the full window with start/end elevations, and a **sky condition** indicating the lighting when the target peaks:

- **Dark sky** — peak falls within astronomical darkness *and* the moon is below the horizon (best conditions)
- **Astro night** — peak falls within astronomical darkness but the moon is up
- **Moon wash** — peak falls while the moon is above the horizon and bright (≥25% illuminated); sky background is significantly elevated
- **Twilight** — peak falls outside astronomical darkness (sun less than 18° below horizon)

Milky Way targets are automatically included in prime results whenever they're visible during astronomical darkness.

### Milky Way

The Milky Way section synthesises visibility across a catalog of 10 waypoints placed at uniform 36° galactic-longitude intervals, creating 5 symmetric declination pairs. Each visible waypoint represents a distinct 36° slice of the galactic plane, making the visible fraction (e.g. "5 of 8 waypoints visible") a meaningful sky-coverage metric — 8 is the maximum ever geometrically reachable from mid-northern latitudes, 10 from equatorial latitudes.

An arch summary appears above the individual waypoint rows:

```
  Milky Way
    8.8/10  (Altitude 10.0/10  ·  Waypoints 6.2/10  ·  Window 9.7/10)
    Visible  10:38 PM –  3:28 AM  ·  4h 47m  ·  Core 21°/21°  ·  5 of 8 waypoints visible
    Best time      1:18 AM  —  core 21° S, arch sweeps to Cygnus Star Cloud (84° SE)
```

**Score components** — rated relative to the best this latitude can ever offer:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Altitude** | 50% | Tonight's core peak altitude ÷ the geometric maximum from this latitude |
| **Waypoints** | 30% | Visible waypoints ÷ maximum ever visible from this latitude |
| **Window** | 20% | Moon-free arch window ÷ 5-hour reference |
| **Moon penalty** | ×0.7 | Applied when the moon clips the usable window or directly interferes with the core |

The **Core altitude ratio** (e.g. `21°/21°`) shows tonight's peak versus the latitude's geometric ceiling (`90° − |lat − (−29°)|`). Denver (40°N) can never see the core above 21°; Buenos Aires (35°S) can reach 84°; Quito (0°) reaches 61°. Identical values mean tonight is as good as it ever gets from this location.

**Moon handling** — when the moon is ≥25% illuminated:
- The arch window is clipped to the moon-free period: capped at moonrise (if the moon rises during the night) or advanced to moonset (if the moon is already up and sets mid-night)
- The `· moon-limited` flag appears on the Visible line, and `· moon penalty` in the score breakdown
- Any target whose best-viewing peak falls while the moon is up shows **Moon wash** as its sky condition
- Milky Way waypoints that straddle moonrise show direction and arch angle only — no peak time — with the Window column clipped to moonrise, eliminating any contradiction with the arch summary
- When the core's geometric peak falls outside the moon-free window, "Best time" becomes **Best before**, pointing to the last usable moment

**High-latitude note:** From latitudes where the galactic core never clears the 10° elevation floor (roughly above 51°N or below 51°S), the summary block is replaced by a "Core below horizon" note listing the visible northern or southern band waypoints.

The target catalog lives in [`targets.json`](targets.json) and is easy to extend — see [`TARGETS.md`](TARGETS.md) for the schema. Prime target thresholds and global observation defaults are in [`config.json`](config.json).

### Specific date

```bash
# Future date
python pynightsky.py --location "Sedona, Arizona" --date 2026-06-21

# Past date (for reference/analysis)
python pynightsky.py --location "Sedona, Arizona" --date 2025-06-21 --weather
```

Note: Past dates up to 92 days ago can include weather data via Open-Meteo's recent-archive API. Dates older than that fall back to the ERA5 reanalysis archive (open-meteo.com/archive), which covers years back to 1940 but may occasionally be unavailable. Astronomical events are always shown regardless of date.

### Location formats

The `--location` argument accepts any OpenStreetMap geocoding format:
- City names: `"New York"`, `"Tokyo"`, `"London"`
- Place names: `"Sedona, Arizona"`, `"Mauna Kea Observatory"`, `"Death Valley"`
- Addresses: `"1600 Pennsylvania Avenue, Washington DC"`
- Landmarks: `"Statue of Liberty"`

### Save & reuse locations

```bash
# Save coordinates under a name
python pynightsky.py --coords 40.7128 -74.0060 --save-location "home"

# Use saved location next time
python pynightsky.py --location "home"

# List all saved locations
python pynightsky.py --list-locations
```

## Trip Builder

`tripbuilder.py` compares multiple dark-sky locations across a date range — useful for planning a trip where you want to find the best location and timing for dark skies.

```bash
# Compare three locations over a month
python tripbuilder.py \
  --locations "Death Valley" "Sedona, AZ" "Grand Canyon Village, AZ" \
  --date-range 2026-06-01 2026-06-30

# Top 5 nights only, skip weather fetch
python tripbuilder.py \
  --locations "Death Valley" "Sedona, AZ" \
  --date-range 2026-06-01 2026-06-30 \
  --top 5 --no-weather
```

Output shows a **location × date score matrix** followed by a **ranked list** of the best nights across all locations:

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

**Weather** is automatically included for dates within the 16-day forecast window (`~` marker) and omitted for dates beyond it. Score weights redistribute automatically so near-future and far-future nights are scored on the same basis as the data available.

Results are cached on disk — the first run computes everything, subsequent runs for the same locations and dates are nearly instant.

### Trip Builder options

```
--locations, -l NAME [NAME ...]   One or more location names to compare (required)
--date-range, -d START END        Date range as YYYY-MM-DD YYYY-MM-DD (required)
--top, -n N                       Number of nights in the ranked list (default: 10)
--no-weather                      Astronomical factors only — skip weather fetch
--units imperial|si               Temperature/wind units (default: auto-detect)
--verbose, -v                     Print debug information
```

## pynightsky.py Options

```
--location, -l NAME        Location name or city (geocoded and cached)
--coords, -c LAT LON       Decimal-degree coordinates (e.g., -c 40.7128 -74.0060)
--date, -d YYYY-MM-DD      Date to predict (default: today)
--weather, -w              Include weather forecast (requires internet)
--targets, -t              Show all visible targets for the night
--prime-targets, -p        Show only prime targets (see config.json for thresholds)
--list-locations           Show all saved/cached locations
--save-location NAME       Save coordinates under a name for future use
--units imperial|si        Temperature/wind units (default: auto-detect from locale)
--verbose, -v              Print debug information
```

## Architecture

The project is structured as a layered engine with a thin CLI on top, making it straightforward to drive from a future web or application frontend.

| Module | Role |
|--------|------|
| `pynightsky.py` | CLI entry point — argument parsing, formatting, and output |
| `predictor.py` | Engine — assembles a `NightReport` dataclass from all data sources |
| `scoring.py` | Scoring logic — night rating and weather score calculations |
| `sky_events.py` | Astronomical primitives — sun/moon events, dark intervals, moon phase |
| `targets.py` | Visible targets engine — window computation, moon interference, per-type clipping |
| `targets.json` | Curated target catalog — nebulae, galaxies, clusters, Milky Way, planets, meteor showers |
| `config.py` | Configuration loader — reads `config.json` with built-in defaults |
| `darksky.py` | Light pollution lookup (VIIRS 2025 + Falchi 2016) |
| `weather.py` | Weather forecast abstraction (Open-Meteo providers) |
| `location.py` | Geocoding and timezone resolution |
| `tripbuilder.py` | Trip Builder CLI — matrix output and ranked list across locations and dates |
| `trip.py` | Trip planning engine — `plan_trip()` loops locations × dates, returns `TripReport` |
| `cache.py` | Disk-backed JSON cache with per-entry TTL (SHA256-keyed files) |

To use the engine directly (e.g. from a backend service), call `predictor.assemble_night()`:

```python
from datetime import date
from zoneinfo import ZoneInfo
from predictor import assemble_night

report = assemble_night(
    lat=36.4229, lon=-116.9137,
    target=date.today(),
    tz=ZoneInfo("America/Los_Angeles"),
    display_name="Death Valley",
)
print(report.score)           # overall 0–10 score
print(report.phase_name)      # e.g. "First Quarter"
print(report.dark_hours)      # moon-free dark hours tonight
print(report.weather_points)  # list of WeatherPoint dataclasses
```

## Light Pollution

Light pollution is measured using two datasets in a priority order, with the result expressed as three values:

- **SQM** (Sky Quality Meter, mag/arcsec²) — higher is darker; a truly dark site reads ~22.0
- **Bortle class** (1–9) — a standard astronomer's scale; 1 = exceptional dark sky, 9 = inner city
- **Zone** — the djlorenz Light Pollution Index, a finer-grained subdivision of the Bortle scale (e.g. Zone 1a, 2b, 3a)

### Two-tier data strategy

**Primary: VIIRS Black Marble 2025** (NASA/NOAA satellite)

Current satellite radiance data (2025). Used whenever the sensor detects a measurable signal (> ~0.2 nW/cm²/sr). This is the most up-to-date reading and reflects post-2016 light growth that older datasets miss.

**Fallback: Falchi New World Atlas 2016** (GFZ Potsdam)

A radiative-transfer physical model of artificial sky luminance. Used only when VIIRS reads zero — meaning the site is genuinely dark and below the satellite's detection floor. Unlike raw satellite data, Falchi's model propagates city-glow from all surrounding sources, so very dark sites (Bortle 1, 2, 3) get distinguishable values rather than all reading zero. A calibration factor is applied to Falchi values to align them with real-world observer SQM measurements and IDA dark-sky park classifications.

## License

MIT License - See [LICENSE](LICENSE) for details.

Development assisted by GitHub Copilot and Claude.

## Data Download & Caching

The application automatically downloads and caches external datasets:

- **VIIRS Black Marble 2025** (Satellite light pollution data)
- **Falchi World Atlas 2016** (Physical light pollution model)
- **Nominatim Geocoding** (Location name resolution)

These datasets are downloaded **on first use** and cached locally in `~/.pynightsky-predictor/` for offline access.

### Bundled Ephemeris

The file `de421.bsp` is the **JPL DE421 planetary ephemeris**, bundled directly in this repository. It is used by [Skyfield](https://rhodesmill.org/skyfield/) to compute precise sun and moon positions, rise/set times, and moon phase angles. DE421 covers years 1900–2050 and is released by NASA/JPL as a public-domain dataset.

No download or internet access is required for ephemeris data — it is included with the project.

### Data Source Attribution

All datasets remain under their original open licenses and attributions (see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)):
- DE421 Ephemeris: NASA/JPL (Public Domain)
- VIIRS: NASA/NOAA (Public Domain)
- Falchi: GFZ Potsdam (ODbL with attribution)
- Nominatim: OpenStreetMap contributors (ODbL)

### Fair Use

This project uses these datasets for non-commercial research and educational purposes. Commercial users should review the respective source terms:
- DE421/NASA: Public domain, free for all uses
- VIIRS/NASA: Free for most uses
- Falchi: Academic citation required
- OSM/Nominatim: Attribution required; share-alike if redistributing

For details, see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
