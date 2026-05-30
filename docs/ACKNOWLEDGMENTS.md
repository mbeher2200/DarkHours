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

### Astronomical Data
- **JPL Ephemeris (DE421)**: NASA Jet Propulsion Laboratory
- **Celestrak**: Two-Line Element sets (TLEs) for ISS, Hubble Space Telescope, Tiangong, and Starlink satellites, used for satellite pass prediction
  - https://celestrak.org/
  - Data is freely available for non-commercial use; see https://celestrak.org/data/update-policy.php

## Python Dependencies

- **skyfield** - Astronomical calculations (MIT License)
- **geopy** - Geocoding library (MIT License)
- **timezonefinder** - Timezone lookups (MIT License)
- **rasterio** - GeoTIFF raster I/O (BSD License)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

External data sources retain their original licenses as specified above.
