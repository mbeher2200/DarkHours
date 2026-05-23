# PyNightSkyPredictor

A night sky prediction tool for astronomy and astrophotography planning.

Predicts **sun and moon rise/set times**, **total night sky availability**, **moon phase, and percent illumination**, **light pollution levels**, and **weather conditions** to generate an an Night Quality Score (1-10) for any date and location. Great for planning dark sky observations, astrophotography sessions, and trips.

## Data Download & Caching

The application automatically downloads and caches external datasets:

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
Date:      2026-05-23
Location:  Death Valley, California, 92328, United States  (36.4229°)
Moon:      First Quarter  |  56.9% illuminated
Darkness:  SQM 22.0  ·  Zone 0  ·  Bortle 1  (Exceptional dark sky)  [Falchi 2016]
Dark sky:  1h 55m  (1:54 AM – 3:49 AM PDT)  ·  avg 3.0h  ±2.4h over lunar cycle
Night score:  3.5/10  (Moon 4.3 · Dark 3.3 · Wx 9.8 · Bortle 10.0)

  May 23, 12:53 PM PDT  Moonrise
  May 23,  7:54 PM PDT  Sunset
  May 23,  9:39 PM PDT  Night begins
  May 24,  1:54 AM PDT  Moonset
  May 24,  3:49 AM PDT  Night ends
  May 24,  5:34 AM PDT  Sunrise

  Time                  Wx Rating  Cloud   Temp  Feels  Humid     Wind  Precip
  --------------------  ---------  -----  -----  -----  -----  -------  ------
  May 23,  7:00 PM PDT       9/10     0%  102°F   91°F     5%  13.0mph  None  
  May 23,  8:00 PM PDT       9/10     0%   96°F   87°F     7%   8.8mph  None  
  May 23,  9:00 PM PDT      10/10     0%   92°F   85°F     7%   6.1mph  None  
  May 23, 10:00 PM PDT      10/10     0%   89°F   83°F     8%   3.4mph  None  
  May 23, 11:00 PM PDT       9/10     0%   87°F   80°F    17%   9.2mph  None  
  May 24, 12:00 AM PDT      10/10     0%   86°F   79°F    18%   8.1mph  None  
  May 24,  1:00 AM PDT      10/10     0%   84°F   79°F    18%   5.2mph  None  
  May 24,  2:00 AM PDT      10/10     0%   83°F   77°F    17%   5.7mph  None  
  May 24,  3:00 AM PDT      10/10     0%   82°F   76°F    18%   6.0mph  None  
  May 24,  4:00 AM PDT      10/10     0%   81°F   76°F    20%   4.5mph  None  
  May 24,  5:00 AM PDT      10/10     0%   80°F   73°F    21%   8.4mph  None  
  May 24,  6:00 AM PDT      10/10     0%   79°F   73°F    23%   8.2mph  None  
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
