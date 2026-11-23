#!/bin/bash -l
#SBATCH --job-name=cw-regen
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:15:00
#SBATCH --array=0-63
#SBATCH --output=/u/kdhande/agent-graph/cw-regen-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/cw-regen-%A_%a.err

set -euo pipefail

# ------------------------------------------------------------
# Environment
# ------------------------------------------------------------
module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/hub"

cd /u/kdhande/agent-graph/agent-graph-v3

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"
NUM_NODES=64
RUN_NODE="${SLURM_ARRAY_TASK_ID}"

MANIFEST="analysis/coordinator_workers_regeneration/filtered_manifest.json"

OUTPUT_ROOT="production_run/coordinator_workers_regeneration"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"

mkdir -p "$OUTPUT_DIR"

VLLM_PID=""
PORT=""
READY=0

cleanup() {
    echo "Cleaning up vLLM..."

    if [[ -n "$VLLM_PID" ]]; then
        # Stop the vLLM process and its children.
        pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
        kill -TERM "$VLLM_PID" 2>/dev/null || true
        wait "$VLLM_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

echo "=================================================="
echo "Coordinator-workers regeneration"
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
python - "$MANIFEST" <<'PY'
import json
import sys

path = sys.argv[1]

with open(path) as f:
    data = json.load(f)

scenarios = data["scenarios"]
n = len(scenarios)

assert n == 4937, f"Expected 4937 executions, found {n}"

run_ids = [s["run_id"] for s in scenarios]
scenario_ids = [s["scenario_id"] for s in scenarios]

assert len(set(run_ids)) == n, "Duplicate run IDs"
assert len(set(scenario_ids)) == n, "Duplicate scenario IDs"

assert all(
    s["topology"] == "coordinator_workers"
    for s in scenarios
), "Non-CW execution found"

assert all(
    s["execution_variant"] == "standard"
    for s in scenarios
), "Non-standard execution found"

assert all(
    "MEMORY_POISONING" not in str(s.get("lep_type", ""))
    for s in scenarios
), "Memory Poisoning execution found"

assert len(data["fixture_manifest_sha256"]) == 703, \
    "Fixture hash map must contain 703 entries"

print("Manifest preflight PASSED")
print(f"Executions: {n}")
PY

# ------------------------------------------------------------
# Coordinator-first scheduler verification
# ------------------------------------------------------------
if ! grep -q "topology.is_coordinator_first()" generation/runner.py; then
    echo "ERROR: Coordinator-first scheduler fix missing."
    exit 1
fi

python - <<'PY'
from generation.topology import get_topology

agents = {
    "coordinator": "a0",
    "specialist_a": "a1",
    "specialist_b": "a2",
    "synthesizer": "a3",
}

topology = get_topology("coordinator_workers", agents)

assert topology.is_coordinator_first() is True

print("Coordinator-first scheduler guard PASSED")
PY

# ------------------------------------------------------------
# Select an available port
# ------------------------------------------------------------
select_port() {
    python - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

# ------------------------------------------------------------
# Start vLLM with port-conflict protection
# ------------------------------------------------------------
MAX_PORT_ATTEMPTS=5

for attempt in $(seq 1 "$MAX_PORT_ATTEMPTS"); do

    PORT="$(select_port)"

    echo "vLLM startup attempt ${attempt}/${MAX_PORT_ATTEMPTS}"
    echo "Selected port: ${PORT}"

    vllm serve "$MODEL" \
        --port "$PORT" \
        --host 127.0.0.1 \
        --max-model-len 32768 \
        --gpu-memory-utilization 0.90 \
        --enable-auto-tool-choice \
        --tool-call-parser hermes \
        > "${OUTPUT_DIR}/vllm.log" 2>&1 &

    VLLM_PID=$!

    READY=0
    STARTUP_FAILED=0

    for i in $(seq 1 120); do

        # Check whether our own process is still alive.
        if ! kill -0 "$VLLM_PID" 2>/dev/null; then
            echo "vLLM process exited during startup."
            STARTUP_FAILED=1
            break
        fi

        # Check HTTP readiness.
        if curl -sf \
            "http://127.0.0.1:${PORT}/v1/models" \
            >/dev/null 2>&1; then

            # Confirm that the listener belongs to our
            # vLLM process tree, not another Slurm job.
            if python - "$PORT" "$VLLM_PID" <<'PY'
import os
import subprocess
import sys

port = int(sys.argv[1])
root_pid = int(sys.argv[2])

def descendants(pid):
    result = {pid}
    pending = [pid]

    while pending:
        parent = pending.pop()
        try:
            output = subprocess.check_output(
                ["pgrep", "-P", str(parent)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            continue

        for line in output.splitlines():
            child = int(line)
            if child not in result:
                result.add(child)
                pending.append(child)

    return result

owned_pids = descendants(root_pid)

try:
    output = subprocess.check_output(
        ["ss", "-H", "-ltnp", f"sport = :{port}"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
except (FileNotFoundError, subprocess.CalledProcessError):
    sys.exit(1)

import re

listener_pids = {
    int(pid)
    for pid in re.findall(r"pid=(\d+)", output)
}

# Fail closed if listener ownership is not visible.
if not listener_pids:
    sys.exit(1)

if not listener_pids.intersection(owned_pids):
    sys.exit(1)

sys.exit(0)
PY
            then
                READY=1
                echo "vLLM ready on port ${PORT}"
                break
            else
                echo "Port ${PORT} is not verified as owned by this job."
                STARTUP_FAILED=1
                break
            fi
        fi

        sleep 5
    done

    if [[ "$READY" -eq 1 ]]; then
        break
    fi

    echo "vLLM startup attempt failed."

    # Avoid retrying a fatal model/configuration failure.
    if ! grep -Eqi \
        "address already in use|port.*in use|errno 98" \
        "${OUTPUT_DIR}/vllm.log"; then

        echo "Failure does not appear to be a port conflict."
        tail -100 "${OUTPUT_DIR}/vllm.log" || true
        exit 1
    fi

    echo "Port conflict detected. Retrying..."

    pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
    kill -TERM "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true

    VLLM_PID=""
    sleep 3
done

if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM failed to start."
    tail -100 "${OUTPUT_DIR}/vllm.log" || true
    exit 1
fi

# ------------------------------------------------------------
# Run ONLY this shard's manifest executions
# ------------------------------------------------------------
echo "Starting benchmark shard ${RUN_NODE}/${NUM_NODES}"
date

# Create a shard-local manifest with the actual vLLM endpoint.
# The original validated manifest remains unchanged.
RUNTIME_MANIFEST="${OUTPUT_DIR}/runtime_manifest.json"

python - "$MANIFEST" "$RUNTIME_MANIFEST" "$PORT" <<'PYTHON'
import json
import sys

source, destination, port = sys.argv[1:]

with open(source) as f:
    data = json.load(f)

url = f"http://127.0.0.1:{port}/v1"

# Update the saved backend URL without changing experiment scenarios.
settings = data.get("settings")
if not isinstance(settings, dict) or "vllm_url" not in settings:
    raise SystemExit(
        "Cannot locate settings.vllm_url; "
        "original manifest and script remain unchanged."
    )

settings["vllm_url"] = url

with open(destination, "w") as f:
    json.dump(data, f, indent=2)

print(f"Runtime vLLM endpoint: {url}")
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
# Backend failure analysis
# ------------------------------------------------------------
RECORDS="${OUTPUT_DIR}/benchmark_records.jsonl"

if [[ ! -f "$RECORDS" ]]; then
    echo "ERROR: benchmark_records.jsonl missing."
    exit 1
fi

python - "$RECORDS" <<'PY'
import json
import sys

path = sys.argv[1]

attempts = 0
backend_failures = 0
invalid_lines = 0

with open(path) as f:
    for line in f:
        if not line.strip():
            continue

        attempts += 1

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue

        reason = str(record.get("termination_reason", ""))

        if reason.startswith("backend_"):
            backend_failures += 1

print(f"Total records:     {attempts}")
print(f"Backend failures:  {backend_failures}")
print(f"Invalid JSONL:     {invalid_lines}")

if attempts == 0:
    sys.exit("ERROR: No benchmark records")

if invalid_lines:
    sys.exit("ERROR: Invalid benchmark records")

if backend_failures * 2 > attempts:
    sys.exit("ERROR: Majority of executions failed on backend")

if backend_failures:
    print("WARNING: Partial backend failures detected")
PY

echo "=================================================="
echo "Shard ${RUN_NODE} finished"
echo "Benchmark status: ${STATUS}"
echo "Finished: $(date)"
echo "Output: ${OUTPUT_DIR}"
echo "=================================================="

exit "$STATUS"
