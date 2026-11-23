# SWE-bench Verified 101–300 Addition — Launch Kit

**Status: READY — NOT LAUNCHED.** This directory contains everything needed to
run all **200** SWE-bench Verified fixtures (IDs 101–300) through the
production benchmark runner. Nothing in this directory has been submitted to
Slurm and no inference has been run.

## Population

- Cohort: SWE-bench Verified fixtures `code_review_swe_101…300` (200 distinct
  instance IDs, `task_variant: swe_bench_verified`), sourced from
  `workspace_fixtures_v3/`.
- Fixtures 1–100 are **excluded** from this manifest; their historical traces
  remain on the main-root `workspace_fixtures` manifests (5,818-execution
  regeneration manifest in `analysis/production_regeneration_manifest_v1/` —
  untouched).
- 50 of the 200 fixtures' answer-key defect is a **false positive**, not a
  real grounding error: their `issue.function` holds either the bare module
  basename (an authoring artifact, never a real identifier) or a specific
  name grounded by the issue's own `patch-concepts-v1` grading rubric. The
  grader reads `issue.keywords` / `issue.grading`, never `issue.function`, so
  these are grading-safe. Under the narrowed `fixture-contract-v1` gate they
  are now valid.
- 2 of the 200 fixtures (`code_review_swe_129_django_django_15863` and
  `code_review_swe_214_django_django_15930`) had been **excluded** on the
  genuine 8k-token task-context gate (≈ 8,011 and ≈ 8,558 tokens). Both have
  now been recovered by minimally trimming the materialized context so the
  fixtures fall back under the gate (≈ 2,677 and ≈ 2,722 tokens respectively),
  with gold patches, grading rubrics, issue keywords, required issues, LEP
  payloads, and expected answers left untouched. Fixture 214's trim is a
  **documented deviation**: instead of the five-symbol plan it originally
  carried (which still included `Func.as_sql` / `RawSQL.as_sql` and stayed at
  ~10 KB, over the gate), the context was reduced to the three-symbol minimal
  set `Value.as_sql`, `When.as_sql`, and `Case.as_sql` — enough to understand
  the issue, locate the changes, and evaluate the response. See
  `fixture_compatibility_report.md` for the full defect table and per-fixture
  reasons.

The manifest is therefore scoped to all **200** valid fixtures (zero
exclusions). The re-issue is deterministic and re-runnable: `build_manifest.py`
preserves the 148 originally-scheduled entries byte-stable
(`run-000001`…`run-04292`), appends the 50 recovered answer-key fixtures
(`run-04293`…`run-05742`), and finally appends the two recovered size-gated
fixtures 129 and 214 (`run-05743`…`run-05800`). Re-running it is an
idempotent no-op.

## Exact execution counts (200 fixtures × 29 cells = 5,800)

| dimension | count |
|---|---|
| total executions | **5,800** |
| benign (3 reps × 3 topologies, standard variant, single_origin) | **1,800** (9 per fixture) |
| LEP total | **4,000** |
| — single_origin LEP (all 3 topologies × 4 LEPs × 1 rep) | 2,400 |
| — many_to_one LEP (branch_and_verify only) | 800 |
| — one_to_many LEP (coordinator_workers only) | 800 |
| per retained LEP (Tool Result Corruption / Indirect Prompt Injection / Handoff Corruption / Input Disregard) | 1,000 each |
| by topology: review_loop / branch_and_verify / coordinator_workers | 1,400 / 2,200 / 2,200 |
| by mode: single_origin / many_to_one / one_to_many | 4,200 / 800 / 800 |

Memory Poisoning and every `memory_enabled` execution are excluded
permanently. The O2M-matched CW benign diff in `benchmark/benchmark_runner.py`
is **not** adopted; the manifest carries the committed 29-cell semantics
(coordinator-first scheduler fix 860c820 is what makes CW schedule
coordinator-first in all modes).

## Deliverables in this directory

| file | purpose |
|---|---|
| `swebench_200_addition_manifest.json` | Strict-schema execution manifest (5,800 entries, `run-000001`…`run-05800`, 14 fields/entry, v3 `fixture_manifest_sha256` map of 200 fixtures, all settings frozen from production) |
| `build_manifest.py` | Deterministic, idempotent builder that re-derives the 200-fixture / 5,800-entry manifest from the relaxed gate; re-running is a byte-stable no-op once all 50 recovered answer-key fixtures plus the 2 recovered size-gated fixtures are present |
| `validation_report.json` | Machine-readable §5 checklist — all 12 items PASS |
| `fixture_compatibility_report.md` | 4-LEP fixture audit under the narrowed gate, with full defect tables and zero mock-delivery failures (200 × 12 cells) |
| `bnd_cohort_compatibility_report.md` | BND rule/reference-level compatibility between cohort A (1–100) and cohort B (101–300) |
| `provenance.json` | Cohort mapping: fixture root, hash map, scheduled/excluded fixture + instance IDs |
| `launch_slurm.sh` / `slurm/swebench200_node{0..3}.slurm` | Ready-to-use 4-node launch (NOT executed) |
| `merge_slurm.sh` | Post-run merge step |

## Launch (user action — do not run from agent sessions)

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
for i in 0 1 2 3; do
    sbatch analysis/swebench_200_addition/slurm/swebench200_node${i}.slurm
done
# after all jobs finish:
./analysis/swebench_200_addition/merge_slurm.sh
```

Single-node variant: edit one `.slurm` to drop `--run-node/--num-nodes`
(`--run-node` requires `--num-nodes >= 2`). Outputs land in
`production_runs/swebench_200_addition/node_<i>/`; the merge writes
`production_runs/swebench_200_addition/merged/`.

> **Path-convention note:** the launch scripts write to
> `production_runs/` (plural), as specified in the task. The existing
> production traces live under `production_run/` (singular). These are kept
> separate on purpose; unify later if desired.

## Slurm headers

The four `slurm/swebench200_node{0..3}.slurm` files use the established
account/partition conventions from `benchmark_sbatch/chunk*.slurm`:
account `biha-dtai-gh`, partition `ghx4`, 1 node / 1 GPU / 8 cpus / 32g /
`02:00:00`. If your quota requires `ghx4-interactive`, override the partition
at submit time (`sbatch --partition ghx4-interactive ...`) rather than editing
the files. Each node runs vLLM on **port 8001** — matching the frozen
manifest setting `vllm_url: http://127.0.0.1:8001/v1` — while the existing
`chunk*.slurm` files use port 8000; do not mix the two.

## BND comparability caveats (read before interpreting results)

1. Cohort B (101–300) has **zero frozen BND references**; its 1,800 benign
   executions are what create its reference pool (BND V4.1 requires ≥ 3
   benign references). Verdicts for this cohort are only producible *after*
   the run completes.
2. O2M `coordinator_workers` traces are structurally non-comparable under
   V4.1 until coordinator-first CW benign controls exist (V4.1 mode-free
   REPORT, 2,796 prior O2M traces with 0 compatible refs). The committed
   860c820 scheduler fix makes the new SO CW benigns coordinator-first, so
   they will satisfy the arbiter once in the pool — the unadopted O2M-matched
   CW benign diff is deliberately not part of this launch.
3. Grading representation differs between cohorts (keyword `required_issues`
   in 1–100 vs `patch-concepts-v1` blocks in v3 fixtures) → cross-cohort
   BND score comparisons are not valid; compare within cohort.
4. Four residual cross-fixture grading collisions (fixtures 178, 150, 38, 176)
   remain from the `swebench_300_repair` notes; they don't block execution.

## Resolved blocker (previously carried to the user)

**All 200 fixtures are now scheduled** — zero exclusions. The two that were
previously held out on the genuine 8k-token task-context gate
(`code_review_swe_129_django_django_15863` ≈ 8,011 tokens;
`code_review_swe_214_django_django_15930` ≈ 8,558 tokens) have been
recovered by minimally trimming their materialized context back under the gate
(≈ 2,677 and ≈ 2,722 tokens respectively); no gold patch, grading rubric,
issue keyword, required issue, LEP payload, or expected answer was modified,
and fixture 214's trim is the documented three-symbol `Value.as_sql` /
`When.as_sql` / `Case.as_sql` decision rather than its original five-symbol
plan. The 50 fixtures that were previously
held out for "ungrounded issue-function answer keys" are false positives
(module basename / grading-anchored specific names) and were recovered
under the narrowed validator gate — no grading criteria were altered. Downstream
ID-TRC target selection and BND issue anchors were guarded so the module
basenames are not mistaken for function identifiers. No invalid scenarios are
included.
