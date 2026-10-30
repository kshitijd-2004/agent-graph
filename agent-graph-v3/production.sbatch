#!/bin/bash -l
#SBATCH --job-name=agentprop-prod
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --array=0-159
#SBATCH --output=/u/kdhande/agent-graph/prod-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/prod-%A_%a.err

set -e

# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=$HF_HOME/hub
export TRANSFORMERS_CACHE=$HF_HOME/hub

cd /u/kdhande/agent-graph/agent-graph-v3

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"
PORT=8001
NUM_NODES=160
RUN_NODE="${SLURM_ARRAY_TASK_ID}"

OUTPUT_ROOT="production_run"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"

mkdir -p "$OUTPUT_DIR"

echo "============================================================"
echo "AgentProp production shard"
echo "Slurm job:      ${SLURM_JOB_ID}"
echo "Array task:     ${SLURM_ARRAY_TASK_ID}"
echo "Host:           $(hostname)"
echo "Run node:       ${RUN_NODE}/${NUM_NODES}"
echo "Output:         ${OUTPUT_DIR}"
echo "Started:        $(date)"
echo "============================================================"

# ------------------------------------------------------------------
# Start vLLM
# ------------------------------------------------------------------

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
}

trap cleanup EXIT

# ------------------------------------------------------------------
# Wait for vLLM
# ------------------------------------------------------------------

echo "Waiting for vLLM..."

READY=0

for i in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        READY=1
        echo "vLLM ready after ${i} checks."
        break
    fi

    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited before becoming ready."
        tail -100 "${OUTPUT_DIR}/vllm.log" || true
        exit 1
    fi

    sleep 5
done

if [ "$READY" -ne 1 ]; then
    echo "ERROR: vLLM did not become ready."
    tail -100 "${OUTPUT_DIR}/vllm.log" || true
    exit 1
fi

curl -s "http://127.0.0.1:${PORT}/v1/models"
echo

# ------------------------------------------------------------------
# Production benchmark shard
# ------------------------------------------------------------------

echo "Starting benchmark shard ${RUN_NODE}/${NUM_NODES}..."
date

python -m benchmark \
  --real-model \
  --backend vllm \
  --vllm-url "http://127.0.0.1:${PORT}/v1" \
  --model "$MODEL" \
  --repetitions 1 \
  --benign-repetitions 3 \
  --max-events 300 \
  --run-node "$RUN_NODE" \
  --num-nodes "$NUM_NODES" \
  --output-dir "$OUTPUT_DIR"

STATUS=$?

echo
echo "============================================================"
echo "Shard ${RUN_NODE} finished with status ${STATUS}"
echo "Finished: $(date)"
echo "Output: ${OUTPUT_DIR}"
echo "============================================================"

exit "$STATUS"
