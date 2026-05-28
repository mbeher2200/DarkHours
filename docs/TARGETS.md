# Target Catalog

The file `targets.json` is the curated list of objects the predictor checks for visibility each night. It is a JSON array where each entry is one target.

---

## Global Defaults

These apply to all targets unless overridden on an individual entry:

| Setting | Default | Description |
|---------|---------|-------------|
| `min_elevation` | 20° | Minimum altitude above the horizon for a target to be considered visible |
| `moon_min_separation` | 30° | Minimum angular distance from the moon |
| `moon_max_illumination` | 50% | Moon illumination above which the separation check is enforced |

---

## Common Fields

These fields apply to all target types:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name shown in output (use common names, not catalog IDs) |
| `type` | Yes | One of: `nebula`, `galaxy`, `cluster`, `galactic_core`, `planet`, `meteor_shower` |
| `min_elevation` | No | Override the global minimum elevation for this target (degrees) |

---

## Target Types

### `nebula`, `galaxy`, `cluster`, `galactic_core`

Fixed positions on the celestial sphere. RA/Dec are static — look them up once from any star atlas or astronomy database (e.g. Simbad, SkySafari, Stellarium).

| Field | Required | Description |
|-------|----------|-------------|
| `ra` | Yes | Right ascension in the format `"HHh MMm SSs"` (e.g. `"05h 35m 17s"`) |
| `dec` | Yes | Declination in the format `"±DD° MM' SS\""` (e.g. `"-05° 23' 28\""`) |

**Example:**
```json
{
  "name": "Eagle Nebula",
  "type": "nebula",
  "ra": "18h 18m 48s",
  "dec": "-13° 49' 00\""
}
```

---

### `planet`

No coordinates needed — positions are computed dynamically from the ephemeris each run.

Supported names (must match exactly): `Mercury`, `Venus`, `Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune`

**Example:**
```json
{
  "name": "Saturn",
  "type": "planet"
}
```

---

### `meteor_shower`

Uses a fixed radiant point (the position in the sky the meteors appear to originate from) plus a peak date. Visibility is computed from the radiant's altitude and proximity to the moon. The output also indicates how many days before or after the peak the observation date falls.

| Field | Required | Description |
|-------|----------|-------------|
| `radiant_ra` | Yes | Right ascension of the radiant in the format `"HHh MMm SSs"` |
| `radiant_dec` | Yes | Declination of the radiant in the format `"±DD° MM' SS\""` |
| `peak_month` | Yes | Month of peak activity (1–12) |
| `peak_day` | Yes | Day of peak activity (1–31) |
| `active_window_days` | Yes | Total number of days the shower is active (centred on peak) |
| `peak_zhr` | Yes | Zenithal hourly rate at peak (meteors/hour under ideal conditions) |

**Example:**
```json
{
  "name": "Geminids",
  "type": "meteor_shower",
  "radiant_ra": "07h 28m 00s",
  "radiant_dec": "+32° 00' 00\"",
  "peak_month": 12,
  "peak_day": 14,
  "active_window_days": 10,
  "peak_zhr": 150
}
```

---

## How to Add a Target

1. Open `targets.json`
2. Add a new entry to the JSON array following the schema above
3. Use the correct `type` — this determines how visibility is calculated
4. For deep sky objects, look up RA/Dec from a source like:
   - [Simbad](http://simbad.u-strasbg.fr/simbad/) — most comprehensive
   - [NASA/IPAC Extragalactic Database](https://ned.ipac.caltech.edu/) — galaxies
   - Stellarium or SkySafari (desktop/mobile)
5. Only add `min_elevation` if this target specifically needs a different threshold than the global default

## Notes

- Deep sky objects are visible year-round in principle — the engine determines whether they're above the horizon *during the current night* at your location
- Southern hemisphere objects (negative declination) may never rise for high northern latitudes, and vice versa — the engine handles this automatically
- Meteor shower entries outside their `active_window_days` are silently skipped
