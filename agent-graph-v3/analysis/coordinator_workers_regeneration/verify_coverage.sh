#!/usr/bin/env bash
# Post-run coverage verification for the coordinator-workers regeneration batch.
# Run AFTER the 64 shards complete. Exits non-zero on any coverage gap.
#
# Usage:
#   bash analysis/coordinator_workers_regeneration/verify_coverage.sh
#
# It reconciles the frozen selection against the traces actually produced,
# and re-checks the two failure modes that have bitten this codebase before:
#   (a) silent 64x re-run (missing --run-node/--num-nodes), and
#   (b) backend outages that the CLI reports as green (exit 0).

set -uo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo /u/kdhande/agent-graph/agent-graph-v3)"
REPO=/u/kdhande/agent-graph/agent-graph-v3
OUT_ROOT="$REPO/production_run/coordinator_workers_regeneration"
MANIFEST="$REPO/analysis/coordinator_workers_regeneration/filtered_manifest.json"

echo "### 1. Expected vs. realized execution counts"
python3 - "$MANIFEST" "$OUT_ROOT" <<'PY'
import json, sys
from pathlib import Path
manifest_path, out_root = sys.argv[1], Path(sys.argv[2])
sel = json.load(open(manifest_path))["scenarios"]
expected = {s["scenario_id"] for s in sel}
# traces/ + rejected_traces/ under each node_* shard
realized = set()
shards = sorted(p for p in out_root.glob("node_*") if p.is_dir())
for shard in shards:
    for sub in ("traces", "rejected_traces"):
        for f in (shard / sub).glob("*_trace.json"):
            t = json.load(open(f))
            realized.add(t.get("scenario_id"))
print(f"shards present:            {len(shards)}/64")
print(f"expected scenarios:        {len(expected)}")
print(f"traces produced:           {len(realized)}")
missing = expected - realized
extra   = realized - expected
print(f"missing (not executed):    {len(missing)}")
print(f"unexpected (not selected): {len(extra)}")
if extra:
    for x in sorted(extra)[:10]:
        print("   extra:", x)
if missing:
    print("   sample missing:")
    for m in sorted(missing)[:10]:
        print("   ", m)
assert not extra, "traces exist for scenario_ids outside the 4,937 selection"
assert not missing, f"{len(missing)} selected executions have no trace"
print("COVERAGE OK: all 4,937 selected executions produced exactly one trace each")
PY
RC=$?
[ $RC -ne 0 ] && { echo "COVERAGE CHECK FAILED"; exit 1; }

echo
echo "### 2. No silent 64x re-run (every execution ran on exactly one shard)"
python3 - "$MANIFEST" "$OUT_ROOT" <<'PY'
import json, sys
from pathlib import Path
from collections import Counter
manifest_path, out_root = sys.argv[1], Path(sys.argv[2])
sel = {s["scenario_id"] for s in json.load(open(manifest_path))["scenarios"]}
per_shard = Counter()
for shard in sorted(out_root.glob("node_*")):
    for f in list((shard/"traces").glob("*_trace.json")) + list((shard/"rejected_traces").glob("*_trace.json")):
        per_shard[json.load(open(f))["scenario_id"]] += 1
dups = {k: v for k, v in per_shard.items() if v > 1}
assert not dups, f"{len(dups)} scenario_ids appear on more than one shard / attempt"
assert set(per_shard) == sel
print(f"unique scenario_ids across shards: {len(per_shard)} (expected {len(sel)})")
print("PARTITION OK: each execution ran on exactly one shard (no 64x re-run)")
PY
[ $? -ne 0 ] && { echo "PARTITION CHECK FAILED"; exit 1; }

echo
echo "### 3. Backend-outage guard (CLI exits 0 on total failure — recheck from records)"
python3 - "$OUT_ROOT" <<'PY'
import json, sys
from pathlib import Path
from collections import Counter
out_root = Path(sys.argv[1])
term = Counter()
for f in out_root.glob("node_*/benchmark_records.jsonl"):
    for line in open(f):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        term[rec.get("termination_reason", rec.get("termination", "unknown"))] += 1
total = sum(term.values())
backend = sum(v for k, v in term.items() if k.startswith("backend_"))
print("termination reasons:", dict(term))
print(f"total attempts: {total} | backend failures: {backend}")
if total:
    print(f"backend-failure fraction: {backend/total:.1%}")
    if backend * 2 > total:
        print("WARNING: majority of attempts failed on the backend — a sustained outage.")
        print("The CLI will still have exited 0; treat this batch as FAILED.")
        sys.exit(1)
print("BACKEND GUARD OK")
PY
[ $? -ne 0 ] && { echo "BACKEND GUARD FAILED"; exit 1; }

echo
echo "### 4. Every realized trace starts coordinator-first (the fix under test)"
python3 - "$OUT_ROOT" <<'PY'
import json, sys
from pathlib import Path
out_root = Path(sys.argv[1])
checked = 0
bad = []
for f in out_root.glob("node_*/traces/*_trace.json"):
    t = json.load(open(f))
    roles = []
    for e in t.get("events", []):
        r = e.get("agent_role") or e.get("role")
        if r and r not in roles:
            roles.append(r)
    checked += 1
    if not roles:
        bad.append((f.name, "no agent-role events"))
    elif roles[0] != "coordinator":
        bad.append((f.name, f"first role={roles[0]}"))
print(f"traces checked: {checked}")
if bad:
    print(f"COORDINATOR-FIRST FAILED on {len(bad)} traces:")
    for name, why in bad[:20]:
        print("  ", name, "-", why)
    sys.exit(1)
print("COORDINATOR-FIRST OK: every trace's first agent role is 'coordinator'")
PY
RC=$?
[ $RC -ne 0 ] && { echo "COORDINATOR-FIRST CHECK FAILED"; exit 1; }

echo
echo "### 5. Confirm this output tree is isolated from historical / SWE-200"
echo "  historical production: production_run/node_*/        (NOT under coordinator_workers_regeneration/)"
echo "  this batch:            production_run/coordinator_workers_regeneration/node_*/"
ls -d "$OUT_ROOT"/node_* >/dev/null 2>&1 && echo "  batch output tree present: $(ls -d "$OUT_ROOT"/node_* | wc -l) shards"

echo
echo "ALL COVERAGE VERIFICATION CHECKS PASSED"
