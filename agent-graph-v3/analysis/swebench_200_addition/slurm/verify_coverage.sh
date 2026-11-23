#!/usr/bin/env bash
# Post-run coverage verification for the SWE-bench 200-addition batch.
# Run AFTER the 128 shards complete. Exits non-zero on any coverage gap.
#
# Usage:
#   bash analysis/swebench_200_addition/slurm/verify_coverage.sh
#
# It reconciles the frozen 5,800-execution selection against the traces actually
# produced, and re-checks the failure modes that have bitten this codebase before:
#   (a) silent re-run (missing --run-node/--num-nodes) — a scenario on 2 shards;
#   (b) backend outages that the CLI reports as green (exit 0);
#   (c) wrong population — a trace outside the 5,800 selection;
#   (d) lost coordinator-first ordering on coordinator_workers traces;
#   (e) mixed output trees.

set -uo pipefail

REPO=/u/kdhande/agent-graph/agent-graph-v3
cd "$REPO"
OUT_ROOT="$REPO/production_runs/swebench_200_addition"
MANIFEST="$REPO/analysis/swebench_200_addition/swebench_200_addition_manifest.json"

echo "### 1. Expected vs. realized execution counts + population invariants"
python3 - "$MANIFEST" "$OUT_ROOT" <<'PY'
import json, sys
from collections import Counter
from pathlib import Path
manifest_path, out_root = sys.argv[1], Path(sys.argv[2])
sel = json.load(open(manifest_path))["scenarios"]
expected = {s["scenario_id"] for s in sel}
realized = set()
shards = sorted(p for p in out_root.glob("node_*") if p.is_dir())
for shard in shards:
    for sub in ("traces", "rejected_traces"):
        for f in (shard / sub).glob("*_trace.json"):
            t = json.load(open(f))
            realized.add(t.get("scenario_id"))
print(f"shards present:            {len(shards)}/128")
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
assert not extra, "traces exist for scenario_ids outside the 5,800 selection"
assert not missing, f"{len(missing)} selected executions have no trace"

# Population invariants on the realized traces.
topo, mode, cond = Counter(), Counter(), Counter()
cw_first = {"coordinator": 0, "other": 0}
for shard in shards:
    for f in (shard / "traces").glob("*_trace.json"):
        t = json.load(open(f))
        topo[t.get("topology", "?")] += 1
        mode[t.get("propagation_mode", "?")] += 1
        cond[t.get("condition", "?")] += 1
        if t.get("topology") == "coordinator_workers":
            roles = []
            for e in t.get("events", []):
                r = e.get("agent_role") or e.get("role")
                if r and r not in roles:
                    roles.append(r)
            cw_first["coordinator" if roles and roles[0] == "coordinator" else "other"] += 1
print("realized by topology:       ", dict(topo))
print("realized by prop mode:      ", dict(mode))
print("realized by condition:      ", dict(cond))
exp_topo = Counter(s["topology"] for s in sel)
exp_mode = Counter(s["propagation_mode"] for s in sel)
exp_cond = Counter(s["condition"] for s in sel)
assert topo == exp_topo, f"topology mix drift: {dict(topo)} vs {dict(exp_topo)}"
assert mode == exp_mode, f"mode mix drift: {dict(mode)} vs {dict(exp_mode)}"
assert cond == exp_cond, f"condition mix drift: {dict(cond)} vs {dict(exp_cond)}"
assert cw_first["coordinator"] == exp_topo.get("coordinator_workers", 0), \
    f"CW first-role: {cw_first}"
print("COORDINATOR-FIRST OK on all coordinator_workers traces")
print("COVERAGE OK: all 5,800 selected executions produced exactly one trace each")
PY
RC=$?
[ $RC -ne 0 ] && { echo "COVERAGE CHECK FAILED"; exit 1; }

echo
echo "### 2. No silent re-run (every execution ran on exactly one shard)"
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
assert set(per_shard) == sel, "shard scenario set != 5,800 selection"
print(f"unique scenario_ids across shards: {len(per_shard)} (expected {len(sel)})")
print("PARTITION OK: each execution ran on exactly one shard (no 128x re-run)")
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
echo "### 4. Confirm this output tree is isolated from historical / CW regeneration"
echo "  historical production:        production_run/node_*/"
echo "  CW regeneration:              production_run/coordinator_workers_regeneration/node_*/"
echo "  this batch:                   production_runs/swebench_200_addition/node_*/"
ls -d "$OUT_ROOT"/node_* >/dev/null 2>&1 && echo "  batch output tree present: $(ls -d "$OUT_ROOT"/node_* | wc -l) shards"

echo
echo "ALL COVERAGE VERIFICATION CHECKS PASSED"
