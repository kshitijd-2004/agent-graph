# AgentProp Production Regeneration Manifest v1 — Audit Report

Date: 2026-11-10. **This is a read-only audit + manifest for human review. No jobs were
submitted; no traces were regenerated; no Slurm job was launched.** The manifest is
executable by the existing benchmark CLI only after explicit human approval.

## 1. Scope and hard constraints honored

- Only `standard` executions are in scope. **Memory Poisoning and every
  `memory_enabled` execution variant (including benign controls) are permanently
  excluded.**
- Frozen consolidated dataset (`analysis/full_production_consolidated/`), frozen BND
  V4.1 outputs, and all prior production/pilot traces are untouched.
- No `sbatch`/`srun`/`salloc`, no vLLM startup, no inference, no trace regeneration.

## 2. Methodology

The intended execution matrix was rebuilt from the **actual production planning
logic** (`planning_audit_oct19/canonical_plan.json`, schema `agentprop-frozen-plan-v1`,
30,229 entries) filtered to the retained standard-only population:

- 3 topologies (`review_loop`, `branch_and_verify`, `coordinator_workers`),
- 3 task families (`code_review`, `financial_analysis`, `research_synthesis`),
- 4 retained LEPs (`TOOL_RESULT_CORRUPTION`, `INDIRECT_PROMPT_INJECTION`,
  `HANDOFF_CORRUPTION`, `INPUT_DISREGARD`); `MEMORY_POISONING` dropped,
- `execution_variant = standard` only; `memory_enabled` dropped.

The canonical plan = **20,387 intended standard scenarios** + 9,842 excluded
`memory_enabled`/`MEMORY_POISONING` rows. The 20,387 authoritative standard matrix
was used as the population (NOT the frozen `agentprop-frozen-plan-v1` 18,005
readiness-subset, and NOT "inferred from observed traces").

Status of each intended scenario was joined against:

- `/tmp/accepted_index.json` (26,975 scenario-id lists of attempts,
  `production_run` + `rerun_production_oct19`), and
- `/tmp/rejected_index.json` (10,557 scenario-id lists of attempts).

"Has a valid accepted execution" = an attempt with `kind=accepted`,
`termination=completed`, and `eligible=true` in the accepted index.

## 3. The authoritative population and the "scheduled" reconciliation

| Quantity | Count | Note |
|---|---:|---|
| Canonical plan (frozen, full incl. memory) | 30,229 | `canonical_plan.json` |
| Intended standard matrix | **20,387** | retained LEPs, standard only |
| Frozen `agentprop-frozen-plan-v1` readiness subset | 18,005 | 20,387 − 2,382 |
| Intended-standard **never scheduled** | 2,382 | of which 2,255 *already* have eligible accepted traces (they ran in original production outside the readiness cut) |
| Intended-standard **lacking a valid accepted execution** | **1,322** | 1,176 benign + 146 attacked |

The 1,322 figure, not the frozen-plan 18,005, drives the regeneration population.

## 4. Regeneration categories (deduplicated to 5,818 executions)

Deduplication uses the canonical identity
`(fixture_id, topology, execution_variant, lep_code, propagation_mode,
repetition_index, experimental_config)`. No fixture appears in two categories; a
single benign run serves every compatible LEP comparison; rejected attempts that
have an accepted replacement are excluded; accepted valid O2M attacks are preserved.

| Category | Description | Executions |
|---|---|---:|
| **A** | `coordinator_workers` benign — all historical CW benign ran worker-first; regenerate coordinator-first. **Not multiplied by LEP or mode** (3 reps × 703 fixtures). These same coordinator-first benigns are the controls that restore O2M comparability (mode-free REPORT §9, case C). | 2,109 |
| **B** | `coordinator_workers × single_origin` LEP — historical worker-first; regenerate coordinator-first. | 2,812 |
| **B-O2M** | `coordinator_workers × one_to_many` LEP **missing** an accepted execution (the accepted, coordinator-first O2M attacks are *preserved*, not regenerated). | 16 |
| **C** | `review_loop` fixtures with < 3 valid benign references — regenerate only the missing repetition. | 370 |
| **D** | `branch_and_verify` fixtures with < 3 valid benign references (serves both SO and M2O; deduplicated with any SO need) — regenerate only the missing repetitions. | 395 |
| **E** | non-CW attacked LEPs lacking an accepted valid execution (both original production and Oct-19 recovery). The 40 M2O TRC cases include 34 `injection_count_mismatch` rejections (partial-injection bug family) plus backend errors. | 116 |
| **Total** | | **5,818** |

### Category F — remaining structural mismatches (investigated, NOT auto-regenerated)

The prior BND audit's "71 structurally incompatible SO LEP traces" corresponds to the
`execution_structure_mismatch` rows in the frozen V4.1 census
(`analysis/full_production_baseline_deviation_v41/bnd_v41_per_trace.jsonl`):
**73 single-origin rows, all `coordinator_workers`, all a single CW-SO scenario-id
family, LEP = INDIRECT_PROMPT_INJECTION** (frozen census count 73; the "71" figure is
the earlier manual subset). These are the worker-first CW-SO LEP traces the scheduler
correction (commit 860c820) fixes. **Category B's forced coordinator-first CW-SO
regeneration already supersedes them**, so no separate Category F execution is added.
The scheduler correction is the verified root-cause explanation; the attacked or
benign reference is not independently defective. The mode-free V4.1 census already
reports 0 `execution_structure_mismatch` rows because the corrected structure is
recognized there.

### 5. Consistency cross-checks (all internal totals reconcile)

- Attacked-lacking total = **146** = 116 (E, non-CW) + 16 (CW O2M-missing) +
  14 (CW SO-missing, subsumed in B).
- Benign-lacking total = **1,176** = 370 (C) + 395 (D) + 411 (CW benign lacking an
  eligible accepted trace; the full CW benign population A = 2,109 is regenerated
  regardless of individual status, since the worker-first protocol itself is
  invalid for the corrected population).
- The injection-mismatch audit's `historical.json` total = **116** exactly matches
  Category E.
- O2M accepted preserved = **2,796** (matches mode-free census LEP-O2M count); O2M
  missing = 16.

## 6. Manifest file

- `regeneration_manifest.json` — schema `agentprop-readiness-selection-v1`,
  5,818 scenarios. **Validated read-only** against the current working-tree
  `build_plan()` via `benchmark.plan_manifest.load_plan_manifest` (see
  `validation_report.json`). Loader accepted all 5,818 with exact field agreement
  and settings round-trip.
- `regeneration_inventory.csv` — one row per unique execution with category,
  reason, historical status, executability.
- `regeneration_summary.json` — machine-readable totals.

## 7. Slurm shard estimate (Phase 6)

Original production reference: 160 array tasks; observed shard walltime
(`prod-3291540_*.log`): median **1.59 h**, p90 **2.02 h**, max **2.25 h**,
16 of 160 shards at/over the 2 h limit. Per-scenario runtime: median ~25 s, p90
~47 s (code_review ~34 s, financial ~27 s, research ~17 s medians).

For 5,818 scenarios, using the original per-shard throughput (original 160 shards
≈ original ~18,005 standard scenarios ⇒ ≈ 112 scenarios/shard, ~1.6 h/shard):

- **≈ 52–53 shards of ~112 scenarios** keeps the 2 h walltime (p75 ≈ 1.84 h).
- Recommend **64 shards of ~91 scenarios** to leave headroom for the 2 h cap,
  stragglers, and a retry batch, or **48 shards** if accepting the ~2.0 h p90.

These are **estimates**, not guarantees (unequal family durations and model-startup
overhead per shard are the dominant variance drivers). No job was submitted.

## 8. Assumptions and limitations requiring human review

1. **CW benign represented by `propagation_mode=single_origin`.** The planner labels
   all CW benign scenarios `single_origin` (there is no injected LEP). The task asks
   for the compatible representation without treating the label as a scientific match
   requirement; the canonical planner emits `single_origin`, so the manifest uses it.
   The **O2M-matched-CW-benign change in uncommitted `benchmark/benchmark_runner.py`
   is deliberately NOT adopted** — it would multiply the benign population by mode,
   violating Category A's "not multiplied by mode" rule. If the human reviewer instead
   wants mode-matched O2M benigns, that is a separate, larger generation task, not a
   CLI-subset selection.
2. **`injection_count_mismatch` M2O TRC (34 of the 40 Cat-E M2O TRC).** The historical
   partial-injection delivery bug is reported as repaired by
   `injection_mismatch_audit/IMPLEMENTATION_REPORT.md`. Those 40 are scheduled as
   **executable** but flagged as the highest-risk slice; if the repair is not yet in
   the frozen scheduler commit, they should be re-verified before submission.
3. **2,255 never-scheduled-but-accepted** standard scenarios are intentionally
   *not* regenerated — they already have a valid accepted execution and belong to the
   intended matrix even though they fell outside the frozen readiness cut.
4. Slurm shard counts are estimates (see §7).

## 9. Validation result

See `validation_report.json`. All 15 Phase-7 checks are addressed there.
