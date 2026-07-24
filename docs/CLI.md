# darkhours.py Reference

Single-night reports, monthly calendars, and nearby dark-sky search for one location.

```bash
python darkhours.py --location "Grand Canyon Village, AZ" --date 2026-08-12 --targets --weather
python darkhours.py --location "Roswell, GA" --show-nearby
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --date 2026-08
```

---

## Contents

- [Options](#options)
- [Output](#output)
- [Examples](#examples)
- [Night Quality Score](#night-quality-score)
- [Moonlight Modeling](#moonlight-modeling-krisciunas--schaefer-1991)
- [Clear Dark Sky Hours](#clear-dark-sky-hours)
- [Weather](#weather)
- [Targets](#targets)
- [Milky Way](#milky-way)
- [Nearby Skies](#nearby-skies)
- [Month Calendar](#month-calendar)
- [Light Pollution](#light-pollution)
- [Location Formats](#location-formats)
- [Past Dates & Historical Weather](#past-dates--historical-weather)

---

## Options

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

You need one of `--location` or `--coords`.

---

## Output

Every run prints a single-night report:

- **Night Quality Score** (1 to 10). A composite of lunar interference, dark hours, weather, and light pollution.
- **Night Timeline.** Sunset, astronomical night begin and end, moonrise and set, sunrise.
- **Light Pollution.** SQM, Bortle class, and djlorenz zone for the coordinates.
- **Moon.** Phase, illumination, distance. Supermoon and micromoon flags. Eclipse type and magnitude when one applies.
- **Meteor Showers.** Active showers with a peak note and ZHR, always shown, no flag needed. The engine also models day-decayed, radiant-altitude-corrected local rates. See [TARGETS.md](TARGETS.md#meteor-shower-zhr-decay-model).
- **Clear Dark Sky Hours.** Effective dark time, adjusted for cloud and corrected for the moon, with the lunar-cycle average alongside for context.

`--weather` adds an hourly conditions table: cloud cover, seeing, transparency, wind (speed and direction), dew point, feels-like, humidity, and precipitation. Each hour gets rated 1 to 10 for astrophotography.

`--targets` adds prime targets by type (Milky Way, clusters, planets, nebulae, galaxies, meteor showers) with visibility windows and moon-interference clipping.

`--satellites` adds a single pass table for ISS, Hubble Telescope, Tiangong, and any Starlink train still raising. Each row shows rise, peak, and set times with altitude, azimuth, pass duration, and moon separation. Twilight passes get a `†`. Passes that end in Earth's shadow get a `*`.

`--show-nearby` adds a table of named darker sky areas and light domes inside the search radius. The search is POI-first. When the routable OSM POI index is present (see [Architecture, Offline Spatial Index (OSM POIs)](ARCHITECTURE.md#offline-spatial-index-osm-pois)), it surfaces named, reachable destinations that sit on a dark pixel: trailhead parking, viewpoints, campsites, observatories, and the like, instead of raw off-road coordinates. Areas with no routable POI fall back to a plain coordinate flagged as remote. Results come back at least one Bortle class darker than the origin, capped at Bortle 3, so an already-dark origin like a Bortle 3 site still surfaces the reachable Bortle 2 areas nearby. Drive times and road distances only compute on the cloud (AWS) deployment. See [Cloud Deployment](DEPLOYMENT.md).

`--all` is shorthand for `--weather --targets --satellites --show-nearby` in one flag.

`--calendar` swaps the single-night report for a full-month score grid.

---

## Examples

### Single night with targets, weather, and nearby search

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

### Satellite passes

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

### Monthly calendar

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
  2026-06-11               7.8/10            6h 00m        —  9.9
  2026-06-12               8.3/10            5h 58m        —  10.0
  2026-06-13               8.3/10            5h 58m        —  10.0
  2026-06-14               8.3/10            5h 58m        —  10.0
  2026-06-27               0.0/10            0h 00m        —  0.0
  2026-06-28               0.0/10            0h 00m        —  0.0  ·  *** Micromoon ***

  Best nights:  Jun 12 (8.3/10)  ·  Jun 13 (8.3/10)  ·  Jun 14 (8.3/10)
```

---

## Night Quality Score

The Night Quality Score (1 to 10) is a composite of four factors:

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Weather** | 40% | Cloud cover, seeing, transparency, humidity, and precipitation |
| **Lunar Interference** | 25% | K&S sky-brightening credit at 90° separation, 30° altitude. 10 = new moon, ≈0 = gibbous or full |
| **Dark Sky Hours** | 25% | Based on your location's typical lunar cycle, scored against its best conditions |
| **Light Pollution** | 10% | 10 = Bortle 1 (no pollution), dropping toward zero at Bortle 9 (inner city) |

When a factor is missing, the weights shift on their own. Lose weather data, for example, and Dark Sky Hours and Lunar each pick up part of that 40%.

**Formula, a weighted geometric mean:**

```
score = (weather^0.40) × (lunar^0.25) × (dark_hours^0.25) × (light_pollution^0.10)
```

A geometric mean lets every factor pull its own weight. One zero factor, full cloud or full moon, zeros the whole score. A factor of 1/10 at 40% weight multiplies the product by roughly 0.25, so a bad factor drags the score down hard with no separate penalty term bolted on.

**Score interpretation:**

| Score | Tier | Meaning |
|-------|------|---------|
| 9–10 | Excellent | Ideal conditions for astronomy |
| 7–8 | Good | Fine for astrophotography and observing |
| 5–6 | Fair | Usable but compromised (clouds, moon, or light pollution) |
| 3–4 | Poor | Challenging conditions |
| 1–2 | Unusable | Heavy clouds, full moon, or severe weather |
| 0 | Pass | Full cloud cover or full moon. No viable window |


---

## Moonlight Modeling (K&S 1991 × Winkler 2022 hybrid)

DarkHours models scattered moonlight with a hybrid of two photometric models:

- **Krisciunas, K. & Schaefer, B. E. (1991)**, *"A model of the brightness of moonlight,"* PASP 103(667), 1033–1039. [doi:10.1086/132921](https://doi.org/10.1086/132921). This gives the phase-dependent lunar luminosity and the optical-pathlength form.
- **Winkler, H. (2022)**, *"A revised simplified scattering model for the moonlit sky brightness profile based on photometry at SAAO,"* MNRAS 514(1), 208–226. [doi:10.1093/mnras/stac1387](https://doi.org/10.1093/mnras/stac1387). This gives the single-scatter kernel with correct lunar-beam extinction, plus the two-component Rayleigh + Henyey–Greenstein (g = 0.8) phase function.

The model computes how much the sky surface brightness rises (Δ mag/arcsec²) at any sky position. It takes the moon's illumination, altitude, angular separation from the target, the target's own altitude (slant path), and the atmosphere's aerosol load. Extinction is a live optical-depth decomposition (Rayleigh + aerosol + ozone), not a fixed coefficient. The aerosol optical depth (AOD) forecast from Open-Meteo's air-quality API (CAMS) feeds in as the night's median. So wildfire smoke and haze both dim the lunar beam and amplify the forward-scattered aureole near the moon. Smoke brightens the sky close to the moon while dimming it far away. When AOD isn't available (past dates, a fetch failure, or beyond the roughly 7-day air-quality horizon) the model falls back to a reference clear sky whose extinction equals the classic k = 0.172 exactly. The normalisation anchors the new kernel to the legacy K&S intensity at the site-wide proxy geometry, so nightly planning scores stay unchanged at reference conditions. `scripts/verify_moonwash_grid.py` verifies this.

### Why it matters

A plain moonrise and moonset boundary treats every moon phase the same. A 5% crescent and a 90% gibbous both just count as "moon up." K&S makes the difference physically real:

| Phase | Δmag at 90° sep, 30° alt | Impact |
|-------|--------------------------|--------|
| 5% crescent | 0.06 | Imperceptible |
| 15% crescent | 0.21 | Minor |
| 50% quarter | 1.03 | Severe |
| 75% gibbous | 1.73 | Severe |
| 100% full | 3.16 | Very severe |

The jump from negligible to severe is sharp, roughly between 20% and 30% illumination. A waxing crescent above the horizon is barely different from a moonless night.

### Severity thresholds

| Threshold | Δmag/arcsec² | Meaning |
|-----------|--------------|---------|
| Imperceptible | < 0.10 | No practical effect on deep-sky imaging |
| Minor | 0.10 – 0.50 | Slight brightening; faint nebulae unaffected |
| Moderate | 0.50 – 1.50 | Noticeable; low-surface-brightness targets impacted |
| Severe | ≥ 1.50 | Sky substantially brighter; deep DSO imaging limited |

### Proxy geometry for site-wide evaluation

K&S is directional by nature. It depends on where you look relative to the moon. Site-wide metrics like the night score and clear dark sky hours need a reference sky position. DarkHours uses 90° separation at 30° altitude as the proxy:

- **90° separation** is the darkest sky position you can get. The scattering function bottoms out there (the cos²ρ term vanishes), which is the best realistic spot when the moon is up.
- **30° altitude** is a fair mid-sky moon position over an evening.

For per-target evaluation, the actual moon-to-target separation, moon altitude, and target altitude come from the Skyfield ephemeris at each 10-minute sample.

### How it affects the output

**Lunar Interference score.** The moon-up fraction of astronomical night is weighted by the K&S credit at the proxy geometry, not by `(1 − illumination/100)`. A quarter moon's moonlit hours get 0.31 credit (down from 0.50). A gibbous moon's moonlit hours get 0 (down from 0.25).

**Clear Dark Sky Hours.** When illumination sits at 20% or below (imperceptible to minor impact at any altitude), the full astronomical window counts as dark sky time. With weather data on hand, each dark interval gets clipped further to hours where cloud cover is 30% or less.

**Astro Window per target.** The model runs at the actual moon-to-target separation, moon altitude, and target altitude at every 10-minute sample. The window clips when Δmag passes the per-type contrast threshold (nebulae/galaxies: surface brightness − sky background − 3.2 mag; clusters: integrated magnitude − site SQM − 13.0; Milky Way: surface brightness − sky background − 1.5 mag).

**Light pollution interaction.** The site's SQM enters the K&S denominator as the natural-sky baseline. On a darker site the same moon adds less fractional brightening. On a light-polluted site the moon piles onto a sky that's already degraded.

**Earth-Moon distance correction.** K&S (1991) assumes the Moon at its mean distance of 384,400 km. The real distance swings ±8.5%, worth up to ±0.35 mag/arcsec² on supermoon and micromoon nights. DarkHours corrects with the inverse-square law: lunar irradiance scales by `(mean_dist / actual_dist)²` at every sample, applied to both the site-wide score and per-target evaluations.

**Meteor shower local rates.** `local_rate_at_peak` applies the standard IMO visual-rate correction on top of the decay model and radiant geometry: rate = ZHR_effective × sin(radiant alt) × min(1, r^(lm − 6.5)), where lm is the naked-eye limiting magnitude (NELM = 7.93 − 5·log₁₀(10^(4.316 − SQM/5) + 1)) under the moon-brightened site sky and r is the shower's magnitude-distribution (population) index from the catalog. Faint-meteor-rich showers (Delta Aquariids, r = 3.2) collapse under moonlight or city skies far harder than fireball-rich ones (Perseids, r = 2.2).

**Aurora moon factor.** Aurora is an emission source, so moonlight raises the background it competes against rather than washing out the source. The aurora condition vector degrades but never blocks, tier-scaled: photographic-tier nights at Δ ≥ 0.50 mag/arcsec², naked-eye at ≥ 1.50, and overhead storms punch through any moon.

**Deliberate AOD exclusions.** `ks_moon_credit` (moon_score, calendar dark-cycle scores) always runs at the reference sky. Planning scores can't wobble with 30-minute weather refetches, and the calendar path fetches no weather at all.

---

## Clear Dark Sky Hours

Effective dark sky time is the overlap of three windows:

1. **Astronomical darkness.** Sun more than 18° below the horizon (from the Skyfield ephemeris).
2. **Moon-free periods.** K&S moonlight at 0.10 Δmag or less at the proxy geometry, or illumination at 20% or less (the crescent threshold).
3. **Clear sky.** When weather data is on hand, cloud cover at 30% or less during the dark window.

The output shows tonight's hours next to a lunar-cycle average with its standard deviation, so you can see how typical tonight is for this spot:

```
Clear Dark Sky Hours:  6h 7m  (10:34 PM –  4:41 AM EDT)  ·  avg 3.0h  ±2.1h over lunar cycle
```

---

## Weather

### `--weather` flag

Adds an hourly conditions table across the night window:

```
Weather  [NOAA/NWS + 7Timer]:

  Time (MST)        Wx Rating  Cloud Cover  Temp  Dew Pt  Feels  Seeing        Transparency  Humidity      Wind  Precip
  ----------------  ---------  -----------  ----  ------  -----  ------------  ------------  --------  --------  ------
  Aug 12,  9:00 PM       9/10           3%  80°F    55°F   80°F  8/10 (0.87")         10/10       40%    5mph S  None
  ...
```

| Column | Description |
|--------|-------------|
| **Wx Rating** | 1 to 10 astrophotography score for that hour |
| **Cloud Cover** | Percentage sky coverage |
| **Temp** | Air temperature at 2 m |
| **Dew Pt** | Dew point. A dew point near Temp means high moisture and dew risk |
| **Feels** | Apparent temperature (wind chill or heat index) |
| **Seeing** | Atmospheric steadiness as N/10 plus an arcsecond value. Lower arcseconds mean steadier |
| **Transparency** | Sky clarity and extinction as N/10 |
| **Humidity** | Relative humidity at 2 m |
| **Wind** | Speed and compass direction, e.g. `12mph SW` |
| **Precip** | Precipitation type: None, Rain, or Snow |

### Wx Rating formula

A weighted combination of every hourly parameter available:

| Factor | Weight | Notes |
|--------|--------|-------|
| Cloud cover | 50% | Non-linear. Heavy cloud is penalised harder above 50% |
| Seeing | 20% | Atmospheric steadiness |
| Transparency | 15% | Sky clarity and extinction |
| Wind speed | 10% | Vibration, tracking error, turbulence |
| Humidity | 5% | Dew risk. No penalty below 50%, zero score above 90% |

Any precipitation caps the Wx Rating at 1. Weights redistribute on their own when a field is missing.

### Providers

| Provider | Coverage | Used for |
|----------|----------|---------|
| **NOAA/NWS** | US locations only | Primary for the US. NAM-based, with accurate cloud percentages, wind chill, and heat index |
| **Open-Meteo** | Global | Primary outside the US. Also covers past dates up to 92 days (recent archive) and older dates via ERA5 reanalysis back to 1940 |
| **7Timer ASTRO** | Global | Blended in for seeing and transparency, derived from Cn² profile integration through GFS. The only free, scientifically grounded seeing source |

---

## Targets

The `--targets` flag shows prime targets for the night: no meaningful moon interference, peak altitude 40° or higher, visible window an hour or longer. Targets group by type: Meteor Showers, Milky Way, Clusters, Planets, Nebulae, Galaxies.

### Sky condition tags

Each target's sky condition reflects the lighting when the target peaks:

| Tag | Meaning |
|-----|---------|
| **Dark sky** | Peak within astronomical darkness and K&S Δmag < 0.50 |
| **Astro night** | Peak within astronomical darkness but K&S shows minor moon interference (0.10–0.50) |
| **Moon wash** | K&S Δmag ≥ 0.50 at the target's position. Sky background noticeably raised |
| **Twilight** | Peak outside astronomical darkness (sun less than 18° below horizon) |

### Astro Window

The Astro Window column shows the span where K&S-modelled sky conditions are good enough to image. When scattered moonlight pushes the sky past the contrast threshold, the window clips at the start or end.

### Meteor showers

Active meteor showers always show in the report header, no `--targets` needed:

```
Meteor Showers:     Perseids · Peak night · ZHR 100
```

With `--targets`, showers also land in the targets table with the full astro window.

The ZHR shown here is always the catalog's raw peak value, no matter how far the queried night sits from the actual peak. The engine also computes a day-decayed, radiant-altitude-corrected local rate estimate. That one shows up today in the web app's scorecard alert, not yet in this CLI output. See [docs/TARGETS.md § Meteor Shower ZHR Decay Model](TARGETS.md#meteor-shower-zhr-decay-model) for the formula and sourcing.

---

## Milky Way

The Milky Way section synthesises visibility across a catalog of 10 waypoints spaced at even 36° galactic-longitude intervals, which makes 5 symmetric declination pairs. Each visible waypoint stands for a distinct 36° slice of the galactic plane, so the visible fraction (say "5 of 8 waypoints visible") is a real sky-coverage number.

```
Milky Way: 8.5/10  (Altitude 10.0/10  ·  Waypoints 7.5/10  ·  Window 6.2/10)
Visible   8:56 PM – 12:01 AM  ·  3h 06m  ·  Core 25°/25°  ·  6 of 8 waypoints visible
Best time      8:56 PM  —  core 25° S, arch sweeps to Cygnus Star Cloud (88° S)
```

### Score components

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Altitude** | 50% | Tonight's core peak altitude ÷ the geometric maximum from this latitude |
| **Waypoints** | 30% | Visible waypoints ÷ the most ever visible from this latitude |
| **Window** | 20% | Moon-free arch window ÷ a 5-hour reference |
| **Moon penalty** | ×0.7 | Applied when the moon clips the usable window or hits the core directly |

### Core altitude ratio

The core altitude ratio (say `25°/25°`) shows tonight's peak against the latitude's geometric ceiling (`90° − |lat − (−29°)|`). Denver (40°N) can never see the core above 21°. Buenos Aires (35°S) can reach 84°. Quito (0°) reaches 61°. Matching values mean tonight is as good as it ever gets from this spot.

### Moon handling

K&S sky-brightening is sampled at each waypoint's position through the night. When scattered moonlight pushes a waypoint past the photo threshold (Δmag ≥ 0.50):

- The arch window clips at the first and last photo-viable sample.
- The `· moon-limited` flag shows up on the Visible line.
- Any waypoint straddling the K&S cutoff shows direction and arch angle only, no peak time.

**High-latitude note.** From latitudes where the galactic core never clears the 10° elevation floor (roughly above 51°N or below 51°S), the summary block is replaced by a "Core below horizon" note that lists the visible northern or southern band waypoints.

---

## Nearby Skies

```bash
python darkhours.py --location "Roswell, GA" --show-nearby
python darkhours.py --location "Sedona, AZ" --show-nearby 40
python darkhours.py --location "Denver, CO" --show-nearby 95
```

It reads the VIIRS and Falchi raster windows over the search area (light domes always get searched out to 150 miles no matter the radius), pulls dark pixels straight from the arrays (land-masked, POI-first through the routable OSM POI index in the US), clusters them, and reports darker sky areas and light domes.

### Dark sky areas

A candidate qualifies if it's at least one Bortle class darker than the origin, capped at Bortle 3. So a Bortle 7 origin surfaces Bortle 3 or darker sky, while an already-dark Bortle 3 origin still surfaces the reachable Bortle 2 areas nearby (a Bortle 2 or darker origin needs Bortle 1). Candidates get band-selected across the distance range, then up to 10 areas are named and shown, re-sorted for display (drive-time order on the web).

Naming uses these sources in order:
1. **Routable OSM POI index** (`cache/osm_pois.npz`). Named, reachable destinations sitting on dark pixels: trailhead parking, viewpoints, campsites, observatories, and more. Pre-named, no reverse-geocode needed.
2. **PAD-US H3 index** (`cache/darkhours_padus_h3.npz`). Named public and protected lands.
3. **OpenStreetMap Overpass API** (local backend only). Named protected and natural areas that cross the search radius.
4. **Reverse geocoding.** Nominatim (local) or AWS Location (cloud) fallback, returning a county or settlement name.

### Light domes

Searched only when the origin is Bortle 7 or brighter (a dome has to be 2 classes brighter than the origin, and the brightest possible blob is Bortle 9, so a brighter origin can never qualify). Contiguous blobs of Bortle 8 or brighter pixels count as domes if they are:
- Strictly brighter than the origin and at least 2 Bortle classes above it.
- At least 5 miles away.

Up to 10 domes are named and shown.

### Performance & caching

Geocoded names cache for 90 days. Drive-time legs (cloud) cache for 24 hours. On the cloud deployment `/nearby` runs as an async job on the worker Lambda, usually about 1 to 3 seconds warm. See [PERF_FINDNEARBY.md](PERF_FINDNEARBY.md). On the CLI, a first run in a new area is dominated by Overpass and Nominatim calls. Repeat runs are cache-fast.

A spinner shows during computation when stdout is a terminal.

---

## Month Calendar

```bash
python darkhours.py --location "Grand Canyon Village, AZ" --calendar
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --date 2026-08
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --weather
```

One row per night across a calendar month. The Moon column shows the lunar interference score (0 to 10) and flags special events inline:

```
Calendar — Grand Canyon Village, Coconino County, Arizona, United States
Light Pollution:    SQM 21.9  ·  Zone 2a  ·  Bortle 2  (Truly dark sky)  [Falchi 2016]  ·  Score 8.9/10
March 2026

  Date        Night Quality Score  Clear Dark Hours  Weather  Moon
  ----------  -------------------  ----------------  -------  ----
  2026-03-01               0.0/10            0h 00m        —  0.0
  2026-03-02               0.0/10            0h 00m        —  0.0  ·  *** Total lunar eclipse at  4:33 AM  (mag umbral 1.149) ***
  ...
  2026-03-15               9.4/10            9h 10m        —  10.0
  2026-03-19               9.8/10            9h 00m        —  10.0
  ...

  Best nights:  Mar 19 (9.8/10)  ·  Mar 15 (9.4/10)  ·  Mar 16 (9.4/10)
```

The Light Pollution header adds the location's Bortle score contribution (0 to 10) so you can see what light pollution costs you every single night.

Calendar scores match single-night report scores for the same date. The same engine runs both.

---

## Light Pollution

Light pollution shows up as three values:

- **SQM** (Sky Quality Meter, mag/arcsec²). Higher is darker. A truly dark site reads about 22.0.
- **Bortle class** (1 to 9). The standard astronomer's scale. 1 is an exceptional dark sky, 9 is inner city.
- **Zone.** The djlorenz Light Pollution Index, a finer split of the Bortle scale (say Zone 2a or 7b).

### Two-tier data strategy

**Primary: VIIRS Black Marble 2025** (NASA/NOAA satellite)

Current satellite radiance data. Used whenever the sensor picks up a measurable signal (above about 0.2 nW/cm²/sr). It's the most up-to-date, and it catches post-2016 light growth that older datasets miss.

**Fallback: Falchi New World Atlas 2016** (GFZ Potsdam)

A radiative-transfer physical model of artificial sky luminance. Used only when VIIRS reads zero, meaning the site is genuinely dark and below the satellite's detection floor. Unlike raw satellite data, Falchi's model carries city-glow in from surrounding sources, so very dark sites (Bortle 1 to 3) get distinct values instead of all reading zero.

### `[VIIRS 2025]` vs `[Falchi 2016]` label

The label on the Light Pollution line tells you which dataset produced the SQM shown. A `[Falchi 2016]` label means the site is dark enough that no satellite radiance was detected. A `[VIIRS 2025]` label means the satellite recorded measurable light pollution.

---

## Location Formats

`--location` takes any OpenStreetMap geocoding format:

- City names: `"New York"`, `"Tokyo"`, `"London"`
- Place names: `"Sedona, Arizona"`, `"Mauna Kea Observatory"`, `"Death Valley"`
- Addresses: `"1600 Pennsylvania Avenue, Washington DC"`
- Landmarks: `"Statue of Liberty"`

Geocoding results cache, so a repeat lookup for the same name is instant.

### Save & reuse locations

```bash
# Save coordinates under a name
python darkhours.py --coords 40.7128 -74.0060 --save-location "home"

# Use saved location
python darkhours.py --location "home"

# List saved locations
python darkhours.py --list-locations
```

---

## Past Dates & Historical Weather

```bash
# Past date with weather
python darkhours.py --location "Sedona, AZ" --date 2025-06-21 --weather
```

Astronomical events always show, whatever the date. Weather data for past dates works like this:

| Date range | Source | Notes |
|------------|--------|-------|
| Within 16 days | NOAA / Open-Meteo forecast | Same as future dates |
| 17–92 days ago | Open-Meteo recent archive | High-resolution, usually available |
| > 92 days ago | Open-Meteo ERA5 reanalysis | Covers back to 1940; occasionally unavailable |

---

## Target Catalog

Targets are defined in [`targets.json`](../darkhours/targets.json). The schema is documented in [`TARGETS.md`](TARGETS.md). Global observation thresholds and defaults live in [`config.json`](../darkhours/config.json).
