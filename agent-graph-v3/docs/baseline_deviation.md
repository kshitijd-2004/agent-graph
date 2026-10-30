# BND — Baseline-Normalized Behavioral Deviation (experimental)

Independent, offline, read-only analyzer. `benchmark/baseline_deviation.py`
does not import or modify the existing propagation detectors
(`propagation_analysis`, `behavioral_anomaly`, `id_trc_v2`,
`evidence_propagation`) or their outputs; it reads production traces and
fixture specs and writes only to its own output directory.

## Question

For one perturbed (LEP) execution P and its three matched benign reference
executions B1..B3, does P deviate from normal agent behavior *significantly
more* than the benign executions deviate from one another?

## Definition

```
D_benign = mean( d(B1,B2), d(B1,B3), d(B2,B3) )
D_LEP    = mean( d(P,B1), d(P,B2), d(P,B3) )
BND      = D_LEP - D_benign          (reported alongside D_LEP and D_benign)
```

`d` is a task-aware bounded distance in [0,1] over four transparent
components — **assertions** (the run's own produced content: financial fact
values, claim verdicts, repair targets), **decisions** (overall verdict /
repair verdict / final numeric answer), **actions** (role-aligned
(role,tool,target) multiset via total variation; index-free, so reordering
independent branches cannot move it), **outcomes** (what was actually
delivered: final-answer presence, verdict lines, patch files). Formatting,
verbosity, trace length, and absolute event order never enter the score;
each sub-kernel is bounded by construction.

Feature extraction is spec-driven (`workspace_fixtures/<fixture_id>/manifest.json`):
`required_facts` labels map to numbers in agent-emitted text (tool results and
LEP boundary text are deliberately excluded — those are inputs the agent
observed, not behavior it produced); `required_facts.claim_*_verdict` map to
SUPPORTED/CONTRADICTED/NOT_ENOUGH; `required_issues` map to identified repair
targets.

### BND is an excess-distance score, not a scale-normalized effect size

BND is a *difference of two means on the same underlying [0,1] scale*. It is:

* **signed and unbounded on the positive side** (D_LEP is bounded by 1, D_benign
  by 1, but their difference can approach +1 with no natural ceiling meaning);
* **centered on this reference triple** — its zero point is the observed
  benign spread of B1..B3, not a population expectation;
* **heteroscedastic** — the same absolute D_LEP yields a large positive BND
  when benign runs agree and a negative BND when they do not.

It must therefore never be reported as a Cohen's-d-like standardized effect
size, and no universal BND cutoff should be adopted. A positive BND says only
"P deviates more from these three controls than these three controls deviate
from each other"; it does not attribute the deviation to the LEP and does not
establish propagation.

### Experimental threshold (kept separate from propagation)

```
T = max( d(B1,B2), d(B1,B3), d(B2,B3) )        # per-configuration
behavioral_anomaly = (D_LEP > T)                # comparable configurations only
```

This is an exploratory heuristic (the LEP must deviate beyond the *whole*
observed benign spread, not just its mean). `behavioral_anomaly` is stored
next to, and never merged with, `propagation_occurred` in the v2 ledger.
Incompatible reference sets are reported `incomparable` and never forced to a
classification.

### Reference-only calibration (three-run baseline caveat)

Three benign runs do not support distributional inference. For every
configuration the CLI also emits leave-one-out reference-only scores
(mean distance from B_i to the other two), and the summary reports the
fraction of LEP traces whose D_LEP exceeds *all* of them — as a diagnostic
check only, with no calibrated p-value and no universal percentage threshold.

## Comparability gating

A configuration is `incomparable` when any of:
* fewer than three benign references are available for the cell;
* a hard identity field (fixture, task family/variant, topology,
  propagation mode, parallel flag) disagrees between P and a reference;
* the references do not share one population;
* the recorded execution shapes disagree — serial worker schedules and
  coordinator-first parallel schedules differ in shape even when they run
  the same operations, and are never compared as if they were identical.

Unverifiable fields on a reference (no admission contract recorded) are
soft diagnostics, not hard gates. Component-level `unavailable` is tracked
per pair (missing extracted facts/verdicts) and is excluded from pair
means, never scored as agreement — but a *genuinely absent* behavior (both
sides action-silent) scores 0 by construction and is labeled in `unavailable`.

## Outputs (per run)

* `bnd_per_trace.jsonl` — full per-trace measurement: D_LEP, D_benign, BND,
  T, behavioral_anomaly, per-component distances (LEP vs benign means),
  per-pair breakdowns, unavailable components, reference-only scores,
  extraction coverage and diagnostics, v2 classification join.
* `bnd_by_lep_type.json`, `bnd_by_task_family.json`, `bnd_by_topology.json`,
  `bnd_by_propagation_mode.json`, `bnd_by_configuration.json` — BND
  distributions + anomaly fractions per group.
* `cross_tabulation.json` — anomaly flag x v2 classification grid, with
  highest/lowest-BND exemplars per v2 class (representative disagreements,
  e.g. high-BND traces v2 marked unknown/negative, low-BND traces marked
  positive, plus per-class counts).
* `examples_high_deviation.json`, `examples_low_deviation.json`,
  `examples_meaningful_vs_benign.json`.
* `coverage_report.json` — discovery skips, incomparability reasons,
  unavailable-component counts, spec availability, v2 join status.
* `reference_calibration.json` — leave-one-out reference-only diagnostics.
* `bnd_summary.json`, `run_manifest.json` (deterministic; code + input
  hashes; 0 agent executions, 0 external model calls).

## How to run

```
python3 -m scripts.analyze_baseline_deviation \
  --source analysis/full_production_consolidated/source \
  --output analysis/full_production_baseline_deviation_v1 \
  --v2-dir analysis/full_production_id_trc_v2
```

Tests: `python3 -m pytest tests/test_baseline_deviation.py` (deterministic
synthetic regressions covering all nine required behavioral scenarios plus
symmetry/identity/bounds/weight/threshold properties).

## Interpretation guardrails

* BND values are measurements, not classifications; disagreements with the
  attribution-based v2 detectors are complementary signal, not an error in
  either analyzer.
* No threshold in this document is validated; the per-configuration T is an
  explicitly exploratory heuristic.
* Absent observed behavior (0/0 counters) is reported as agreement with a
  note, never silently zero-folded; missing *evidence* is `unavailable`
  and excluded from means.

## Full-production results (v1 run)

Measured 16,833 LEP traces against their 4,023 benign reference cells
(2,747 cells complete with three repetitions; the rest measured with 0-2
references and gated `incomparable`).

* **Comparable:** 9,143 (54%). Incomparable for: execution-structure
  mismatch (2,013), fewer than 3 benign repetitions (5,677), with
  propagation-mode soft diagnostics recorded throughout.
* **Overall anomaly fraction (comparable):** ~0.35.
* **By LEP type** (anomaly fraction among comparable):
  TRC 0.57, IPI 0.51, MP 0.32, HC 0.18, ID 0.17 — i.e. content-substitution
  LEPs show large excess deviation, scheduling/instruction LEPs mostly do
  not, matching intuition without reusing any v2 label.
* **Cross-tab vs v2 (comparable rows):** anomaly=True: 1,388 v2-unknown,
  834 v2-negative, 462 v2-positive, 503 unmeasured-by-v2.
  anomaly=False: 2,909 v2-negative, 1,663 v2-unknown, 423 v2-positive.
  Representative disagreements: ~1,113 high-BND (>=p75) v2-unknown traces
  and ~273 low-BND (<=0) v2-positive traces — the latter suggests v2
  positives driven by event-depth evidence that the task-aware features do
  not see.
* **Reference-only calibration:** 35.4% of D_LEP scores exceed all three
  leave-one-out reference-only scores; reference-only scores themselves
  span 0.00-0.83, confirming three-run baselines are noisy (p25=0.03,
  p75=0.14) — no significance claims are made.

## Known limitations

1. **Assertion extraction is heuristic.** Financial facts are resolved by
   label-signature word proximity in agent-emitted text, with power-of-1000
   unit rescaling. Residual statement-level drift (rounded vs exact
   figures, e.g. "$6 million" vs 6.8) remains in the distance and can
   inflate D_benign; symmetric drift cancels, asymmetric drift does not.
2. **Three-run baselines** give a single triple, not a distribution; T and
   the leave-one-out scores are diagnostics, not inferential statistics.
3. **`decisions` is unavailable in most financial pairs** (answer-line
   grammar rarely matches free-form finals); the fixed-denominator union
   handles this, but it means financial BND rests mostly on
   assertions+actions+outcomes.
4. **`one_to_many` LEPs are structurally incomparable** against
   single-origin benign references in this dataset (1,940 execution-
   structure mismatches); they are reported, never forced.
5. **v2 join is by `attempt_id`**; 7,479 LEP traces are not in the v2
   ledger (different detector-suite population) and are marked
   `unmeasured_by_v2`, not assumed negative.

## Recommendations for a paper metric

BND works best as a *supplementary baseline-relative deviation index*
(stratified per LEP type and task family), not as a standalone anomaly
detector: its per-configuration threshold is a heuristic, its three-run
baseline is too small for p-values, and it intentionally does not answer
the attribution question the v2 detectors answer. A defensible paper
statement is: "a transparent, task-aware baseline-normalized distance
identifies which configurations deviate beyond observed benign spread
(35% of comparable LEP traces; 57% for TRC, 18% for HC), complementing
but not replacing attribution-based detection."
