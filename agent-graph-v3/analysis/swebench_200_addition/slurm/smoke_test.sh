#!/usr/bin/env bash
# Single-shard SMOKE TEST for the SWE-bench 200-addition batch — OFFLINE ONLY.
# No Slurm submission, no vLLM startup, no inference. It re-runs, in-process,
# the exact checks the sbatch performs before it would start vLLM, and proves
# the sharding is a disjoint partition of the 5,800 selection:
#
#   (1) Strict manifest validation — load_plan_manifest (5,800 entries, 200 fixtures)
#   (2) Manifest preflight  — the same assertions the sbatch runs before vLLM
#   (3) Coordinator-first   — the same guard the sbatch runs
#   (4) Single-shard split  — split_plan(node 3, 128): scenario count, disjoint from
#                             the other 127 shards (union == 5,800, pairwise no overlap)
#   (5) Runtime URL rewrite — the same transform that writes runtime_manifest.json,
#                             verified on a copy in /tmp (original manifest untouched)
#
# Usage:
#   bash analysis/swebench_200_addition/slurm/smoke_test.sh
#
# All steps run against the on-disk repo; nothing is submitted or executed remotely.

set -uo pipefail

REPO=/u/kdhande/agent-graph/agent-graph-v3
cd "$REPO"

MANIFEST="analysis/swebench_200_addition/swebench_200_addition_manifest.json"
NUM_NODES=128
SMOKE_NODE=3
SMOKE_PORT=39999          # arbitrary; proves the rewrite math, no real vLLM

echo "### 1. Strict manifest validation (load_plan_manifest, in-process)"
python3 - "$MANIFEST" <<'PY'
import sys
from pathlib import Path
from benchmark.benchmark_runner import BenchmarkManifest
from benchmark.plan_manifest import load_plan_manifest

mpath, = [Path(sys.argv[1])]
manifest, entries = load_plan_manifest(
    mpath, BenchmarkManifest(fixture_root=Path("workspace_fixtures_v3"))
)
assert len(entries) == 5800, f"Expected 5800, got {len(entries)}"
assert sum(len(v) for v in manifest.fixture_ids.values()) == 200, "fixture count != 200"
assert set(manifest.topologies) == {"review_loop", "branch_and_verify", "coordinator_workers"}
assert manifest.dry_run is False
assert manifest.backend_name == "vllm"
print(f"STRICT VALIDATION PASSED: {len(entries)} entries, "
      f"{sum(len(v) for v in manifest.fixture_ids.values())} fixtures, "
      f"fixture_root={manifest.fixture_root}")
PY
[ $? -eq 0 ] || { echo "STRICT VALIDATION FAILED"; exit 1; }

echo
echo "### 2. Manifest preflight (sbatch assertions, in-process)"
python3 - "$MANIFEST" <<'PY'
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)

scenarios = data["scenarios"]
n = len(scenarios)
assert n == 5800, f"Expected 5800 executions, found {n}"

run_ids = [s["run_id"] for s in scenarios]
scenario_ids = [s["scenario_id"] for s in scenarios]
assert len(set(run_ids)) == n, "Duplicate run IDs"
assert len(set(scenario_ids)) == n, "Duplicate scenario IDs"

VALID_TOPOS = {"review_loop", "branch_and_verify", "coordinator_workers"}
assert all(s["topology"] in VALID_TOPOS for s in scenarios), "Unexpected topology"
assert all(s["execution_variant"] == "standard" for s in scenarios), "Non-standard execution"
assert all(
    "MEMORY_POISONING" not in str(s.get("lep_code", "")) + str(s.get("lep_codes", ""))
    for s in scenarios
), "Memory Poisoning execution found"
assert len({s["fixture_id"] for s in scenarios}) == 200, "Fixture count != 200"
assert len(data["fixture_manifest_sha256"]) == 200, "Fixture hash map != 200"

s = data["settings"]
assert s["dry_run"] is False and s["backend_name"] == "vllm"
assert s["num_repetitions"] == 1 and s["num_benign_repetitions"] == 3
assert s["seed"] == 42 and s["model_name"] == "Qwen/Qwen2.5-72B-Instruct-AWQ"
print(f"MANIFEST PREFLIGHT PASSED: {n} executions, 200 fixtures, no memory poisoning")
PY
[ $? -eq 0 ] || { echo "MANIFEST PREFLIGHT FAILED"; exit 1; }

echo
echo "### 3. Coordinator-first guard (sbatch assertions, in-process)"
python3 - <<'PY'
from generation.topology import get_topology

topology = get_topology("coordinator_workers", {
    "coordinator": "a0", "specialist_a": "a1", "specialist_b": "a2", "synthesizer": "a3"
})
assert topology.is_coordinator_first() is True
print("COORDINATOR-FIRST GUARD PASSED (coordinator_workers cells schedule coordinator first)")
PY
[ $? -eq 0 ] || { echo "COORDINATOR-FIRST GUARD FAILED"; exit 1; }

echo
echo "### 4. Single-shard split: node $SMOKE_NODE/$NUM_NODES — disjoint, complete partition"
python3 - "$MANIFEST" "$SMOKE_NODE" "$NUM_NODES" <<'PY'
import json, sys
from benchmark.split_merge import split_plan

manifest_path, node, num_nodes = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
full = json.load(open(manifest_path))["scenarios"]

# Full-partition check: the num_nodes shards are a disjoint cover of the selection.
shards = [split_plan(full, i, num_nodes) for i in range(num_nodes)]
shard_ids = [ {e["scenario_id"] for e in sh} for sh in shards ]
union = set().union(*shard_ids)
total = sum(len(s) for s in shards)
overlap = total - len(union)
assert union == {e["scenario_id"] for e in full}, "shard union != full selection"
assert overlap == 0, f"silent re-run risk: {overlap} scenario_ids on >1 shard"
sizes = sorted(len(s) for s in shards)
print(f"FULL PARTITION: union={len(union)}, overlap={overlap}, "
      f"shards with work={sum(1 for s in shard_ids if s)}, "
      f"min={sizes[0]}, median={sizes[len(sizes)//2]}, max={sizes[-1]}")

smoke = shards[node]
print(f"SMOKE NODE {node}: {len(smoke)} executions, "
      f"{len(set(e['pair_tag'] for e in smoke))} pairs, "
      f"topologies={ {e['topology'] for e in smoke} }")
assert len(smoke) == len(set(e["scenario_id"] for e in smoke)), "duplicate within smoke shard"
PY
[ $? -eq 0 ] || { echo "SINGLE-SHARD SPLIT FAILED"; exit 1; }

echo
echo "### 5. Runtime URL rewrite (sbatch transform) on a copy — original untouched"
python3 - "$MANIFEST" "$SMOKE_PORT" <<'PY'
import json, sys, tempfile, os
from pathlib import Path

manifest_path, port = sys.argv[1], int(sys.argv[2])
source = Path(manifest_path)

original_bytes = source.read_bytes()
before_url = json.loads(original_bytes)["settings"]["vllm_url"]

tmp = Path(tempfile.mkdtemp()) / "runtime_manifest.json"
with open(source) as f:
    data = json.load(f)
url = f"http://127.0.0.1:{port}/v1"
assert isinstance(data["settings"], dict) and "vllm_url" in data["settings"]
data["settings"]["vllm_url"] = url
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)

# The runtime manifest's scenarios must be byte-identical to the source's.
assert json.load(open(tmp))["scenarios"] == json.load(open(source))["scenarios"], \
    "runtime manifest altered the scenario list"
assert json.load(open(tmp))["settings"]["vllm_url"] == url, "URL rewrite did not stick"
assert source.read_bytes() == original_bytes, "original manifest was modified"
print(f"RUNTIME REWRITE PASSED: source vllm_url={before_url} -> runtime {url} "
      f"(scenarios unchanged, original byte-identical)")
PY
[ $? -eq 0 ] || { echo "RUNTIME URL REWRITE FAILED"; exit 1; }

echo
echo "ALL SMOKE-TEST CHECKS PASSED — batch is ready to submit."
echo "To actually launch (after user confirmation):"
echo "  sbatch analysis/swebench_200_addition/slurm/swebench_200_addition.sbatch"
echo "After the 128 shards complete:"
echo "  bash analysis/swebench_200_addition/slurm/verify_coverage.sh"
