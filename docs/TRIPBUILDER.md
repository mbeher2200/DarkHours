# tripbuilder.py — Reference Documentation

Compare multiple dark-sky locations across a date range to find the best combination of site and night.

```bash
python tripbuilder.py \
  --locations "Death Valley" "Sedona, AZ" "Grand Canyon Village, AZ" \
  --date-range 2026-06-01 2026-06-30
```

---

## Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--locations NAME [NAME ...]` | `-l` | — | One or more location names to compare (required) |
| `--date-range START END` | `-d` | — | Date range as YYYY-MM-DD YYYY-MM-DD (required) |
| `--top N` | `-n` | 10 | Number of nights in the ranked list |
| `--no-weather` | | off | Astronomical factors only — skip weather fetch |
| `--units imperial\|si` | | auto | Temperature/wind units |
| `--verbose` | `-v` | off | Debug output to stderr |

---

## Output

### Score matrix

A location × date grid where each cell is the Night Quality Score for that combination:

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
```

The **Best location** callout is the location with the highest average Night Quality Score across the date range.

### Top Nights ranked list

The best individual nights across all locations, with score component breakdown:

```
Top Nights:

  Rank  Date    Location             Score  Lunar  Dark  Bortle  Weather
  ────  ──────  ──────────────────  ──────  ─────  ────  ──────  ───────
     1  Jun 14  Grand Canyon Vill…  9.4/10   10.0   9.3    10.0        —
     2  Jun 13  Death Valley        9.3/10    9.8   9.3    10.0        —
     3  Jun 14  Death Valley        9.3/10   10.0   9.2    10.0        —
```

The `—` in the Weather column appears for dates beyond the 16-day forecast window, where no weather data is available. The Night Quality Score for those nights is calculated without a weather component (weights redistribute automatically).

---

## Scoring in a Trip Context

Trip Builder uses the same Night Quality Score formula as the single-night report (weighted geometric mean of Lunar, Dark Hours, Bortle, and Weather). The behavior for weather:

- **Within the 16-day forecast window** — weather data is fetched and the full four-factor score is used
- **Beyond 16 days** — weather is not available; the remaining weights (Lunar 25 / Dark Hours 25 / Bortle 10) are renormalized proportionally to ≈41.7%, 41.7%, and 16.7%. The `~` marker appears next to scored-with-weather cells in the matrix.
- **`--no-weather`** — forces the no-weather weighting for all dates; useful for a pure astronomical comparison across a longer range

Because both near and far dates use the same weight-redistribution logic, scores for different dates within the same run are directly comparable.

---

## Caching

Computations are cached per location per date. The first run across a date range computes everything; subsequent runs for the same locations and dates return instantly. Weather forecasts expire after their natural freshness window; astronomical data is cached indefinitely (deterministic from ephemeris).

---

## Use Cases

**"Where should I go this month for the best dark skies?"**
```bash
python tripbuilder.py \
  --locations "Death Valley" "Joshua Tree" "Anza-Borrego" \
  --date-range 2026-06-01 2026-06-30
```

**"What are the top 5 nights across three sites this summer?"**
```bash
python tripbuilder.py \
  --locations "Sedona, AZ" "Grand Canyon Village, AZ" "Bryce Canyon, UT" \
  --date-range 2026-06-01 2026-08-31 \
  --top 5 --no-weather
```

**"I have a trip booked — which of the two nights will be better?"**
```bash
python tripbuilder.py \
  --locations "Bryce Canyon, UT" \
  --date-range 2026-07-14 2026-07-15 \
  --top 2
```

Trip Builder is the right tool when you're choosing *between* dates or locations. For a single confirmed night at a confirmed location, `pynightsky.py` gives the full detail — weather table, targets, nearby skies.
