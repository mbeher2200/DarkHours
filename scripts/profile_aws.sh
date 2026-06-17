#!/usr/bin/env bash
# Profile find_nearby against the REAL aws backend with profiling on.
#
# Prereqs:
#   1. An authenticated AWS session (e.g. `aws sso login --profile <p>` / `aws login`).
#   2. Export the deployment resource names (kept out of source on purpose):
#        export PYNIGHTSKY_RASTER_BUCKET=<your raster bucket>
#        export PYNIGHTSKY_CACHE_TABLE=<your cache table>
#      (place index / route calculator default to the CDK names; override if changed.)
#
# Usage:
#   scripts/profile_aws.sh                                  # Phoenix, 60 mi
#   scripts/profile_aws.sh --lat 34.05 --lon -118.24 --radius 60
#   scripts/profile_aws.sh --workers 1                      # serial baseline
#
# Makes live, billable (tiny / free-tier) AWS calls and writes geocode entries
# into the shared cache.
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export PYNIGHTSKY_BACKEND=aws
export PYNIGHTSKY_RASTER_BUCKET="${PYNIGHTSKY_RASTER_BUCKET:?set PYNIGHTSKY_RASTER_BUCKET to your raster bucket}"
export PYNIGHTSKY_CACHE_TABLE="${PYNIGHTSKY_CACHE_TABLE:?set PYNIGHTSKY_CACHE_TABLE to your cache table}"
export PYNIGHTSKY_PLACE_INDEX="${PYNIGHTSKY_PLACE_INDEX:-pynightsky-place-index}"
# Drive times use the resource-less GeoRoutes API (no route-calculator name needed).
export PYNIGHTSKY_PROFILE=1

echo "Authenticated as: $(aws sts get-caller-identity --query Arn --output text)"
exec .venv/bin/python scripts/aws_one_search.py "$@"
