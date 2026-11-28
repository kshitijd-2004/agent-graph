# calibration_v3 — the V4.4 statistically valid null machinery

Corresponds to `benchmark/baseline_deviation_v44.py` (the V4.4 extractor)
and replaces the defective V4.3 calibration constructions identified by
the independent investigation (fractional-count binomial tests, saturated
non-firing channels, circular probe thresholds, mixed-version cached
statistics, downstream evidence mis-attribution).

## Files

| file | role |
|---|---|
| `m44.py` | the shared null library: cell statistics, fixture-disjoint pools, the gate forms (targets and benign probes score IDENTICALLY), the discrete-support nulls, S1/S2/S3 specificity objects, fixture-cluster CI, the corrected ceiling |
| `compute_checkpoint_v44.py` | the M2 checkpoint recompute (V4.4 evidence localization + recorded integer evidence); writes `out_v44/checkpoint_pairs_v44.jsonl` |
| `compute_production_v44.py` | the consolidated M2/M3/M4 production gate; writes `out_v44/m2m3_production_v44.json` + `m2_rows_v44.jsonl` |
| `dev_eval_v44.py` | the round-2 DEVELOPMENT evaluation of the pre-registered operating points V44A–V44E + the three specificity objects; writes `out_v44/dev_eval_v44.json` |

## Pre-registered operating points (M1, per-dimension + logical OR)

- **V44A** self-cell recentered MEAN `(T_d − B_d) > th_d` — the V4.3 OP0
  gate form on V4.4 representations (isolates the representation /
  evidence-scope correction from the statistic change).
- **V44B** self-cell recentered MAX `(max_p T_p − B_d) > th_d`, strict
  `>` — the anti-dilution within-dimension statistic (the G2/G2s
  ablation showed the strict form; the tie-inclusive form collapsed
  specificity and is NOT a candidate).
- **V44C / V44D** cross-fixture family q98 (fixture-disjoint LOO of the
  LEP-free probe pool), mean / max.
- **V44E** V44B on the continuous dims + the discrete-support null on
  decisions/outcomes (the RECORDED integer disagree counts, one-sided
  tail on the LEP-free family pool over their actual support — the
  `round(mean*n)` binomial reconstruction is not used); saturated
  self-cell dims are NAMED uncalibrated, not silently dropped.

Selection (pre-registered): among candidates with 0 S1 violations and
dev P ≥ 0.70, R ≥ 0.70, F1 ≥ 0.65, highest dev recall (tie: F1).  If no
candidate passes, the strongest defensible point is reported with its
failures named — a passing operating point is never manufactured.

## Specificity objects (reported SEPARATELY, never conflated)

- **S1** structural in-cell probe OR — a property (0 violations
  verified numerically on every evaluable P1 cell);
- **S2** cross-fixture execution-level OR over the full P1 population
  (the unseen-execution object; the ≤5% target is read off here, with
  the fixture-cluster bootstrap CI — the independent-multiplicity model
  is NOT used; if S2 does not support ≤5%, the claim is explicitly
  qualified and the needed evidence named);
- **S3** semantic dev FPs (FP/137, Wilson CI) — imperfect-label
  development evidence, not a certificate.

## Commands (run in order; all deterministic, PYTHONHASHSEED=0, no LLM)

```
PYTHONHASHSEED=0 python3.11 -m scripts.analyze_baseline_deviation_v44 \
    --source analysis/consolidated_20261121_bnd_v41/staged/merged \
    --spec-root analysis/bnd_v41_cw_swe200_provisional/specroot \
    --v4-dir analysis/full_production_baseline_deviation_v4 \
    --v43-dir analysis/full_production_baseline_deviation_v43 \
    --ledger-dir analysis/full_production_id_trc_v2 \
    --output analysis/full_production_baseline_deviation_v44 --workers 24

PYTHONHASHSEED=0 python3.11 analysis/behavioral_divergence_final/calibration_v3/compute_checkpoint_v44.py
PYTHONHASHSEED=0 python3.11 analysis/behavioral_divergence_final/calibration_v3/compute_production_v44.py
PYTHONHASHSEED=0 python3.11 analysis/behavioral_divergence_final/calibration_v3/dev_eval_v44.py
python3.11 analysis/behavioral_divergence_final/v43_freeze/verify.py   # the freeze stays intact
```

## Comparison baselines (frozen)

- V4.2: `calibration_v2/out_v42/` (OP0 P 0.825 R 0.619 F1 0.707; M2 2,272; M3 815; M4 262/815)
- V4.3: `calibration_v2/out_v43/` (OP0 P 0.828 R 0.631 F1 0.716; M2 2,275; M3 816; M4 263/816)
- both pinned byte-for-byte: `v42_freeze/` (19 artifacts) and
  `v43_freeze/` (38 artifacts).
