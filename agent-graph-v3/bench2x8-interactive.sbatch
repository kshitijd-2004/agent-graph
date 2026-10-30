#!/bin/bash -l
#SBATCH --job-name=agentprop-2x8-int
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4-interactive
#SBATCH --nodes=2
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00
#SBATCH --output=/u/kdhande/agent-graph/traces/agentprop-2x8-int-%j.log

set -euo pipefail
set -x

V3=/u/kdhande/agent-graph/agent-graph-v3
OUT=/u/kdhande/agent-graph/traces/agentprop-2x8-int-${SLURM_JOB_ID}
MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ

# 8 concurrent benchmark processes per physical GPU
N=8

# 2 physical nodes * 8 processes = 16 global shards
PHYSICAL_NODES=2
TOTAL_SHARDS=$((PHYSICAL_NODES * N))

mkdir -p "$OUT"

export HF_HOME=/u/kdhande/.cache/huggingface

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd "$V3"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")

echo "===== ALLOCATED NODES ====="
printf '%s\n' "${NODES[@]}"
echo "Processes per physical node: $N"
echo "Total benchmark shards: $TOTAL_SHARDS"

if [[ "${#NODES[@]}" -ne "$PHYSICAL_NODES" ]]; then
    echo "ERROR: expected $PHYSICAL_NODES nodes, got ${#NODES[@]}"
    exit 1
fi

#
# Create worker script. One copy runs on each physical node.
#
WORKER="$OUT/run_node.sh"

cat > "$WORKER" <<'WORKER_EOF'
#!/bin/bash -l

set -euo pipefail
set -x

PHYS_INDEX="$1"
N="$2"
TOTAL_SHARDS="$3"
OUT="$4"
V3="$5"
MODEL="$6"

NODE_OUT="$OUT/physical_${PHYS_INDEX}"
mkdir -p "$NODE_OUT"

export HF_HOME=/u/kdhande/.cache/huggingface

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

cd "$V3"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

echo "===== PHYSICAL NODE $PHYS_INDEX ====="
echo "HOST=$(hostname)"
echo "MODEL=$MODEL"

nvidia-smi

echo "===== STARTING VLLM ====="

vllm serve "$MODEL" \
  --port 8001 \
  --host 127.0.0.1 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  > "$NODE_OUT/vllm.log" 2>&1 &

VLLM_PID=$!

cleanup() {
    echo "Stopping vLLM PID $VLLM_PID on $(hostname)"
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
        tail -100 "$NODE_OUT/vllm.log"
        exit 1
    fi

    sleep 15
done

if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM never became ready"
    tail -100 "$NODE_OUT/vllm.log"
    exit 1
fi

echo "===== VLLM READY ON $(hostname) ====="

curl -s http://127.0.0.1:8001/v1/models
echo

#
# Node 0 gets global shards 0-7.
# Node 1 gets global shards 8-15.
#
START_SHARD=$((PHYS_INDEX * N))
END_SHARD=$((START_SHARD + N - 1))

echo "Launching global shards $START_SHARD through $END_SHARD"

pids=()
shard_ids=()

for GLOBAL_SHARD in $(seq "$START_SHARD" "$END_SHARD"); do

    SHARD_OUT="$NODE_OUT/shard_${GLOBAL_SHARD}"
    mkdir -p "$SHARD_OUT"

    echo "Starting global shard $GLOBAL_SHARD / $TOTAL_SHARDS"

    python -m benchmark \
      --real-model \
      --backend vllm \
      --vllm-url http://127.0.0.1:8001/v1 \
      --model "$MODEL" \
      --fixture-root workspace_fixtures \
      --topologies review_loop,branch_and_verify,coordinator_workers \
      --task-families code_review,financial_analysis,research_synthesis \
      --lep-codes LEP_TOOL_RESULT_CORRUPTION,LEP_INDIRECT_PROMPT_INJECTION,LEP_MEMORY_POISONING,LEP_HANDOFF_CORRUPTION,LEP_INPUT_DISREGARD \
      --propagation-modes single_origin,one_to_many,many_to_one \
      --repetitions 1 \
      --benign-repetitions 3 \
      --num-nodes "$TOTAL_SHARDS" \
      --run-node "$GLOBAL_SHARD" \
      --output-dir "$SHARD_OUT" \
      > "$NODE_OUT/shard_${GLOBAL_SHARD}.log" 2>&1 &

    pids+=($!)
    shard_ids+=("$GLOBAL_SHARD")
done

echo "===== WAITING FOR SHARDS $START_SHARD-$END_SHARD ====="

FAILED=0

for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    shard="${shard_ids[$i]}"

    if wait "$pid"; then
        echo "Shard $shard completed successfully"
    else
        echo "ERROR: shard $shard failed"
        FAILED=1
    fi
done

if [[ "$FAILED" -ne 0 ]]; then
    echo "ERROR: one or more shards failed on physical node $PHYS_INDEX"
    exit 1
fi

echo "===== PHYSICAL NODE $PHYS_INDEX COMPLETE ====="
WORKER_EOF

chmod +x "$WORKER"

echo "===== STARTING TWO PHYSICAL WORKERS ====="

worker_pids=()

for PHYS_INDEX in $(seq 0 $((PHYSICAL_NODES-1))); do

    NODE="${NODES[$PHYS_INDEX]}"

    echo "Starting physical worker $PHYS_INDEX on $NODE"

    srun \
      --nodes=1 \
      --ntasks=1 \
      --nodelist="$NODE" \
      --exclusive \
      "$WORKER" \
      "$PHYS_INDEX" \
      "$N" \
      "$TOTAL_SHARDS" \
      "$OUT" \
      "$V3" \
      "$MODEL" \
      > "$OUT/physical_${PHYS_INDEX}.log" 2>&1 &

    worker_pids+=($!)
done

echo "===== WAITING FOR PHYSICAL NODES ====="

FAILED=0

for i in "${!worker_pids[@]}"; do

    if wait "${worker_pids[$i]}"; then
        echo "Physical node $i completed successfully"
    else
        echo "ERROR: physical node $i failed"
        FAILED=1
    fi

done

if [[ "$FAILED" -ne 0 ]]; then
    echo "ERROR: one or more physical nodes failed"
    exit 1
fi

echo "===== ALL 16 SHARDS COMPLETE ====="

MERGE_DIRS=()

for PHYS_INDEX in $(seq 0 $((PHYSICAL_NODES-1))); do

    START_SHARD=$((PHYS_INDEX * N))
    END_SHARD=$((START_SHARD + N - 1))

    for GLOBAL_SHARD in $(seq "$START_SHARD" "$END_SHARD"); do
        MERGE_DIRS+=(
            "$OUT/physical_${PHYS_INDEX}/shard_${GLOBAL_SHARD}"
        )
    done

done

echo "===== MERGING ====="

python -m benchmark \
  --merge "${MERGE_DIRS[@]}" \
  --output "$OUT/merged"

echo "===== AGENTPROP 2x8 INTERACTIVE COMPLETE ====="
echo "Output: $OUT"
