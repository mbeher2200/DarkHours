# PyNightSkyPredictor

A night sky prediction tool for astronomy and astrophotography planning.

Predicts **sun and moon rise/set times**, **total night sky availability**, **moon phase, and percent illumination**, **light pollution levels**, and **weather conditions** to generate an an Night Quality Score (1-10) for any date and location. Great for planning dark sky observations, astrophotography sessions, and trips.

## Output

The tool displays:
- **Astronomy Score (1-10)** — Overall night sky quality
- **Sky Events** — Sunset, night begins, night ends, sunrise
- **Moon Info** — Phase, illumination, rise/set times
- **Dark Time** — Total hours of astronomical darkness
- **Light Pollution** — Bortle classification and SQM reading
- **Weather** — Cloud cover, seeing, transparency, temperature (with `--weather`)
- **Visible Targets** — What's observable tonight, grouped by type (with `--targets` or `--prime-targets`)

Example output:
```
Date:      2026-05-23
Location:  Grand Canyon Village, Coconino County, Arizona, United States  (36.0578°)
Moon:      First Quarter  |  56.8% illuminated
Darkness:  SQM 21.9  ·  Zone 2a  ·  Bortle 1  (Exceptional dark sky)  [Falchi 2016]
Dark sky:  1h 57m  ( 1:34 AM –  3:32 AM MST)  ·  avg 3.0h  ±2.4h over lunar cycle
Night score:  3.1/10  (Moon 4.3 · Dark 3.3 · Wx 7.2 · Bortle 10.0)

Sky Events:

  Time (MST)        Event       
  ----------------  ------------
  May 23, 12:33 PM  Moonrise
  May 23,  7:33 PM  Sunset
  May 23,  9:18 PM  Night begins
  May 24,  1:34 AM  Moonset
  May 24,  3:32 AM  Night ends
  May 24,  5:16 AM  Sunrise

Weather:

  Time (MST)        Wx Rating  Cloud  Temp  Feels  Humid     Wind  Precip
  ----------------  ---------  -----  ----  -----  -----  -------  ------
  May 23,  7:00 PM       3/10    71%  69°F   59°F    11%   9.4mph  None  
  May 23,  8:00 PM       2/10   100%  65°F   55°F    13%   8.5mph  None  
  May 23,  9:00 PM       2/10   100%  64°F   54°F    16%  10.7mph  None  
  May 23, 10:00 PM       2/10   100%  63°F   53°F    17%  11.0mph  None  
  May 23, 11:00 PM       6/10    34%  59°F   49°F    19%  10.8mph  None  
  May 24, 12:00 AM       9/10     0%  58°F   48°F    22%   9.8mph  None  
  May 24,  1:00 AM       9/10     0%  56°F   47°F    23%   9.0mph  None  
  May 24,  2:00 AM       9/10     0%  55°F   46°F    24%   8.9mph  None  
  May 24,  3:00 AM      10/10     0%  54°F   45°F    25%   8.3mph  None  
  May 24,  4:00 AM      10/10     0%  52°F   43°F    27%   8.1mph  None  
  May 24,  5:00 AM      10/10     0%  52°F   44°F    26%   5.9mph  None  
  May 24,  6:00 AM      10/10     0%  52°F   44°F    26%   7.0mph  None  

Prime Targets  ( 7:33 PM –  5:16 AM MST):

  Target                Best Viewing    Sky          Window                         
  --------------------  --------------  -----------  -------------------------------
  Milky Way
  Galactic Core          2:03 AM @ 25°  dark sky     12:23 AM @ 20° –  3:23 AM @ 22°
  Cygnus Star Cloud      3:23 AM @ 74°  dark sky     10:33 PM @ 21° –  3:23 AM @ 74°

  Clusters
  Hercules Cluster       1:03 AM @ 90°  astro night   9:23 PM @ 46° –  3:23 AM @ 62°
  Wild Duck Cluster      3:13 AM @ 48°  dark sky     11:23 PM @ 22° –  3:23 AM @ 48°

  Planets
  Jupiter                7:33 PM @ 42°  twilight      7:33 PM @ 42° –  9:13 PM @ 22°

  Nebulae
  Eagle Nebula           2:43 AM @ 40°  dark sky     11:13 PM @ 20° –  3:23 AM @ 39°
  Ring Nebula            3:13 AM @ 87°  dark sky      9:23 PM @ 20° –  3:23 AM @ 86°
  Dumbbell Nebula        3:23 AM @ 72°  dark sky     11:03 PM @ 21° –  3:23 AM @ 72°
  Veil Nebula            3:23 AM @ 68°  dark sky     11:23 PM @ 21° –  3:23 AM @ 68°
  North America Nebula   3:23 AM @ 66°  dark sky     10:53 PM @ 20° –  3:23 AM @ 66°

  Galaxies
  Bode's Galaxy          9:23 PM @ 49°  astro night   9:23 PM @ 49° –  3:13 AM @ 20°
  Sombrero Galaxy        9:23 PM @ 42°  astro night   9:23 PM @ 42° – 12:33 AM @ 21°
  Whirlpool Galaxy       9:53 PM @ 79°  astro night   9:23 PM @ 78° –  3:23 AM @ 30°
  Pinwheel Galaxy       10:23 PM @ 72°  astro night   9:23 PM @ 69° –  3:23 AM @ 37°
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

### Visible targets

```bash
# All visible targets tonight, grouped by type
python pynightsky.py --location "Death Valley" --targets

# Prime targets only — no moon interference, peak ≥40°, visible window ≥1h
python pynightsky.py --location "Death Valley" --prime-targets
```

Targets are grouped as: Meteor Showers · Milky Way · Clusters · Planets · Nebulae · Galaxies. Each entry shows best viewing time, peak altitude, the full window with start/end elevations, and a **sky condition** — `dark sky`, `astro night`, or `twilight` — indicating what lighting conditions the target peaks in.

Milky Way targets (Galactic Core, Cygnus Star Cloud) are automatically included in prime results whenever they're visible during astronomical darkness.

The target catalog lives in [`targets.json`](targets.json) and is easy to extend — see [`TARGETS.md`](TARGETS.md) for the schema. Prime target thresholds and global observation defaults are in [`config.json`](config.json).

### Specific date

```bash
# Future date
python pynightsky.py --location "Sedona, Arizona" --date 2026-06-21

# Past date (for reference/analysis)
python pynightsky.py --location "Sedona, Arizona" --date 2025-06-21 --weather
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
python pynightsky.py --coords 40.7128 -74.0060 --save-location "home"

# Use saved location next time
python pynightsky.py --location "home"

# List all saved locations
python pynightsky.py --list-locations
```

## Options

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
