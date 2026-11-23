#!/bin/bash -l
#SBATCH --job-name=bndv41-cw-def
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --time=00:50:00
#SBATCH --array=0-63
#SBATCH --output=/u/kdhande/agent-graph/bndv41-cw-def-%A_%a.log
#SBATCH --error=/u/kdhande/agent-graph/bndv41-cw-def-%A_%a.err
# ────────────────────────────────────────────────────────────────────────────
# BND V4.1 benign regeneration — coordinator_workers FANOUT benigns,
# DEFAULT fixture root (workspace_fixtures).  672 benign executions across
# 482 CW cells, 64 runner shards via the frozen split_plan() md5 assignment.
#
# Submit (single command, whole array):
#   sbatch analysis/bnd_v41_cw_swe200_regeneration_audit/slurm/bndv41_cw_default.sbatch
#
# This cluster's sbatch (slurm 25.11.8, DeltaAI) has no --concurrent array
# option.  Concurrency control is NOT needed for backend protection here:
# each shard owns its OWN GPU + its OWN local vLLM instance (127.0.0.1
# random port), so no shared inference server exists to overwhelm.  The
# ghx4 partition's native GPU accounting (1× GH200/shard) + the
# 5× 1-GPU × 64-shard TRES request bound effective concurrency to however
# many GPUs are actually free, which is exactly the cap we want.  The
# wall limit (00:50:00) is sized for the WORST shard (19 execs ≈ 32m
# estimated); shorter shards exit well inside.  NUM_NODES is fixed at 64
# (= runner shard space); RUN_NODE is the global array index, so every
# runner shard runs exactly once across the full array.
#
#   manifest   analysis/bnd_v41_cw_swe200_regeneration_audit/cw_regen_fanout_default_manifest.json
#   output     production_run/bndv41_cw_default_regen/node_<i>/
#
# Wall model: vLLM startup ~9m + worst shard (19 execs) x p90 56s ~ 18m +
# shutdown ~5m = ~32m; --time 00:50:00 leaves headroom for p99 tails.
# Every entry is condition=benign, execution_variant=standard, lep_codes=[]
# => memory poisoning and all memory_enabled cohorts are structurally absent.
# New rep indices are collision-free against the frozen staged pools (0/937),
# so no existing trace is ever overwritten; the output root is new.
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/hub"

cd /u/kdhande/agent-graph/agent-graph-v3

MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"
# Runner shard space is FIXED at 64 (frozen split_plan() md5 assignment).
# The Slurm array may be submitted in chunks (this sbatch header: 0-31;
# second chunk: -a 32-63).  RUN_NODE is the GLOBAL array index, so each of
# the 64 runner shards runs exactly once across both chunks.
NUM_NODES=64
RUN_NODE="${SLURM_ARRAY_TASK_ID}"

MANIFEST="analysis/bnd_v41_cw_swe200_regeneration_audit/cw_regen_fanout_default_manifest.json"
OUTPUT_ROOT="production_run/bndv41_cw_default_regen"
OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"

mkdir -p "$OUTPUT_DIR"
# Overwrite-safety: this output root must be new or hold only OUR frozen plan.
if [[ -f "${OUTPUT_DIR}/benchmark_records.jsonl" ]]; then
    echo "ERROR: ${OUTPUT_DIR} already has records; refusing to run (no re-runs here)."
    exit 1
fi

VLLM_PID=""
PORT=""
READY=0

cleanup() {
    if [[ -n "$VLLM_PID" ]]; then
        pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
        kill -TERM "$VLLM_PID" 2>/dev/null || true
        wait "$VLLM_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

echo "=================================================="
echo "BND V4.1 CW-FANOUT benign regen (default root)"
echo "Job:       ${SLURM_JOB_ID}"
echo "Shard:     ${RUN_NODE}/${NUM_NODES}"
echo "Host:      $(hostname)"
echo "Manifest:  ${MANIFEST}"
echo "Output:    ${OUTPUT_DIR}"
echo "Started:   $(date)"
echo "=================================================="

# ------------------------------------------------------------
# Manifest preflight (read-only; matches bndv41_regen_plan.json Job A)
# ------------------------------------------------------------
python - "$MANIFEST" "$NUM_NODES" <<'PY'
import json, sys, hashlib
from pathlib import Path
path, num_nodes = sys.argv[1], int(sys.argv[2])
data = json.load(open(path))
n = len(data["scenarios"])
assert data["schema"] == "agentprop-readiness-selection-v1"
run_ids = [s["run_id"] for s in data["scenarios"]]
sids = [s["scenario_id"] for s in data["scenarios"]]
assert len(set(run_ids)) == n, "Duplicate run IDs"
assert len(set(sids)) == n, "Duplicate scenario IDs"
for s in data["scenarios"]:
    assert s["topology"] == "coordinator_workers", f"Out-of-scope topology: {s['topology']}"
    assert s["condition"] == "benign", f"Non-benign execution: {s['scenario_id']}"
    assert s["execution_variant"] == "standard", "Non-standard execution found"
    assert s["lep_codes"] == [] and "MEMORY_POISONING" not in str(s.get("lep_code","")), \
        "LEP / Memory Poisoning execution found"
plan = json.load(open("analysis/bnd_v41_cw_swe200_regeneration_audit/bndv41_regen_plan.json"))
ja = next(j for j in plan["jobs"] if j["id"] == "A")
assert num_nodes == ja["n_shards"], f"--array size ({num_nodes}) != plan Job A shards ({ja['n_shards']})"
assert n == ja["n_exec"], f"manifest execs ({n}) != plan Job A n_exec ({ja['n_exec']})"
# fixture hash map vs disk (default root)
for fx, h in data["fixture_manifest_sha256"].items():
    raw = Path("workspace_fixtures") / fx / "manifest.json"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == h, f"Fixture manifest changed: {fx}"
print(f"Manifest preflight PASSED: {n} executions, {num_nodes} shards, "
      f"{len(data['fixture_manifest_sha256'])} fixtures verified")
PY

# ------------------------------------------------------------
# Committed-fix guard: canonical CW topology (commit 93985e5) must be present
# in the working tree so newly generated benigns record the FANOUT skeleton.
# ------------------------------------------------------------
if ! grep -q "topology.is_coordinator_first()" generation/runner.py; then
    echo "ERROR: Coordinator-first scheduler fix missing in generation/runner.py."; exit 1
fi
python - <<'PY'
from generation.topology import get_topology
topology = get_topology("coordinator_workers",
                        {k: f"a{i}" for i, k in enumerate(
                            ["coordinator","specialist_a","specialist_b","synthesizer"])})
assert topology.is_coordinator_first() is True
print("Canonical CW topology guard PASSED (coordinator-first FANOUT skeleton)")
PY

select_port() {
    python - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

MAX_PORT_ATTEMPTS=5
for attempt in $(seq 1 "$MAX_PORT_ATTEMPTS"); do
    PORT="$(select_port)"
    echo "vLLM startup attempt ${attempt}/${MAX_PORT_ATTEMPTS} port ${PORT}"
    vllm serve "$MODEL" \
        --port "$PORT" \
        --host 127.0.0.1 \
        --max-model-len 32768 \
        --gpu-memory-utilization 0.90 \
        --enable-auto-tool-choice \
        --tool-call-parser hermes \
        > "${OUTPUT_DIR}/vllm.log" 2>&1 &
    VLLM_PID=$!

    READY=0
    for i in $(seq 1 120); do
        if ! kill -0 "$VLLM_PID" 2>/dev/null; then break; fi
        if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
            READY=1
            echo "vLLM ready on port ${PORT}"
            break
        fi
        sleep 5
    done
    if [[ "$READY" -eq 1 ]]; then break; fi
    echo "vLLM startup attempt failed."
    if ! grep -Eqi "address already in use|port.*in use|errno 98" "${OUTPUT_DIR}/vllm.log"; then
        tail -100 "${OUTPUT_DIR}/vllm.log" || true; exit 1
    fi
    pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
    kill -TERM "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
    VLLM_PID=""; sleep 3
done
if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM failed to start."; tail -100 "${OUTPUT_DIR}/vllm.log"; exit 1
fi

# ------------------------------------------------------------
# Run ONLY this shard via the frozen split_plan()
# ------------------------------------------------------------
RUNTIME_MANIFEST="${OUTPUT_DIR}/runtime_manifest.json"
python - "$MANIFEST" "$RUNTIME_MANIFEST" "$PORT" <<'PYTHON'
import json, sys
source, destination, port = sys.argv[1:]
data = json.load(open(source))
settings = data.get("settings")
if not isinstance(settings, dict) or "vllm_url" not in settings:
    raise SystemExit("Cannot locate settings.vllm_url; original manifest unchanged.")
settings["vllm_url"] = f"http://127.0.0.1:{port}/v1"
json.dump(data, open(destination, "w"), indent=2)
print(f"Runtime vLLM endpoint: http://127.0.0.1:{port}/v1")
PYTHON

STATUS=0
python -m benchmark \
    --real-model \
    --backend vllm \
    --vllm-url "http://127.0.0.1:${PORT}/v1" \
    --model "$MODEL" \
    --plan-manifest "$RUNTIME_MANIFEST" \
    --fixture-root workspace_fixtures \
    --run-node "$RUN_NODE" \
    --num-nodes "$NUM_NODES" \
    --output-dir "$OUTPUT_DIR" || STATUS=$?

echo "Benchmark exit status: ${STATUS}"

# ------------------------------------------------------------
# Backend failure analysis (shard guard: outages must not look green)
# ------------------------------------------------------------
RECORDS="${OUTPUT_DIR}/benchmark_records.jsonl"
if [[ ! -f "$RECORDS" ]]; then
    echo "ERROR: benchmark_records.jsonl missing."; exit 1
fi
python - "$RECORDS" <<'PY'
import json, sys
path = sys.argv[1]
attempts = backend_failures = invalid = 0
for line in open(path):
    if not line.strip():
        continue
    attempts += 1
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        invalid += 1
        continue
    if str(record.get("termination_reason", "")).startswith("backend_"):
        backend_failures += 1
print(f"Total records:     {attempts}")
print(f"Backend failures:  {backend_failures}")
print(f"Invalid JSONL:     {invalid}")
if attempts == 0:
    sys.exit("ERROR: No benchmark records")
if invalid:
    sys.exit("ERROR: Invalid benchmark records")
if backend_failures * 2 > attempts:
    sys.exit("ERROR: Majority of executions failed on backend")
if backend_failures:
    print("WARNING: Partial backend failures detected")
PY

echo "=================================================="
echo "Shard ${RUN_NODE} finished; status ${STATUS}"
echo "Finished: $(date)"
echo "Output:   ${OUTPUT_DIR}"
echo "=================================================="
exit "$STATUS"
