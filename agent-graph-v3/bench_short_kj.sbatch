#!/bin/bash -l

# AgentProp full benchmark
# 48 independent array jobs, 1 GH200 GPU each, 2 hours each.
# Each array job starts one Qwen vLLM server and launches 8 benchmark shards.
#
# 48 jobs × 8 shards = 384 global shards.
#
# Submit:
#   sbatch bench_short_kj.sbatch
#
# Monitor:
#   squeue -u $USER
#
# Array status:
#   squeue -j <JOB_ID> -o "%.18i %.9P %.20j %.2t %.10M %.10l %.6D %R"

#SBATCH --job-name=agentprop48
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --array=0-47
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --output=/u/kdhande/agent-graph/traces/agentprop48-%A_%a.log

set -x
set -o pipefail

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

V3=/u/kdhande/agent-graph/agent-graph-v3
OUT=/u/kdhande/agent-graph/traces/agentprop48
FIXTURES="$V3/workspace_fixtures"

MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ

FAMILIES=code_review,financial_analysis,research_synthesis

TOPOLOGIES=review_loop,branch_and_verify,coordinator_workers

LEPS=LEP_TOOL_RESULT_CORRUPTION,LEP_INDIRECT_PROMPT_INJECTION,LEP_MEMORY_POISONING,LEP_HANDOFF_CORRUPTION,LEP_INPUT_DISREGARD

MODES=single_origin,one_to_many,many_to_one

PER_JOB=8
JOBS=48
TOTAL=$((JOBS * PER_JOB))

J=$SLURM_ARRAY_TASK_ID

mkdir -p "$OUT"

export HF_HOME=/u/kdhande/.cache/huggingface

# ----------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd "$V3"

# ----------------------------------------------------------------------
# Start one vLLM server on this physical node
# ----------------------------------------------------------------------

vllm serve "$MODEL" \
    --host 127.0.0.1 \
    --port 8001 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.90 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    > "$OUT/vllm_j${J}.log" 2>&1 &

VLLM_PID=$!

# ----------------------------------------------------------------------
# Wait for vLLM
# ----------------------------------------------------------------------

READY=0

for i in $(seq 1 160); do
    if curl -sf http://127.0.0.1:8001/v1/models >/dev/null; then
        READY=1
        break
    fi

    sleep 15
done

if [ "$READY" -ne 1 ]; then
    echo "ERROR: vLLM never came up for array task $J"
    tail -100 "$OUT/vllm_j${J}.log"
    kill "$VLLM_PID" 2>/dev/null || true
    exit 1
fi

echo "vLLM ready for array task $J"

# ----------------------------------------------------------------------
# Launch 8 benchmark shards against this node's vLLM server
# ----------------------------------------------------------------------

pids=()

for s in $(seq 0 $((PER_JOB - 1))); do

    # Global shard index: 0..383
    k=$((J * PER_JOB + s))

    echo "Launching global shard $k / $TOTAL"

    python -m benchmark \
        --real-model \
        --backend vllm \
        --vllm-url http://127.0.0.1:8001/v1 \
        --model "$MODEL" \
        --fixture-root "$FIXTURES" \
        --task-families "$FAMILIES" \
        --topologies "$TOPOLOGIES" \
        --lep-codes "$LEPS" \
        --propagation-modes "$MODES" \
        --repetitions 1 \
        --benign-repetitions 3 \
        --num-nodes "$TOTAL" \
        --run-node "$k" \
        --output-dir "$OUT/node_$k" \
        > "$OUT/node_$k.log" 2>&1 &

    pids+=($!)

done

# ----------------------------------------------------------------------
# Wait for all 8 shards
# ----------------------------------------------------------------------

FAILED=0

for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        FAILED=1
    fi
done

echo "Array job $J finished; shard range $((J*PER_JOB))-$((J*PER_JOB+PER_JOB-1))"

kill "$VLLM_PID" 2>/dev/null || true

exit "$FAILED"
