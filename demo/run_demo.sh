#!/usr/bin/env bash
# Wave 5B demo runner — exercises all 4 scenarios via LocalClient (no HTTP).
#
# Usage: ./demo/run_demo.sh
# Exit: 0 if all 4 scenarios produce expected outcomes, 1 otherwise.

set -u
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"

echo "=== ACP Wave 5B demo runner ==="
echo "[1/4] Running scenarios..."

FAILED=0
SUMMARY=""

for scenario in 01 02 03 04; do
  echo ""
  echo "  --- scenario $scenario ---"
  if $PY demo/oncall_agent.py --scenario "$scenario" > "/tmp/acp_demo_${scenario}.json" 2>&1; then
    STATUS="PASS"
    tail -12 "/tmp/acp_demo_${scenario}.json" | head -11
  else
    STATUS="FAIL"
    FAILED=1
    cat "/tmp/acp_demo_${scenario}.json"
  fi
  SUMMARY="${SUMMARY}  scenario ${scenario}: ${STATUS}
"
done

echo ""
echo "[2/4] Summary"
printf "%s" "$SUMMARY"

echo ""
echo "[3/4] Verifying integration + adversarial test suite..."
if $PY -m pytest tests/integration tests/adversarial -q --no-header 2>&1 | tail -3; then
  echo "  integration + adversarial: PASS"
else
  FAILED=1
  echo "  integration + adversarial: FAIL"
fi

echo ""
echo "[4/4] Done."
if [ "$FAILED" -eq 0 ]; then
  echo "ALL GREEN."
  exit 0
else
  echo "SOMETHING FAILED."
  exit 1
fi
