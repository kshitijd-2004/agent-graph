# AgentProp production audit and rerun plan (2026-10-19)

**Scope: read-only audit of `production_run/` plus an execution plan. No Slurm job
was submitted, no vLLM server was started, no production file was modified.**

The audit re-derives every number from the repository's own experiment identity
and admission rules (`generation/admission.py:validate_trace`, re-run on disk),
not from historical summaries. Where a historical summary disagrees with the
current disk state, the disk state is reported.

---

## 1. Production completion summary

| Quantity | Value |
|---|---|
| Shards scheduled (Slurm array 3291540, `--array=0-159`) | 160 |
| Shard directories present | 160 (node_0 … node_160; node_160 is an empty stray dir) |
| Shards that produced at least one accepted trace | 120 |
| Shards that ran to completion but accepted **zero** traces | 40 |
| Shards cancelled at the 2 h Slurm time limit | 9 (tasks 0, 3, 6, 47, 61, 62, 65, 145, 155) |
| Trace files on disk (`traces/` + `rejected_traces/`) | 29,949 |
| Distinct attempts (deduplicated by `attempt_id`) | 29,949 — **no duplicate or conflicting attempts** |
| Ingestion errors (unparseable trace files) | 0 |
| Admitted benign traces (re-validated on disk) | 8,289 |
| Admitted perturbed traces (re-validated on disk) | 10,345 |
| Perturbed traces that pass runtime eligibility but **fail re-admission** | 761 (`unverifiable_legacy`: IPI 381, TRC 380) |
| Frozen plan slots recorded on disk | 26,768 (141 shards wrote a plan) |
| Canonical plan slots for this configuration | 30,229 |
| Protocol variants observed in `production_run/` | 1 (`write_interception_v1`) |

**Slurm outcome by shard class.** All 9 time-limit cancellations are recorded
verbatim in `/u/kdhande/agent-graph/prod-3291540_<task>.err`. Six of the nine
still had undispatched plan entries when they were killed (node_61: 54,
node_62: 53, node_47: 23, node_155: 17, node_65: 6, node_145: 2) — 155 planned
executions that never ran at all, of which 93 are perturbed.

The 40 zero-accept shards are not empty: each wrote a full frozen plan, a full
record ledger and a full `rejected_traces/` set. Every one of their 7,442 records
terminates with `backend_http_error`. This is a single sustained backend outage
(HTTP errors returned with ~0 s runtime), not a per-fixture problem. Their jobs
exited 0, so **"job succeeded" must not be read as "shard complete."**

### Analysis eligibility (the denominator that actually matters)

| | Traces |
|---|---|
| Admitted perturbed traces with ≥3 matched clean references (**analysis-ready**) | 4,574 |
| Admitted perturbed traces blocked by <3 clean references | 5,771 |
| Perturbed traces rejected on re-admission (`unverifiable_legacy`) | 761 |
| Rejected perturbed attempts with recoverable backend causes | 5,848 |
| Rejected perturbed attempts, genuinely unreachable injection target | 276 |
| Rejected perturbed attempts, other non-recoverable causes | 69 |

The 4,574 analysis-ready figure reproduces `preliminary_analysis_oct16` exactly,
which confirms the audit's admission logic matches the analyzer's.

### Per-cell analysis readiness (LEP × topology × mode × variant)

| Cell | Ready | Blocked |
|---|---:|---:|
| MEMORY_POISONING / coordinator_workers / one_to_many / memory_enabled | 52 | 58 |
| MEMORY_POISONING / coordinator_workers / single_origin / memory_enabled | 192 | 253 |
| MEMORY_POISONING / review_loop / single_origin / memory_enabled | 201 | 240 |
| HANDOFF_CORRUPTION / coordinator_workers / one_to_many / standard | 201 | 250 |
| INPUT_DISREGARD / coordinator_workers / one_to_many / standard | 201 | 250 |
| INPUT_DISREGARD / coordinator_workers / single_origin / standard | 202 | 250 |
| HANDOFF_CORRUPTION / coordinator_workers / single_origin / standard | 203 | 250 |
| INPUT_DISREGARD / review_loop / single_origin / standard | 203 | 271 |
| HANDOFF_CORRUPTION / review_loop / single_origin / standard | 203 | 272 |
| INPUT_DISREGARD / branch_and_verify / many_to_one / standard | 204 | 259 |
| HANDOFF_CORRUPTION / branch_and_verify / many_to_one / standard | 204 | 260 |
| HANDOFF_CORRUPTION / branch_and_verify / single_origin / standard | 204 | 260 |
| INPUT_DISREGARD / branch_and_verify / single_origin / standard | 204 | 260 |
| MEMORY_POISONING / branch_and_verify / single_origin / memory_enabled | 214 | 248 |
| MEMORY_POISONING / branch_and_verify / many_to_one / memory_enabled | 214 | 250 |
| TOOL_RESULT_CORRUPTION / branch_and_verify / many_to_one / standard | 229 | 208 |
| INDIRECT_PROMPT_INJECTION / coordinator_workers / one_to_many / standard | 238 | 211 |
| INDIRECT_PROMPT_INJECTION / branch_and_verify / many_to_one / standard | 238 | 217 |
| INDIRECT_PROMPT_INJECTION / coordinator_workers / single_origin / standard | 240 | 211 |
| INDIRECT_PROMPT_INJECTION / branch_and_verify / single_origin / standard | 241 | 221 |
| TOOL_RESULT_CORRUPTION / coordinator_workers / one_to_many / standard | 243 | 211 |
| TOOL_RESULT_CORRUPTION / branch_and_verify / single_origin / standard | 243 | 221 |
| TOOL_RESULT_CORRUPTION / coordinator_workers / single_origin / standard | 245 | 210 |
| INDIRECT_PROMPT_INJECTION / review_loop / single_origin / standard | 257 | 214 |
| TOOL_RESULT_CORRUPTION / review_loop / single_origin / standard | 259 | 216 |

Every cell is missing roughly half its analyzable population. The deficit is
uniform across cells, which is the signature of the two systemic causes (one
backend outage covering 40 shards, plus clean-reference attrition) rather than of
any cell-specific scientific problem.

---

## 2. Missing-trace breakdown

### A. Missing clean references — 1,871 executions

The analyzer keys a clean reference as `(fixture_id, topology, execution_variant)`
and requires **3 distinct admitted repetition indices** (`MIN_CLEAN_RUNS_REQUIRED = 3`,
`benchmark/behavioral_anomaly.py:37`). `execution_variant` is `memory_enabled` for
`LEP_MEMORY_POISONING` and `standard` for every other LEP
(`propagation_analysis.py:_select_execution_variant`). Variants are never mixed.

1,534 distinct clean-reference groups are short. Repetition indices 0, 1, 2 are
distinct slots, so the deficit is counted per absent slot, not per cell:

| Breakdown | Count |
|---|---|
| Total additional clean executions required | **1,871** |
| Groups needing 1 more run | 1,202 |
| Groups needing 2 more runs | 327 |
| Groups needing 3 more runs (zero valid clean runs today) | 5 |

By execution variant: `standard` 958, `memory_enabled` 913.
By topology: coordinator_workers 632, review_loop 627, branch_and_verify 612.
By absent repetition index: rep 0 → 22, rep 1 → 929, rep 2 → 920.
By task family: research_synthesis 864, financial_analysis 766, code_review 241.

Every one of these 1,534 groups currently blocks at least one **already-admitted**
perturbed trace (5,771 blocked traces in total). Filling them converts existing
perturbed runs into analysis-eligible ones; **none of those perturbed traces needs
to be rerun.**

Five groups have **zero** valid clean runs and need all three repetitions
(covering 12 blocked LEP cells):
`code_review_swe_022_sympy_sympy_15349`/review_loop/standard (2 cells),
`code_review_swe_064_astropy_astropy_14539`/coordinator_workers/standard (4 cells),
`fin_finqa_058_mas_2012`/coordinator_workers/memory_enabled (1),
`fin_finqa_103_cme_2010`/review_loop/memory_enabled (1),
`fin_finqa_145_gs_2012`/coordinator_workers/standard (1), plus 3 further cells.

Only 48 of the 1,871 slots have no recorded frozen plan entry — all in shards
0–18, which ran an earlier planner build and never wrote `benchmark_plan.json`.
Their identities are reconstructed from the canonical planner and match exactly.

### B. Recoverable perturbed failures — 5,848 executions

All 5,848 have exactly one recorded cause, a backend error:

| Termination reason | Slots |
|---|---|
| `backend_http_error` | 4,427 |
| `backend_connection_error` | 1,421 |

No slot in this class has any other failure recorded, so none of them is a
scientific negative. 4,334 of them (74%) sit in the 40 zero-accept shards.

By task family: research_synthesis 2,935, financial_analysis 2,271, code_review 642.
By topology: coordinator_workers 2,433, branch_and_verify 2,288, review_loop 1,127.
By LEP and mode (each LEP appears once per mode):

| LEP | many_to_one | one_to_many | single_origin |
|---|---:|---:|---:|
| HANDOFF_CORRUPTION | 228 | 243 | 690 |
| INDIRECT_PROMPT_INJECTION | 230 | 247 | 697 |
| INPUT_DISREGARD | 229 | 245 | 692 |
| MEMORY_POISONING | 229 | 241 | 717 |
| TOOL_RESULT_CORRUPTION | 228 | 243 | 689 |

By execution variant: `standard` 4,661, `memory_enabled` 1,187.

### B2. Never-attempted executions — 93

Plan entries that were never dispatched because their shard hit the 2 h time
limit. All 93 are perturbed; all lie in shards 47, 61, 62, 65, 145, 155. Every
one is research_synthesis. These are missing execution coverage, not failed
science.

### C. Injection-target-unreachable — 276 slots, **no retry scheduled**

All 276 are the same cell: `LEP_MEMORY_POISONING` × `coordinator_workers` ×
`one_to_many` × `memory_enabled`, and all 276 record the identical
unfulfilled-origin status `boundary_not_reached`. Breakdown: research_synthesis
133, financial_analysis 100, code_review 43.

This is the documented static-opportunity/dynamic-path mismatch in
`docs/injection_reachability_investigation.md`: the coordinator's initial
one-to-many turn may hand off without ever writing shared memory, so there is no
eligible `MEMORY_WRITE` boundary. Of the 276, the same document reports the cell
is *conditionally* possible (12 of 71 historical attempts in that cell were
admitted), but acceptance depends on a model-selected path that cannot be forced
without changing the experiment.

**These are excluded from the rerun manifest.** Retrying them would (a) burn
GPU-hours on a population with a low and fixture-dependent acceptance rate, and
(b) if accepted selectively, change the measured population. If this cell must be
covered, it needs the documented redesign (a task whose natural upstream work
produces a shared-memory artifact, or the separate
`existing_record_poisoning_v1` protocol) — a scientific decision, not a rerun.

### C2. Existing-record-poisoning subtype — no production traces exist

`existing_record_poisoning_v1` is fully implemented, admission-validated and
frozen (`experiments/scifact_memory_v1/`, 10 fixtures, 40 planned attempts:
30 benign + 10 perturbed), but **no model-backed pilot has ever been launched**
(its README states this explicitly, and a recursive scan of
`analysis/result_x_contract/` finds zero traces carrying that
`memory_protocol`). It is therefore neither missing nor present — it is
unscheduled. Its scope is research_synthesis / coordinator_workers / one_to_many
only, so it is a *separate experimental population* and must not be merged with
the write-interception population (the analyzer raises on mixed protocols).

### D. Other non-recoverable — 69 slots, **no retry scheduled**

| Termination reason | Slots | Note |
|---|---:|---|
| `injection_count_mismatch` | 30 | 24 TRC + 6 IPI, all branch_and_verify/many_to_one; recorded as `partial_injection` with `mutation_not_applied` or `target_not_realized` |
| `max_events_reached` | 27 | 25 MEMORY_POISONING + 2 others |
| `protocol_violation` | 8 | scattered |
| ledger-only, no trace on disk | 4 | all in time-limit shards; `unverifiable_legacy` |

The 30 `injection_count_mismatch` cases include the three documented
source-rewrite no-ops (`fin_finqa_103_cme_2010`, `fin_finqa_021_disca_2017`,
`fin_finqa_039_amat_2012`) where a worker overwrites the source file with
already-altered figures. These are scientifically valid negatives or protocol
limits, not infrastructure faults. Retrying them would be re-rolling a dice
whose outcome is part of the measurement.

The 4 ledger-only slots are the exception worth noting: they are attempts that
started (recorded in `attempt_starts.jsonl`) but produced no trace because the
shard was killed. They are **execution coverage** gaps, but with no trace file
they cannot be validated as recoverable, and they are already counted in the 93
never-attempted class by shard. Flagged for the reviewer rather than silently
scheduled.

---

## 3. Rerun manifest

`planning_audit_oct19/rerun_manifest.json` — schema `agentprop-rerun-manifest-v1`.

7,812 executions, each carrying the full execution identity (scenario_id, task
family, fixture, topology, LEP, propagation mode, execution variant, injection
subtype where applicable, clean/perturbed designation, repetition index,
pair_tag, memory protocol), the reason it is being rerun, the dependent LEP codes
for clean top-ups, and the origin shard.

| Category | Rows | What it is |
|---|---:|---|
| `A_clean_reference_topup` | 1,871 | benign runs to reach 3 matched clean references |
| `B_recoverable_perturbed` | 5,848 | backend-failed perturbed slots |
| `B_never_attempted` | 93 | slots cut off by the Slurm time limit |

Excluded, reported but not scheduled: `excluded_unreachable` (276) and
`excluded_other` (69).

**Deduplication and safety checks, all enforced in code:**

- No duplicate `scenario_id` in the manifest (asserted).
- No manifest row corresponds to a trace already admitted on disk (asserted
  against every file in `production_run/node_*/traces/`).
- Manifest ∩ excluded_unreachable = 0; manifest ∩ excluded_other = 0;
  excluded_unreachable ∩ excluded_other = 0.
- Every identity is an exact entry from the canonical planner — no resampling,
  no substitution, no invented fixtures.

`planning_audit_oct19/rerun_plan_manifest.json` is the same selection expressed
as schema `agentprop-readiness-selection-v1`. It was verified to round-trip
through the repository's own strict loader (`benchmark/plan_manifest.py:load_plan_manifest`,
7,812 scenarios resolved), which independently confirms every identity still
matches current fixture manifests and planner rules.

**Missing execution coverage vs missing analysis eligibility.** 5,771 perturbed
traces are already admitted and scientifically valid; they are excluded from
propagation metrics *only* because their clean references are short. The plan
fixes that with 1,871 clean runs rather than 5,771 perturbed reruns. Only 32 of
the 5,848 recoverable perturbed slots also sit in a clean-deficit group, so the
two work streams are almost entirely disjoint and can be scheduled together.

---

## 4. Proposed Slurm execution plan

**Recommendation: one array of 100 tasks, 1 GPU each, reusing `production.sbatch`
unchanged except for the output root and the plan manifest.**

Sizing evidence from the existing run (`planning_audit_oct19/timing.py`):

- Per-scenario runtime: mean 24.2 s, p50 25.4 s, p90 46.8 s, p99 94.9 s.
- vLLM readiness wait: median 115 s, p90 190 s, max 475 s.
- Original 160-shard walltime: median 1.59 h, p90 2.02 h, max 2.25 h; 16 shards
  hit or passed the 2 h limit.

`split_merge.assign_node` distributes the 7,812 selections by `pair_tag`:

| Array size | Max node walltime (conservative) | Median | Nodes >2 h | Total GPU-hours |
|---|---:|---:|---:|---:|
| 60 | 2.23 h | 1.34 h | yes | ~79 |
| 80 | 2.38 h | 1.24 h | yes | ~79 |
| **100** | **1.43 h** | 0.84 h | **0** | **~80** |
| 120 | 1.53 h | 0.62 h | 0 | ~81 |

100 tasks is the smallest size that keeps every node under the 2 h limit with
headroom for the observed tail, while using ~80 GPU-hours. 60 or 80 tasks would
recreate the timeout pattern that caused the 9 lost shards and 155 lost
executions in the first place.

**Requested resources** (mirroring `production.sbatch`):
`--partition=ghx4 --nodes=1 --gpus-per-node=1 --cpus-per-task=32
--time=02:00:00 --array=0-99 --account=biha-dtai-gh`

**Command** (nothing submitted; this is the exact intended invocation):

```bash
OUTPUT_ROOT=rerun_production_oct19   # NEW isolated root; production_run/ untouched
sbatch --array=0-99 \
  --output=/u/kdhande/agent-graph/rerun-%A_%a.log \
  --error=/u/kdhande/agent-graph/rerun-%A_%a.err \
  planning_audit_oct19/rerun.sbatch
```

with `rerun.sbatch` identical to `production.sbatch` except:

```diff
-OUTPUT_ROOT="production_run"
+OUTPUT_ROOT="rerun_production_oct19"
 NUM_NODES=160            →  NUM_NODES=100
+  --plan-manifest planning_audit_oct19/rerun_plan_manifest.json \
```

`--plan-manifest` is the mechanism that makes this a delta run: the runner
executes exactly the 7,812 selected entries and nothing else. Note that
`benchmark/__main__.py` rejects combining `--plan-manifest` with
`--topologies/--task-families/--lep-codes/--propagation-modes`, so the matrix
filters must be dropped, and the saved settings must agree with any explicitly
passed flags.

**Two corrections applied during preflight (see `preflight_report.md`):**

1. `--run-node "$RUN_NODE" --num-nodes "$NUM_NODES"` are **required** alongside
   `--plan-manifest`. `load_plan_manifest` does not carry node partitioning
   (`benchmark/plan_manifest.py:89-93` only replaces settings and filters), and
   `benchmark_runner.py:502` only partitions when `num_nodes > 1`. Without them
   every task would have executed all 7,812 entries — 781,200 executions.
2. A backend-failure guard was added. `python -m benchmark` returns 0 even when
   every execution fails on the backend (`benchmark/__main__.py:325`), which is
   how 40 production shards silently lost 7,442 executions. The shard now counts
   `backend_` termination reasons and exits 1 on a majority.

### Risks and controls

| Risk | Control |
|---|---|
| **Overwriting production traces** | New `OUTPUT_ROOT`. The runner also refuses to start if `benchmark_plan.json` already exists with different content, so a re-run into the same root is safe but must be intentional. |
| **Port collision between concurrent tasks** | `production.sbatch` binds vLLM to `127.0.0.1:8001`. Each Slurm task gets its own network namespace on this cluster, as evidenced by 160 concurrent shards using port 8001 without conflict. If that changes, derive the port from `$SLURM_ARRAY_TASK_ID`. |
| **Database/file write collisions** | Each task writes only `node_<task>/`. No shared file is opened for writing. `attempt_starts.jsonl` is per-shard. |
| **Same identity scheduled twice** | Manifest uniqueness is asserted; `attempt_id` is a fresh UUID per execution, so even an accidental re-dispatch produces distinct files rather than silent overwrites. |
| **Protocol pooling** | All 7,812 rows are `write_interception_v1`. The analyzer raises if it ever sees a mixed-protocol directory, so the ERP population can never leak into this run. |
| **Timeout recurrence** | 100 tasks keeps max walltime at ~1.43 h conservative. The 40 zero-accept shards show the failure is backend stability, not per-task duration. |
| **Selective acceptance of the unreachable cell** | Not scheduled at all. No retry quota, no acceptance filter. |

### Merging reruns into analysis without touching production

1. Leave `production_run/` read-only. Analyze `rerun_production_oct19/`
   independently first (`PropagationAnalyzer(rerun_production_oct19)`).
2. To analyze the combined dataset, either point one analyzer at a merged
   directory built with `python -m benchmark --merge`, or run the analyzer
   twice. `split_merge.merge_outputs` already refuses on conflicting duplicate
   trace files and refuses on conflicting frozen plan slots, so an accidental
   identity clash fails loudly rather than silently.
3. The 19 shards that never wrote a frozen plan (0–18) mean a whole-directory
   merge will emit "No complete frozen plan; plan coverage is unavailable" and
   lose plan-based coverage denominators. Prefer merging only the 141 shards
   that have plans, or reconstruct plans for 0–18 first.
4. `existing_record_poisoning_v1` output, if ever produced, must live in its own
   directory tree. Never co-locate it with this population.

---

## 5. Validation plan

After the reruns (not before — these are the acceptance checks):

1. **Identity check.** `rerun_production_oct19` contains exactly the 7,812
   scenario_ids in the manifest, no more. Re-run
   `planning_audit_oct19/inventory.py` against the combined tree and confirm
   `distinct_attempts` grew by the admitted subset with zero conflicting
   duplicates.
2. **Clean-reference closure.** Re-run the clean-deficit computation. Target:
   `clean_deficit_cells` drops from 5,771 toward 0. Because clean runs are
   stochastic, some of the 1,871 will themselves be rejected
   (`max_events_reached`, `protocol_violation`); the audit does not assume 100%
   yield. Expected residual deficit is reported, not hidden.
3. **Analysis eligibility.** Admitted-and-analyzable perturbed traces should rise
   from 4,574 toward ~10,400. Re-run `PropagationAnalyzer` and compare
   `total_lep_runs`.
4. **Unreachable cell unchanged.** The 276 `injection_target_unreachable` slots
   must still be excluded. Confirm no new accepted trace appeared in that cell
   from this run (there should be none, since none was scheduled).
5. **No production mutation.** `git status --short production_run/` and a
   checksum of the tree must be unchanged from the pre-run snapshot.
6. **Rejection accounting preserved.** `attempt_accounting.summarize_attempts`
   must still show the original rejection reasons. Underfilled cells are
   reported, never dropped from the denominator.

---

## 6. What could not be determined confidently

- **Why the 40 shards saw a sustained `backend_http_error` outage.** The records
  show ~0 s runtime per failed call and no trace-level error text; the vLLM logs
  for those shards are 0 bytes. The outage window (2026-10-11) is inferable from
  directory mtimes but the server-side cause is not in these records.
- **Whether the 761 `unverifiable_legacy` perturbed traces are recoverable.**
  They are `dataset_eligible=True` at runtime but lack the mutation evidence the
  current admission validator requires (all are IPI and TRC, i.e. the two
  tool-result LEPs). Whether this is an evidence-capture regression or a genuine
  absence of a real mutation cannot be settled from the traces alone, so they
  are reported as a distinct class and not scheduled.
- **Per-fixture acceptance probability for the unreachable cell.** The historical
  12/71 figure is descriptive only and cannot support a retry estimate.
- **Shards 0–18 planned entries.** They never wrote `benchmark_plan.json`; their
  planned slots are reconstructed from the canonical planner. The reconstruction
  is exact for the 3,461 unrecorded slots, but the original shard-level split
  for those shards is not recoverable from disk.
- **Result X live validation** (`analysis/result_x_contract/live_validation_07`):
  4 of 7 pairs completed, 3 failed with `ContractError`. This is a separate
  opt-in protocol with its own artifact layout and is not part of the production
  population being audited here.
