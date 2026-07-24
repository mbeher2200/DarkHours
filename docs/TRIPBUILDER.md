# tripbuilder.py Reference

Compare several dark-sky sites across a date range. Find the best pairing of place and night.

```bash
python tripbuilder.py \
  --locations "Death Valley" "Sedona, AZ" "Grand Canyon Village, AZ" \
  --date-range 2026-06-01 2026-06-30
```

---

## Options

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--locations NAME [NAME ...]` | `-l` | none | One or more location names to compare (required) |
| `--date-range START END` | `-d` | none | Date range as YYYY-MM-DD YYYY-MM-DD (required) |
| `--top N` | `-n` | 10 | Number of nights in the ranked list |
| `--no-weather` | | off | Astronomical factors only. Skips the weather fetch |
| `--units imperial\|si` | | auto | Temperature and wind units |
| `--verbose` | `-v` | off | Debug output to stderr |

---

## Output

### Score matrix

A location by date grid. Each cell is the Night Quality Score for that pairing:

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

The Best location callout is the site with the highest average Night Quality Score across the range.

### Top Nights ranked list

The best individual nights across every location, with the score broken into parts:

```
Top Nights:

  Rank  Date    Location             Score  Lunar  Dark  Bortle  Weather
  ────  ──────  ──────────────────  ──────  ─────  ────  ──────  ───────
     1  Jun 14  Grand Canyon Vill…  9.4/10   10.0   9.3    10.0        —
     2  Jun 13  Death Valley        9.3/10    9.8   9.3    10.0        —
     3  Jun 14  Death Valley        9.3/10   10.0   9.2    10.0        —
```

A dash in the Weather column means the date sits past the 16-day forecast window, so no weather data exists yet. The score for those nights leaves weather out, and the other weights grow to fill the gap.

---

## Scoring in a trip context

Trip Builder uses the same Night Quality Score formula as the single-night report. It's a weighted geometric mean of Lunar, Dark Hours, Bortle, and Weather. Weather works like this:

- **Inside the 16-day forecast window.** Weather data comes back, and the full four-factor score runs.
- **Past 16 days.** No weather is available. The other weights (Lunar 25, Dark Hours 25, Bortle 10) grow proportionally to about 41.7%, 41.7%, and 16.7%. A `~` marker sits next to cells that were scored with weather.
- **`--no-weather`.** Forces the no-weather weighting for every date. Handy for a pure astronomy comparison across a long range.

Near dates and far dates run through the same redistribution logic. So scores from one run line up against each other, no matter the date.

---

## Caching

Every computation is cached per location per date. The first run across a range does all the work. Run it again for the same sites and dates and it returns right away. Weather forecasts expire on their own freshness clock. Astronomical data caches forever, since it's deterministic from the ephemeris.

---

## Use cases

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

**"I have a trip booked. Which of the two nights will be better?"**
```bash
python tripbuilder.py \
  --locations "Bryce Canyon, UT" \
  --date-range 2026-07-14 2026-07-15 \
  --top 2
```

Reach for Trip Builder when you're choosing between dates or places. For one confirmed night at one confirmed spot, `darkhours.py` gives the full detail: weather table, targets, nearby skies.
