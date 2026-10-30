# Preflight report — proposed rerun plan (2026-10-19)

**Verdict: FAIL as originally written → PASS after two fixes, both now applied and
verified. Nothing submitted; nothing executed.**

Scope: `planning_audit_oct19/rerun.sbatch`, `rerun_plan_manifest.json`, and the
current production runner. Read-only throughout. `production_run/` has 0 files
modified; `rerun_production_oct19/` was never created; no Slurm job is queued.

---

## Blocking defects found

### D1 — Every array task would have run the entire selection (critical)

`rerun.sbatch` passed `--plan-manifest` but not `--run-node`/`--num-nodes`.

- `benchmark/plan_manifest.py:89-93` — `load_plan_manifest` replaces only
  settings, task families, fixtures, topologies, propagation modes, LEPs and
  `smoke_fixtures`. It never sets `node_id`/`num_nodes`, so both keep their
  defaults (`benchmark_runner.py:261-262`: `node_id=0`, `num_nodes=1`).
- `benchmark/benchmark_runner.py:502-504` — `split_plan` is called **only** when
  `num_nodes > 1`. With `num_nodes=1` the branch is skipped.
- `benchmark/__main__.py:499` — `plan = self.manifest.build_plan() if plan is None
  else plan`, so the loaded selection passes through untouched.

Net effect: 100 tasks × 7,812 = **781,200 executions** instead of 7,812, and
because each task writes only its own `node_<task>/`, the output would have
looked complete while being 100× duplicated. Measured directly with
`planning_audit_oct19/check_partitioning.py`:
`manifest.num_nodes after load_plan_manifest: 1`.

**Fix (applied, `rerun.sbatch:106-117`):** add `--run-node "$RUN_NODE"` and
`--num-nodes "$NUM_NODES"`. These are *not* in the conflict tuple at
`benchmark/__main__.py:267-276`, so they are accepted alongside
`--plan-manifest` without tripping the settings check. Verified: task 0 → 102
entries, all 100 tasks sum to exactly 7,812 with 7,812 distinct scenario_ids and
no duplicate dispatch (`verify_fixes.py`).

### D2 — A backend outage would still have been reported as a green run (high)

`benchmark/__main__.py:325` returns 0 unconditionally. `_summarize()` counts
`dataset_eligible=False` into `failed_runs`
(`benchmark/benchmark_runner.py:877, 919-920`) but nothing converts that into an
exit status. Production confirms the consequence: `prod-3291540_44.log` records
`Shard 44 finished with status 0` while all 183 attempts terminated with
`backend_http_error`. This is precisely the silent-loss mode in the brief, and
the readiness gate does not cover it — the backend can pass `/v1/models` and then
fail every request.

**Fix (applied, `rerun.sbatch:125-137`):** count
`"termination_reason": "backend_` in the shard's `benchmark_records.jsonl` and
`exit 1` when it is a strict majority. Verified against four cases
(`verify_fixes.py`): all-backend-failure → exit 1; healthy shard with normal
scientific rejections → exit 0; exactly 50% → exit 0; missing ledger → exit 0
(no crash).

---

## Verified PASS

### 1. Selection is complete and executed exactly once

- 7,812 scenarios, `scenario_id` and `run_id` both unique
  (`preflight.py`, 38 checks, 0 failures).
- Resolves through the repository's own strict loader
  (`benchmark/plan_manifest.py:load_plan_manifest`) — which independently
  re-checks every identity against the canonical planner, current fixture
  manifests, variant/LEP/topology/mode compatibility, pair tags and repetition
  indices.
- All 701 referenced fixture manifests exist and their SHA-256 hashes match the
  live tree.
- Seed 42, temperature 0.4, `num_repetitions=1`, `num_benign_repetitions=3`,
  `max_events=300`, `max_agent_turns=80`, `dry_run=False`, backend `vllm`, model
  `Qwen/Qwen2.5-72B-Instruct-AWQ` — all preserved and matching production.
- Every audit row agrees field-for-field with its planner entry (no identity
  drift introduced during manifest construction).
- `_short_trace_id` is unique across all 7,812 entries, so execution IDs do not
  collide.
- `attempt_id` is a fresh `uuid4` per execution
  (`benchmark_runner.py:536`) and the trace filename embeds it
  (`benchmark_runner.py:630-631`), so a same-scenario rerun cannot overwrite an
  existing trace.

### 2. Per-task vLLM backend, readiness, and failure signalling

- vLLM is started inside each task (`rerun.sbatch:58-66`), bound to
  `127.0.0.1`, with `--max-model-len 32768`,
  `--gpu-memory-utilization 0.90`, `--enable-auto-tool-choice`,
  `--tool-call-parser hermes` — identical to production.
- Readiness is polled against the real `/v1/models` endpoint
  (`rerun.sbatch:75-82`), bounded to 120 × 5 s = 10 min.
- Exits 1 if vLLM dies before ready (`rerun.sbatch:83-86`) and exits 1 if it
  never becomes ready (`rerun.sbatch:91-95`). `trap cleanup EXIT` kills it.
- Backend retry policy is unchanged: `stage_runner.py:124-144` retries each
  request up to 3 times with 2 s/4 s backoff, and `backend/errors.py:16-37`
  classifies HTTP 5xx/408/429, connection and timeout errors as retryable.
- Post-run failure signalling now covered by D2.

### 3. Output isolation and selective rerun

- `OUTPUT_ROOT="rerun_production_oct19"`; the string `production_run` appears
  only in two explanatory comments (`rerun.sbatch:14,35`), never in an
  executable line. (`preflight_isolation.py` flagged this; the check was
  over-broad — the reference is prose, not a path.)
- `OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"` — one directory per array task,
  no shared write target.
- Partial recovery: `benchmark_runner.py:530-531` refuses to start if the output
  directory already holds a *different* frozen plan, but a re-submission with the
  *same* plan is idempotent. `split_plan` keys on `pair_tag`, so
  `--array=37` alone regenerates exactly that task's 53 entries — verified
  deterministic across two calls.
- Collision safety: production trace filenames include their `attempt_id`, and
  the rerun generates fresh UUIDs, so no filename can collide.

### 4. Slurm settings, resources, ports, walltime

- `--partition=ghx4 --nodes=1 --gpus-per-node=1 --cpus-per-task=32
  --time=02:00:00 --array=0-99 --account=biha-dtai-gh`.
- Partition max timelimit is `2-00:00:00` (`sinfo -p ghx4`), so the request is
  exactly at the ceiling — same as the original production run.
- GH200 nodes carry 4 × `nvidia_gh200_120gb` and 288 CPUs, so 1 GPU + 32 CPUs
  leaves room for up to 4 concurrent tasks per node. The 72B AWQ model loads in
  roughly 40 GB against a 108 GB budget (0.90 utilization), and the production
  vLLM log records `GPU KV cache size: 133,536` — it fits, as it did for 160
  concurrent production shards.
- Ports: every task binds `127.0.0.1:8001`. 160 production shards did the same
  with zero `address already in use` errors, confirming per-task network
  isolation. Flagged as a residual risk only if that cluster behaviour changes;
  the fallback is `PORT=$((8001 + SLURM_ARRAY_TASK_ID))`.

### 5. Runtime estimate

Conservative model: 35 s per scenario (production p50 is 25.4 s, mean 24.2 s,
p90 46.8 s) plus 150 s startup (production readiness median 115 s, p90 190 s).

- Task 0 executes 102 entries → ~1.24 h conservative; heaviest task 143 entries
  → ~1.43 h.
- Original run: median 1.59 h, p90 2.02 h, max 2.25 h, with 16 shards at or past
  the 2 h limit and 9 killed outright. The 100-task split keeps the worst task
  ~17% under the limit, which is the headroom that addresses the observed
  timeouts rather than assuming they were bad luck.
- ~80 GPU-hours total. 60 or 80 tasks would push the max task back over 2 h.

### 6. Legacy and ERP populations excluded

- The 761 `unverifiable_legacy` perturbed traces were re-identified by re-running
  admission on disk (exactly 761, all IPI 381 / TRC 380) and confirmed to have
  **zero** overlap with the rerun manifest.
- No `existing_record_poisoning_v1` rows and no `_briefing_v1` fixtures appear in
  the manifest; all 7,812 rows are `write_interception_v1`. The analyzer raises on
  mixed protocols (`propagation_analysis.py:486-487`), so pooling is impossible
  rather than merely discouraged.
- No manifest row regenerates a trace already admitted on disk (0 overlaps).

### Manifest ordering and fixture hashes

- `split_plan` hashes `pair_tag`, so the partition is independent of manifest
  order — verified by shuffling the 7,812 entries with a fixed seed and confirming
  tasks 0, 37 and 99 produce identical sets.
- Fixture hashes are re-verified at load time inside `load_plan_manifest`
  (`plan_manifest.py:84-88`) and again at dispatch for protocol-bearing entries
  (`benchmark_runner.py:583-587`), so drift fails closed before any backend call.

---

## Proposed submission command

Not executed.

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
sbatch --array=0-99 planning_audit_oct19/rerun.sbatch
```

`--array=0-99` is redundant with the `#SBATCH --array=0-99` directive in the
script but makes the intent explicit. Do not add
`--topologies/--task-families/--lep-codes/--propagation-modes`; the CLI rejects
them with `--plan-manifest`.

For a single failed task: `sbatch --array=37 planning_audit_oct19/rerun.sbatch`.

---

## Residual risks (accepted, not blocking)

1. **Backend instability recurrence.** The guard makes failure visible but does
   not prevent it. If a second outage occurs, expect shard-level `exit 1` and
   re-run the affected `--array` values.
2. **Port assumption.** Relies on per-task network namespaces, evidenced by the
   160-shard production run.
3. **Partial clean-reference yield.** Some of the 1,871 clean top-ups will
   themselves be rejected (`max_events_reached`, `protocol_violation`). The
   residual deficit must be reported, not assumed away; a second top-up round may
   be needed.
4. **`set -e` interaction.** `STATUS=$?` is captured immediately after the Python
   call, so `set -e` cannot abort before the guard runs; verified by reading the
   script order, and `bash -n` passes.
