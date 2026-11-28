#!/bin/bash
# Research MP with the "sources" poison (fss) vs the verdict poison (fsr), same clean runs.
# Run from /work/nvme/biha/$USER after the fss jobs finish.
FX=ag-run/agent-graph-v3/workspace_fixtures
echo "fss slices done: $(ls traces/pilot/fss/node_*/benchmark_summary.json 2>/dev/null | wc -l) / 32"
cat traces/pilot/fss/node_*/benchmark_records.jsonl > pe_fss.jsonl
grep -h -v '"lep_code": "LEP_' traces/pilot/fsr/node_*/benchmark_records.jsonl >> pe_fss.jsonl
for f in fsr fss; do
  echo; echo "== $f"
  python3 matched_effect.py pe_$f.jsonl --family research_synthesis --fixtures $FX | grep -A3 "attack vs matched"
  python3 mp_strength.py pe_$f.jsonl --fixtures $FX --trace-roots traces/pilot/$f traces/pilot/fsr
done
