# Execution specification — BND V4.1 benign-control calibration (270 runs)

This file documents **how** to generate the 270 new clean traces. It is a
specification only; it is not launched here. Everything below is derived
from the frozen production artifacts, not invented.

## 1. The one model (verified, not Qwen3-8B)

| property | value | source of truth |
|---|---|---|
| model | `Qwen/Qwen2.5-72B-Instruct-AWQ` | `production.sbatch`, trace metadata `workflow_config.model_name` |
| arch | `Qwen2ForCausalLM` | HF `config.json` + `vllm.log` |
| quantization | AWQ 4-bit, group 128, zero_point, gemm, dtype float16 | HF `config.json` `quantization_config` |
| checkpoint revision | `698703eae6604af048a3d2f509995dc302088217` | `~/.cache/huggingface/hub/models--Qwen--Qwen2.5-72B-Instruct-AWQ/refs/main` |
| HF cache | `/u/kdhande/.cache/huggingface` | `production.sbatch` `HF_HOME` |

The same 72B AWQ checkpoint was served on every production shard
(`production_run/node_*/vllm.log` → `model Qwen/Qwen2.5-72B-Instruct-AWQ`).
There is **no** Qwen3-8B in the production path.

## 2. The one vLLM server (must match production flags)

`production.sbatch` (the script that actually produced the frozen traces)
starts the server with exactly:

```bash
vllm serve "Qwen/Qwen2.5-72B-Instruct-AWQ" \
  --port 8001 \
  --host 127.0.0.1 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

Confirmed in `production_run/node_0/vllm.log`:
`non-default args: {'model': ...AWQ, 'enable_auto_tool_choice': True,
'tool_call_parser': 'hermes', 'host': '127.0.0.1', 'port': 8001,
'max_model_len': 32768, 'gpu_memory_utilization': 0.9}`.
Engine: V1 runner, `quantization=auto_awq`, `tensor_parallel_size=1`,
`dtype=torch.float16`, `enforce_eager=False`.

Default sampling params that vLLM adopts from the checkpoint
`generation_config.json` (the production log records this override):
`top_k=20, top_p=0.8, repetition_penalty=1.05`.

## 3. Per-request inference settings (from the frozen code path)

The benchmark talks to vLLM through `backend/hf_backend.py`
(`HFBackend`), which for the production path is selected by
`benchmark/benchmark_runner.py:_default_backend()` when
`--backend vllm` is set. Its request payload is:

| param | value | source |
|---|---|---|
| `model` | `Qwen/Qwen2.5-72B-Instruct-AWQ` | `--model` |
| `temperature` | `0.4` | `BenchmarkManifest.temperature` default, recorded in every trace `workflow_config.temperature` |
| `max_tokens` | `4096` | `HFBackend.__init__` default |
| `seed` | not sent in the request | `HFBackend._call_api` payload has no `seed` key |
| `top_k`/`top_p`/`repetition_penalty` | inherited (20 / 0.8 / 1.05) | checkpoint `generation_config.json` |

`workflow_config.seed=42` is recorded in trace metadata but is **not** part
of the OpenAI-compatible request, so run-to-run variability across B4–B9
comes from `temperature=0.4` sampling, not a per-run seed. Reproducibility
is therefore **configuration-level** (same checkpoint, same vLLM flags, same
prompt/tool/execution config), not output-level. This is documented in the
manifest's `seed_note`.

## 4. The one execution config (from trace `workflow_config`, all 3 refs agree)

`max_events=300`, `max_agent_turns=80`, `timeout_seconds=300`,
`sharing_policy=handoff_summary_only`, `verification_mode=self_check`,
`allow_parallel_agents=False`, `allow_retries=True`,
`memory_protocol=write_interception_v1`.

Memory mode is set by execution variant (`benchmark_runner._build_workflow_config`):
- `standard` → `ephemeral_private` (no shared memory)
- `memory_enabled` → `ephemeral_shared` (benign runs keep a clean store)

## 5. Scheduling semantics that the new runs must reproduce

The new clean runs execute the **same topology + fixture + variant** as the
B1–B3 references, so they reproduce the recorded scheduling skeleton
(worker-first for `coordinator_workers`, i.e. the transition order begins
`specialist_a → specialist_b`, not `coordinator`). Because
`is_comparable`'s structural gate (`execution_shape`) already matched the
references on all 45 selected cells, re-running the identical config keeps
the new reps in the same structural class. Do **not** change topology,
variant, or the `--max-events`/turn limits when generating.

## 6. Generating exactly the 270 (no wasted compute)

```bash
# 0. environment (matches production.sbatch)
module load python/miniforge3_pytorch/2.10.0
source /sw/user/python/miniforge3-pytorch-2.10.0/etc/profile.d/conda.sh
conda activate agentgraph-vllm
export HF_HOME=/u/kdhande/.cache/huggingface
cd /u/kdhande/agent-graph/agent-graph-v3

# 1. start the production server (section 2) on port 8001

# 2. run exactly the 270 planned benign repetitions (reps 3-8 of 45 cells)
python3.11 -m benchmark \
  --real-model --backend vllm \
  --vllm-url http://127.0.0.1:8001/v1 \
  --model "Qwen/Qwen2.5-72B-Instruct-AWQ" \
  --plan-manifest analysis/bnd_v41_benign_calibration_manifest/plan_manifest.json \
  --output-dir analysis/bnd_v41_benign_calibration_manifest/execution
```

Notes:
- `--plan-manifest` resolves **only** the 270 benign entries
  (`num_repetitions=0` in the plan manifest, `num_benign_repetitions=9`,
  but the runner executes the selected list, not the full cross-product), so
  reps 0–2 (already produced) and all LEP runs are NOT re-executed.
- Conflict-checks verified by `preflight_validate.py`: `--model`,
  `--backend`, `--vllm-url`, `--real-model` all match the saved manifest
  settings; no matrix-filter flags are supplied; 270/270 scenario ids are
  unique and resolve in the frozen planner.
- In any Slurm array, add `--run-node "$SLURM_ARRAY_TASK_ID"
  --num-nodes <N>` and keep `N` in sync with the array size (see
  `[[plan-manifest-needs-partition-flags]]`); and add the shard guard from
  `[[benchmark-cli-exits-zero-on-backend-failure]]` so a backend outage fails
  loudly.
- After generation, each new trace lands in
  `execution/traces/…` and should be appended to a consolidation so a V4.1
  re-run can read B4–B9. The B7–B9 reps must stay held out: do not look at
  their V4.1 anomaly status until the threshold is fixed on B4–B6.
