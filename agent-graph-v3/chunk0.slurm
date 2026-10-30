#!/bin/bash
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH --time=02:00:00
#SBATCH --job-name=agentprop-c0
#SBATCH --output=/u/kdhande/agent-graph/agent-graph-v3/benchmark_sbatch/chunk0-%j.out
#SBATCH --error=/u/kdhande/agent-graph/agent-graph-v3/benchmark_sbatch/chunk0-%j.err

set -euo pipefail

module load python/miniforge3_pytorch/2.10.0

source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd /u/kdhande/agent-graph/agent-graph-v3
export PYTHONPATH="$PWD:$PYTHONPATH"

PORT=8000
LOG="benchmark_sbatch/vllm-c0-${SLURM_JOB_ID}.log"

echo "=== Environment ==="
echo "Host: $(hostname)"
echo "Job: $SLURM_JOB_ID"
echo "Python: $(which python)"
echo "vLLM: $(which vllm)"

python - <<'PY'
import torch
import vllm
print("torch:", torch.__version__)
print("vllm:", vllm.__version__)
print("cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
PY

nvidia-smi

echo "=== Starting vLLM on port $PORT ==="

vllm serve Qwen/Qwen2.5-72B-Instruct-AWQ \
  --host 127.0.0.1 \
  --port "$PORT" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 32768 \
  > "$LOG" 2>&1 &

VLLM_PID=$!

cleanup() {
    echo "Stopping vLLM PID $VLLM_PID"
    kill "$VLLM_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Waiting for vLLM..."

READY=0
for i in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; then
        READY=1
        break
    fi

    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited during startup"
        tail -100 "$LOG"
        exit 1
    fi

    sleep 5
done

if [ "$READY" -ne 1 ]; then
    echo "ERROR: vLLM did not become ready"
    tail -100 "$LOG"
    exit 1
fi

echo "=== vLLM READY ==="
curl -s "http://127.0.0.1:$PORT/v1/models"
echo

echo "=== Starting benchmark chunk 0 ==="

python -m benchmark \
  --real-model \
  --backend vllm \
  --vllm-url "http://127.0.0.1:$PORT/v1" \
  --model Qwen/Qwen2.5-72B-Instruct-AWQ \
  --topologies review_loop,branch_and_verify,coordinator_workers \
  --task-families code_review,financial_analysis,research_synthesis \
  --lep-codes LEP_TOOL_RESULT_CORRUPTION,LEP_INDIRECT_PROMPT_INJECTION,LEP_MEMORY_POISONING,LEP_HANDOFF_CORRUPTION,LEP_INPUT_DISREGARD \
  --propagation-modes single_origin,one_to_many,many_to_one \
  --repetitions 1 \
  --max-events 300 \
  --run-node 0 \
  --num-nodes 4 \
  --output-dir benchmark_sbatch/chunk_0

echo "=== CHUNK 0 COMPLETE ==="
