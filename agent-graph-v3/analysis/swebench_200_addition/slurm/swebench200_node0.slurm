#!/bin/bash
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH --time=02:00:00
#SBATCH --job-name=swe200-node0
#SBATCH --output=/u/kdhande/agent-graph/agent-graph-v3/analysis/swebench_200_addition/slurm/node0-%j.out
#SBATCH --error=/u/kdhande/agent-graph/agent-graph-v3/analysis/swebench_200_addition/slurm/node0-%j.err
# Swebench-200 addition — node 0 of 4 (plan-manifest mode).
# Manifest: analysis/swebench_200_addition/swebench_200_addition_manifest.json
# Traces:   production_runs/swebench_200_addition/node_0/

set -euo pipefail

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd /u/kdhande/agent-graph/agent-graph-v3
export PYTHONPATH="$PWD:$PYTHONPATH"

PORT=8001   # must match saved manifest settings.vllm_url: http://127.0.0.1:8001/v1
LOG="analysis/swebench_200_addition/slurm/vllm-node0-${SLURM_JOB_ID}.log"

echo "=== Starting vLLM on port $PORT ==="
vllm serve Qwen/Qwen2.5-72B-Instruct-AWQ \
  --host 127.0.0.1 \
  --port "$PORT" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 32768 \
  > "$LOG" 2>&1 &
VLLM_PID=$!
trap 'kill "$VLLM_PID" 2>/dev/null || true' EXIT

echo "Waiting for vLLM..."
READY=0
for i in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; then READY=1; break; fi
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM exited during startup"; tail -100 "$LOG"; exit 1
    fi
    sleep 5
done
[ "$READY" -eq 1 ] || { echo "ERROR: vLLM did not become ready"; tail -100 "$LOG"; exit 1; }
echo "=== vLLM READY ==="

echo "=== Running swebench-200 addition, node 0/4 ==="
# Plan-manifest mode: matrix filters are forbidden by the CLI; execution
# settings come from the manifest (model_name, vllm_url, repetitions, seed, …).
# --real-model is required (saved dry_run=false). --fixture-root must be the
# v3 root so fixture manifest hashes verify.
python -m benchmark \
  --plan-manifest analysis/swebench_200_addition/swebench_200_addition_manifest.json \
  --fixture-root workspace_fixtures_v3 \
  --real-model \
  --backend vllm \
  --run-node 0 \
  --num-nodes 4 \
  --output-dir production_runs/swebench_200_addition/node_0

echo "=== NODE 0 COMPLETE ==="
