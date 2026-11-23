#!/bin/bash -l
#SBATCH --job-name=bvrl-regen
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=01:45:00
#SBATCH --array=0-23
#SBATCH --output=/u/kdhande/agent-graph/bvrl-regen-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/bvrl-regen-%A_%a.err
# Array size 0-23 matches regeneration_summary.json shard_plan.target_shards = 24,
# recomputed by plan_shards.py on the ACTUAL split_plan() distribution (uneven,
# 20-59 exec/shard) and the pilot-observed per-topology runtimes (BV median 28s,
# RL median 62s; RL p95 213s truncation-tail allowance in the conservative model).
# Worst shard ~93m conservative; --time 01:45:00 leaves ~12m headroom. If the
# manifest or shard count changes, re-run `python3 analysis/bv_rl_regeneration_v2/plan_shards.py`
# and update these lines; the preflight below re-checks the array size.

set -euo pipefail

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/hub"

cd /u/kdhande/agent-graph/agent-graph-v3

MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"
NUM_NODES="${SLURM_ARRAY_TASK_COUNT:-1}"        # runner shards == Slurm array tasks
RUN_NODE="${SLURM_ARRAY_TASK_ID}"

MANIFEST="analysis/bv_rl_regeneration_v2/regeneration_manifest.json"
OUTPUT_ROOT="production_run/bv_rl_regeneration_v2"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"
mkdir -p "$OUTPUT_DIR"

VLLM_PID=""
PORT=""
READY=0

cleanup() {
    if [[ -n "$VLLM_PID" ]]; then
        pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
        kill -TERM "$VLLM_PID" 2>/dev/null || true
        wait "$VLLM_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

echo "=================================================="
echo "BV+RL regeneration  (Branch & Verify + Review Loop)"
echo "Job:       ${SLURM_JOB_ID}"
echo "Shard:     ${RUN_NODE}/${NUM_NODES}"
echo "Host:      $(hostname)"
echo "Manifest:  ${MANIFEST}"
echo "Output:    ${OUTPUT_DIR}"
echo "Started:   $(date)"
echo "=================================================="

# ------------------------------------------------------------
# Manifest preflight
# ------------------------------------------------------------
python - "$MANIFEST" "$NUM_NODES" <<'PY'
import json, sys

path, num_nodes = sys.argv[1], int(sys.argv[2])
data = json.load(open(path))
n = len(data["scenarios"])
assert data["schema"] == "agentprop-readiness-selection-v1"

run_ids = [s["run_id"] for s in data["scenarios"]]
sid = [s["scenario_id"] for s in data["scenarios"]]
assert len(set(run_ids)) == n, "Duplicate run IDs"
assert len(set(sid)) == n, "Duplicate scenario IDs"

for s in data["scenarios"]:
    assert s["topology"] in ("branch_and_verify", "review_loop"), \
        f"Out-of-scope topology: {s['topology']}"
    assert s["execution_variant"] == "standard", "Non-standard execution found"
    assert "MEMORY_POISONING" not in str(s.get("lep_code", "")), \
        "Memory Poisoning execution found"
    assert s["condition"] in ("benign", "single_lep")

# Runner shard count must match the sharding plan recorded by build_manifest.py
import json as _json
summary = _json.load(open("analysis/bv_rl_regeneration_v2/regeneration_summary.json"))
target = summary["shard_plan"]["target_shards"]
assert num_nodes == target, \
    f"--array size ({num_nodes}) != shard_plan.target_shards ({target}); update SLURM_ARRAY_TASK_MAX"

print("Manifest preflight PASSED:", n, "executions,", num_nodes, "shards")
PY

# ------------------------------------------------------------
# Committed-fix guards (read-only checks against the working tree)
# ------------------------------------------------------------
if ! grep -q "topology.is_coordinator_first()" generation/runner.py; then
    echo "ERROR: Coordinator-first scheduler fix missing in generation/runner.py."; exit 1
fi
if ! grep -q "origin_structure_errors" generation/runner.py; then
    echo "ERROR: injection_structure_mismatch admission fix missing in generation/runner.py."; exit 1
fi
if ! grep -q "review_event_budget" generation/runner.py; then
    echo "ERROR: Review-cap event-budget finalization fix missing in generation/runner.py."; exit 1
fi
python - <<'PY'
from generation.topology import get_topology
topology = get_topology("coordinator_workers", {k: f"a{i}" for i, k in enumerate(
    ["coordinator", "specialist_a", "specialist_b", "synthesizer"])})
assert topology.is_coordinator_first() is True
print("Scheduler fix guard PASSED")
PY

select_port() {
    python - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

MAX_PORT_ATTEMPTS=5
for attempt in $(seq 1 "$MAX_PORT_ATTEMPTS"); do
    PORT="$(select_port)"
    echo "vLLM startup attempt ${attempt}/${MAX_PORT_ATTEMPTS} port ${PORT}"
    vllm serve "$MODEL" \
        --port "$PORT" \
        --host 127.0.0.1 \
        --max-model-len 32768 \
        --gpu-memory-utilization 0.90 \
        --enable-auto-tool-choice \
        --tool-call-parser hermes \
        > "${OUTPUT_DIR}/vllm.log" 2>&1 &
    VLLM_PID=$!

    READY=0; STARTUP_FAILED=0
    for i in $(seq 1 120); do
        if ! kill -0 "$VLLM_PID" 2>/dev/null; then STARTUP_FAILED=1; break; fi
        if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
            READY=1
            echo "vLLM ready on port ${PORT}"
            break
        fi
        sleep 5
    done
    if [[ "$READY" -eq 1 ]]; then break; fi
    echo "vLLM startup attempt failed."
    if ! grep -Eqi "address already in use|port.*in use|errno 98" "${OUTPUT_DIR}/vllm.log"; then
        tail -100 "${OUTPUT_DIR}/vllm.log" || true; exit 1
    fi
    pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
    kill -TERM "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
    VLLM_PID=""; sleep 3
done
if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM failed to start."; tail -100 "${OUTPUT_DIR}/vllm.log"; exit 1
fi

# ------------------------------------------------------------
# Run ONLY this runner shard via the frozen split_plan()
# ------------------------------------------------------------
echo "Starting benchmark shard ${RUN_NODE}/${NUM_NODES}"
date

RUNTIME_MANIFEST="${OUTPUT_DIR}/runtime_manifest.json"
python - "$MANIFEST" "$RUNTIME_MANIFEST" "$PORT" <<'PYTHON'
import json, sys
source, destination, port = sys.argv[1:]
data = json.load(open(source))
settings = data.get("settings")
if not isinstance(settings, dict) or "vllm_url" not in settings:
    raise SystemExit("Cannot locate settings.vllm_url; original manifest remains unchanged.")
settings["vllm_url"] = f"http://127.0.0.1:{port}/v1"
json.dump(data, open(destination, "w"), indent=2)
print(f"Runtime vLLM endpoint: http://127.0.0.1:{port}/v1")
PYTHON

STATUS=0
python -m benchmark \
    --real-model \
    --backend vllm \
    --vllm-url "http://127.0.0.1:${PORT}/v1" \
    --model "$MODEL" \
    --plan-manifest "$RUNTIME_MANIFEST" \
    --run-node "$RUN_NODE" \
    --num-nodes "$NUM_NODES" \
    --output-dir "$OUTPUT_DIR" || STATUS=$?

echo "Benchmark exit status: ${STATUS}"

# ------------------------------------------------------------
# Backend failure analysis (shard guard)
# ------------------------------------------------------------
RECORDS="${OUTPUT_DIR}/benchmark_records.jsonl"
if [[ ! -f "$RECORDS" ]]; then
    echo "ERROR: benchmark_records.jsonl missing."; exit 1
fi

python - "$RECORDS" <<'PY'
import json, sys
path = sys.argv[1]
attempts = backend_failures = invalid = 0
for line in open(path):
    if not line.strip():
        continue
    attempts += 1
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        invalid += 1
        continue
    if str(record.get("termination_reason", "")).startswith("backend_"):
        backend_failures += 1
print(f"Total records:     {attempts}")
print(f"Backend failures:  {backend_failures}")
print(f"Invalid JSONL:     {invalid}")
if attempts == 0:
    sys.exit("ERROR: No benchmark records")
if invalid:
    sys.exit("ERROR: Invalid benchmark records")
if backend_failures * 2 > attempts:
    sys.exit("ERROR: Majority of executions failed on backend")
if backend_failures:
    print("WARNING: Partial backend failures detected")
PY

echo "=================================================="
echo "Shard ${RUN_NODE} finished — status ${STATUS}"
echo "Finished: $(date)"
echo "=================================================="

exit "$STATUS"
