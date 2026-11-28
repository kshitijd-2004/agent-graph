#!/bin/bash -l
# MP / ID pilot, 1 GPU per task. pilot_submit.sh passes FAM (an arm from pilot_families.sh).
# Code runs from $BASE/ag-pilot (ag-run plus pilot.patch), so the main checkout is untouched.
# The arm's switches (PILOT_ENV) are set only for the benchmark process. Slices that already
# have benchmark_summary.json are skipped, so re-running a task is safe.
#SBATCH --job-name=pilot
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=01:50:00
#SBATCH --output=/work/nvme/biha/%u/pilot-%x-%A_%a.log

set -x
START=$(date +%s); LIMIT=6600   # seconds, must match --time
BASE=/work/nvme/biha/$USER
V3=$BASE/ag-pilot/agent-graph-v3
source $BASE/pilot_families.sh "$FAM" || { echo "unknown FAM=$FAM"; exit 1; }
OUT=$BASE/traces/pilot/$FAM
FIXTURES=$BASE/traces/pilot/fixtures_$FAM
MODEL=casperhansen/llama-3.3-70b-instruct-awq
GPUS=1
PER_GPU=8
TOTAL=$((NJOBS*4*PER_GPU))   # same slicing as the 4-GPU jobs
NODE=$SLURM_ARRAY_TASK_ID
mkdir -p "$OUT" "$FIXTURES"
export HF_HOME=$BASE/hf
git -C "$BASE/ag-pilot" rev-parse HEAD > "$OUT/commit_job$NODE.txt"; echo "$PILOT_ENV" > "$OUT/pilot_env.txt"

# every FIXSTEP-th fixture in sorted order (15 = the 20-task pilot, 1 = all tasks)
for d in $(ls -d $V3/$FIXGLOB | sort | awk -v s=${FIXSTEP:-15} '(NR-1)%s==0'); do ln -sfn "$d" "$FIXTURES/$(basename "$d")"; done
echo "fixtures: $(ls "$FIXTURES" | wc -l)"

for g in $(seq 0 $((GPUS-1))); do
  apptainer exec --nv --bind /work --env HF_HOME=$HF_HOME \
    $BASE/vllm.sif \
    vllm serve $MODEL --port $((8001+g)) --max-model-len 16384 \
    --enable-auto-tool-choice --tool-call-parser llama3_json \
    > "$OUT/vllm_n${NODE}_g${g}.log" 2>&1 &
done

ports=()
for g in $(seq 0 $((GPUS-1))); do
  for i in $(seq 1 120); do
    curl -sf localhost:$((8001+g))/v1/models >/dev/null && break
    sleep 10
  done
  if curl -sf localhost:$((8001+g))/v1/models >/dev/null; then
    ports+=($((8001+g)))
  else
    echo "WARNING: vLLM on GPU $g never came up, its slices move to the other servers"
  fi
done

if [ ${#ports[@]} -gt 0 ]; then
  echo "servers up: ${ports[*]}"
  module load python/3.11.9
  export VLLM_MODEL=$MODEL VLLM_API_KEY=dummy VLLM_TEMPERATURE=0.4
  cd "$V3"
  pids=(); mine=()
  for j in $(seq 0 $((GPUS*PER_GPU-1))); do
    k=$(( NODE*GPUS*PER_GPU + j ))
    [ $k -ge $TOTAL ] && continue
    if [ -f "$OUT/node_$k/benchmark_summary.json" ]; then echo "slice $k already done, skipping"; continue; fi
    if ! mkdir "$OUT/node_$k.lock" 2>/dev/null; then
      owner=$(cat "$OUT/node_$k.lock/owner" 2>/dev/null)
      # a lock whose job is gone (killed before it could clean up) is stale: take it over
      if [ -n "$owner" ] && [ -z "$(squeue -h -j "$owner" 2>/dev/null)" ]; then
        rm -rf "$OUT/node_$k.lock"; mkdir "$OUT/node_$k.lock" 2>/dev/null || continue
      else
        echo "slice $k taken by job $owner, skipping"; continue
      fi
    fi
    echo "$SLURM_JOB_ID" > "$OUT/node_$k.lock/owner"; mine+=($k)
    port=${ports[$(( (j / PER_GPU) % ${#ports[@]} ))]}
    # timeout ends every slice 10 min before the time limit, so the cleanup below always runs
    VLLM_BASE_URL=http://localhost:$port/v1 \
    env $PILOT_ENV timeout $(( (START + LIMIT - 600 - $(date +%s)) / 60 ))m python3 -m benchmark --real-model --backend vllm \
      --vllm-url http://localhost:$port/v1 --model $MODEL \
      --task-families $FAMILY --lep-codes $LEPS --topologies review_loop,branch_and_verify,coordinator_workers \
      --repetitions 1 --benign-repetitions $BENIGN \
      --num-nodes $TOTAL --run-node $k --fixture-root $FIXTURES \
      --output-dir "$OUT/node_$k" > "$OUT/node_$k.log" 2>&1 &
    pids+=($!)
  done
  wait "${pids[@]}"
  # free locks of slices that did not finish (timeout), so a later job can redo them
  for k in "${mine[@]}"; do [ -f "$OUT/node_$k/benchmark_summary.json" ] || rm -rf "$OUT/node_$k.lock"; done
else
  echo "no vLLM server came up"
fi

done_n=$(ls "$OUT"/node_*/benchmark_summary.json 2>/dev/null | wc -l)
echo "$FAM job $NODE finished; slices done so far: $done_n of $TOTAL"
