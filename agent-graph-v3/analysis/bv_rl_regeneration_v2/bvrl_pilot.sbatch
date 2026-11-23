#!/bin/bash -l
#SBATCH --job-name=bvrl-pilot
#SBATCH --account=biha-dtai-gh
#SBATCH --partition=ghx4-interactive
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32g
#SBATCH --time=02:00:00
#SBATCH --output=/u/kdhande/agent-graph/bvrl-pilot-%A.log
#SBATCH --error=/u/kdhande/agent-graph/bvrl-pilot-%A.err

set -euo pipefail

module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm

export HF_HOME=/u/kdhande/.cache/huggingface
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/hub"

cd /u/kdhande/agent-graph/agent-graph-v3

MODEL="Qwen/Qwen2.5-72B-Instruct-AWQ"
MANIFEST="analysis/bv_rl_regeneration_v2/pilot_manifest.json"
OUTPUT_DIR="production_run/bv_rl_regeneration_v2/pilot"
mkdir -p "$OUTPUT_DIR"

# Same committed-fix guards as the full-batch script
grep -q "topology.is_coordinator_first()" generation/runner.py || { echo "ERROR: missing coordinator-first fix"; exit 1; }
grep -q "origin_structure_errors" generation/runner.py || { echo "ERROR: missing structural admission fix"; exit 1; }
grep -q "review_event_budget" generation/runner.py || { echo "ERROR: missing review-cap fix"; exit 1; }

# Manifest preflight
python - "$MANIFEST" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
n = len(data["scenarios"])
assert data["schema"] == "agentprop-readiness-selection-v1", "bad schema"
assert len({s["scenario_id"] for s in data["scenarios"]}) == n, "duplicate scenario ids"
assert all(s["topology"] in ("branch_and_verify", "review_loop") for s in data["scenarios"])
assert all("MEMORY_POISONING" not in str(s.get("lep_code", "")) for s in data["scenarios"])
print("Pilot preflight PASSED:", n, "executions")
PY

PORT="$(python - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
)"
echo "Selected port: ${PORT}"

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
    if [[ -n "${VLLM_PID:-}" ]]; then
        pkill -TERM -P "$VLLM_PID" 2>/dev/null || true
        kill -TERM "$VLLM_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

READY=0
for i in $(seq 1 120); do
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then break; fi
    if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
        READY=1; echo "vLLM ready on port ${PORT}"; break
    fi
    sleep 5
done
if [[ "$READY" -ne 1 ]]; then
    echo "ERROR: vLLM failed to start"; tail -100 "${OUTPUT_DIR}/vllm.log"; exit 1
fi

RUNTIME_MANIFEST="${OUTPUT_DIR}/runtime_manifest.json"
python - "$MANIFEST" "$RUNTIME_MANIFEST" "$PORT" <<'PY'
import json, sys
source, destination, port = sys.argv[1:]
data = json.load(open(source))
url = f"http://127.0.0.1:{port}/v1"
data["settings"]["vllm_url"] = url
json.dump(data, open(destination, "w"), indent=2)
print(f"Runtime vLLM endpoint: {url}")
PY

STATUS=0
python -m benchmark \
    --real-model \
    --backend vllm \
    --vllm-url "http://127.0.0.1:${PORT}/v1" \
    --model "$MODEL" \
    --plan-manifest "$RUNTIME_MANIFEST" \
    --output-dir "$OUTPUT_DIR" || STATUS=$?

echo "Pilot benchmark exit status: ${STATUS}"

# Per-execution outcomes + runtime for the pilot acceptance gate
python - "$OUTPUT_DIR/benchmark_records.jsonl" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    if not line.strip():
        continue
    r = json.loads(line)
    print(
        f"{r.get('scenario_id')}\t"
        f"term={r.get('termination_reason')}\t"
        f"eligible={r.get('dataset_eligible')}\t"
        f"exp={r.get('expected_injection_origins')} act={r.get('actual_injection_origins')}\t"
        f"runtime_s={round(float(r.get('runtime_seconds') or 0), 1)}"
    )
PY

exit "$STATUS"
