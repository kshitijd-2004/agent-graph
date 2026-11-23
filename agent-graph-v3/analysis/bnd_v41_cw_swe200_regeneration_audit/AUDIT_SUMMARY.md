# BND V4.1 Comparability Recovery — Audit & Regeneration Plan

**Scope.** Read-only audit of the 7,783 incomparable executions in the
provisional consolidated + CW-regen + SWE-200 BND V4.1 run
(`analysis/bnd_v41_cw_swe200_provisional/`), plus the minimal benign
regeneration batch that recovers the maximum possible comparability
without touching any frozen artifact.

**Fidelity.** Every number below was recomputed by re-importing the frozen
functions (`discover`, `select_compatible_references`, `execution_shape`,
`trace_cell` — V1 module SHA `57014560…`, identical in live tree and
frozen snapshot) over the exact staged population the frozen run used.
The recomputation reproduces the frozen run exactly:
**comparable = 10,057 / incomparable = 7,783 / 17,840 measured** — no
divergence.

## 1. Resolved source paths (from `run_manifest.json`, verified on disk)

| Source | Resolved absolute path | Notes |
|---|---|---|
| Consolidated production | `/u/kdhande/agent-graph/agent-graph-v3/analysis/full_production_consolidated/source/` | `traces/` = 26,975 symlinks → `production_run/node_*` + `rerun_production_oct19/node_*`; `rejected_traces/` exists (10,686 files) but the frozen pipeline never reads it |
| CW regeneration | `/u/kdhande/agent-graph/agent-graph-v3/production_run/coordinator_workers_regeneration/` | 64 nodes; 4,845 files (4,802 admitted standard staged, 43 rejected) |
| SWE-200 | `/u/kdhande/agent-graph/agent-graph-v3/production_runs/swebench_200_addition/` | 120+ nodes; 5,714 files (5,661 admitted staged, 53 rejected) |
| Merged staged population | `/u/kdhande/agent-graph/agent-graph-v3/analysis/bnd_v41_cw_swe200_provisional/staged/merged/traces/` | 25,159 symlinks; what the frozen analyzer actually read |
| Benign pools (merged) | same staged dir, condition=benign files, keyed by frozen `discover()` on `(fixture, topology, execution_variant)` | 2,649 benign cells, 7,319 kept files |
| Spec root | `/u/kdhande/agent-graph/agent-graph-v3/analysis/bnd_v41_cw_swe200_provisional/specroot/` | 903 fixtures, hash-verified against both generation manifests |
| V4 ledger | `/u/kdhande/agent-graph/agent-graph-v3/analysis/full_production_baseline_deviation_v4/` | frozen join only |
| ID-TRC v2 ledger | `/u/kdhande/agent-graph/agent-graph-v3/analysis/full_production_id_trc_v2/` | frozen join only |

Frozen SHAs verified: live `benchmark/baseline_deviation.py` = `57014560`
(matches manifest v1_module_sha256). The live `benchmark/baseline_deviation_v41.py`
is at `75f1e77f`, i.e. **the working tree has drifted from the frozen
`6f0f7ff3`** (an uncommitted cosmetic change to issue-anchor handling in
the code-review feature extractor; does NOT affect matching/gating).
All audits here use the frozen matching logic (V1 module + frozen
`select_compatible_references`), which is byte-identical to what the
frozen run used. If a post-regeneration BND V4.1 analysis is rerun, it
should run from the frozen snapshot
(`analysis/bnd_v41_cw_swe200_provisional/frozen_run/`), or the working
tree must be reconciled to `6f0f7ff3` first — flagged, not acted on.

## 2. Root-cause breakdown of all 7,783 incomparable executions

Frozen matching chain (unchanged): a LEP is comparable iff the
`select_compatible_references` pool — keyed on
`(fixture_id, topology, execution_variant)` with propagation mode
deliberately **not** an axis — yields ≥3 benign candidates that
(a) match all HARD identity fields and (b) share the LEP's **exact**
recorded `execution_shape` (the structural arbiter).

Per topology × mode (exact recount, sums to 7,783):

| topology | mode | incomparable | dominant root cause |
|---|---|---:|---|
| coordinator_workers | one_to_many | 2,559 | ≥3 **canonical FANOUT** benigns missing in 493/642 cells (pools are mixed-shape: 587 of 902 CW cells have ≥2 recorded skeletons across reps; discover's first-3-by-filename rule can keep a RELAY rep in the 3-ref slot) |
| coordinator_workers | single_origin | 2,435 | FANOUT deficit (1,288 LEPs, recoverable) + 1,147 LEPs that record the **pre-fix RELAY skeleton** (unrecoverable by any new benign; includes 8 edge LEPs in cells `res_scifact_065`/`res_scifact_068` where the pool holds 3 RELAY members but the exact-shape arbiter selects <3) |
| branch_and_verify | many_to_one | 933 | canonical serial 2-root pools with <3 reps (never 3 reps generated, or dedup excluded 2 of them) |
| branch_and_verify | single_origin | 939 | same |
| review_loop | single_origin | 917 | canonical serial single-root pools with <3 reps |
| coordinator_workers | (edge) | 8 | pool has ≥3 RELAY members but LEP is the worker-first RELAY variant (or vice-versa); no new benign records these pre-fix skeletons |

Missing-reference census (frozen `reasons`): `:0` = 558, `:1` = 2,697,
`:2` = 4,528 — matches the provisional analysis exactly.

### Classification of the missing benign references (5 categories)

1. **Benign executions never generated** — bv/rl pools with 0–2 reps
   (~447 LEPs at pool_size 0/1 in bv/rl; the 558 zero-ref LEPs overall
   minus the 41 CW empty-pool cells). Regenerable.
2. **Generated but structurally incompatible** — the 587 mixed-shape CW
   benign cells: RELAY (pre-fix) reps sit in the same pool as FANOUT
   reps; the arbiter rejects cross-shape matches. The *FANOUT* reps are
   kept but there are too few of them; a fresh FANOUT rep is the fix.
   Regenerable (FANOUT side only).
3. **Rejected by admission** — CW 43 + SWE-200 53 rejected files;
   rejected benigns never enter pools. The 41 empty-pool CW cells and
   several bv/rl zero-pool cells trace to this. Regenerable.
4. **Excluded by deduplication** — staging supersession
   (cw_regen > swe_200 > consolidated) dropped 4,369 duplicate attempts;
   where both a superseded source and the winner hold a rep, only the
   winner's rep counts. This shrinks some pools below 3.
   Regenerable via fresh-rep additions (never re-staging).
5. **Other — pre-fix RELAY LEP skeleton** — 1,287 CW single-origin LEPs
   (cw_regen 864 + swe_200 322, incl. both relay variants) record a
   worker-relay DAG that the current generation code (commit `93985e5`)
   does not produce. **No newly generated benign can match these; they
   are unrecoverable by benign regeneration.** Recovery would require
   re-executing the LEP traces themselves under post-fix code — a
   population-level decision outside this batch.

### The 587 mixed-shape cells do NOT explain all 7,783

Confirmed: mixed-shape CW cells account for the CW one_to_many (2,559)
+ CW single-origin FANOUT (1,288) demand, but the remaining
7,783 − 3,847 = **3,936 incomparables are bv/rl missing-rep pools**
(2,789) + **1,147 pre-fix RELAY CW single-origin LEPs** (of 1,287 total
RELAY-skeleton CW SO LEPs, 140 are already comparable; every incomparable
one is unrecoverable). The bv/rl cells are NOT
mixed-shape; their pools are homogeneous serial skeletons that simply
have <3 reps.

### Injection-origin discrepancy: CW single-origin — actual mismatch, not intentional variation

Exact origin×shape cross-tab (admitted CW single-origin LEPs):

| tree | coordinator-origin | shape | n |
|---|---:|---|---:|
| cw_regen | 783 | RELAY | 783 |
| cw_regen | 1,928 (worker) | FANOUT | 1,847 |
| cw_regen | 1,928 (worker) | RELAY | 81 |
| swe_200 | 421 | FANOUT | 99 |
| swe_200 | 421 | RELAY | 322 |
| swe_200 | 338 (worker) | FANOUT | 338 |

The origin role is **fully correlated with the recorded skeleton**, and
the skeleton is determined by the **generation code version** in force
at trace-creation time, not by the LEP's target configuration:

- Pre-`860c820`/`93985e5` CW code recorded the **worker-relay** DAG;
  an unbound single-origin LEP then fired at the first eligible
  boundary = the first *worker* (specialist_a).
- Post-fix code records the canonical **coordinator-first FANOUT** DAG;
  the first eligible boundary is now the *coordinator's* tool-result
  read, so unbound LEPs fire there.
- The 421 swe_200 coordinator-origin LEPs are the shifted-variant
  (post-fix code, coordinator-origin) mixed with 338 worker-origin
  (pre-fix) in the same tree — i.e. the two trees were generated by
  **different code generations**, not by two intended origin
  variants. All target configs are `topology_target=None` (unbound);
  no fixture was ever run with an explicit worker target.

**Verdict: experimental version skew, not intentional variation.**
Implications for this batch:
- Coordinator-origin FANOUT LEPs (421 in swe_200; the O2M ones
  intentionally fire at the coordinator by canonical O2M semantics) are
  structurally fine — they match FANOUT benigns, which is exactly what
  the regeneration provides.
- The 3,405 RELAY-variant LEPs (coordinator-origin 783+322+81 and
  worker-origin 81+322 in cw_regen/swe_200 RELAY rows) are part of the
  1,287 unrecoverable population: the current code cannot generate
  RELAY-skeleton benigns. They must be re-executed under post-fix code
  (population decision, out of scope here) or accepted as
  permanently incomparable under the frozen arbiter.
- Do **not** treat the two origin classes as equivalent; the provisional
  analysis already flags them, and this audit confirms the flag is a
  version-skew artifact.

## 3. Regeneration manifest (minimal, frozen-matching-faithful)

Artifacts in this directory:

- `regen_manifest.jsonl` — 1,108 unique regen configurations (one per
  cell × generable exact shape), each with exact regen config,
  existing compatible refs, missing count, and LEPs served.
- `incomparable_leps.jsonl` — all 7,783 incomparable LEP rows with
  source path, exact matching key, existing refs, missing count,
  reason, and per-LEP regen config.
- `unrecoverable_rows.jsonl` — the 1,147 unrecoverable LEPs with
  per-row reason.
- `cw_fanout_regen_plan.json` — per-cell fresh rep indices for the 937
  new CW FANOUT benigns (collision-verified: 0 of 937 collide with an
  existing pool rep index).
- `cw_regen_fanout_default_manifest.json` — executable plan manifest,
  **default fixture root** (`workspace_fixtures`), 672 benign
  executions / 482 cells. Validated against `load_plan_manifest`.
- `v3_manifest.json` — executable plan manifest, **v3 fixture root**
  (`workspace_fixtures_v3`), 265 CW FANOUT benigns / 160 cells **+ 13
  bv/rl gap benigns / 11 cells** not covered by the pending bvrl-regen
  job 3600260 (those 11 SWE-bench cells postdate its manifest).
  Validated against `load_plan_manifest`.
- `bndv41_regen_plan.json` — shard plan (Job A: 64 shards, worst 19
  execs; Job B: 32 shards, worst 13 execs), wall model, resource spec.
- `slurm/bndv41_cw_default.sbatch`, `slurm/bndv41_v3_gap.sbatch` —
  ready-to-submit arrays, modelled on the proven `bvrl_regen.sbatch`
  pattern (per-shard vLLM on the allocated GPU, port rewrite to a
  shard-local runtime manifest, coordinator-first topology guard,
  backend-failure shard guard).

### Headline numbers

| Quantity | Value |
|---|---:|
| Incomparable LEP executions | 7,783 |
| Recoverable by benign regen | **6,636** |
| Unrecoverable (pre-fix RELAY skeletons) | 1,147 |
| Unique regen configurations | 1,108 (937 of them CW FANOUT) |
| **New benign executions required** | **1,695** (CW 937 + bv/rl 758, of which 745 already covered by pending job 3600260) |
| New executions THIS batch submits | **950** (672 + 265 + 13) |
| bv/rl executions covered by pending bvrl-regen job 3600260 | 745 of the 758 |
| Expected comparable after regen | **16,693 / 17,840 = 93.57%** |
| Remaining incomparable (structural, pre-fix RELAY) | 1,147 (6.43%) |

Notes:
- **Unique configurations vs total executions.** The 1,108
  configurations are (cell, generable exact-shape) units; the frozen
  matching permits one benign rep to serve every LEP code and
  propagation mode in the cell, so 1,695 total benign executions
  recover 6,636 LEP executions (3.9× leverage).
- **CW FANOUT recovery.** Simulating the frozen arbiter with the 937
  new FANOUT benigns in place: all 3,847 incomparable FANOUT-shaped
  CW LEPs reach ≥3 compatible refs (1 cell, `code_review_swe_148`,
  has an empty pool; its 3 fresh reps close it).
- **Fresh rep indices, never overwrites.** New benigns use rep indices
  strictly above every existing pool rep; staging supersession then
  adds them to pools without displacing any existing file.
- **Coordinator-first structure preserved.** CW O2M LEPs are served by
  canonical FANOUT benigns; the sbatch guards assert
  `topology.is_coordinator_first()` in the working tree before any run.
- **Exclusions.** All entries: `condition=benign`,
  `execution_variant=standard`, `lep_codes=[]` — LEP_MEMORY_POISONING
  and every memory_enabled cohort is structurally absent from both
  manifests (asserted in every shard's preflight).

## 4. Slurm plan (DeltaAI, ghx4)

Two array jobs, each shard = 1 node × 1 GH200 + its own local vLLM
instance (no shared server → nothing to overwhelm; GPU allocation is
the natural concurrency bound). High shard count, short wall, small
batches:

| Job | array | shards/execs | worst shard | wall limit | submission |
|---|---|---|---:|---|---|
| `bndv41-cw-def` | 0-63 | 64 shards / 672 execs | 19 | 00:50:00 | `sbatch …/slurm/bndv41_cw_default.sbatch` |
| `bndv41-v3-gap` | 0-31 | 32 shards / 278 execs | 13 | 00:40:00 | `sbatch …/slurm/bndv41_v3_gap.sbatch` |

- Total: 96 array shards, 950 new benign executions.
- Wall model: vLLM startup ≈ 9 min (observed) + execs × ~56 s p90
  (cw_regen measurements) + shutdown ≈ 5 min; worst case 32 min vs 50
  min limit (Job B: 26 min vs 40 min). No GPU beyond the 1 per shard;
  32 cpus/task for vLLM host processes.
- The cluster sbatch has no `--concurrent` (slurm 25.11.8); the
  intended "concurrency limit" is inherent: shards only run while a GH200
  is free. If a softer cap is wanted, submit as chunks
  (e.g. `sbatch -a 0-31`, then `sbatch -a 32-63`) — the sbatch
  comments document both modes.
- **The pending `bvrl-regen` job 3600260** (24 shards, 1,064 execs,
  default root, covers 745 of the 758 bv/rl benigns) should be
  **cancelled or allowed to run** — it does NOT cover the 11 SWE-bench
  gap cells (v3 root). This batch's Job B covers exactly that gap and
  is independent. Running both is safe (disjoint cells, disjoint
  output roots); Job B's 13 gap execs do not duplicate any bvrl-regen
  scenario_id.

## 5. Preflight checklist (completed / to complete at submit)

1. Manifest entries validated against `load_plan_manifest` — DONE
   (672 + 278 scenarios pass the strict loader incl. fixture hashes).
2. All referenced fixtures exist — DONE (482 default + 160 v3 + 11 gap
   fixtures all present; hashes verified in-sbatch per shard).
3. No existing valid benign execution regenerated — DONE: fresh rep
   indices only (0 collisions across 937 CW + 13 gap reps);
   sbatch refuses to start a shard whose output root already holds
   records.
4. Expected topology / propagation mode — DONE: CW → single_origin
   benign entries under canonical FANOUT (coordinator-first asserted);
   bv/rl gap → single_origin serial.
5. memory_enabled / poisoning excluded — DONE (asserted per shard).
6. Output paths cannot overwrite — DONE: new roots
   `production_run/bndv41_cw_default_regen/`,
   `production_run/bndv41_v3_gap_regen/` (verified absent); runtime
   manifests are shard-local.
7. Slurm commands + resources — DONE in the two sbatch files;
   `sbatch --concurrent` unsupported → chunk submission documented.
8. Totals: 96 shards, 950 execs, ~2 GPU-hours/shard worst-case
   (50 min × 1 GPU), ~80 GPU-hours + ~8 CPU-hours total for the batch.

## 6. Residual structural issues (reported, not resolvable by benign regen)

- **1,147 LEPs (6.43%) unrecoverable**: CW single-origin LEPs recorded
  under the pre-fix RELAY skeleton (1,287 incl. 8 coordinator-first
  relay edge cases in 2 cells) vs the 1,147 exact-shape census.
  Recoverable only by re-executing those LEP traces under post-fix code
  (population decision).
- **Working-tree drift**: live `baseline_deviation_v41.py` (75f1e77f)
  ≠ frozen 6f0f7ff3. Use the frozen snapshot for any post-regeneration
  analysis run, or reconcile the tree first.
- **Origin version skew**: document the cw_regen/swe_200 skew in the
  final analysis; coordinator-origin FANOUT LEPs are legitimate
  post-fix rows, RELAY-origin rows are pre-fix artifacts.
