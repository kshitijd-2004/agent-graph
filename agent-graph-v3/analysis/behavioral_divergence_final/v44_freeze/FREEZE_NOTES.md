# V4.4 final freeze — notes, rationale, limitations, reproducibility

Freeze: `v44_freeze/manifest.json` (886 pinned artifacts, SHA-256),
verified by `v44_freeze/verify.py` (also re-verifies the pinned v42
19/19 and v43 38/38 freezes). V4.4 is the final measurement development
version; work moves to PropHunter.

## Selected operating points (approved 2026-11-26)

- **M1-C** — whole-trace gate on the V4.4 ledger: continuous dims
  (assertions/actions) fire on `(T_max − B) > 0.50·th` (self-cell
  recentered max form); discrete dims (decisions/outcomes) fire on the
  recorded-integer LEP-free 3-pair disagree-mass null, one-sided
  `P(null ≥ k) ≤ 0.05`. Union = logical OR across the four
  independent dimensions. No weighted composite, including checkpoint
  admission.
- **M2-O4** — checkpoint gate: continuous channels at the
  fixture-disjoint family q98 threshold; discrete channels at the LEP-
  free disagree-mass null with **alpha = 0.10** (the O4 knee of the
  fine sweep: the largest perturbed-minus-benign separation,
  +16.3pp, at half M2-F's benign-control cost; b2b execution-level
  control 1,391/16,767 = 8.30%, fixture-cluster CI [7.23%, 9.39%]).
- **M3 (new headline)** — observed matched task degradation,
  independent of dependency support: the frozen five-way criterion
  (LEP failure AND ≥1 gradeable matched benign reference correct,
  ANY-correct aggregation) with the dependency gate removed. The old
  M4 dependency-supported object (299 at O4) is now an internal M3
  breakdown; the old M3 dependency-supported propagation (978 at O4)
  is now an internal M2 breakdown. Old four-metric naming is retired
  from final reporting; historical artifacts are preserved unchanged.

## Why these points (rationale, not re-derivation)

- M1-C: P 0.797 / R 0.750 / F1 0.773 on the round-2 development
  reference (84 pos / 137 neg; development evidence only). Recall
  rises from 0.643 (M1-A, c=1.0) without any increase in the matched
  benign control: the LEP-free in-cell continuous probe fires 0 times
  at both c=1.0 (the S1 structural property) and c=0.50 (verified
  empirically), so the execution-level control row-OR is driven solely
  by the discrete alpha=0.05 channel, which M1-C leaves unchanged —
  M1-C's control is 2.43% [1.81%, 3.11%] and byte-identical to M1-A's
  (the same 4,070 rows). Recall gains are target-side: +946 actions,
  +31 assertions continuous target cells vs c=1.0.
- M2-O4: on the sensitivity↔specificity frontier, O4 achieves
  +16.3pp perturbed-minus-benign separation (the fine-sweep peak) at
  b2b 8.30%; raising alpha to .25 (M2-F) adds ~zero separation and
  3.5pp of benign control, and most of M2-F's extra detections are
  graph-exposure-only, not dependency-supported propagation.

## Known limitations (stated, not cured)

1. Development evidence only: round-2 annotations (221 scored) are
   reused development labels; M2-O4 has no independent semantic
   validation; the 8.30% b2b control is a descriptive matched control,
   not a specificity certificate.
2. The stricter cross-fixture execution-level control is 9.38% (CI
   [8.20%, 10.59%]); the ≤5% execution-level FPR target is NOT
   supported by the available benign repetitions — an independent
   benign repetition set (new fixtures, no reused reference sets)
   is required to claim it. Never manufactured.
3. Grading coverage: 4,142/16,767 (24.70%) of the corpus is
   not_gradeable (3,806 LEP-ungradeable ∪ 336 no-gradeable-benign,
   2,042 in both); 578/4,123 (14.02%) within M2-O4. Observed
   degradation (1,992 corpus; 948 within M2-O4) is over the 12,625
   grading-eligible rows; conditional rate 15.78%.
4. Causal attribution: matched benign degradation is perturbation-
   ASSOCIATED, not proven causal — benign references are separate
   executions, not counterfactuals of the same run.
5. Discrepancy documentation: the unpersisted earlier estimates
   (4,461/1,195 observed-matched; 7,398/570 not_gradeable; 1,099
   dep-supported) do not reconcile to any per-execution formula over
   the cached grades; the reproducible per-execution frozen-criterion
   counts (1,992 / 948 / 4,142 / 578 / 421 / 299) and the nearest
   per-reference-repetition variants (4,373 corpus / 2,264 within
   M2-O4) are recorded in `final_aggregates_v44.json` under
   `discrepancy_notes` + `reconciliation.variant_census`.
6. Saturated / non-firing channels are NAMED, not forced: decisions
   th≥1.0 in 2,355 cells, assertions 1,231, outcomes 791;
   uncalibrated (th=None): assertions 4,021, decisions 4,162.

## Reproducibility (exact commands, deterministic, no LLM / no
trace re-read / no new statistics)

```
cd agent-graph-v3
PYTHONHASHSEED=0 python3.11 analysis/behavioral_divergence_final/calibration_v3/final_v44.py
PYTHONHASHSEED=0 python3.11 analysis/behavioral_divergence_final/v44_freeze/verify.py
```

`final_v44.py` asserts every anchor before writing (M1-C dev
0.797/0.750/0.773; M1-C control 2.43% == M1-A control; M2-O4 4,123 /
dep 978 / M2-A byte cross-check 2,312 / 1,130 / 781; M3 observed
1,992 / 4,142 / 421 / 299; A1 observed==frozen on M3-supported).
Outputs: `calibration_v3/out_v44/final/{per_execution_v44.jsonl
(PropHunter input), m2_rows_O4_v44.jsonl,
final_aggregates_v44.json, eligibility_census_v44.json}`.

## PropHunter readiness

`per_execution_v44.jsonl` (16,767 rows) carries stable
`execution_id` / `fixture_id`, task-family, LEP code, topology,
propagation-mode, per-metric selected labels + per-dimension
firing/provenance (M2), dependency-supported and graph-exposure-only
classifications, and measurement-availability flags (M1-C dim
availability, checkpoint count, gradeable flag, LEP/benign grades,
unavailable-pair counts) — the full support evidence a downstream
propagation hunter needs, with no re-derivation.
