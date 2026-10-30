# Reproduction

All work is offline, deterministic, read-only against the production
traces. No agent executions, no GPU, no LLM calls (asserted in
`run_manifest.json`). The per-trace output is **byte-identical across
independent process invocations** (verified: two consecutive full runs
produced `bnd_v2_per_trace.jsonl` with matching SHA-256).

## 1. Tests (synthetic + real-trace regressions)

```bash
# V1 suite (must stay green — V2 imports V1 helpers but does not modify it)
python3.12 -m pytest tests/test_baseline_deviation.py -q
# V2 suite (33 tests: 29 synthetic + 2 real-trace via tests/fixtures/bnd_v2/
#   + 2 kernel-determinism regressions incl. a cross-process byte check)
python3.12 -m pytest tests/test_baseline_deviation_v2.py -q
# both together
python3.12 -m pytest tests/test_baseline_deviation.py tests/test_baseline_deviation_v2.py -q
```

(`pytest` 9.1.1 is installed for python3.12; the 3.11 interpreter used for
the production run has no pytest — not needed for the analysis.)

Real-trace fixtures (one-time copy; already done — re-run only if the
source traces change; procedure also in `docs/baseline_deviation_v2.md`):

```bash
python3.11 - <<'PY'
import sys, json
from pathlib import Path
sys.path.insert(0, 'analysis/bnd_v1_validation/scripts')
from common_index import trace_paths
tp = trace_paths()
fix = Path('tests/fixtures/bnd_v2'); fix.mkdir(parents=True, exist_ok=True)
for tid, name in [('code_review_conflicting.bv.LEP_MEMORY_POISONING.many_to_one.00.mem__b',
                   'code_review_conflicting_lep_mem.json'),
                  ('fin_finqa_243_etr_2011.bv.LEP_MEMORY_POISONING.many_to_one.00.mem__b',
                   'fin_finqa_243_lep.json')]:
    Path(fix/name).write_text(json.dumps(json.loads(Path(tp[tid]).read_text()), sort_keys=True))
print('wrote', sorted(p.name for p in fix.glob('*')))
PY
```

## 2. Full-production V2 analysis

```bash
python3.11 -m scripts.analyze_baseline_deviation_v2 \
  --output analysis/full_production_baseline_deviation_v2 \
  --workers 24
```

Defaults (used for this run):
`--source analysis/full_production_consolidated/source`,
`--spec-root workspace_fixtures`,
`--v2-dir analysis/full_production_id_trc_v2` (read-only cross-tab join),
`--v1-dir analysis/full_production_baseline_deviation_v1` (frozen V1 ledger
for the comparison),
`--audit-dir analysis/bnd_v1_validation` (frozen audit ledgers for the
rescale-flip and recovery reevaluation).

Runtime: ~3 min end-to-end (discovery of 26,975 trace files ~90 s;
measurement of 16,833 LEP traces with a 24-worker process pool ~15 s).
The script refuses to write into a non-empty output directory; `rm -rf`
the output dir first if re-running.

## 3. What each output contains

| file | content |
|---|---|
| `bnd_v2_per_trace.jsonl` | per-trace V2 payload: legacy BND/D_LEP/D_benign + `legacy_anomaly`, stage block (`d_peak`, `d_final`, `stage_threshold`, `high_intermediate_deviation`, `final_deviation`, `behavioral_convergence`, `recovery_stage`, `verified_recovery`, `verified_unavailable_reason`), extracted features + diagnostics, V2-propagation join (`v2_classification`, read-only) |
| `bnd_v2_summary.json` | overall + by LEP / task family / topology / propagation mode, incl. stage fractions |
| `threshold_sensitivity_v2.json` | independently-computed rules R0–R5 + near-threshold / zero-T counts |
| `stage_coverage.json` | per-stage-metric missingness + checkpoint / verified-recovery unavailable reasons |
| `v1_vs_v2_comparison.json` | aligned 9,143-row comparison: flag flips, per-LEP rates, 20 feature-level flip examples |
| `rescale_flip_check.json` | the audit's 78/32 flip populations re-examined on frozen ledgers |
| `recovery_reevaluation.json` | the audit's 277 recovery rows (86 true-recovery, 33 V1-missed) vs V2 stage metrics |
| `population_accounting.json` | reconciled denominators (9,143 = 8,546 + 597; 10,395 = 8,546 + 1,849) |
| `run_manifest.json` | input hashes, provenance, 0-execution assertion |
| `REVIEW.md` | what changed / what did not + limitations |

## 4. Determinism note

V2's assertion and action kernels sum over a **sorted** key union
(`sorted(set(a)|set(b))`), and the total-variation action kernel is a
V2-local `total_variation_deterministic` (V1's set-iteration
`total_variation` is unchanged). This makes the float summation order
stable and the per-trace records byte-reproducible. The in-process
(`test_deterministic_output`) and cross-process
(`test_cross_process_determinism`) regressions guard this; the full-run
SHA-256 match above is the end-to-end confirmation.
