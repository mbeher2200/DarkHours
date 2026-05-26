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
- **Moon** — Phase, percent illumination, and Earth-Moon distance at sunset. Supermoon and micromoon events are flagged inline. Any lunar eclipse whose mid-point falls during the night is shown with its type (penumbral / partial / total) and magnitude
- **Prime Dark Sky Hours** — Effective dark hours within astronomical darkness tonight, adjusted for actual sky impact using the Krisciunas & Schaefer moonlight model (see [Moonlight Modeling](#moonlight-modeling-krisciunas--schaefer-1991)). When the moon is ≤20% illuminated its scattered light is negligible and the full astronomical window is reported; brighter phases use the geometric moon-free window. The average and standard deviation across the current 30-night lunar cycle are shown alongside for context.
- **Weather** — Hourly cloud cover, seeing, transparency, temperature, humidity, wind, and precipitation — each hour rated 1–10 for astrophotography conditions (with `--weather`)
- **Visible Targets** — What's observable tonight, grouped by type (with `--targets` or `--prime-targets`)
- **Month Calendar** — A full-month view of night scores, prime dark hours, weather, and lunar conditions — one row per night, best nights highlighted at the bottom (with `--calendar`)

Example output (`python pynightsky.py --location "Grand Canyon Village, Arizona" --date 2026-05-14 --prime-targets --weather`):
```
Date:               2026-05-14
Location:           Grand Canyon Village, Coconino County, Arizona, United States  (36.0578°, -112.1282°)
Light Pollution:    SQM 21.9  ·  Zone 2a  ·  Bortle 2  (Truly dark sky)  [Falchi 2016]
Moon:               Waning Crescent  |  4.4% illuminated
Prime Dark Sky Hours:  6h 33m  ( 9:07 PM –  3:41 AM MST)  ·  avg 3.1h  ±2.5h over lunar cycle
Night Quality Score:  9.4/10  (Lunar 10.0 · Dark Hours 10.0 · Weather 8.9 · Bortle 8.9)

Night Timeline:

  Time (MST)        Event
  ----------------  -------------------------
  May 14,  7:26 PM  Sunset
  May 14,  9:07 PM  Astronomical night begins
  May 15,  3:41 AM  Astronomical night ends
  May 15,  4:11 AM  Moonrise
  May 15,  5:22 AM  Sunrise

Weather:

  Time (MST)        Wx Rating  Cloud  Temp  Feels  Humid     Wind  Precip
  ----------------  ---------  -----  ----  -----  -----  -------  ------
  May 14,  7:00 PM       9/10     0%  70°F   60°F    12%   9.4mph  None
  May 14,  8:00 PM      10/10     0%  66°F   57°F    14%   7.0mph  None
  May 14,  9:00 PM       5/10    40%  62°F   51°F    16%  11.4mph  None
  May 14, 10:00 PM       9/10     0%  60°F   49°F    18%  13.1mph  None
  May 14, 11:00 PM       9/10     0%  59°F   47°F    18%  15.1mph  None
  May 15, 12:00 AM       9/10     0%  55°F   43°F    18%  15.6mph  None
  May 15,  1:00 AM       9/10     0%  54°F   43°F    20%  12.1mph  None
  May 15,  2:00 AM       9/10     0%  54°F   42°F    18%  13.5mph  None
  May 15,  3:00 AM       9/10     0%  50°F   39°F    28%  12.9mph  None
  May 15,  4:00 AM       9/10     0%  48°F   38°F    30%  11.1mph  None
  May 15,  5:00 AM       9/10     0%  48°F   38°F    34%  10.2mph  None
  May 15,  6:00 AM       9/10     0%  49°F   39°F    31%  10.9mph  None

Prime Targets  ( 7:26 PM –  5:22 AM MST):

  Milky Way: 9.0/10  (Altitude 10.0/10  ·  Waypoints 7.5/10  ·  Window 8.5/10)
  Visible  11:26 PM –  3:41 AM  ·  4h 12m  ·  Core 25°/25°  ·  6 of 8 waypoints visible
  Best time      2:46 AM  —  core 25° S, arch sweeps to Cygnus Star Cloud (72° E)


  Target                Best Viewing                                  Sky       Astrophotography Window
  --------------------  --------------------------------------------  --------  -------------------------------

  Galactic Core          2:46 AM @ 25°  181°(S)  arch 50° (moderate)  Dark sky  11:26 PM @ 10° –  3:41 AM @ 24°
  Cygnus Star Cloud      3:41 AM @ 72°   89°(E)  arch 56° (moderate)  Dark sky  10:16 PM @ 10° –  3:41 AM @ 72°
  Scutum Star Cloud      3:41 AM @ 57°  174°(S)  arch 25° (flat)      Dark sky  10:36 PM @ 10° –  3:41 AM @ 57°
  Cepheus Cloud          3:41 AM @ 45°   39°(NE)  arch 73° (steep)    Dark sky  10:16 PM @ 10° –  3:41 AM @ 45°

  Clusters
  Beehive Cluster        9:07 PM @ 42°  265°(W)                       Dark sky   9:07 PM @ 42° – 10:46 PM @ 22°
  Hercules Cluster       1:36 AM @ 89°   48°(NE)                      Dark sky   9:07 PM @ 36° –  3:41 AM @ 65°
  Wild Duck Cluster      3:41 AM @ 48°  178°(S)                       Dark sky  11:56 PM @ 21° –  3:41 AM @ 48°

  Planets
  Jupiter                7:26 PM @ 49°  263°(W)                       Twilight   7:26 PM @ 49° –  9:46 PM @ 21°

  Nebulae
  Eagle Nebula           3:16 AM @ 40°  180°(S)                       Dark sky  11:56 PM @ 21° –  3:41 AM @ 40°
  Ring Nebula            3:41 AM @ 86°  148°(SE)                      Dark sky  10:06 PM @ 22° –  3:41 AM @ 86°
  Dumbbell Nebula        3:41 AM @ 69°  124°(SE)                      Dark sky  11:36 PM @ 21° –  3:41 AM @ 69°
  Veil Nebula            3:41 AM @ 64°   93°(E)                       Dark sky  11:56 PM @ 20° –  3:41 AM @ 64°
  North America Nebula   3:41 AM @ 63°   61°(NE)                      Dark sky  11:36 PM @ 21° –  3:41 AM @ 63°

  Galaxies
  Leo Triplet            9:07 PM @ 64°  209°(SW)                      Dark sky   9:07 PM @ 64° –  1:06 AM @ 22°
  Bode's Galaxy          9:07 PM @ 52°  341°(N)                       Dark sky   9:07 PM @ 52° –  3:41 AM @ 21°
  Sombrero Galaxy        9:36 PM @ 42°  180°(S)                       Dark sky   9:07 PM @ 42° –  1:06 AM @ 21°
  Whirlpool Galaxy      10:26 PM @ 79°    1°(N)                       Dark sky   9:07 PM @ 71° –  3:41 AM @ 33°
  Pinwheel Galaxy       10:56 PM @ 72°    2°(N)                       Dark sky   9:07 PM @ 63° –  3:41 AM @ 40°
```

## Night Quality Score (1–10)

The tool evaluates four factors and produces a composite score:

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Weather** | 40% | Cloud cover, seeing, transparency, humidity, and precipitation |
| **Lunar Interference** | 25% | K&S sky-brightening credit at 90° separation, 30° altitude — 10 = new moon, ≈0 = gibbous or full; crescent moons score near 10 even when above the horizon |
| **Dark Sky Hours** | 25% | Based on your location's typical lunar cycle; scores relative to best conditions |
| **Light Pollution** | 10% | 10 = no pollution (Bortle 1), decreases with light-polluted skies (Bortle 9) |

Weights redistribute automatically when a factor is unavailable (e.g. no weather data).

The score uses a weighted geometric mean: every factor influences the result proportionally, and a single zero factor (complete cloud cover, full moon) will zero the overall score. A factor of 1/10 with 40% weight contributes roughly 0.25× to the product, so bad factors still drag the score down significantly without needing a separate penalty term.

See [Moonlight Modeling](#moonlight-modeling-krisciunas--schaefer-1991) for how the Lunar Interference factor is computed.

**Score interpretation:**
- **9–10**: Excellent — Perfect conditions for astronomy
- **7–8**: Good — Suitable for astrophotography and observing
- **5–6**: Fair — Usable but compromised (clouds, moon, or light pollution)
- **3–4**: Poor — Challenging conditions
- **1–2**: Unusable — Heavy clouds, full moon, or bad weather

## Moonlight Modeling (Krisciunas & Schaefer 1991)

PyNightSkyPredictor models scattered moonlight using the empirical photometric model of **Krisciunas, K. & Schaefer, B. E. (1991)**, *"A model of the brightness of moonlight,"* Publications of the Astronomical Society of the Pacific, 103(667), 1033–1039. [https://doi.org/10.1086/132921](https://doi.org/10.1086/132921)

The model computes the sky surface brightness increase (Δ mag/arcsec²) at any sky position given the moon's illumination, altitude, and angular separation from the target. It accounts for the moon's phase-dependent luminosity, atmospheric extinction along the moon's air-mass path, and a scattering phase function that produces the characteristic brightening both near the moon *and* at the antisolar point (~180° away).

### Why it matters

A simple moonrise/moonset boundary treats all moon phases identically — a 5% crescent and a 90% gibbous both count as equally "moon-up." K&S makes the distinction physically meaningful:

| Phase | Δmag at 90° sep, alt 30° | Impact |
|---|---|---|
| 5% crescent | 0.06 | Imperceptible |
| 15% crescent | 0.21 | Minor |
| 50% quarter | 1.03 | Severe |
| 75% gibbous | 1.73 | Severe |
| 100% full | 3.16 | Very severe |

The transition from negligible to severe is sharp — it occurs between roughly 20% and 30% illumination. This means a waxing or waning crescent moon being "above the horizon" is not meaningfully different from a moonless night.

### Severity thresholds

| Threshold | Δmag/arcsec² | Meaning |
|---|---|---|
| Imperceptible | < 0.10 | No practical effect on deep-sky imaging |
| Minor | 0.10 – 0.50 | Slight brightening; faint nebulae and galaxies unaffected |
| Moderate | 0.50 – 1.50 | Noticeable; low-surface-brightness targets impacted |
| Severe | ≥ 1.50 | Sky substantially brighter; deep DSO imaging limited |

### Proxy geometry for site-wide evaluation

K&S is inherently directional — it depends on where in the sky you're looking relative to the moon. For site-wide metrics (night score, prime dark sky hours) a reference sky position is needed. PyNightSkyPredictor uses **90° separation at 30° altitude** as the proxy:

- **90° separation** is the darkest accessible sky position: the K&S scattering function reaches its minimum there (the cos²ρ term vanishes), so 90° represents the best realistic observing position when the moon is up — not the worst case and not an unreachable antipode
- **30° altitude** is a representative mid-sky moon position over the course of an evening

For per-target evaluation, the actual moon–target separation and moon altitude are computed from the Skyfield ephemeris at each 20-minute sample window.

### How it affects the output

**Lunar Interference score** — The moon-up fraction of the astronomical night is weighted by the K&S credit at the proxy geometry rather than the naive `(1 − illumination/100)` formula. A quarter moon's moonlit hours receive 0.31 credit (down from 0.50); a gibbous moon's moonlit hours receive 0 (down from 0.25).

**Prime Dark Sky Hours** — When illumination is ≤20% (imperceptible-to-minor impact at any altitude), the full astronomical window is reported as prime dark sky time rather than subtracting the brief crescent-up intervals.

**Astrophotography Window per target** — For each target, K&S is evaluated at the actual moon–target separation and moon altitude at every sample. The photo window is clipped at the point where Δmag exceeds the per-type contrast threshold (nebulae/galaxies: surface brightness minus sky background minus 3.2 mag; clusters: integrated magnitude minus site SQM minus 13.0; Milky Way: surface brightness minus sky background minus 1.5 mag).

**Light pollution interaction** — The site's SQM (sky quality meter reading) enters the K&S denominator as the natural-sky baseline. On a darker site the same moon produces less fractional brightening; on a light-polluted site the moon adds less on top of what is already a degraded sky.

**Earth-Moon distance correction** — K&S (1991) assumes the Moon at its mean distance of 384,400 km. The actual distance varies ±8.5% over the lunar orbit, which translates to up to ±0.35 mag/arcsec² error on supermoon and micromoon nights. PyNightSkyPredictor corrects for this via the inverse-square law: the modelled lunar irradiance is scaled by `(mean_dist / actual_dist)²` at every sample. The actual distance is obtained from the Skyfield ephemeris and is automatically applied to both the site-wide score and per-target K&S evaluations.

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

- **Dark sky** — peak falls within astronomical darkness *and* K&S sky brightening is below the moderate threshold (Δmag < 0.50); best conditions
- **Astro night** — peak falls within astronomical darkness but K&S indicates minor moon interference
- **Moon wash** — K&S sky brightening at the target's position is ≥0.50 mag/arcsec² (moderate or severe); sky background is significantly elevated
- **Twilight** — peak falls outside astronomical darkness (sun less than 18° below horizon)

The **Astrophotography Window** column shows the time span during which K&S-modelled sky conditions are good enough for imaging. When scattered moonlight degrades the sky past the photo threshold, the window is clipped at the start or end accordingly.

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

**Moon handling** — K&S sky-brightening is sampled at each waypoint's position throughout the night. When scattered moonlight degrades a waypoint past the photo threshold (Δmag ≥ 0.50 relative to the site's sky background):
- The arch window is clipped at the first/last photo-viable sample (`photo_start` / `photo_cutoff`). When K&S runs and finds the entire window viable, no clipping is applied — the geometric moonrise/moonset heuristic is not used, preventing false cutoffs from thin crescent moons
- The `· moon-limited` flag appears on the Visible line, and `· moon penalty` in the score breakdown
- Any target whose best-viewing peak exceeds the K&S moderate threshold shows **Moon wash** as its sky condition
- Milky Way waypoints that straddle the K&S cutoff show direction and arch angle only — no peak time — with the Window column clipped accordingly
- When the core's geometric peak falls outside the K&S-viable window, "Best time" becomes **Best before**, pointing to the last usable moment

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

## Month Calendar

The `--calendar` flag shows a full-month view for a single location — useful for answering "what are my best nights this month?" without running the trip planner.

```bash
# Current month
python pynightsky.py --location "Grand Canyon Village, Arizona" --calendar

# Specific month
python pynightsky.py --location "Grand Canyon Village, Arizona" --calendar --date 2026-03

# Include weather scores for dates within the 16-day forecast window
python pynightsky.py --location "Grand Canyon Village, Arizona" --calendar --weather
```

Each row shows one night. The **Moon** column shows the lunar interference score (0–10, same component as the Night Quality Score breakdown) and flags any special events inline:

```
Calendar — Grand Canyon Village, Coconino County, Arizona, United States
Light Pollution:    SQM 21.9  ·  Zone 2a  ·  Bortle 2  (Truly dark sky)  [Falchi 2016]  ·  Score 8.9/10
March 2026

  Date        Night Quality Score  Prime Dark Hours  Weather  Moon
  ----------  -------------------  ----------------  -------  ----
  2026-03-01               0.0/10            0h 00m        —  0.0
  2026-03-02               0.0/10            0h 00m        —  0.0  ·  *** Total lunar eclipse at  4:33 AM  (mag umbral 1.149) ***
  2026-03-03               0.0/10            0h 00m        —  0.0
  2026-03-04               0.4/10            0h 10m        —  0.2
  ...
  2026-03-15               9.4/10            9h 10m        —  10.0
  2026-03-19               9.8/10            9h 00m        —  10.0
  ...
  2026-03-31               0.0/10            0h 00m        —  0.0

  Best nights:  Mar 19 (9.8/10)  ·  Mar 15 (9.4/10)  ·  Mar 16 (9.4/10)
```

The **Light Pollution** header line appends the location's Bortle score contribution (0–10) so you can immediately see how much light pollution is costing you on every night.

Scores in the calendar are identical to those from the single-night report for the same date — the same engine runs both.

## pynightsky.py Options

```
--location, -l NAME        Location name or city (geocoded and cached)
--coords, -c LAT LON       Decimal-degree coordinates (e.g., -c 40.7128 -74.0060)
--date, -d DATE            Date to predict (YYYY-MM-DD, default: today);
                           with --calendar, accepts YYYY-MM to pick a month
--calendar                 Show a month-view calendar of night scores
--weather, -w              Include weather forecast (requires internet)
--targets, -t              Show all visible targets for the night
--prime-targets, -p        Show only prime targets (see config.json for thresholds)
--list-locations           Show all saved/cached locations
--save-location NAME       Save coordinates under a name for future use
--units imperial|si        Temperature/wind units (default: auto-detect from locale)
--verbose, -v              Print debug information
```

## Architecture

The project is split into three layers: a pure engine, a formatting context, and thin CLI shells. The engine layer has no print statements and returns only dataclasses, so it can be called directly from a web backend or any other frontend.

**Engine layer** — no I/O, returns dataclasses:

| Module | Role |
|--------|------|
| `predictor.py` | Assembles a `NightReport` dataclass from all data sources |
| `scoring.py` | Night rating and weather score calculations |
| `sky_events.py` | Astronomical primitives — sun/moon events, dark intervals, moon phase |
| `moonlight.py` | Krisciunas & Schaefer (1991) moonlight model — Δmag sky brightening, moon credit, severity thresholds, per-type contrast constants |
| `moon_events.py` | Lunar distance, eclipse detection (`eclipselib`), and supermoon/micromoon classification |
| `milky_way.py` | Galactic coordinate helpers (IAU matrix) and Milky Way arch synthesis |
| `targets.py` | Visible targets engine — window computation, K&S moonlight interference, per-type photo/visual clipping |
| `targets.json` | Curated target catalog — nebulae, galaxies, clusters, Milky Way, planets, meteor showers |
| `config.py` | Configuration loader — reads `config.json` with built-in defaults |
| `darksky.py` | Light pollution lookup (VIIRS 2025 + Falchi 2016) |
| `weather.py` | Weather forecast abstraction (Open-Meteo providers) |
| `location.py` | Geocoding and timezone resolution |
| `trip.py` | Trip planning engine — `plan_trip()` loops locations × dates, returns `TripReport` |
| `cache.py` | Disk-backed JSON cache with per-entry TTL (SHA256-keyed files) |

**Formatting layer** — timezone/unit conversion, locale detection:

| Module | Role |
|--------|------|
| `format_ctx.py` | `FormatCtx` dataclass — bundles timezone and unit system; instantiated once per CLI invocation or per web request (no shared state) |

**Render layer** — terminal output only; each module receives a dataclass and prints to stdout:

| Module | Role |
|--------|------|
| `render_report.py` | Single-night report and targets table (`print_report`, `print_targets`) |
| `render_calendar.py` | Month calendar view (`print_calendar`) |
| `render_trip.py` | Trip matrix and ranked list (`print_matrix`, `print_ranked`) |

**CLI shells** — argument parsing and orchestration only:

| Module | Role |
|--------|------|
| `pynightsky.py` | Single-night and calendar CLI |
| `tripbuilder.py` | Trip Builder CLI |

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

## Testing

```bash
# Full suite — 105 tests (requires de421.bsp, included in the repo)
python -m pytest

# Fast suite — 94 tests, pure math only, no ephemeris needed
python -m pytest -m "not eph"

# Verbose output
python -m pytest -v
```

Coverage spans the core physics and scoring layers:

| Test file | What's covered |
|---|---|
| `test_moonlight.py` | `ks_delta_mag` (including distance correction), `ks_moon_credit` monotonicity, `moon_wash_severity` thresholds |
| `test_scoring.py` | `rate_night` geometric mean formula, weight redistribution, `weighted_weather_score` 3× darkness weighting |
| `test_milky_way.py` | `gal_to_radec` IAU rotation matrix, `mw_max_visible`, `mw_theoretical_core_max` |
| `test_moon_events.py` | `classify_full_moon` thresholds + eclipse integration against known 2026 events |
| `test_sky_events.py` | `dark_moon_intervals`, `find_event`, moon phase, sunset timing (ephemeris) |
| `test_mw_geometry.py` | Five-location Milky Way geometry regression (Whitehorse → Ushuaia) |

Tests marked `@pytest.mark.eph` require the bundled `de421.bsp` and are skipped by `-m "not eph"`. All other tests are pure math with no network or file dependencies.

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
- Krisciunas & Schaefer moonlight model: cited below (academic use)

### Fair Use

This project uses these datasets for non-commercial research and educational purposes. Commercial users should review the respective source terms:
- DE421/NASA: Public domain, free for all uses
- VIIRS/NASA: Free for most uses
- Falchi: Academic citation required
- OSM/Nominatim: Attribution required; share-alike if redistributing

For details, see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

### Scientific Reference

The moonlight scattering model is based on:

> Krisciunas, K. & Schaefer, B. E. (1991). "A model of the brightness of moonlight."
> *Publications of the Astronomical Society of the Pacific*, 103(667), 1033–1039.
> DOI: [10.1086/132921](https://doi.org/10.1086/132921)

The paper derives an empirical model for sky surface brightness as a function of lunar phase angle, moon altitude, and angular separation from the observed position, using V-band photometry and a standard atmospheric extinction coefficient. The model is widely used in observational astronomy for planning and site evaluation.
