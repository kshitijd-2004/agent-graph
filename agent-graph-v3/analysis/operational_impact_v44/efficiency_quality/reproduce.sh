#!/usr/bin/env bash
# Efficiency/quality extension — single reproduction entry point.
# Usage: bash analysis/operational_impact_v44/efficiency_quality/reproduce.sh [--skip-gen]
#   --skip-gen  reuse out/generation_length_per_trace.jsonl
# Reads the BASE operational-impact artifacts (hash-pinned — verify they are
# unchanged) and the frozen V4.4 inputs (read-only). Writes only to this
# directory. No LLM calls, no GPU, no re-runs, no threshold sweeps.
set -euo pipefail
REPO=/u/kdhande/agent-graph/agent-graph-v3
D=$REPO/analysis/operational_impact_v44/efficiency_quality
SKIP_GEN=0
[ "${1:-}" = "--skip-gen" ] && SKIP_GEN=1

echo "## 0) pre-conditions: frozen anchors + base-artifact hash pin"
python3.11 $REPO/analysis/behavioral_divergence_final/v44_freeze/verify.py
python3.11 $REPO/analysis/operational_impact_v44/extraction/verify_freeze_anchors.py
(cd $REPO/analysis/operational_impact_v44 && sha256sum -c efficiency_quality/out/pre_extension_hashes.txt | tail -2)

if [ $SKIP_GEN -eq 0 ]; then
  echo "## 1) §5 generation-length extraction (~12 min; staged trace pool, read-only)"
  PYTHONHASHSEED=0 python3.11 $D/gen_extract.py
else
  echo "## 1) generation table reused"
fi

echo "## 2) main analysis (fixture-cluster bootstrap; ~2 min)"
python3.11 $D/analyze_efficiency_quality.py

echo "## 3) H2 tail contrasts + mode/population robustness"
python3.11 $D/supplemental_contrasts.py

echo "## 4) figures (needs matplotlib; python3.12 here)"
python3.12 $D/make_figures.py

echo "## 5) integrity & regression tests (10)"
cd $D && PYTHONPATH=. python3.11 tests/test_integrity.py

echo "## 6) post-analysis: base-artifact hash pin + frozen anchors"
(cd $REPO/analysis/operational_impact_v44 && sha256sum -c efficiency_quality/out/pre_extension_hashes.txt)
python3.11 $REPO/analysis/operational_impact_v44/extraction/verify_freeze_anchors.py
python3.11 $REPO/analysis/behavioral_divergence_final/v44_freeze/verify.py
echo "EXTENSION REPRODUCTION COMPLETE"
