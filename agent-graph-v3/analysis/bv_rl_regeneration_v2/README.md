# BV + RL Regeneration v2 (Branch & Verify / Review Loop)

Scope limited to `branch_and_verify` and `review_loop`, standard executions only,
4 retained LEPs, memory permanently excluded. Built on the committed fixes from the
GO audit (injection-structure admission + review-cap finalization + coordinator-first
scheduler). Frozen production/BND artifacts and the v1 manifest are untouched;
outputs go to `production_run/bv_rl_regeneration_v2/`.

## Contents

| File | Purpose |
|---|---|
| `build_manifest.py` | Deterministic builder → `regeneration_manifest.json` + inventory + summary (persistent sources only) |
| `regeneration_manifest.json` | Loadable `agentprop-readiness-selection-v1` plan manifest |
| `regeneration_inventory.csv` | One row per execution with category, historical status, rejection terms |
| `regeneration_summary.json` | Counts by topology/family/LEP/mode + cost-weighted shard plan |
| `build_pilot.py` | Deterministic pilot selection → `pilot_manifest.json` + `pilot_report.md` |
| `bvrl_pilot.sbatch` | Single-node pilot (ghx4-interactive) with per-execution outcome + runtime report |
| `bvrl_regen.sbatch` | Full-batch array job (ghx4), one runner shard per array task (currently `--array=0-23`, N=24, `--time=01:45:00`) |
| `verify_coverage.py` | Post-run coverage/admission/comparability/systematic-rejection checks |
| `plan_shards.py` | Re-computes shard sizing on the ACTUAL `split_plan()` distribution + pilot-observed costs; rewrites `regeneration_summary.shard_plan` |

## Post-pilot correction (2026-11-12, pilot job 3581169)

- **Shard plan was stale.** The original `shard_plan` (even 104–109, "max 99 min")
  was an LPT re-binning that does NOT match the runtime sharder: `--run-node/--num-nodes`
  splits via `split_plan()` (`md5(pair_tag) % N`), which is uneven.
  `plan_shards.py` now sizes on the true distribution.
- **Cost model updated from the pilot.** RL median observed 62 s (p95 213 s
  truncation-tail, only 3 such RL rows in the population); BV median 28 s;
  vLLM startup observed ~3 min (sbatch readiness poll caps at 120 × 5 s = 600 s).
- **Final sizing (this pass):** N=24, uneven 20–59 exec/shard. Worst-shard wall:
  ~51 m under the median model, ~93 m under the conservative model
  (BV 51 s / RL 130 s + 600 s startup). `--time=01:45:00` clears the
  conservative worst with ~12 m headroom; 02:15:00 is no longer requested.
- **Manifest scenarios are byte-unchanged** (1,064, sha256
  `05ded435…4f3e2c3`); only `regeneration_summary.shard_plan` and the
  sbatch `--array`/`--time` lines changed.

## Build & validate (offline, no inference)

```sh
cd /u/kdhande/agent-graph/agent-graph-v3
python3 analysis/bv_rl_regeneration_v2/build_manifest.py
python3 analysis/bv_rl_regeneration_v2/build_pilot.py
# loader validation (read-only, against working-tree build_plan):
python3 - <<'PY'
from pathlib import Path
from benchmark.plan_manifest import load_plan_manifest
from benchmark.benchmark_runner import BenchmarkManifest
base = BenchmarkManifest(fixture_root=Path('workspace_fixtures'),
                        output_dir=Path('production_run/bv_rl_regeneration_v2'))
for p in ('analysis/bv_rl_regeneration_v2/regeneration_manifest.json',
          'analysis/bv_rl_regeneration_v2/pilot_manifest.json'):
    m, plan = load_plan_manifest(Path(p), base)
    print(p, '->', len(plan), 'scenarios OK')
PY
```

## 1. Pilot (measure delivery / admission / termination / runtime first)

```sh
sbatch analysis/bv_rl_regeneration_v2/bvrl_pilot.sbatch
# outputs: production_run/bv_rl_regeneration_v2/pilot/{benchmark_records.jsonl,traces/,rejected_traces/}
```

Acceptance before full batch:
- B&V M2O rows: `expected_injection_origins=2`; admitted only when actual=2 with
  distinct researcher/analyst attribution; 1-origin → `injection_count_mismatch`
  (rejected, never admitted), structural errors → `injection_structure_mismatch`.
- RL rows: no recurrence of the old `max_events_reached`-via-loop starvation;
  `injection_origin_count == expected`; finalization succeeds when budget permits;
  text-only model outputs remain correct `protocol_violation` rejections.
- Benign rows: 0 origins, completed, eligible; same scheduler path as perturbed.
- Runtime: p95 per topology within ~1.25× of `COST_P95_S` in the summary.

## 2. Full batch

Update the array size in `bvrl_regen.sbatch` to
`shard_plan.target_shards` in `regeneration_summary.json`:

```sh
sbatch analysis/bv_rl_regeneration_v2/bvrl_regen.sbatch
# outputs: production_run/bv_rl_regeneration_v2/node_<shard>/
```

## 3. Post-run verification

```sh
python3 analysis/bv_rl_regeneration_v2/verify_coverage.py \
    --records production_run/bv_rl_regeneration_v2/node_*/benchmark_records.jsonl \
             production_run/bv_rl_regeneration_v2/pilot/benchmark_records.jsonl
# writes post_run_report.json; flags >70% rejected cells and benign anomalies
# Consolidation rule: 18 of the 23 pilot executions are also in the full
# manifest. verify_coverage.py dedupes records by scenario_id (keeps the
# latest, i.e. the full-batch record when both are passed) so shared
# scenarios are never double-counted. The 5 pilot-only benign controls
# (rep-00 twins whose fixtures already had 3 valid benign reps and which
# the full manifest therefore correctly omits) consolidate as
# pilot-sourced records only.
```
