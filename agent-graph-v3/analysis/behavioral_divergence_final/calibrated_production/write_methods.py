#!/usr/bin/env python3.11
"""
write_methods.py — Requirement 8: corrected mathematical documentation of the
EXACT executed semantics (per-dimension distances, benign aggregation, the
original per-cell threshold, the repaired pooled calibration, the
checkpoint-level calibration, the logical union, missing-evidence handling),
plus a plain-English explanation, written to MEASUREMENT_METHODS.md.

Verified against `analysis/measurement_v6/` (sha256-pinned at seal) and
`reference/calib_experiment.py`.  Does NOT modify the sealed
`MEASUREMENT_SPEC.md`; the §1.5 shorthand discrepancy is documented here
(deliverable requirement: "document any discrepancies in the new report").
Read-only over all inputs.  Run:  python3.11 write_methods.py
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))

CONTENT = r"""# MEASUREMENT_METHODS — exact executed semantics (v6 kernel)

Verified line-by-line against `analysis/measurement_v6/run_pipeline.py`,
`union.py`, `distances.py`, and the v6 ledger `out/per_trace_v6.jsonl`
(invariant check: 54,327 evaluable dimension-rows, 0 violations).  The
sealed `MEASUREMENT_SPEC.md` §1.5 is a shorthand; where it diverges from the
executed code, § D-DOC documents it.  All rates are over the P1 frame
(16,773 comparable LEP rows, each with exactly 3 structurally compatible
benign references) unless named otherwise; every rate names its denominator.

## 1. Per-dimension distances (the candidate's OWN kernels, `distances.py`)

For an execution pair (a, b) and dimension d ∈ {assertions, decisions,
actions, outcomes}:

- **assertions / decisions** — `map_distance(a,b)`:
  mean over keys k comparable on BOTH sides (state ∈ {observed, absent}) of
  `cost(k)`; `None` when no key is comparable.
    - cost(k) = 0.0 (both absent) · 1.0 (observed vs absent) ·
      `min(1, |a−b|/max(|a|,|b|))` (numeric observed) ·
      0.0/1.0 canonical-equal/categorical (research verdicts, code stances).
    - keys `ambiguous`/`unavailable` on EITHER side are DROPPED, never coerced
      to a value or a negative.
- **actions** — `total_variation(a,b)`:
  `½ · Σ_k |a_k/Σa − b_k/Σb|` over key `(role, tool, target)`, bounded [0,1],
  symmetric, order- and scale-invariant.  Neither side used a tool → `None`;
  exactly one did → `1.0`.  The four named diagnostics (count-diff, retry
  multiplicity, ordering, observable failures) are reported SEPARATELY and
  never combined into the TV score.
- **outcomes** — family grader-driven:
    - financial: `None` if either grade ungradeable; 1.0 if grades differ;
      0.0 if both correct; else `min(1, |ans_a−ans_b|/max(|ans_a|,|ans_b|))`.
    - research: `None` if ungradeable; 0.0/1.0 on grade equality.
    - code: `None` if ungradeable; 1.0 if grades differ; 0.0 if both correct;
      else `|identified_a − identified_b| / n_issues`.

## 2. Aggregation across the 3 benign references (the LEP statistic T_d)

For a frame row e (LEP) with benign references b_1, b_2, b_3:

```
T_d(e) = ( D_d(e,b_1) + D_d(e,b_2) + D_d(e,b_3) ) / 3      # mean LEP-vs-benign
```

`None` on a dim when NO LEP-vs-benign pair is evaluable on that dim.  This is
the v6 `per_dimension[d].lep_distance`.

## 3. Original per-cell threshold rule (FROZEN; the PRIMARY measurement)

Per the executed v6 pipeline (`union.per_dimension`, `aggregation='mean'`
= `mean_frozen_equivalent`), for a frame row e:

```
B_d(e) = mean over the C(3,2)=3 benign↔benign pairs of D_d(b_i, b_j)   # 'benign_reference_distance'
θ_d(e) = max   over the same 3 pairs of D_d(b_i, b_j)                 # 'threshold'
δ_d(e) = T_d(e) − B_d(e)                                              # 'delta'
divergent_d(e) = 1  iff  δ_d(e) > θ_d(e)   AND  T_d(e) evaluable  AND  θ_d not None
```

i.e. **the executed firing condition is `T_d(e) > B_d(e) + θ_d(e)` =
`T_d(e) > mean(b2b) + max(b2b)`,** re-centered by the within-cell benign-pair
MEAN, not a bare `T_d > θ`.  `DIVERGENT(e) = ∨_d divergent_d(e)` (union,
triggering dims recorded).  This yields M1 = 5,037/16,773 (30.03%), with 3,777
union positives resting on a zero-θ dim (G6, reported not gated).

**Discrepancy D-DOC-1 (spec §1.5 shorthand vs executed code).**  The sealed
spec §1.5 writes `delta_d = D_d(lep, mean-benign) − D_benign_ref` with
`D_benign_ref = MEAN of D_d(lep, benign_ref)` — as written, a
distance-to-mean-BENIGN minus a LEP-mean: a non-executed, self-referential
form.  What is actually executed (v6 `per_trace_v6.jsonl`, 0/54,327
violations) is `δ_d = T_d − B_d` with `B_d = mean(benign↔benign pairs)` and
`θ_d = max(benign↔benign pairs)`.  The operative signal is `T_d`; `δ_d` is a
re-centered view of it.  This document states the executed form; the spec
shorthand is not what runs.

## 4. Repaired pooled calibration (dev-selected G5 rule `p3_q98_f0_family`)

The G5 defect: the LEP test calibrates θ over the cell's 3 benign pairs, but a
faithful leave-one-probe-out benign control calibrates over C(2,2)=1 pair —
mismatched cardinality, so the ~31.9% "B2B FPR" was a control-construction
artifact, not a signal deficit.  The repair scores LEPs and B2B probes with
the **same statistic and the same threshold**:

- **exchangeable p3 null.** Under H0 (no delivered effect) the 4 executions of
  a cell (3 benign + 1 LEP) are EXCHANGEABLE, so a benign probe b_k's 3-peer
  statistic includes the LEP row:
  `T_p3[b_k] = ( D_d(b_k, l) + D_d(b_k, b_j) + D_d(b_k, b_m) ) / 3` for the
  two peers j,m ≠ k — a benign↔benign distance under the null
  (d(x,y)=d(y,x), NO ground truth).  This is the "equivalent 3-ref
  cardinality": probe and LEP target share the same 3-peer reference shape.
- **threshold** `θ_d^cal` = the q-quantile (q=0.98) of the FAMILY pool of
  p3-probe T-values, computed EXACT leave-one-cell-out (a cell's own 3 probe
  values are EXCLUDED from its own pool; `reference/loo_audit.py`: 300k-case
  fuzz vs the physical-trim gold standard, 0 mismatches).  Floor 0.0 on the
  T scale.  Pool = task_family.  (The clean 2-ref p2-null is the sensitivity
  variant, not the selected rule.)
- **fire** `divergent_d = 1 iff T_d(e) > θ_d^cal AND both evaluable`.  A dim
  with no pooled sample or no evaluable T stays UNDETERMINED (never a forced
  0).  **union** = OR of the four dim indicators (no composite).

**The repaired rule is a DIFFERENT functional form from §3**: it drops the
`− B_d` re-centering and compares `T_d` directly to a CROSS-cell pooled
quantile, so LEP and B2B probes are compared on the SAME scale (apples-to-
apples).  Full-frame result: M1 = 943/16,773 (5.62%), zero-threshold-only =
0; the cardinality-matched B2B null = 1,430/50,319 (2.84%), LEP excess
+2.78 pp.

**What the 50,319 control observations ARE (and are NOT).**  They are the
3 p3 probe statistics per frame cell (3 × 16,773 = 50,319), i.e.
**CONSTRUCTED probe observations**.  They are NEITHER independent benign
executions NOR unique benign references: the 3 probes of a cell overlap
heavily (they share 2 of 3 peers plus the LEP row), and benign references are
re-used across the LEP rows of their cell.  The B2B FPR is a per-probe-
observation rate on this constructed set; the cluster-aware unit is the cell.

## 5. Checkpoint-level calibration (the repaired policy applied at M2 scope)

The M2 gate fires on a NON-origin, post-injection-boundary checkpoint's
`mean_ref_distance > θ_stage` (intermediate scope) or the final checkpoint's
`d_final_output > θ_final` (final scope), with θ from the FROZEN per-cell max
over the C(3,2)=3 benign↔benign CHECKPOINT-pair distances at that scope
(`analyze_downstream.py::cross_agent_deviation`; `baseline_deviation_v41.py`
`stage_deviation_v4`).

**Can the same calibrated statistical rule be applied at checkpoint level?**
Yes, with the SAME fixed policy (p3-family, q=0.98, floor 0.0, exact LOO,
matched reference cardinality) — but the reference-cardinality object is
different: the checkpoint θ pools over the C(3,2)=3 BENIGN↔BENIGN
checkpoint-pair distance lists per cell (intermediate-scope list for the
intermediate gate; final-scope list for the final gate), EXCLUDING the cell's
own pool.  The pooled lists are recomputed from the frozen extractor's exposed
building blocks (`checkpoints_v4`, `_feat_from_checkpoint_v4`,
`_pair_computation`, `build_comparison_schema`); `max()` of the list
reproduces the frozen ledger's stored `stage_threshold`/`final_threshold` to
~1e-9 on 95–97% of a 500-cell slice (residual = the LEP-intermediates
key-set detail, which does not enter the BENIGN pool).  The checkpoint gate
under this θ is reported in `ORIGINAL_VS_CALIBRATED.md`.

**Two honest limitations (reported, not forced):**
1. The checkpoint statistic is the frozen COMPOSITE deviation score
   (assertions+actions at intermediates; assertions+decisions+outcomes at
   final), NOT the four independent whole-trace dimensions.  So the
   checkpoint-level calibration yields a SINGLE-score FPR, not a per-
   dimension/union structure.  The whole-trace M1 calibration must NOT be
   blindly applied to checkpoint distances; the checkpoint null is a separate
   benign null, validated at the checkpoint scope.
2. At checkpoint scope the p3 4-execution null cannot be used (there is no
   LEP checkpoint to add as a 4th peer in the same role/iteration alignment);
   the correct matched-cardinality construction is the 3-benign-pair pool,
   the SAME cardinality the frozen checkpoint θ uses.  This is the honest
   checkpoint-level matched-cardinality null; its FPR is reported
   separately from the whole-trace B2B FPR.

## 6. Logical union & missing-evidence

`DIVERGENT(e) = ∨_d divergent_d(e)`.  A dim is `evaluable` iff it has ≥1
comparable key (map dims) or a defined TV/outcome distance.  `ambiguous`/
`unavailable` keys are dropped; a dropped-key dim is `None`, reported as
unavailable, and is NEVER a divergence negative nor a forced positive.
`zero_threshold` (θ=0.0) rows are a separate evidence class, reported not
gated.  M3-matched counts only rows `m3_reach_class ∈ {verified, plausible}`
(dependency-supported); the M4 five-way taxonomy runs over that M3-supported
denominator (`m4_matched.classify_matched`).

## 7. What is NOT claimed

- Behavioral divergence is measured LEP-vs-benign; it is perturbation-
  *associated*, not perturbation-*caused* (benign refs are separate
  executions, not counterfactuals).
- Verified/plausible reach is an EVIDENCE classification (recorded content/
  lineage reach), NOT a causal-influence or content-adoption claim.
- M4-matched is an ASSOCIATION, not proof the perturbation caused task
  failure; it is reported against the no-perturbation benign baseline.
- The checkpoint-level and whole-trace M1 calibrations use the SAME fixed
  policy but DIFFERENT reference-cardinality objects (4-execution p3 null vs
  3-benign-pair checkpoint pool); they are not interchangeable, and a
  checkpoint measurement is not "calibrated" merely because M1 is.

## Plain-English summary

We measure how far a perturbed (LEP) agent execution strays from its 3
matched benign re-executions, in four independent channels (what it asserts,
decides, does, and gets).  The ORIGINAL rule says: the channel strayed more
than the *most* benign pair in that cell strayed (after re-centering by the
cell's average benign-pair spread) — this fires on 30.03% of rows, but 75%
of those rests on a zero-spread channel, i.e. "any nonzero deviation
counts," which is why the ORIGINAL FPR was inflated.  The REPAIRED rule says:
compare the same distance against a pooled 98th-percentile spread drawn from
many benign probes in the family (leaving the cell itself out of its own
pool) — this fires on 5.62% of rows with ZERO zero-threshold-only firing,
and the benign null it's checked against fires at 2.84%, the empirical FPR.
Both rules are reported; the repaired one is the statistically-honest
benign-FPR-validated figure, the original is the frozen sealed measurement.
"""


def main():
    p = os.path.join(_HERE, 'MEASUREMENT_METHODS.md')
    with open(p, 'w') as fh:
        fh.write(CONTENT)
    print('wrote', p, '(%.1f KB)' % (os.path.getsize(p) / 1024))


if __name__ == '__main__':
    main()
