# Corrected downstream-propagation analysis (BND V4.1)

Read-only re-analysis of the frozen `analysis/bnd_v41_cw_swe200_provisional/`
population (10,057 comparable standard executions). Implements the six
corrections from the causal-dependency audit; modifies **no** existing
traces, benchmark code, frozen artifacts, or Slurm jobs
(3600260 / 3627515 / 3627516 untouched).

## Reproduce (v1 — original 3-source population, `results/`)

```
cd /u/kdhande/agent-graph/agent-graph-v3
python3.11 analysis/downstream_propagation_corrected/analyze_downstream.py   # writes results/per_trace.jsonl + results/summary.json
python3.11 analysis/downstream_propagation_corrected/render_report.py        # writes results/report.md + results/breakdowns.json
```

Both scripts are read-only over all inputs and write only into this
directory's `results/`. No model calls, no GPU, no Slurm. Runtime ~5 min
(dominated by the 25k-trace index build + the 4,845 unique benign reference
gradings, which load the frozen extractor `frozen_run/benchmark/baseline_deviation_v41.py`
in memory).

## Reproduce (v2 — expanded 4-source population incl. bvrl-regen, `results_v2/`)

bvrl-regen (job 3600260) completed 2026-11-19 into
`production_run/bv_rl_regeneration_v2/` (1,053 admitted standard traces:
773 benign + 280 LEP). v2 pipeline:

```
cd /u/kdhande/agent-graph/agent-graph-v3
python3.11 analysis/downstream_propagation_corrected/stage_v2.py   # staged_v2/merged/traces (4 sources, bvrl>swe_200>cw_regen>consolidated)
cd analysis/bnd_v41_cw_swe200_provisional/frozen_run              # frozen analyzer, byte-identical module 6f0f7ff3
PYTHONPATH=. python3.11 scripts/analyze_baseline_deviation_v41.py \
  --source "$ROOT/analysis/downstream_propagation_corrected/staged_v2/merged" \
  --output "$ROOT/analysis/downstream_propagation_corrected/frozen_run_v2" \
  --spec-root "$ROOT/analysis/bnd_v41_cw_swe200_provisional/specroot" \
  --execution-variant standard --exclude-lep LEP_MEMORY_POISONING \
  --v4-dir "$ROOT/analysis/full_production_baseline_deviation_v4" \
  --ledger-dir "$ROOT/analysis/full_production_id_trc_v2" --workers 16
export DP_LEDGER=.../frozen_run_v2/bnd_v41_per_trace.jsonl
export DP_STAGED=.../staged_v2/merged/traces
export DP_SPECROOT=.../bnd_v41_cw_swe200_provisional/specroot
export DP_OUTDIR=.../downstream_propagation_corrected/results_v2
python3.11 analysis/downstream_propagation_corrected/analyze_downstream.py
DP_RESULTS_DIR=.../results_v2 python3.11 analysis/downstream_propagation_corrected/render_report.py
```

v2 headliners: N=12,891 comparable (bv/rl incomparables 2,789 → 52);
M1 3,156 (24.48%), M2 2,242 (17.39% all / 20.55% evaluable), M3 1,184
(verified 293 + plausible 891), M4 842 (vs benign baseline 47.5%).

## Inputs (all read-only)

- Frozen ledger `analysis/bnd_v41_cw_swe200_provisional/run_merged/bnd_v41_per_trace.jsonl`
  (comparable rows, stage block, per-trace LEP feature extractions)
- Staged traces `analysis/bnd_v41_cw_swe200_provisional/staged/merged/traces/`
  (raw events, `event_labels`, `hidden.mutation_evidence`, `metadata.admission_contract`)
- Ground-truth specs `analysis/bnd_v41_cw_swe200_provisional/specroot/*/manifest.json`
- Frozen V4.1 extractor module (loaded in memory, never executed as a process)

## Metrics

- **M1** whole-trace deviation (existing 4-dim rule, unchanged): 2,322 (23.09%)
- **M2** cross-agent downstream deviation (stage-gated, NON-origin agent
  role; origin / later-origin-M2O / later-turn-of-origin excluded; ID
  authored-origin preserved): 1,654 = 16.45% all / 19.32% evaluable
- **M3** dependency-supported propagation (M2 + byte-verified delivery +
  positive downstream reach evidence): 843 = verified 172 + plausible 671;
  the 652 rows with only structural DAG exposure are UNSUPPORTED
- **M4** ground-truth downstream task failure (M3-supported AND final
  deliverable graded wrong vs fixture ground truth): 565 — read against the
  benign (no-LEP) task-failure baseline 47.5% (2,301/4,845; per family in
  summary)

CIs are cluster-binomial over the 1,615 distinct benign reference sets
(shared-control clustering); execution-level Wilson intervals are present in
`summary.json` only as flagged anti-conservative references.

## IPI/TRC transformed-handoff recovery

Prior rounds classified IPI/TRC cross-role deviations as unverifiable because
the injected tool result is confined to the origin agent by design. Here the
mutation's *introduced content* (TRC substituted/added numeric values; IPI
injected instruction text; HC/ID distinctive new tokens) is probed in a
downstream agent's own authored output (verified) or recorded input/lineage
(plausible) — no verbatim requirement. Of 580 delivery-verified IPI and 542
TRC cross-agent M2 positives: 258 IPI (44.5%) and 212 TRC (39.1%) recovered
positive reach evidence (verified: 87 IPI / 82 TRC).

## Files

- `analyze_downstream.py` — full classification (per-trace + aggregates)
- `render_report.py` — renders `report.md` + `breakdowns.json` from results
- `results/per_trace.jsonl` — per-trace classification (M1–M4, reach basis,
  delivery, evaluable, task_grade)
- `results/summary.json` — aggregates, denominators, CIs, breakdowns
- `results/report.md` — human report incl. comparisons and limitations
- `results/breakdowns.json` — machine-readable cross-tabs
