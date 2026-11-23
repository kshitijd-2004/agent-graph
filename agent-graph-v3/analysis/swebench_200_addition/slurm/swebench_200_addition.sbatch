#!/bin/bash -l
#SBATCH --job-name=swe200add
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=32g
#SBATCH --time=01:45:00
#SBATCH --array=0-127
#SBATCH --output=/u/kdhande/agent-graph/agent-graph-v3/analysis/swebench_200_addition/slurm/swe200add-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/agent-graph-v3/analysis/swebench_200_addition/slurm/swe200add-%A_%a.err

# ------------------------------------------------------------
# SWE-bench 200-addition — 5,800-execution batch (PREPARATION ONLY).
#
# 200 code_review SWE fixtures x 29 cells = 5,800 executions.
#   review_loop       3 benign + 4 LEP single_origin        = 7/fixture
#   branch_and_verify 3 benign + 8 LEP {SO, M2O}            = 11/fixture
#   coordinator_workers 3 benign + 8 LEP {SO, O2M}          = 11/fixture
#   LEPs: TOOL_RESULT_CORRUPTION, HANDOFF_CORRUPTION,
#         INPUT_DISREGARD, INDIRECT_PROMPT_INJECTION.
#         MEMORY_POISONING is permanently excluded (asserted below).
#
# Sharding = md5(pair_tag)[:8] % 128 — the exact benchmark.split_merge.split_plan
# partition, so `--run-node N --num-nodes 128` reproduces it deterministically.
# 128 shards fit in one wave on the 148 free ghx4 nodes; worst shard = 99 execs,
# comfortably under the (uncapped) partition walltime.
#
# Each shard runs its own vLLM on a dynamically selected port (no 8001 collisions),
# rewrites a shard-local runtime manifest's settings.vllm_url to that port, and writes
# traces to an isolated production_runs/swebench_200_addition/node_<shard>/ tree.
#
# This file is NOT submitted. Run explicitly after user confirmation:
#   sbatch analysis/swebench_200_addition/slurm/swebench_200_addition.sbatch
# ------------------------------------------------------------

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
NUM_NODES=128
RUN_NODE="${SLURM_ARRAY_TASK_ID}"

MANIFEST="analysis/swebench_200_addition/swebench_200_addition_manifest.json"

OUTPUT_ROOT="production_runs/swebench_200_addition"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"

mkdir -p "$OUTPUT_DIR"

VLLM_PID=""
PORT=""
READY=0

cleanup() {
    echo "Cleaning up vLLM..."
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
echo "SWE-bench 200-addition"
echo "Job:       ${SLURM_JOB_ID}"
echo "Shard:     ${RUN_NODE}/${NUM_NODES}"
echo "Host:      $(hostname)"
echo "Manifest:  ${MANIFEST}"
echo "Output:    ${OUTPUT_DIR}"
echo "Started:   $(date)"
echo "=================================================="

# ------------------------------------------------------------
# Manifest preflight — must be EXACTLY the validated 5,800-set
# ------------------------------------------------------------
python - "$MANIFEST" <<'PY'
import json
import sys

path = sys.argv[1]

with open(path) as f:
    data = json.load(f)

scenarios = data["scenarios"]
n = len(scenarios)

assert n == 5800, f"Expected 5800 executions, found {n}"

run_ids = [s["run_id"] for s in scenarios]
scenario_ids = [s["scenario_id"] for s in scenarios]

assert len(set(run_ids)) == n, "Duplicate run IDs"
assert len(set(scenario_ids)) == n, "Duplicate scenario IDs"

VALID_TOPOS = {"review_loop", "branch_and_verify", "coordinator_workers"}
# Compute the set difference BEFORE the f-string: a comprehension embedded in an
# f-string expression is rejected by Python >= 3.12 (PEP 701 parser) and the
# whole preflight died with a SyntaxError in every shard (job 3580547).
invalid_topos = {s["topology"] for s in scenarios} - VALID_TOPOS
assert not invalid_topos, \
    f"Unexpected topology found: {invalid_topos}"

assert all(
    s["execution_variant"] == "standard" for s in scenarios
), "Non-standard execution found"

assert all(
    "MEMORY_POISONING" not in str(s.get("lep_code", "")) + str(s.get("lep_codes", ""))
    for s in scenarios
), "Memory Poisoning execution found"

assert len({s["fixture_id"] for s in scenarios}) == 200, "Fixture count != 200"
assert len(data["fixture_manifest_sha256"]) == 200, "Fixture hash map must contain 200 entries"

s = data["settings"]
assert s["dry_run"] is False, "dry_run must be False"
assert s["backend_name"] == "vllm", "backend_name must be vllm"
assert s["num_repetitions"] == 1 and s["num_benign_repetitions"] == 3, "Wrong repetition counts"
assert s["seed"] == 42, "seed must be 42"
assert s["model_name"] == "Qwen/Qwen2.5-72B-Instruct-AWQ", "Model mismatch"

print("Manifest preflight PASSED")
print(f"Executions: {n}")
PY

# ------------------------------------------------------------
# Coordinator-first scheduler verification (governs the CW cells)
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

print("Coordinator-first scheduler guard PASSED (applies to coordinator_workers cells)")
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

            # Confirm the listener belongs to our vLLM process tree,
            # not another Slurm job.
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

# Create a shard-local runtime manifest with the actual vLLM endpoint.
# The original validated manifest remains unchanged. The command line must pass
# the same --vllm-url so the CLI's conflict check against the manifest passes.
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
    --fixture-root workspace_fixtures_v3 \
    --run-node "$RUN_NODE" \
    --num-nodes "$NUM_NODES" \
    --output-dir "$OUTPUT_DIR" || STATUS=$?

echo "Benchmark exit status: ${STATUS}"

# ------------------------------------------------------------
# Backend failure analysis (the CLI exits 0 on a total outage)
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
