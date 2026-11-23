# Coordinator-Workers Production Regeneration — Batch Preparation Report

Date: 2026-11-10. **Preparation only: no jobs submitted, no vLLM started, no
inference run, and `analysis/production_regeneration_manifest_v1/` is unmodified.**

## 1. What was produced

All artifacts are in `analysis/coordinator_workers_regeneration/`:

| File | Purpose |
|---|---|
| `filtered_manifest.json` | Execution manifest: **4,937 coordinator_workers executions**, `agentprop-readiness-selection-v1` schema, field-compatible with `benchmark.plan_manifest.load_plan_manifest` |
| `manifest_provenance.json` | Source provenance + per-fixture SHA-256 resolution for all 703 fixtures |
| `execution_counts.json` | Exact counts by category, mode, LEP, task family |
| `validation_report.json` | Per-requirement validation evidence (all PASS) |
| `coordinator_workers_regeneration.sbatch` | Slurm submission script (64-shard array), **not submitted** |
| `verify_coverage.sh` | Post-run coverage verification (run after the batch) |
| `build_filtered_manifest.py` | Deterministic regeneration of the filtered manifest |

Traces will land in `production_run/coordinator_workers_regeneration/node_<shard>/`
— separate from historical `production_run/node_*` and any SWE-200 addition.

## 2. Execution population (verified)

```
total:                        4,937
  coordinator_workers benign          2,109   (703 fixtures × 3 benign reps)
  coordinator_workers SO LEP         2,812
  coordinator_workers O2M LEP           16
  task families: code_review 716, financial_analysis 2114, research_synthesis 2107
  LEPs: HANDOFF_CORRUPTION 709, TRC 707, IPI 706, INPUT_DISREGARD 706
  modes: single_origin 4,921 (incl. all 2,109 benign), one_to_many 16
  fixtures: 703/703, all resolving to recorded SHA-256s
```

## 3. Requirements status

| # | Requirement | Status |
|---|---|---|
| 1 | Identities, fixture hashes, repetition assignments preserved | PASS — field-by-field byte comparison vs. source; `fixture_manifest_sha256` map copied verbatim |
| 2 | Every fixture resolves to its recorded manifest | PASS — 703/703 SHA-256 match against `workspace_fixtures/<fx>/manifest.json` |
| 3 | Corrected coordinator-first scheduler for all CW executions | PASS — fix commit 860c820 live in tree (`generation/runner.py:1187` `topology.is_coordinator_first()` bypass); scheduler regression tests 14/14 |
| 4 | No Memory Poisoning / memory_enabled | PASS — asserted absent in selection; source summary records 0 |
| 5 | No mode-specific benign controls | PASS — all 2,109 benign entries keep planner-labeled `single_origin`; no O2M-matched benigns added |
| 6 | No duplicate executions | PASS — canonical identity, run_id, scenario_id all unique |
| 7 | Runner executes the filtered manifest without expanding it | PASS — `load_plan_manifest` resolved all 4,937 with exact field agreement; `runner.run(plan=…)` executes `len(selected)=4,937`; 64-shard `split_plan` unions back to 4,937 |
| 8 | Runner code matches pilot-validated scheduler | PASS — pilot traces (job 3576184, 21:51–21:53) postdate 860c820 (20:38:56); no scheduler-affecting commit since; all 3 pilot traces start coordinator-first |

## 4. SWE 200 exclusion — important finding

The 703-fixture population contains **only `code_review_swe_001` … `swe_100`**.
SWE fixtures 101–300 exist solely in `workspace_fixtures_v3/` (a later addition)
and are **not part of this population** — the exclusion is vacuously satisfied,
with zero CW executions touching a fixture numbered 101–300.

> Note for reviewers: a naïve regex `swe_(101..300)_` matches
> `code_review_swe_100_…` as a prefix artifact (the "100" then "_…" tail).
> That fixture is `swe_100` — in-range and **kept**, not excluded.

## 5. Slurm plan

- 64 shards via `--array=0-63`, partition `ghx4` (2 h walltime), one vLLM
  instance per shard on port 8001 (same as `production.sbatch`).
- Sharding = `md5(pair_tag)[:8] % 64` — the exact `split_merge.split_plan`
  partitioning, so `--run-node/--num-nodes` reproduce it deterministically.
  Offline-validated: union of 64 shards = 4,937; shard sizes 46–108
  (worst shard ≈ 5,290 s ≈ 0.96 h, well under the 2 h cap given pilot-observed
  ~25 s median execution time).
- `--run-node "$SLURM_ARRAY_TASK_ID" --num-nodes 64` is passed explicitly
  (the known silent 64× overrun failure mode is guarded against, and so is the
  exit-0-on-backend-outage mode: a majority-backend-failure shard guard fails
  the shard; `verify_coverage.sh` rechecks both after the run).
- Pre-flight in the sbatch asserts the manifest is exactly the validated 4,937
  CW-only set and that the coordinator-first fix is live in
  `generation/runner.py` before starting vLLM.

## 6. Launching

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
sbatch analysis/coordinator_workers_regeneration/coordinator_workers_regeneration.sbatch
# after completion:
bash analysis/coordinator_workers_regeneration/verify_coverage.sh
```

The uncommitted O2M-matched-benign diff in `benchmark/benchmark_runner.py` was
left as-is and **is not required by this batch** — the frozen selection
reconciles against `build_plan()` with or without it (every one of the 4,937
scenario_ids resolves with exact field agreement under both states).
