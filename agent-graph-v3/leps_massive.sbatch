#!/bin/bash -l
#SBATCH --job-name=leps_massive
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=04:00:00
#SBATCH --output=/u/kdhande/agent-graph/traces/leps_massive-%j.log

set -euo pipefail
set -x

V3=/u/kdhande/agent-graph/agent-graph-v3
OUT=/u/kdhande/agent-graph/traces/leps_massive
MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ

# Same concurrency as your successful full benchmark.
N=8

# We already have repetition 00.
# Build reps 00-08, then explicitly execute ONLY 01-08.
REPS=9

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
echo "WORKERS=$N"
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


# ============================================================
# START VLLM
# ============================================================

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


# ============================================================
# BUILD LEP-ONLY PLAN
# ============================================================

echo "===== BUILDING LEP-ONLY PLAN ====="

python - <<'PY'
import pickle
from pathlib import Path

from benchmark.benchmark_runner import BenchmarkManifest
from tasks.registry import get_default_leps


FAMILIES = [
    "code_review",
    "financial_analysis",
    "research_synthesis",
]


# Collect all LEP configs.
#
# Deduplicate by LEP code because get_default_leps() may return
# equivalent configs for multiple task families.
lep_by_code = {}

for family in FAMILIES:

    for lep in get_default_leps(family):

        code = getattr(lep, "code", None)

        if code is None:
            code = getattr(lep, "lep_code", None)

        if code is None:
            raise RuntimeError(
                f"Could not determine LEP code for {lep!r}"
            )

        lep_by_code[code] = lep


lep_configs = list(lep_by_code.values())

print("LEPs:")
for code in lep_by_code:
    print(" ", code)


manifest = BenchmarkManifest(
    topologies=[
        "review_loop",
        "branch_and_verify",
        "coordinator_workers",
    ],

    task_families=FAMILIES,

    lep_configs=lep_configs,

    # Produces repetition indices 0..8.
    num_repetitions=9,

    max_events=300,

    model_name="Qwen/Qwen2.5-72B-Instruct-AWQ",

    dry_run=False,

    output_dir=Path(
        "/u/kdhande/agent-graph/traces/leps_massive"
    ),

    fixture_root=Path("workspace_fixtures"),

    backend_name="vllm",

    vllm_url="http://127.0.0.1:8001/v1",

    propagation_modes=[
        "single_origin",
        "one_to_many",
        "many_to_one",
    ],
)


full_plan = manifest.build_plan()


# ============================================================
# FILTER
#
# Keep:
#   LEP executions only
#   repetitions 01-08
#
# Remove:
#   ALL benign executions
#   existing repetition 00
# ============================================================

plan = []

for entry in full_plan:

    scenario_id = entry["scenario_id"]

    # No benign traces.
    if not scenario_id.endswith("_lep"):
        continue

    repetition = entry.get("repetition_index")

    if repetition is None:
        raise RuntimeError(
            f"No repetition_index in {scenario_id}"
        )

    # Existing dataset already contains repetition 00.
    if repetition < 1 or repetition > 8:
        continue

    plan.append(entry)


# Deduplicate by scenario ID.
unique = {}

for entry in plan:
    sid = entry["scenario_id"]

    if sid in unique:
        raise RuntimeError(
            f"Duplicate scenario_id generated: {sid}"
        )

    unique[sid] = entry

plan = list(unique.values())


print()
print("======================================")
print("LEP PLAN SUMMARY")
print("======================================")

print("Full manifest size:", len(full_plan))
print("Filtered LEP-only size:", len(plan))


# Current benchmark has:
#
#   125 LEP cells
#   × 8 NEW repetitions
#   = 1000 executions
#
EXPECTED = 1000


if len(plan) != EXPECTED:

    print()
    print(
        f"ERROR: expected {EXPECTED} LEP executions, "
        f"but generated {len(plan)}"
    )

    print()
    print("First 50 filtered scenarios:")

    for entry in plan[:50]:
        print(
            entry["repetition_index"],
            entry["scenario_id"],
        )

    raise SystemExit(
        "ABORTING BEFORE MODEL GENERATION."
    )


# Extra safety assertions.

assert all(
    e["scenario_id"].endswith("_lep")
    for e in plan
)

assert all(
    1 <= e["repetition_index"] <= 8
    for e in plan
)


print()
print("PLAN VALIDATED.")
print("Exactly 1000 NEW LEP executions.")
print("No benign executions.")
print("No repetition 00.")
print()


plan_path = Path(
    "/u/kdhande/agent-graph/traces/"
    "leps_massive/lep_plan.pkl"
)

with open(plan_path, "wb") as f:
    pickle.dump(plan, f)

print("Saved plan:", plan_path)

print()
print("First 20:")
for entry in plan[:20]:
    print(
        entry["repetition_index"],
        entry["scenario_id"],
    )
PY


# ============================================================
# START 8 CONCURRENT SHARDS
# ============================================================

echo "===== STARTING $N LEP SHARDS ====="

pids=()

for k in $(seq 0 $((N-1))); do

    echo "Starting LEP shard $k / $N"

    mkdir -p "$OUT/node_$k"

    WORKER_ID="$k" \
    NUM_WORKERS="$N" \
    OUTPUT_DIR="$OUT/node_$k" \
    python - <<'PY' \
        > "$OUT/node_$k.log" 2>&1 &

import os
import pickle
from pathlib import Path

from benchmark.benchmark_runner import (
    BenchmarkManifest,
    BenchmarkRunner,
)
from tasks.registry import get_default_leps


worker_id = int(os.environ["WORKER_ID"])
num_workers = int(os.environ["NUM_WORKERS"])
output_dir = Path(os.environ["OUTPUT_DIR"])


FAMILIES = [
    "code_review",
    "financial_analysis",
    "research_synthesis",
]


lep_by_code = {}

for family in FAMILIES:

    for lep in get_default_leps(family):

        code = getattr(lep, "code", None)

        if code is None:
            code = getattr(lep, "lep_code", None)

        if code is None:
            raise RuntimeError(
                f"Could not determine LEP code for {lep!r}"
            )

        lep_by_code[code] = lep


manifest = BenchmarkManifest(
    topologies=[
        "review_loop",
        "branch_and_verify",
        "coordinator_workers",
    ],

    task_families=FAMILIES,

    lep_configs=list(lep_by_code.values()),

    num_repetitions=9,

    max_events=300,

    model_name="Qwen/Qwen2.5-72B-Instruct-AWQ",

    dry_run=False,

    output_dir=output_dir,

    fixture_root=Path("workspace_fixtures"),

    backend_name="vllm",

    vllm_url="http://127.0.0.1:8001/v1",

    propagation_modes=[
        "single_origin",
        "one_to_many",
        "many_to_one",
    ],
)


runner = BenchmarkRunner(manifest)


with open(
    "/u/kdhande/agent-graph/traces/"
    "leps_massive/lep_plan.pkl",
    "rb",
) as f:
    plan = pickle.load(f)


# Same basic idea as your existing --num-nodes / --run-node:
# deterministic 8-way sharding.
my_plan = plan[worker_id::num_workers]


print(
    f"Worker {worker_id}/{num_workers}: "
    f"{len(my_plan)} LEP executions",
    flush=True,
)


for i, entry in enumerate(my_plan, 1):

    print(
        f"[worker {worker_id}] "
        f"[{i}/{len(my_plan)}] "
        f"{entry['scenario_id']}",
        flush=True,
    )

    try:

        record = runner._execute(entry)

        runner.results.append(record)

        runner._persist_record(record)

        print(
            f"[worker {worker_id}] "
            f"eligible={record.dataset_eligible} "
            f"termination={record.termination_reason} "
            f"trace={record.trace_path}",
            flush=True,
        )

    except Exception as exc:

        print(
            f"[worker {worker_id}] "
            f"FAILED "
            f"{entry['scenario_id']}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


print(
    f"Worker {worker_id} COMPLETE",
    flush=True,
)
PY

    pids+=($!)

done


# ============================================================
# WAIT
# ============================================================

echo "===== WAITING FOR LEP SHARDS ====="

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

    echo "ERROR: one or more LEP shards failed"

    exit 1

fi


echo "===== ALL LEP SHARDS COMPLETE ====="


# ============================================================
# SUMMARY
# ============================================================

echo "Trace file counts by shard:"

for k in $(seq 0 $((N-1))); do

    COUNT=$(find "$OUT/node_$k" \
        -type f \
        -name '*_lep_trace.json' \
        | wc -l)

    echo "node_$k: $COUNT"

done


TOTAL=$(find "$OUT" \
    -type f \
    -name '*_lep_trace.json' \
    | wc -l)


echo "TOTAL LEP TRACE FILES: $TOTAL"

echo "===== MASSIVE LEP RUN COMPLETE ====="
