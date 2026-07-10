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
| `active_window_days` | Yes | Total number of days the shower is active (centred on peak) — also acts as a floor for the decay-derived activity gate, see [ZHR Decay Model](#meteor-shower-zhr-decay-model) below |
| `peak_zhr` | Yes | Zenithal hourly rate at peak (meteors/hour under ideal conditions) |
| `b_rise` | No | Decay-rate constant for days *before* peak. Omitted → treated as 0, meaning ZHR is flat at `peak_zhr` for the whole active window (pre-decay-model behavior) |
| `b_decline` | No | Decay-rate constant for days *at/after* peak |
| `peak_hour_utc` | No | Approximate UTC hour-of-day (0–24, fractional) of peak activity for a specific reference apparition. Omitted → only the peak date is shown, no clock time |

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
  "peak_zhr": 150,
  "b_rise": 0.150,
  "b_decline": 0.462,
  "peak_hour_utc": 5.73
}
```

---

## Meteor Shower ZHR Decay Model

A shower's ZHR isn't constant across its active window — it peaks on `peak_day` and falls off on either side. `PyNightSkyPredictor/targets.py` (`effective_zhr()`) models this with the same double-exponential (asymmetric log-linear) profile used throughout meteor science literature:

```
ZHR(t) = ZHR_peak · 10^(−B·|t|)
```

`t` is the signed day-offset from peak; `b_rise` applies for `t < 0` (approaching peak), `b_decline` for `t ≥ 0` (receding from peak) — real showers commonly rise faster than they decline.

**Days vs. solar longitude:** the canonical form of this equation is parameterized by solar longitude (λ☉), not calendar days, because Earth's orbital speed varies through the year (fastest near perihelion in early January). This catalog already keys every shower off a fixed `peak_month`/`peak_day` rather than a computed λ☉ crossing, so day-offset is used as a deliberate stand-in for λ☉. Solar longitude advances ≈0.9856°/day on average, so `B` values (published per degree of λ☉) carry over to per-day within ~1.5% error for most of the year — up to ~3.4% near perihelion, relevant to Quadrantids (peak Jan 3) and marginally Ursids (peak Dec 22).

### Zenith / radiant-altitude correction

The visual ZHR convention assumes the radiant sits at the zenith; the rate an observer actually sees at any other altitude is reduced roughly by `sin(radiant_altitude)`. `predictor.py`'s condition-vector pipeline inverts this to estimate real observed rate:

```
local_rate = ZHR(t) · sin(radiant_altitude)
```

Below 25° radiant altitude (`_LOW_RADIANT_ALT_DEG` in `predictor.py`; `sin(25°) ≈ 0.42`), local rate collapses to under 45% of the decayed-ZHR figure from foreshortening/atmospheric extinction alone — even under an otherwise clear, dark, moonless sky. This triggers a `low_radiant` blocker, distinct from the existing weather / light-pollution / moon-washout blockers.

### Activity-window gate

Before running the full geometry pass for a shower on a given night, `_gate_half_window_days()` cheaply pre-filters using `MAX(active_window_days / 2, decay-derived half-window)`, where the decay-derived half-window is the day-offset at which `ZHR(t)` drops below a 2/hr floor (`_ZHR_DECAY_FLOOR`), using whichever of `b_rise`/`b_decline` is shallower. Taking the *larger* of the two — not the smaller — means the decay math can only widen a shower's computed active window relative to the curated `active_window_days`, never silently narrow it below what's already been hand-tuned.

### Sourced decay constants

`b_rise` / `b_decline` for the 11 catalog showers are sourced from:

- Moorhead, A., Blaauw, R., Moser, D., Campbell-Brown, M., Brown, P., Cooke, W. (2019). *Meteor Shower Forecasting in Near-Earth Space.* Journal of Spacecraft and Rockets / [arXiv:1904.06370](https://arxiv.org/abs/1904.06370), Table 5 — the NASA Meteoroid Environment Office's operational double-exponential fit to CMOR radar flux measurements.
- Egal, A. et al. (2020). *Activity of the η-Aquariid and Orionid meteor showers.* Astronomy & Astrophysics, 640, A58 — used to corroborate the Eta Aquariid and Orionid values against a visual/video-based fit; Moorhead's radar-fit point estimates for both showers fall inside Egal's reported range.

`peak_hour_utc` values are sourced from [EarthSky](https://earthsky.org/astronomy-essentials/earthskys-meteor-shower-guide/)'s 2026/2027 meteor shower guide (itself drawn from AMS/IMO predictions) and the American Meteor Society calendar.

**Caveats:**
- Moorhead et al.'s table is fit to *radar* flux, not visual ZHR — radar profiles are typically sharper than visual ones (radar detects far more faint meteors, amplifying the apparent gradient). This pairs a radar-fit *shape* (`b_rise`/`b_decline`) with each catalog entry's existing *visual* `peak_zhr` *amplitude* — a reasonable approximation (shape is more transferable than amplitude across detection methods) but a known provenance mismatch.
- Perseids and Leonids use only the "base component" of Moorhead's two-component fit — the paper also gives a sharper "peak spike" component with a degenerate `Bm = 0` that isn't usable in this single-formula model. This means the model slightly *under*-predicts ZHR right at peak night for these two showers specifically (a safe direction to be wrong in).
- Northern/Southern Taurids fit `Bm ≈ 0` (a genuinely flat, broad plateau, consistent with their well-known low-level long tail) — substituted with the `b_rise` value since a literal 0 is degenerate for the decay formula (implies the shower never decays). A modeling choice, not a literature value.
- `peak_hour_utc` is pinned to the 2026/2027 apparition specifically and will drift a few hours in other years (leap-year solar-longitude cycle) — the same order of imprecision this catalog already accepts for the fixed `peak_month`/`peak_day` fields, just one level deeper.
- These values were extracted from the primary sources via automated tooling, not manually cross-checked cell-by-cell — a spot-check against arXiv:1904.06370 Table 5 is a reasonable follow-up before leaning on exact figures for anything beyond "roughly how fast does this decay."
- The CLI (`pynightsky.py`) does not yet surface `zhr_effective` / `local_rate_at_peak` — only the web app's report does. This is a known parity gap.

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
6. For a new `meteor_shower` entry, source `b_rise`/`b_decline` from a published double-exponential activity-profile fit (e.g. the IMO Working List of Visual Meteor Showers, or a paper like Moorhead et al. 2019 — see [ZHR Decay Model](#meteor-shower-zhr-decay-model)) rather than guessing a value; if no such fit is available, omit them and the shower falls back to the pre-decay-model flat-ZHR behavior

## Notes

- Deep sky objects are visible year-round in principle — the engine determines whether they're above the horizon *during the current night* at your location
- Southern hemisphere objects (negative declination) may never rise for high northern latitudes, and vice versa — the engine handles this automatically
- Meteor shower entries outside their (decay-widened) `active_window_days` are silently skipped — see [ZHR Decay Model](#meteor-shower-zhr-decay-model)
