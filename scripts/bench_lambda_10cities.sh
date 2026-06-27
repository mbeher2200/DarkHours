#!/usr/bin/env bash
# Throwaway-Lambda 10-city benchmark (before → after comparison).
#
# Creates a temporary Lambda from the current branch image, invokes it across
# all 10 test cities, scrapes [profile] lines from CloudWatch, and tears
# everything down.  Follows the in-region perf validation recipe in CLAUDE.md.
#
# Prereqs:
#   1. Authenticated AWS session with ECR push + Lambda create/invoke/delete perms
#   2. Export resource names:
#        export PYNIGHTSKY_RASTER_BUCKET=<bucket>
#        export PYNIGHTSKY_CACHE_TABLE=<table>
#        export PYNIGHTSKY_WORKER_ROLE_ARN=<arn:aws:iam::…:role/…>
#        export PYNIGHTSKY_ECR_REPO=<account>.dkr.ecr.<region>.amazonaws.com/pynightsky-worker
#   3. Docker running locally
#
# Usage:
#   scripts/bench_lambda_10cities.sh              # build + run + teardown
#   scripts/bench_lambda_10cities.sh --skip-build # reuse existing :proftest image
#   scripts/bench_lambda_10cities.sh --runs 3     # warm invocations per city (default 3)
#
# Output:
#   Markdown table of median phase timings per city → stdout
#   Raw CloudWatch logs → /tmp/pynightsky-proftest-<timestamp>.log
set -euo pipefail
cd "$(dirname "$0")/.."

FN_NAME="pynightsky-proftest"
TAG="proftest"
RUNS=3
SKIP_BUILD=false
RADIUS=60

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-build) SKIP_BUILD=true; shift ;;
        --runs) RUNS="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

: "${PYNIGHTSKY_RASTER_BUCKET:?set PYNIGHTSKY_RASTER_BUCKET}"
: "${PYNIGHTSKY_CACHE_TABLE:?set PYNIGHTSKY_CACHE_TABLE}"
: "${PYNIGHTSKY_WORKER_ROLE_ARN:?set PYNIGHTSKY_WORKER_ROLE_ARN}"
: "${PYNIGHTSKY_ECR_REPO:?set PYNIGHTSKY_ECR_REPO}"
REGION="${AWS_REGION:-us-east-1}"

IMAGE_URI="${PYNIGHTSKY_ECR_REPO}:${TAG}"
LOG_FILE="/tmp/pynightsky-proftest-$(date +%Y%m%d-%H%M%S).log"

cleanup() {
    echo ""
    echo "=== Tearing down throwaway resources ==="
    aws lambda delete-function --function-name "$FN_NAME" 2>/dev/null && echo "  deleted Lambda $FN_NAME" || true
    aws ecr batch-delete-image --repository-name pynightsky-worker \
        --image-ids imageTag="$TAG" --region "$REGION" 2>/dev/null \
        && echo "  deleted ECR image :$TAG" || true
    aws logs delete-log-group --log-group-name "/aws/lambda/$FN_NAME" \
        --region "$REGION" 2>/dev/null && echo "  deleted log group" || true
}
trap cleanup EXIT

# ── 1. Build ─────────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "false" ]]; then
    echo "=== Building single-platform image ==="
    docker build -f Dockerfile.worker \
        --platform linux/amd64 \
        --provenance=false \
        -t "${IMAGE_URI}" .
    echo "=== Pushing to ECR ==="
    aws ecr get-login-password --region "$REGION" \
        | docker login --username AWS --password-stdin \
            "$(echo "$PYNIGHTSKY_ECR_REPO" | cut -d/ -f1)"
    docker push "${IMAGE_URI}"
fi

# ── 2. Create throwaway Lambda ────────────────────────────────────────────────
echo "=== Creating throwaway Lambda ==="
aws lambda create-function \
    --function-name "$FN_NAME" \
    --package-type Image \
    --code ImageUri="${IMAGE_URI}" \
    --role "$PYNIGHTSKY_WORKER_ROLE_ARN" \
    --timeout 300 \
    --memory-size 2048 \
    --region "$REGION" \
    --environment "Variables={PYNIGHTSKY_BACKEND=aws,PYNIGHTSKY_PROFILE=1,\
PYNIGHTSKY_RASTER_BUCKET=${PYNIGHTSKY_RASTER_BUCKET},\
PYNIGHTSKY_CACHE_TABLE=${PYNIGHTSKY_CACHE_TABLE},\
PYNIGHTSKY_PLACE_INDEX=${PYNIGHTSKY_PLACE_INDEX}}" \
    --output text --query 'FunctionArn' > /dev/null
echo "  waiting for Active state..."
aws lambda wait function-active --function-name "$FN_NAME" --region "$REGION"

# ── 3. Invoke cities ──────────────────────────────────────────────────────────
EVENTS_DIR="scripts/lambda_10cities"
echo "=== Invoking 10 cities × ${RUNS} warm runs ==="
for event_file in "$EVENTS_DIR"/*.json; do
    city=$(basename "$event_file" .json)
    echo -n "  $city: "
    for run in $(seq 1 "$RUNS"); do
        echo -n "${run}/"
        AWS_MAX_ATTEMPTS=1 aws lambda invoke \
            --function-name "$FN_NAME" \
            --cli-read-timeout 120 \
            --payload "fileb://${event_file}" \
            --region "$REGION" \
            /dev/null > /dev/null
        # Bump a dummy env var to force cold container before next city
        if [[ "$run" == "$RUNS" && "$city" != "minneapolis" ]]; then
            aws lambda update-function-configuration \
                --function-name "$FN_NAME" \
                --environment "Variables={PYNIGHTSKY_BACKEND=aws,PYNIGHTSKY_PROFILE=1,\
PYNIGHTSKY_RASTER_BUCKET=${PYNIGHTSKY_RASTER_BUCKET},\
PYNIGHTSKY_CACHE_TABLE=${PYNIGHTSKY_CACHE_TABLE},\
PYNIGHTSKY_PLACE_INDEX=${PYNIGHTSKY_PLACE_INDEX},DUMMY=$RANDOM}" \
                --region "$REGION" > /dev/null 2>&1
            aws lambda wait function-updated \
                --function-name "$FN_NAME" --region "$REGION" 2>/dev/null || true
        fi
    done
    echo ""
done

# ── 4. Scrape CloudWatch ──────────────────────────────────────────────────────
echo "=== Waiting 10 s for logs to flush to CloudWatch ==="
sleep 10
echo "=== Fetching logs → $LOG_FILE ==="
aws logs filter-log-events \
    --log-group-name "/aws/lambda/$FN_NAME" \
    --region "$REGION" \
    --query 'events[*].message' \
    --output text > "$LOG_FILE"

echo ""
echo "=== Phase timing summary (median per city) ==="
echo ""
# Parse and summarise with Python
.venv/bin/python - "$LOG_FILE" <<'PYEOF'
import sys, re, statistics, collections

log_file = sys.argv[1]
city_order = [
    "bench-los-angeles", "bench-new-york", "bench-chicago", "bench-phoenix",
    "bench-houston", "bench-denver", "bench-seattle", "bench-miami",
    "bench-atlanta", "bench-minneapolis",
]
city_label = {
    "bench-los-angeles": "Los Angeles",
    "bench-new-york":    "New York",
    "bench-chicago":     "Chicago",
    "bench-phoenix":     "Phoenix",
    "bench-houston":     "Houston",
    "bench-denver":      "Denver",
    "bench-seattle":     "Seattle",
    "bench-miami":       "Miami",
    "bench-atlanta":     "Atlanta",
    "bench-minneapolis": "Minneapolis",
}

PHASE_COLS = [
    ("raster window reads", "raster"),
    ("extract dark candidates", "extract"),
    ("cluster + band select", "cluster"),
    ("light dome detection", "dome det"),
    ("dome naming (geocode)", "dome nm"),
    ("jit geocode candidates", "jit geo"),
    ("drive times (aws)", "drive"),
]

# Normalise legacy separate raster phase names to combined key
ALIASES = {
    "viirs window read":  "raster window reads",
    "falchi window read": "raster window reads",
}

per_job = collections.defaultdict(list)
current_job = None
job_phases = {}

with open(log_file) as f:
    for line in f:
        # Job boundary: "Processing job bench-los-angeles"
        m = re.search(r'Processing job (bench-[a-z-]+)', line)
        if m:
            if current_job and job_phases:
                per_job[current_job].append(job_phases)
            current_job = m.group(1)
            job_phases = {}
            continue
        if not current_job:
            continue
        # Phase line: "[profile] raster window reads      123.4 ms  (cache ...)"
        pm = re.search(r'\[profile\]\s+(.*?)\s{2,}([\d.]+)\s*ms', line)
        if pm:
            name = ALIASES.get(pm.group(1).strip(), pm.group(1).strip())
            val  = float(pm.group(2))
            if "TOTAL" not in name:
                job_phases[name] = job_phases.get(name, 0) + val
        # Total line: "[profile] TOTAL (sum of phases)   456.7 ms"
        tm = re.search(r'\[profile\]\s+TOTAL.*?([\d.]+)\s*ms', line)
        if tm:
            job_phases["_total"] = float(tm.group(1))

if current_job and job_phases:
    per_job[current_job].append(job_phases)

header = ["City", "total"] + [c for _, c in PHASE_COLS]
widths = [14] + [7] * len(header[1:])
sep = " | "
print(sep.join(f"{h:<{w}}" for h, w in zip(header, widths)))
print(sep.join("-" * w for w in widths))
all_totals = []
for job_id in city_order:
    runs = per_job.get(job_id, [])
    if not runs:
        print(f"{city_label.get(job_id, job_id):<14}  (no data)")
        continue
    totals_v = [r["_total"] for r in runs if "_total" in r]
    total_med = statistics.median(totals_v) if totals_v else None
    if total_med:
        all_totals.append(total_med)
    def med_ms(key):
        vs = [r[key] for r in runs if key in r]
        return f"{statistics.median(vs):>5.0f}" if vs else "    —"
    row = [city_label.get(job_id, job_id),
           f"{total_med:>5.0f}" if total_med else "    —"]
    for key, _ in PHASE_COLS:
        row.append(med_ms(key))
    print(sep.join(f"{str(v):<{w}}" for v, w in zip(row, widths)))
print(sep.join("-" * w for w in widths))
if all_totals:
    print(f"{'median':<14} | {statistics.median(all_totals):>5.0f}")
PYEOF

echo ""
echo "Raw log saved to: $LOG_FILE"
echo "(trap will now clean up Lambda, ECR image, log group)"
