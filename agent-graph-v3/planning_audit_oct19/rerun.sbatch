#!/bin/bash -l
#SBATCH --job-name=agentprop-rerun
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --array=0-99
#SBATCH --output=/u/kdhande/agent-graph/rerun-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/rerun-%A_%a.err

set -euo pipefail

# Environment
module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/hub"

cd /u/kdhande/agent-graph/agent-graph-v3

# Configuration
MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"

NUM_NODES=100
RUN_NODE="${SLURM_ARRAY_TASK_ID:?Missing array task ID}"

# Unique port per array index.
PORT=$((8100 + RUN_NODE))

# Never write into original production_run/.
OUTPUT_ROOT="rerun_production_oct19"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"

MANIFEST="planning_audit_oct19/rerun_plan_manifest.json"

# Prevent retries from mixing old and new records.
# For a retry, archive/move the existing node directory first.
if [ -e "$OUTPUT_DIR" ]; then
    echo "ERROR: Output directory already exists: $OUTPUT_DIR"
    echo "Refusing to mix existing and new trace records."
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "============================================================"
echo "AgentProp missing-trace rerun"
echo "Slurm job:      ${SLURM_JOB_ID}"
echo "Array task:     ${RUN_NODE}"
echo "Partition:      ${SLURM_JOB_PARTITION}"
echo "Host:           $(hostname)"
echo "Run node:       ${RUN_NODE}/${NUM_NODES}"
echo "Port:           ${PORT}"
echo "Output:         ${OUTPUT_DIR}"
echo "Manifest:       ${MANIFEST}"
echo "Started:        $(date)"
echo "============================================================"

# Verify manifest exists.
if [ ! -f "$MANIFEST" ]; then
    echo "ERROR: Missing plan manifest: $MANIFEST"
    exit 1
fi

# Avoid connecting to an unrelated server on this port.
if curl -sf --max-time 2 \
    "http://127.0.0.1:${PORT}/v1/models" \
    >/dev/null 2>&1; then
    echo "ERROR: Port ${PORT} already has a responding API."
    exit 1
fi

echo "Starting vLLM..."

vllm serve "$MODEL" \
    --port "$PORT" \
    --host 127.0.0.1 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.90 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    > "${OUTPUT_DIR}/vllm.log" 2>&1 &

VLLM_PID=$!

cleanup() {
    echo "Stopping vLLM PID ${VLLM_PID}..."
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
}

trap cleanup EXIT

echo "Waiting for vLLM readiness..."

READY=0

for i in $(seq 1 120); do

    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited before readiness."
        tail -100 "${OUTPUT_DIR}/vllm.log" || true
        exit 1
    fi

    if curl -sf --max-time 5 \
        "http://127.0.0.1:${PORT}/v1/models" \
        >/dev/null 2>&1; then

        READY=1
        echo "vLLM ready after ${i} checks."
        break
    fi

    sleep 5
done

if [ "$READY" -ne 1 ]; then
    echo "ERROR: vLLM did not become ready."
    tail -100 "${OUTPUT_DIR}/vllm.log" || true
    exit 1
fi

echo "Starting benchmark shard ${RUN_NODE}/${NUM_NODES}"
date

# Explicit partition flags are essential.
# Without these, each task would execute the full manifest.

# Create a shard-local manifest matching this shard's vLLM port.
SHARD_MANIFEST="${OUTPUT_DIR}/rerun_plan_manifest.json"

python - "$MANIFEST" "$SHARD_MANIFEST" "$PORT" <<'PYMANIFEST'
import json
import sys

source, destination, port = sys.argv[1:]

with open(source) as f:
    manifest = json.load(f)

manifest["settings"]["vllm_url"] = f"http://127.0.0.1:{port}/v1"

with open(destination, "w") as f:
    json.dump(manifest, f)
PYMANIFEST

STATUS=0

python -m benchmark \
    --real-model \
    --backend vllm \
    --vllm-url "http://127.0.0.1:${PORT}/v1" \
    --model "$MODEL" \
    --output-dir "$OUTPUT_DIR" \
    --run-node "$RUN_NODE" \
    --num-nodes "$NUM_NODES" \
    --plan-manifest "$SHARD_MANIFEST" \
    || STATUS=$?

echo
echo "============================================================"
echo "Benchmark exited with status: ${STATUS}"
echo "Finished: $(date)"
echo "Output: ${OUTPUT_DIR}"
echo "============================================================"

LEDGER="${OUTPUT_DIR}/benchmark_records.jsonl"

# A successful exit without records is not a successful shard.
if [ ! -s "$LEDGER" ]; then
    echo "ERROR: Missing or empty benchmark ledger."
    exit 1
fi

TOTAL_RECORDS=$(wc -l < "$LEDGER")

BACKEND_FAILURES=$(
    grep -c '"termination_reason": "backend_' "$LEDGER" || true
)

BACKEND_FAILURES=${BACKEND_FAILURES:-0}

echo "Total records:       ${TOTAL_RECORDS}"
echo "Backend failures:    ${BACKEND_FAILURES}"
echo "Other records:       $((TOTAL_RECORDS - BACKEND_FAILURES))"

# Preserve any benchmark-level error.
if [ "$STATUS" -ne 0 ]; then
    echo "ERROR: Benchmark returned nonzero status ${STATUS}."
    exit "$STATUS"
fi

# Fail shards dominated by backend errors.
if [ "$BACKEND_FAILURES" -gt $((TOTAL_RECORDS / 2)) ]; then
    echo "ERROR: Majority of records failed due to backend errors."
    exit 1
fi

echo "============================================================"
echo "Shard ${RUN_NODE} completed initial execution checks."
echo "Records still require post-run admission/coverage audit."
echo "============================================================"

exit 0

