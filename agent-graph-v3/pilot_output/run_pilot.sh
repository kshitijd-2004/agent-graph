#!/bin/bash
# AgentGraph V3 Pilot Execution Script
# Runs 28 scenarios: 8 benign, 16 single-LEP, 4 counterfactual

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_ROOT="${SCRIPT_DIR}/.."
OUTPUT_DIR="${V3_ROOT}/pilot_output"
FIXTURE_DIR="${V3_ROOT}/workspace_fixtures"
REPORT="${OUTPUT_DIR}/pilot_audit_report.md"
RECORDS="${OUTPUT_DIR}/pilot_records.jsonl"

mkdir -p "${OUTPUT_DIR}"

echo "============================================================"
echo "AGENT-GRAPH V3 PILOT — SCHEMA v3.0.0"
echo "============================================================"
echo "Root:  ${V3_ROOT}"
echo "Out:   ${OUTPUT_DIR}"
echo "Fix:   ${FIXTURE_DIR}"
echo "============================================================"

cd "${V3_ROOT}"

echo ""
echo "[1/2] Running 28 scenarios..."
python pilot/run_pilot.py \
    --dry-run \
    --output-dir "${OUTPUT_DIR}" \
    --fixture-dir "${FIXTURE_DIR}" \
    2>&1 | tail -20

echo ""
echo "[2/2] Pilot complete."
echo ""
echo "Outputs:"
echo "  Report:  ${REPORT}"
echo "  Records: ${RECORDS}"
echo ""
echo "To run with real model:"
echo "  LLM_API_KEY=sk-... python pilot/run_pilot.py --real-model"
echo ""

# Print summary if records exist
if [ -f "${RECORDS}" ]; then
    total=$(wc -l < "${RECORDS}")
    echo "Executions recorded: ${total}"
    echo ""
    echo "--- Last 5 execution IDs ---"
    tail -5 "${RECORDS}" | python3 -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    print(f\"  {r['execution_id']} | {r['task_family']} | {r['condition']} | "
          f"events={r['num_events']} | fired={r['injection_fired']}\")
"
fi

echo ""
echo "============================================================"
echo "PILOT FINISHED"
echo "============================================================"
