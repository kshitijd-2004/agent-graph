#!/bin/bash -l

#SBATCH --job-name=agentprop-ready
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4-interactive
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --output=/u/kdhande/agent-graph/readiness-%j.log
#SBATCH --error=/u/kdhande/agent-graph/readiness-%j.err

set -e

V3=/u/kdhande/agent-graph/agent-graph-v3
MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ

cd "$V3"

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=$HF_HOME/hub
export TRANSFORMERS_CACHE=$HF_HOME/hub

echo "========================================"
echo "AgentProp 50-scenario readiness batch"
echo "Node: $(hostname)"
echo "Started: $(date)"
echo "========================================"

nvidia-smi

# Start vLLM
vllm serve "$MODEL" \
  --host 127.0.0.1 \
  --port 8001 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  > "analysis/readiness_50/vllm-${SLURM_JOB_ID}.log" 2>&1 &

VLLM_PID=$!

cleanup() {
    kill "$VLLM_PID" 2>/dev/null || true
}

trap cleanup EXIT

echo "Waiting for vLLM..."

for i in $(seq 1 120); do

    if curl -sf http://127.0.0.1:8001/v1/models >/dev/null; then
        echo "vLLM ready: $(date)"
        break
    fi

    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited during startup."
        tail -100 "analysis/readiness_50/vllm-${SLURM_JOB_ID}.log"
        exit 1
    fi

    if [ "$i" -eq 120 ]; then
        echo "ERROR: vLLM startup timed out."
        tail -100 "analysis/readiness_50/vllm-${SLURM_JOB_ID}.log"
        exit 1
    fi

    sleep 5
done

echo
echo "========================================"
echo "Running 50 readiness scenarios"
echo "========================================"

python -m benchmark \
  --plan-manifest analysis/readiness_50/plan_manifest.json \
  --real-model \
  --backend vllm \
  --model "$MODEL" \
  --vllm-url http://127.0.0.1:8001/v1 \
  --output-dir analysis/readiness_50/live

echo
echo "========================================"
echo "READINESS BATCH COMPLETE"
echo "Finished: $(date)"
echo "Output: analysis/readiness_50/live"
echo "========================================"

