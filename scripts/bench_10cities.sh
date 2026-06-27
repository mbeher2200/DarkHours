#!/usr/bin/env bash
# Run the 10-city find_nearby() benchmark against the real AWS backend.
#
# Prereqs (same as profile_aws.sh):
#   1. Authenticated AWS session (e.g. `aws sso login --profile <p>`)
#   2. Export resource names:
#        export PYNIGHTSKY_RASTER_BUCKET=<your raster bucket>
#        export PYNIGHTSKY_CACHE_TABLE=<your cache table>
#
# Usage:
#   scripts/bench_10cities.sh                    # 3 runs per city
#   scripts/bench_10cities.sh --runs 5           # 5 runs per city
#   scripts/bench_10cities.sh --no-cache-reset   # fully warm path
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_PROFILE="${AWS_PROFILE:-default}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export PYNIGHTSKY_BACKEND=aws
export PYNIGHTSKY_RASTER_BUCKET="${PYNIGHTSKY_RASTER_BUCKET:?set PYNIGHTSKY_RASTER_BUCKET to your raster bucket}"
export PYNIGHTSKY_CACHE_TABLE="${PYNIGHTSKY_CACHE_TABLE:?set PYNIGHTSKY_CACHE_TABLE to your cache table}"
export PYNIGHTSKY_PLACE_INDEX="${PYNIGHTSKY_PLACE_INDEX:-pynightsky-place-index}"
export PYNIGHTSKY_PROFILE=1

echo "Authenticated as: $(aws sts get-caller-identity --query Arn --output text)"
exec .venv/bin/python scripts/bench_10cities.py "$@"
