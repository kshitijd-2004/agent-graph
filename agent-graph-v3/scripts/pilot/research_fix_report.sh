#!/bin/bash
# Research attacks before vs after the attack text fix. Run from /work/nvme/biha/$USER.
#   memory poisoning: fsr (old text) vs fmn (new text), both compared with fsr clean runs
#   tool corruption:  main run (old text) vs ftr (new text), both compared with main run clean
FX=ag-run/agent-graph-v3/workspace_fixtures
for f in fmn ftr; do echo "$f slices done: $(ls traces/pilot/$f/node_*/benchmark_summary.json 2>/dev/null | wc -l) / 32"; done
FSR_CLEAN=$(mktemp); grep -h -v '"lep_code": "LEP_' traces/pilot/fsr/node_*/benchmark_records.jsonl > $FSR_CLEAN
V3_CLEAN=$(mktemp); grep -h -v '"lep_code": "LEP_' traces/v3/res/node_*/benchmark_records.jsonl > $V3_CLEAN
cat traces/pilot/fmn/node_*/benchmark_records.jsonl $FSR_CLEAN > pe_fmn.jsonl 2>/dev/null
cat traces/pilot/ftr/node_*/benchmark_records.jsonl $V3_CLEAN > pe_ftr.jsonl 2>/dev/null
grep -h '"lep_code": "LEP_TOOL_RESULT_CORRUPTION"' traces/v3/res/node_*/benchmark_records.jsonl | cat - $V3_CLEAN > pe_tool_old.jsonl
show() { python3 matched_effect.py $1 --family research_synthesis --fixtures $FX | grep -A2 "attack vs matched" | grep -v "^--"; }
echo; echo "== memory poisoning, old text (verdict in memory)"; show pe_fsr.jsonl
echo; echo "== memory poisoning, new text (false finding)"; show pe_fmn.jsonl
echo; echo "== tool corruption, old text"; show pe_tool_old.jsonl
echo; echo "== tool corruption, new text"; show pe_ftr.jsonl
rm -f $FSR_CLEAN $V3_CLEAN
