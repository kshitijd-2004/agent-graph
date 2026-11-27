#!/usr/bin/env bash
# V4.4 operational-impact analysis — single reproduction entry point.
# Usage: bash analysis/operational_impact_v44/reproduce.sh [--skip-extract]
#   --skip-extract  reuse existing out/ tables (aggregation + downstream only)
# Honors contract constraints: no LLM calls, no GPU, no re-runs, no
# threshold sweeps, no grading regeneration; frozen inputs are read-only.
set -euo pipefail
REPO=/u/kdhande/agent-graph/agent-graph-v3
D=$REPO/analysis/operational_impact_v44
SKIP_EXTRACT=0
[ "${1:-}" = "--skip-extract" ] && SKIP_EXTRACT=1

echo "## 0) frozen-contract anchors (must pass)"
python3.11 $REPO/analysis/behavioral_divergence_final/v44_freeze/verify.py
python3.11 $D/extraction/verify_freeze_anchors.py

if [ $SKIP_EXTRACT -eq 0 ]; then
  echo "## 1) extraction (deterministic, ~5 min)"
  PYTHONHASHSEED=0 python3.11 $D/extraction/extract_operational.py
  python3.11 $D/extraction/annotate_runtime_trust.py
else
  echo "## 1) extraction SKIPPED (reusing out/)"
fi

echo "## 2) aggregation (fixture-cluster bootstrap; ~2 min)"
python3.11 $D/aggregate_operational.py

echo "## 3) mechanisms + deterministic example selection"
python3.11 $D/extract_mechanisms.py

echo "## 4) figures (needs matplotlib)"
python3.12 $D/make_figures.py

echo "## 5) integrity & regression tests"
cd $D && PYTHONPATH=. python3.11 tests/test_integrity.py

echo "## 6) post-analysis freeze re-verification (required)"
python3.11 $D/extraction/verify_freeze_anchors.py
python3.11 $REPO/analysis/behavioral_divergence_final/v44_freeze/verify.py

echo "ALL STEPS COMPLETE"
