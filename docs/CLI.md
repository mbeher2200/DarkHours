# darkhours.py — Reference Documentation

Single-night reports, monthly calendars, and nearby dark-sky search for a single location.

```bash
python darkhours.py --location "Grand Canyon Village, AZ" --date 2026-08-12 --targets --weather
python darkhours.py --location "Roswell, GA" --show-nearby
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --date 2026-08
```

---

## Contents

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

## Night Quality Score

The Night Quality Score (1–10) is a composite of four factors:

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Weather** | 40% | Cloud cover, seeing, transparency, humidity, and precipitation |
| **Lunar Interference** | 25% | K&S sky-brightening credit at 90° separation, 30° altitude — 10 = new moon, ≈0 = gibbous or full |
| **Dark Sky Hours** | 25% | Based on your location's typical lunar cycle; scored relative to best conditions |
| **Light Pollution** | 10% | 10 = Bortle 1 (no pollution), decreasing to near-zero at Bortle 9 (inner city) |

Weights redistribute automatically when a factor is unavailable (e.g., no weather data — Dark Sky Hours and Lunar each absorb part of the 40% weather weight).

**Formula — weighted geometric mean:**

```
score = (weather^0.40) × (lunar^0.25) × (dark_hours^0.25) × (light_pollution^0.10)
```

The geometric mean means every factor influences the result proportionally, and a single zero factor (complete cloud cover, full moon) zeros the overall score. A factor of 1/10 with 40% weight contributes roughly 0.25× to the product, so bad factors drag the score down significantly without a separate penalty term.

**Score interpretation:**

| Score | Tier | Meaning |
|-------|------|---------|
| 9–10 | Excellent | Ideal conditions for astronomy |
| 7–8 | Good | Suitable for astrophotography and observing |
| 5–6 | Fair | Usable but compromised (clouds, moon, or light pollution) |
| 3–4 | Poor | Challenging conditions |
| 1–2 | Unusable | Heavy clouds, full moon, or severe weather |
| 0 | Pass | Complete cloud cover or full moon — no viable window |


---

## Moonlight Modeling (K&S 1991 × Winkler 2022 hybrid)

DarkHours models scattered moonlight with a hybrid of two photometric models:

- **Krisciunas, K. & Schaefer, B. E. (1991)**, *"A model of the brightness of moonlight,"* PASP 103(667), 1033–1039. [doi:10.1086/132921](https://doi.org/10.1086/132921) — the phase-dependent lunar luminosity and the optical-pathlength form.
- **Winkler, H. (2022)**, *"A revised simplified scattering model for the moonlit sky brightness profile based on photometry at SAAO,"* MNRAS 514(1), 208–226. [doi:10.1093/mnras/stac1387](https://doi.org/10.1093/mnras/stac1387) — the single-scatter kernel with correct lunar-beam extinction, and the two-component Rayleigh + Henyey–Greenstein (g = 0.8) phase function.

The model computes the sky surface brightness increase (Δ mag/arcsec²) at any sky position given the moon's illumination, altitude, angular separation from the target, the target's own altitude (slant path), and the atmosphere's aerosol load. Extinction is a live optical-depth decomposition (Rayleigh + aerosol + ozone) rather than a fixed coefficient: the **aerosol optical depth (AOD)** forecast from Open-Meteo's air-quality API (CAMS) feeds in as the night's median, so wildfire smoke and haze both dim the lunar beam and *amplify* the forward-scattered aureole near the moon — smoke brightens the sky near the moon while dimming it far away. When AOD is unavailable (past dates, fetch failure, beyond the ~7-day air-quality horizon) the model falls back to a reference clear sky whose extinction equals the classic k = 0.172 exactly. The normalisation anchors the new kernel to the legacy K&S intensity at the site-wide proxy geometry, so nightly planning scores are unchanged at reference conditions (verified by `scripts/verify_moonwash_grid.py`).

### Why it matters

A simple moonrise/moonset boundary treats all moon phases identically — a 5% crescent and a 90% gibbous count as equally "moon-up." K&S makes the distinction physically meaningful:

| Phase | Δmag at 90° sep, 30° alt | Impact |
|-------|--------------------------|--------|
| 5% crescent | 0.06 | Imperceptible |
| 15% crescent | 0.21 | Minor |
| 50% quarter | 1.03 | Severe |
| 75% gibbous | 1.73 | Severe |
| 100% full | 3.16 | Very severe |

The transition from negligible to severe is sharp — between roughly 20% and 30% illumination. A waxing crescent above the horizon is not meaningfully different from a moonless night.

### Severity thresholds

| Threshold | Δmag/arcsec² | Meaning |
|-----------|--------------|---------|
| Imperceptible | < 0.10 | No practical effect on deep-sky imaging |
| Minor | 0.10 – 0.50 | Slight brightening; faint nebulae unaffected |
| Moderate | 0.50 – 1.50 | Noticeable; low-surface-brightness targets impacted |
| Severe | ≥ 1.50 | Sky substantially brighter; deep DSO imaging limited |

### Proxy geometry for site-wide evaluation

K&S is inherently directional — it depends on where you're looking relative to the moon. For site-wide metrics (night score, clear dark sky hours) a reference sky position is needed. DarkHours uses **90° separation at 30° altitude** as the proxy:

- **90° separation** is the darkest accessible sky position: the scattering function reaches its minimum there (the cos²ρ term vanishes), representing the best realistic position when the moon is up
- **30° altitude** is a representative mid-sky moon position over the course of an evening

For per-target evaluation, the actual moon–target separation, moon altitude, and target altitude are computed from the Skyfield ephemeris at each 10-minute sample.

### How it affects the output

**Lunar Interference score** — The moon-up fraction of the astronomical night is weighted by the K&S credit at the proxy geometry rather than `(1 − illumination/100)`. A quarter moon's moonlit hours receive 0.31 credit (down from 0.50); a gibbous moon's moonlit hours receive 0 (down from 0.25).

**Clear Dark Sky Hours** — When illumination is ≤ 20% (imperceptible-to-minor impact at any altitude), the full astronomical window is reported as dark sky time. When weather data is available, each dark interval is further clipped to hours where cloud cover ≤ 30%.

**Astro Window per target** — the model is evaluated at the actual moon–target separation, moon altitude, and target altitude at every 10-minute sample. The window is clipped when Δmag exceeds the per-type contrast threshold (nebulae/galaxies: surface brightness − sky background − 3.2 mag; clusters: integrated magnitude − site SQM − 13.0; Milky Way: surface brightness − sky background − 1.5 mag).

**Light pollution interaction** — The site's SQM enters the K&S denominator as the natural-sky baseline. On a darker site the same moon produces less fractional brightening; on a light-polluted site the moon adds less on top of what is already a degraded sky.

**Earth-Moon distance correction** — K&S (1991) assumes the Moon at its mean distance of 384,400 km. The actual distance varies ±8.5%, translating to up to ±0.35 mag/arcsec² error on supermoon/micromoon nights. DarkHours corrects via the inverse-square law: the lunar irradiance is scaled by `(mean_dist / actual_dist)²` at every sample, applied to both site-wide score and per-target evaluations.

**Meteor shower local rates** — `local_rate_at_peak` applies the standard IMO visual-rate correction on top of the decay model and radiant geometry: rate = ZHR_effective × sin(radiant alt) × min(1, r^(lm − 6.5)), where lm is the naked-eye limiting magnitude (NELM = 7.93 − 5·log₁₀(10^(4.316 − SQM/5) + 1)) under the moon-brightened site sky and r is the shower's magnitude-distribution (population) index from the catalog. Faint-meteor-rich showers (Delta Aquariids, r = 3.2) collapse under moonlight or city skies far harder than fireball-rich ones (Perseids, r = 2.2).

**Aurora moon factor** — aurora is an emission source, so moonlight raises the background it must be seen against rather than washing the source. The aurora condition vector degrades (never blocks) tier-scaled: photographic-tier nights at Δ ≥ 0.50 mag/arcsec², naked-eye at ≥ 1.50, and overhead storms punch through any moon.

**Deliberate AOD exclusions** — `ks_moon_credit` (moon_score, calendar dark-cycle scores) always evaluates at the reference sky: planning scores must not wobble with 30-minute weather refetches, and the calendar path fetches no weather at all.

---

## Clear Dark Sky Hours

Effective dark sky time is computed as the overlap of three windows:

1. **Astronomical darkness** — sun more than 18° below the horizon (computed from Skyfield ephemeris)
2. **Moon-free periods** — K&S moonlight ≤ 0.10 Δmag at the proxy geometry, OR illumination ≤ 20% (crescent threshold)
3. **Clear sky** — when weather data is available, cloud cover ≤ 30% during the dark window

The output shows tonight's hours alongside a lunar-cycle average ± standard deviation, giving context for how typical tonight is for this location:

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
| **Wx Rating** | 1–10 astrophotography score for that hour |
| **Cloud Cover** | Percentage sky coverage |
| **Temp** | Air temperature at 2 m |
| **Dew Pt** | Dew point — a Dew Pt close to Temp means high moisture and dew risk |
| **Feels** | Apparent temperature (wind chill / heat index) |
| **Seeing** | Atmospheric steadiness as N/10 + arcsecond value — lower arcseconds = steadier |
| **Transparency** | Sky clarity and extinction as N/10 |
| **Humidity** | Relative humidity at 2 m |
| **Wind** | Speed and compass direction, e.g. `12mph SW` |
| **Precip** | Precipitation type: None / Rain / Snow |

### Wx Rating formula

Weighted combination of all available hourly parameters:

| Factor | Weight | Notes |
|--------|--------|-------|
| Cloud cover | 50% | Non-linear — heavy cloud penalised more steeply above 50% |
| Seeing | 20% | Atmospheric steadiness |
| Transparency | 15% | Sky clarity and extinction |
| Wind speed | 10% | Vibration, tracking error, turbulence |
| Humidity | 5% | Dew risk; no penalty below 50%, zero score above 90% |

Precipitation of any kind caps the Wx Rating at 1. Weights redistribute automatically when a field is unavailable.

### Providers

| Provider | Coverage | Used for |
|----------|----------|---------|
| **NOAA/NWS** | US locations only | Primary for US: NAM-based, accurate cloud percentages, wind chill, heat index |
| **Open-Meteo** | Global | Primary for non-US; also used for past dates up to 92 days (recent archive) and older dates via ERA5 reanalysis back to 1940 |
| **7Timer ASTRO** | Global | Blended in to supply seeing and transparency; derived from Cn² profile integration through GFS — the only free scientifically-grounded seeing source |

---

## Targets

The `--targets` flag shows prime targets for the night — no significant moon interference, peak altitude ≥ 40°, visible window ≥ 1 hour. Targets are grouped by type: Meteor Showers · Milky Way · Clusters · Planets · Nebulae · Galaxies.

### Sky condition tags

Each target's **sky condition** reflects the lighting when the target peaks:

| Tag | Meaning |
|-----|---------|
| **Dark sky** | Peak within astronomical darkness and K&S Δmag < 0.50 |
| **Astro night** | Peak within astronomical darkness but K&S indicates minor moon interference (0.10–0.50) |
| **Moon wash** | K&S Δmag ≥ 0.50 at the target's position — sky background significantly elevated |
| **Twilight** | Peak outside astronomical darkness (sun less than 18° below horizon) |

### Astro Window

The **Astro Window** column shows the span during which K&S-modelled sky conditions are good enough for imaging. When scattered moonlight degrades the sky past the contrast threshold, the window is clipped at the start or end accordingly.

### Meteor showers

Active meteor showers are always shown in the report header (without needing `--targets`):

```
Meteor Showers:     Perseids · Peak night · ZHR 100
```

With `--targets`, showers also appear in the targets table with the full astro window.

The `ZHR` shown here is always the catalog's raw peak value, regardless of how far the queried night falls from the actual peak date. The engine also computes a day-decayed, radiant-altitude-corrected "local rate" estimate (surfaced today in the web app's scorecard alert, not yet in this CLI output) — see [docs/TARGETS.md § Meteor Shower ZHR Decay Model](TARGETS.md#meteor-shower-zhr-decay-model) for the formula and sourcing.

---

## Milky Way

The Milky Way section synthesises visibility across a catalog of 10 waypoints placed at uniform 36° galactic-longitude intervals, creating 5 symmetric declination pairs. Each visible waypoint represents a distinct 36° slice of the galactic plane, making the visible fraction (e.g. "5 of 8 waypoints visible") a meaningful sky-coverage metric.

```
Milky Way: 8.5/10  (Altitude 10.0/10  ·  Waypoints 7.5/10  ·  Window 6.2/10)
Visible   8:56 PM – 12:01 AM  ·  3h 06m  ·  Core 25°/25°  ·  6 of 8 waypoints visible
Best time      8:56 PM  —  core 25° S, arch sweeps to Cygnus Star Cloud (88° S)
```

### Score components

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Altitude** | 50% | Tonight's core peak altitude ÷ the geometric maximum from this latitude |
| **Waypoints** | 30% | Visible waypoints ÷ maximum ever visible from this latitude |
| **Window** | 20% | Moon-free arch window ÷ 5-hour reference |
| **Moon penalty** | ×0.7 | Applied when the moon clips the usable window or directly interferes with the core |

### Core altitude ratio

The **Core altitude ratio** (e.g. `25°/25°`) shows tonight's peak versus the latitude's geometric ceiling (`90° − |lat − (−29°)|`). Denver (40°N) can never see the core above 21°; Buenos Aires (35°S) can reach 84°; Quito (0°) reaches 61°. Identical values mean tonight is as good as it ever gets from this location.

### Moon handling

K&S sky-brightening is sampled at each waypoint's position throughout the night. When scattered moonlight degrades a waypoint past the photo threshold (Δmag ≥ 0.50):

- The arch window is clipped at the first/last photo-viable sample
- The `· moon-limited` flag appears on the Visible line
- Any waypoint that straddles the K&S cutoff shows direction and arch angle only — no peak time

**High-latitude note:** From latitudes where the galactic core never clears the 10° elevation floor (roughly above 51°N or below 51°S), the summary block is replaced by a "Core below horizon" note listing the visible northern or southern band waypoints.

---

## Nearby Skies

```bash
python darkhours.py --location "Roswell, GA" --show-nearby
python darkhours.py --location "Sedona, AZ" --show-nearby 40
python darkhours.py --location "Denver, CO" --show-nearby 95
```

Reads the VIIRS and Falchi raster windows covering the search area (light domes are always searched out to 150 miles regardless of radius), extracts dark pixels directly from the arrays (land-masked, **POI-first** via the routable OSM POI index in the US), clusters them, and reports darker sky areas and light domes.

### Dark sky areas

Candidates qualify if they are **at least one Bortle class darker** than the origin, capped at **Bortle 3** — so a Bortle 7 origin surfaces Bortle ≤ 3 sky, while an already-dark Bortle 3 origin still surfaces the reachable Bortle 2 areas nearby (a Bortle ≤ 2 origin requires Bortle 1). Candidates are band-selected across the distance range, then up to **10** areas are named and shown, re-sorted for display (drive-time order on the web).

Naming uses these sources in order:
1. **Routable OSM POI index** (`cache/osm_pois.npz`) — named, reachable destinations (trailhead parking, viewpoints, campsites, observatories, …) sitting on dark pixels; pre-named, no reverse-geocode needed.
2. **PAD-US H3 index** (`cache/darkhours_padus_h3.npz`) — named public/protected lands.
3. **OpenStreetMap Overpass API** (local backend only) — named protected and natural areas intersecting the search radius.
4. **Reverse geocoding** — Nominatim (local) or AWS Location (cloud) fallback; returns county or settlement name.

### Light domes

Searched only when the origin is **Bortle ≤ 7** (a dome must be ≥ 2 classes brighter than the origin and the brightest possible blob is Bortle 9, so brighter origins can never qualify). Contiguous blobs of Bortle ≥ 8 pixels qualify as domes if they are:
- **Strictly brighter** than the origin and at least **2 Bortle classes above** it
- At least **5 miles away**

Up to 10 domes are named and shown.

### Performance & caching

Geocoded names are cached for 90 days; drive-time legs (cloud) for 24 hours. On the cloud deployment `/nearby` runs as an async job on the worker Lambda — typically ~1–3 s warm; see [PERF_FINDNEARBY.md](PERF_FINDNEARBY.md). On the CLI, first runs in a new area are dominated by Overpass/Nominatim calls; repeat runs are cache-fast.

A spinner is shown during computation when stdout is a terminal.

---

## Month Calendar

```bash
python darkhours.py --location "Grand Canyon Village, AZ" --calendar
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --date 2026-08
python darkhours.py --location "Grand Canyon Village, AZ" --calendar --weather
```

Shows one row per night across a calendar month. The **Moon** column shows the lunar interference score (0–10) and flags special events inline:

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

The Light Pollution header appends the location's Bortle score contribution (0–10) so you can see how much light pollution costs you every night.

Calendar scores are identical to single-night report scores for the same date — the same engine runs both.

---

## Light Pollution

Light pollution is expressed as three values:

- **SQM** (Sky Quality Meter, mag/arcsec²) — higher is darker; a truly dark site reads ~22.0
- **Bortle class** (1–9) — the standard astronomer's scale; 1 = exceptional dark sky, 9 = inner city
- **Zone** — the djlorenz Light Pollution Index, a finer subdivision of the Bortle scale (e.g. Zone 2a, 7b)

### Two-tier data strategy

**Primary: VIIRS Black Marble 2025** (NASA/NOAA satellite)

Current satellite radiance data. Used whenever the sensor detects a measurable signal (> ~0.2 nW/cm²/sr). Most up-to-date; reflects post-2016 light growth that older datasets miss.

**Fallback: Falchi New World Atlas 2016** (GFZ Potsdam)

A radiative-transfer physical model of artificial sky luminance. Used only when VIIRS reads zero — meaning the site is genuinely dark and below the satellite's detection floor. Unlike raw satellite data, Falchi's model propagates city-glow from surrounding sources, so very dark sites (Bortle 1–3) get distinguishable values rather than all reading zero.

### `[VIIRS 2025]` vs `[Falchi 2016]` label

The label in the Light Pollution line indicates which dataset was used for the displayed SQM. A `[Falchi 2016]` label means the site is dark enough that no satellite radiance was detected; a `[VIIRS 2025]` label means measurable light pollution was recorded by the satellite.

---

## Location Formats

`--location` accepts any OpenStreetMap geocoding format:

- City names: `"New York"`, `"Tokyo"`, `"London"`
- Place names: `"Sedona, Arizona"`, `"Mauna Kea Observatory"`, `"Death Valley"`
- Addresses: `"1600 Pennsylvania Avenue, Washington DC"`
- Landmarks: `"Statue of Liberty"`

Geocoding results are cached — repeated lookups for the same name are instant.

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

Astronomical events are always shown regardless of date. Weather data for past dates:

| Date range | Source | Notes |
|------------|--------|-------|
| Within 16 days | NOAA / Open-Meteo forecast | Same as future dates |
| 17–92 days ago | Open-Meteo recent archive | High-resolution, usually available |
| > 92 days ago | Open-Meteo ERA5 reanalysis | Covers back to 1940; occasionally unavailable |

---

## Target Catalog

Targets are defined in [`targets.json`](../darkhours/targets.json). The schema is documented in [`TARGETS.md`](TARGETS.md). Global observation thresholds and defaults are in [`config.json`](../darkhours/config.json).
