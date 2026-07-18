# Acknowledgments

## Development Assistance

This project was developed with substantial assistance from:
- **Claude** (Anthropic) — Core implementation, algorithms, architecture, and code generation
- **GitHub Copilot** (GitHub/Microsoft) — Code suggestions, refactoring, and implementation support

## Data Sources

### Weather Data
- **Open-Meteo**: Free weather forecast and historical climate data API
  - License: CC BY 4.0 (requires attribution)
  - Attribution: Link to https://open-meteo.com/ must be displayed where data is shown
  - https://open-meteo.com/

### Light Pollution Data
- **VIIRS Black Marble 2025**: Raw satellite radiance data from lightpollutionmap.info
- **Falchi World Atlas 2016**: World Atlas of Artificial Night Sky Brightness by Cinzano, Falchi, and Elvidge (GFZ Potsdam)
  - DOI: 10.5880.GFZ.1.4.2016.001
  - Reference: https://datapub.gfz-potsdam.de/

### Geospatial Data
- **Nominatim**: Reverse-geocoding and location name resolution, powered by OpenStreetMap contributors
  - https://nominatim.org/
  - Data licensed under ODbL
- **Overpass API**: OpenStreetMap query API used to fetch named protected and natural areas (national parks, wilderness areas, nature reserves) for the `--show-nearby` feature
  - https://overpass-api.de/
  - Data © OpenStreetMap contributors, licensed under ODbL
- **PAD-US 4.1 (Protected Areas Database of the United States)**: U.S. Geological Survey (USGS) national inventory of protected and conserved lands, used to build the DarkHours public-lands spatial index (`darkhours_padus_h3.parquet`)
  - Published by: U.S. Geological Survey Gap Analysis Project
  - Version: 4.1 (2023)
  - https://www.sciencebase.gov/catalog/item/652d4fc5d34e44db0e2ee45e
  - License: Public Domain (U.S. Government work — no restrictions on use)

### Astronomical Data
- **JPL Ephemeris (DE421)**: NASA Jet Propulsion Laboratory
- **HYG Star Database (v4.x)**: David Nash / Astronexus compilation of the Hipparcos, Yale Bright Star, and Gliese catalogs, used by `scripts/build_star_catalog.py` to build the web sky-dome star catalog (`apps/web/public/stars.v1.bin`)
  - https://github.com/astronexus/HYG-Database (now maintained at https://codeberg.org/astronexus/hyg)
  - License: CC BY-SA 4.0 (requires attribution)
- **Celestrak**: Two-Line Element sets (TLEs) for ISS, Hubble Space Telescope, Tiangong, and Starlink satellites, used for satellite pass prediction
  - https://celestrak.org/
  - Data is freely available for non-commercial use; see https://celestrak.org/data/update-policy.php
- **Meteor shower ZHR decay model**: double-exponential (asymmetric log-linear) activity-profile decay rate constants (`b_rise`/`b_decline` in `targets.json`), used to model how a shower's ZHR falls off before/after its peak date. See [docs/TARGETS.md § Meteor Shower ZHR Decay Model](TARGETS.md#meteor-shower-zhr-decay-model) for the formula and per-shower caveats.
  - Moorhead, A., Blaauw, R., Moser, D., Campbell-Brown, M., Brown, P., Cooke, W. (2019). *Meteor Shower Forecasting in Near-Earth Space.* Journal of Spacecraft and Rockets / [arXiv:1904.06370](https://arxiv.org/abs/1904.06370), Table 5 (NASA Meteoroid Environment Office)
  - Egal, A. et al. (2020). *Activity of the η-Aquariid and Orionid meteor showers.* Astronomy & Astrophysics, 640, A58
  - Peak-time-of-day (`peak_hour_utc`) sourced from [EarthSky's meteor shower guide](https://earthsky.org/astronomy-essentials/earthskys-meteor-shower-guide/) (2026/2027 apparition), drawn from AMS/IMO predictions

## Python Dependencies

- **skyfield** - Astronomical calculations (MIT License)
- **geopy** - Geocoding library (MIT License)
- **timezonefinder** - Timezone lookups (MIT License)
- **rasterio** - GeoTIFF raster I/O (BSD License)
- **geopandas** - Geospatial data processing (BSD License) — build-time only
- **h3** - Uber H3 hexagonal spatial indexing (Apache 2.0) — build-time only
- **fiona** - OGR/GDAL vector I/O (BSD License) — build-time only
- **pyarrow** - Apache Arrow columnar format / Parquet I/O (Apache 2.0) — build-time only

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

External data sources retain their original licenses as specified above.
