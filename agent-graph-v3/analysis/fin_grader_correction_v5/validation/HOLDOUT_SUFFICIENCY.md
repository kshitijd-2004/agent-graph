# Held-Out Sufficiency Verdict

**Question posed:** is the sealed held-out sample sufficient for the stated
acceptance criteria?

**Verdict: YES for the global gates (n=775, power ≥ 97% at every threshold),
PARTIALLY for the per-class gates — three rare classes are underpowered and
their gates are pre-relaxed in `FINAL_PROTOCOL.md §3`. Annotation may begin on
that basis, with the decision on whether to re-draw the split for n≥60/class
rare-class certification made NOW, before any annotation.**

## Evidence (all in `verification/gate_power.json`, reproducible)

Sealed acceptance population = **775 unique LEP executions across 51 fixtures**
(frozen grade mix 133 correct / 77 failure / 565 not-gradeable; the 3 duplicate
IDs excluded per dedup invariant C6).

### 1. Global gates — SUFFICIENT
One-sided 95% Wilson upper bound vs threshold; "kmax" = observed errors that
still pass:

| gate | n | kmax | power at threshold |
|---|---:|---:|---|
| false-correct ≤ 5% | 775 (pre-est. achieved 350–565) | 26 | 98.3% @5%, 98.7% @2% |
| false-incorrect ≤ 15% | 775 | 96 | 97.9% @15% |
| false-ungradeable ≤ 10% | 775 | 61 | 97.5% @10% |
| gradeability accuracy ≥ 90% (i.e. errors ≤ 10%) | 775 | 61 | 97.5% |

Even at the most conservative achieved denominator for G1 (n≈350, if the
corrector recovers only strict-A rows), kmax(5%) = 11 and power stays ≥ 97%.
The 775-based power figures are the upper-bound case; both bounds are reported
in the acceptance report.

### 2. Per-class gates — PARTIALLY SUFFICIENT

| class | sealed n | UB(95%) if 0 errors | ≥95% accuracy certifiable? |
|---|---:|---:|---|
| scale/unit | 105 | 3.5% | **YES** |
| multi-candidate/ambiguity | 564 | 0.7% | **YES** |
| sign | 44 | 8.0% | NO — needs n≥60 (rule of three: 3/0.05) |
| TRC-value-rejection | 34 | 10.2% | NO — needs n≥60 |
| omission detection | 20 | 16.1% | NO — needs n≥60 |

Structural causes (not fixable by resampling within the current split):
- sign: the 20 negative-GT fixtures cap the class at 300 executions total;
  the sealed set took 44 (3 of the 20 fixtures).
- TRC adoption: 177 executions exist, but adoption concentrates in specific
  fixture×LEP cells; sealed set took 34.
- omission: 155 executions exist (value-only-in-tools + no-number finals);
  sealed set took 20.

**Options (decide before annotation):**
- **(a) Accept the relaxed class gates as pre-registered** (UB ≤ 15% for the
  three rare classes; global G2/G3 already bound their M4 impact).
- **(b) Re-draw the fixture split now** to guarantee ≥ 60 sign / ≥ 60 TRC /
  ≥ 60 omission rows in the sealed set (requires: sign ≥ 5 negative-GT
  fixtures fully sealed — feasible, 20 exist; TRC/omission need
  fixture-concentration-aware sealed selection — feasible but shrinks the
  remaining dev CV pool from 249 to ~210 fixtures, 5×42 folds; still adequate
  for rule tuning). Cost: the split, not just the tasks, must be re-locked;
  `split_manifest.json` gets v2.
- **Recommendation: (a).** The rare-class errors are a *subset* of the G2
  denominator, so global gates already cap their aggregate effect; spending
  239 dev fixtures to certify a 95% per-rare-class claim that will not
  change any M1–M4 headline is not the highest-value use of annotation
  budget. The publication text can state "sign/TRC/omission class errors
  bounded at 15% (95% one-sided), aggregate bound via global G2 ≤ 15%."

### 3. What the sealed set cannot support (do not claim)
- Population proportions (quota/seeded sampling, not probability sampling).
- ≥95% certification for the three rare classes (option a).
- M1/M2 accuracy claims (C6: those remain frozen-ledger-based; this set
  validates the answer grader only).

## Discrepancy note (feeds §0 of FINAL_PROTOCOL)
3,130 ledger NG rows = 3,128 unique trace IDs + 2 duplicated NG rows
(`fin_finqa_020_ilmn_2007.bv…TRC.many_to_one`, `fin_finqa_280_lly_2008.rl…IPI.single_origin`).
Per-bucket counts differ from the prior audit only by those two rows
(ilmn→B_SCALE, lly→A_STRICT; ledger view reproduces 1,514/358 exactly).
`verification/two_row_discrepancy.json` records both views.
