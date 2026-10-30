#!/bin/bash -l
#SBATCH --job-name=bench1
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=04:00:00
#SBATCH --output=/u/kdhande/agent-graph/traces/bench1-%j.log

set -euo pipefail
set -x

V3=/u/kdhande/agent-graph/agent-graph-v3
OUT=/u/kdhande/agent-graph/traces/bench1
MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ
N=8

mkdir -p "$OUT"

export HF_HOME=/u/kdhande/.cache/huggingface

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd "$V3"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

echo "===== ENVIRONMENT ====="
echo "HOST=$(hostname)"
echo "JOB_ID=${SLURM_JOB_ID:-unknown}"
echo "MODEL=$MODEL"
echo "PYTHON=$(which python)"
echo "VLLM=$(which vllm)"

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

if [[ -z "$MODEL" || "$MODEL" == "/" ]]; then
    echo "ERROR: Invalid MODEL='$MODEL'"
    exit 1
fi

echo "===== STARTING VLLM ====="

vllm serve "$MODEL" \
  --port 8001 \
  --host 127.0.0.1 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  > "$OUT/vllm.log" 2>&1 &

VLLM_PID=$!

cleanup() {
    echo "Stopping vLLM PID $VLLM_PID"
    kill "$VLLM_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Waiting for vLLM to become ready..."

READY=0

for i in $(seq 1 160); do
    if curl -sf http://127.0.0.1:8001/v1/models >/dev/null; then
        READY=1
        break
    fi

    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited during startup"
        tail -100 "$OUT/vllm.log"
        exit 1
    fi

    sleep 15
done

if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM never came up"
    tail -100 "$OUT/vllm.log"
    exit 1
fi

echo "===== VLLM READY ====="

curl -s http://127.0.0.1:8001/v1/models
echo

echo "===== STARTING $N BENCHMARK SHARDS ====="

pids=()

for k in $(seq 0 $((N-1))); do
    echo "Starting shard $k / $N"

    python -m benchmark \
      --real-model \
      --backend vllm \
      --vllm-url http://127.0.0.1:8001/v1 \
      --model "$MODEL" \
      --topologies review_loop,branch_and_verify,coordinator_workers \
      --task-families code_review,financial_analysis,research_synthesis \
      --lep-codes LEP_TOOL_RESULT_CORRUPTION,LEP_INDIRECT_PROMPT_INJECTION,LEP_MEMORY_POISONING,LEP_HANDOFF_CORRUPTION,LEP_INPUT_DISREGARD \
      --propagation-modes single_origin,one_to_many,many_to_one \
      --repetitions 1 \
      --num-nodes "$N" \
      --run-node "$k" \
      --output-dir "$OUT/node_$k" \
      > "$OUT/node_$k.log" 2>&1 &

    pids+=($!)
done

echo "===== WAITING FOR SHARDS ====="

FAILED=0

for i in "${!pids[@]}"; do
    pid="${pids[$i]}"

    if wait "$pid"; then
        echo "Shard $i completed successfully"
    else
        echo "ERROR: shard $i failed"
        FAILED=1
    fi
done

if [[ "$FAILED" -ne 0 ]]; then
    echo "ERROR: one or more benchmark shards failed"
    exit 1
fi

echo "===== ALL SHARDS COMPLETE ====="

python -m benchmark \
  --merge $(for k in $(seq 0 $((N-1))); do echo "$OUT/node_$k"; done) \
  --output "$OUT/merged"

echo "===== BENCH1 COMPLETE ====="
