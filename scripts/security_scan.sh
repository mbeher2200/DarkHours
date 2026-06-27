#!/usr/bin/env bash
# Repeatable security gate — SAST + dependency audit + container/secret/IaC scan.
#
#   pip install -r requirements-security.txt   # bandit, pip-audit, semgrep
#   scripts/security_scan.sh                    # Trivy runs from its Docker image
#
# Same checks the CI workflow runs (.github/workflows/security.yml). Exits non-zero
# if any check fails. Accepted findings are suppressed at their source with a
# documented reason (e.g. PyNightSkyPredictor/_http.py: validated-egress urlopen).
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
run() {  # run "<label>" cmd...
  local label="$1"; shift
  echo ""; echo "=== $label ==="
  if "$@"; then echo "  PASS: $label"; else echo "  FAIL: $label"; fail=1; fi
}

# 1. Bandit — Python SAST (medium+ severity)
run "Bandit (SAST)" bandit -r PyNightSkyPredictor apps -ll -q

# 2. pip-audit — known CVEs in the runtime (image) dependencies
run "pip-audit (deps)" pip-audit -r requirements-api.txt

# 3. Semgrep — community python + security-audit + secrets rulesets
run "Semgrep (SAST)" semgrep --error -q --metrics off \
  --config p/python --config p/security-audit --config p/secrets \
  PyNightSkyPredictor apps pynightsky.py tripbuilder.py

# 4. gitleaks — secrets across the full git history (needs full clone / fetch-depth 0)
run "gitleaks (secret history)" docker run --rm -v "$PWD:/repo" \
  ghcr.io/gitleaks/gitleaks:latest git /repo --redact

# 5. Trivy (from its Docker image) — IaC misconfig + repo secrets + image CVEs
TRIVY=(docker run --rm -v "$PWD:/repo" -w /repo aquasec/trivy:latest)
SKIP="--skip-dirs .venv,Sky,cdk.out,**/cdk.out,tools,.git"
run "Trivy (IaC/Dockerfile misconfig)" "${TRIVY[@]}" config --severity HIGH,CRITICAL --exit-code 1 -q $SKIP .
run "Trivy (repo secrets)"             "${TRIVY[@]}" fs --scanners secret --exit-code 1 -q $SKIP .

if docker image inspect pynightsky-worker:latest >/dev/null 2>&1; then
  docker save pynightsky-worker:latest -o /tmp/_pns_scan.tar
  run "Trivy (image CVEs)" docker run --rm -v /tmp:/scan -v "$PWD:/repo" aquasec/trivy:latest \
    image --input /scan/_pns_scan.tar --ignorefile /repo/.trivyignore \
    --severity HIGH,CRITICAL --exit-code 1 -q
  rm -f /tmp/_pns_scan.tar
else
  echo ""; echo "=== Trivy (image CVEs) ==="; echo "  SKIP: pynightsky-worker:latest not built locally (CI builds + scans it)"
fi

echo ""
[ "$fail" -eq 0 ] && echo "==> SECURITY SCAN PASSED" || echo "==> SECURITY SCAN FAILED"
exit "$fail"
