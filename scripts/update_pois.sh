#!/usr/bin/env bash
# Refresh the routable OSM POI index (cache/osm_pois.npz) from the latest Geofabrik
# US extract: download → build → clean up the multi-GB .pbf.
#
# We use the US extract (not all of North America): PAD-US is US-only and the engine's
# _is_in_us gate only consults the index inside the US bounding box, so a US extract is
# the matching, smaller download.
#
# Prereqs: pip install -r requirements-build.txt   (provides osmium + h3)
# Usage:   scripts/update_pois.sh
#
# Source data © OpenStreetMap contributors, ODbL. See docs/OSM_POI_INDEX.md.
set -euo pipefail
cd "$(dirname "$0")/.."

PBF_URL="${PBF_URL:-https://download.geofabrik.de/north-america/us-latest.osm.pbf}"
PBF_FILE="Temp/us-latest.osm.pbf"

mkdir -p Temp
# Remove the (large) .pbf on any exit so a failed/aborted run leaves no multi-GB file.
trap 'rm -f "$PBF_FILE"' EXIT

echo "Downloading $PBF_URL → $PBF_FILE …"
curl -L --fail -o "$PBF_FILE" "$PBF_URL"

echo "Building POI index …"
.venv/bin/python scripts/osm_poi_builder.py "$PBF_FILE"

echo "Done. Commit cache/osm_pois.npz to ship the refreshed index."
