# Reproduction — BND V3

Offline, deterministic, read-only against the frozen production traces.
No agent executions, no GPU, no external LLM calls (asserted in
`run_manifest.json`).

## 1. Tests (synthetic + real-trace regressions)

```bash
python3.12 -m pytest tests/test_baseline_deviation.py \
                     tests/test_baseline_deviation_v2.py \
                     tests/test_baseline_deviation_v3.py -q
```

V3 suite: 42 tests (synthetic adversarial probes + the nine audit-documented
real-trace cases loaded via `tests/bnd_v3_helpers.py`). V1 (19) and V2 (33)
suites are unchanged and stay green.

## 2. Full-production V3 analysis

```bash
PYTHONDONTWRITEBYTECODE=1 python3.11 -m scripts.analyze_baseline_deviation_v3 \
  --output analysis/full_production_baseline_deviation_v3 --workers 24
```

Defaults: `--source analysis/full_production_consolidated/source`,
`--spec-root workspace_fixtures`, `--v2-dir
analysis/full_production_baseline_deviation_v2` (frozen V2 ledger for the
V2↔V3 transition, read-only), `--ledger-dir
analysis/full_production_id_trc_v2` (original attribution detector, read-
only cross-tabulation only).

## 3. Outputs in this directory

| File | Content |
|---|---|
| `bnd_v3_per_trace.jsonl` | per-trace: schema, provenance, assertion maps, whole-trace BND (v3 denominator), stage/final metrics, recovery outcomes |
| `bnd_v3_summary.json` | overall + per-LEP/family/topology/mode |
| `v2_v3_transition.json` | V2-legacy vs V3 flag transitions on the shared 9,143 comparable rows |
| `coverage_report_v3.json` | feature-availability state census (M6) |
| `origin_exclusion_evidence.json` | recorded-origin census + excluded delivered content (C2) |
| `stage_coverage_v3.json` / `stage_summary_v3.json` | intermediate/final coverage, peaks, convergence/correction/relapse |
| `threshold_sensitivity_v3.json` | independently recomputed rule families |
| `population_accounting_v3.json` | population-flow accounting (fixed denominators, M7) |
| `AUDIT_CLOSURE.md` | finding-by-finding resolution vs Astra's audit |
| `INDEPENDENT_REVIEW.md` | concise review package |
| `run_manifest.json` | module hashes, execution counts |

## 4. Key numbers (this run)

- comparable: 9,143 / 16,833 LEP traces (identical population to V1/V2)
- V3 legacy-rule anomaly flag: 2,874 / 9,143 = 31.43%
- observational convergence: 356; evidence-supported correction: 356; relapse: 112
- assertion component observed/absent: 8,531 / 9,143 (93.3%)
