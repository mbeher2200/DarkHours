# PyNightSkyPredictor

A comprehensive night sky prediction tool for astronomy and astrophotography planning.

Predicts **sun and moon rise/set times**, **total night sky availability**, **moon phase**, **light pollution levels**, and **weather conditions** to generate an astronomy quality score (1-10) for any date and location. Perfect for planning dark sky observations, astrophotography sessions, and observing campaigns.

## Data Download & Caching

This project automatically downloads and caches external datasets:

- **VIIRS Black Marble 2025** (Satellite light pollution data)
- **Falchi World Atlas 2016** (Physical light pollution model)
- **Nominatim Geocoding** (Location name resolution)

These datasets are downloaded **on first use** and cached locally in `~/.pynightsky-predictor/` for offline access.

### Data Source Attribution

All datasets remain under their original open licenses and attributions (see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)):
- VIIRS: NASA/NOAA (Public Domain)
- Falchi: GFZ Potsdam (ODbL with attribution)
- Nominatim: OpenStreetMap contributors (ODbL)

### Fair Use

This project uses these datasets for non-commercial research and educational purposes. Commercial users should review the respective source terms:
- VIIRS/NASA: Free for most uses
- Falchi: Academic citation required
- OSM/Nominatim: Attribution required; share-alike if redistributing

For details, see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Basic: Today at your location

```bash
python sky_events.py --location "New York"
```

Or use coordinates:

```bash
python sky_events.py --coords 40.7128 -74.0060
```

### With weather forecast

```bash
python sky_events.py --location "New York" --weather
```

### Specific date

```bash
# Future date
python sky_events.py --location "Sedona, Arizona" --date 2026-06-21

# Past date (for reference/analysis)
python sky_events.py --location "Sedona, Arizona" --date 2025-06-21 --weather
```

Note: Past dates up to ~92 days ago can include weather data via historical records. Older dates show astronomical events only.

### Location formats

The `--location` argument accepts any OpenStreetMap geocoding format:
- City names: `"New York"`, `"Tokyo"`, `"London"`
- Place names: `"Sedona, Arizona"`, `"Mauna Kea Observatory"`, `"Death Valley"`
- Addresses: `"1600 Pennsylvania Avenue, Washington DC"`
- Landmarks: `"Statue of Liberty"`

### Save & reuse locations

```bash
# Save coordinates under a name
python sky_events.py --coords 40.7128 -74.0060 --save-location "home"

# Use saved location next time
python sky_events.py --location "home"

# List all saved locations
python sky_events.py --list-locations
```

### Output

The tool displays:
- **Astronomy Score (1-10)** — Overall night sky quality
- **Sky Events** — Sunset, night begins, night ends, sunrise
- **Moon Info** — Phase, illumination, rise/set times
- **Dark Time** — Total hours of astronomical darkness
- **Light Pollution** — Bortle classification and SQM reading
- **Weather** — Cloud cover, seeing, transparency, temperature (with `--weather`)

Example output:
```
Astronomy Score: 7.2 / 10

New York — 40.7128°N, 74.0060°W

Moon phase: Waxing Crescent (12% illuminated)
Dark time: 8h 45m
Light pollution: Bortle 5 (Suburban sky)

Tonight's schedule:
  Sunset          19:45
  Night begins    21:15
  Moonrise        04:22
  Night ends      05:30
  Sunrise         06:45
```

## Astronomy Score (1–10)

The tool evaluates four factors and produces a composite score:

| Factor | Weight | Scoring |
|--------|--------|---------|
| **Moon Phase** | 30% | 10 = new moon, 0 = full moon |
| **Dark Time** | 30% | Based on your location's typical lunar cycle; scores relative to best conditions |
| **Light Pollution** | 25% | 10 = no pollution (Bortle 1), decreases with light-polluted skies (Bortle 9) |
| **Weather** | 15% | Cloud cover, seeing, transparency, humidity, and precipitation |

**Score interpretation:**
- **9–10**: Excellent — Perfect conditions for astronomy
- **7–8**: Good — Suitable for astrophotography and observing
- **5–6**: Fair — Usable but compromised (clouds, moon, or light pollution)
- **3–4**: Poor — Challenging conditions
- **1–2**: Unusable — Heavy clouds, full moon, or bad weather

## Options

```
--location, -l NAME        Location name or city (geocoded and cached)
--coords, -c LAT LON       Decimal-degree coordinates (e.g., -c 40.7128 -74.0060)
--date, -d YYYY-MM-DD      Date to predict (default: today)
--weather, -w              Include weather forecast (requires internet)
--list-locations           Show all saved/cached locations
--save-location NAME       Save coordinates under a name for future use
--units imperial|si        Temperature/wind units (default: auto-detect from locale)
--verbose, -v              Print debug information
```

## License

MIT License - See [LICENSE](LICENSE) for details.

Development assisted by GitHub Copilot and Claude.
