# V4.4 pre-freeze threshold-sensitivity sweep (report — no selection)

Lightweight sweep on **cached V4.4 artifacts only** (`bnd_v44_per_trace.jsonl`,
`checkpoint_pairs_v44.jsonl`, the calibrated LEP-free nulls in `m44.py`).
No extractor/representation change, no new statistical method, no reference-
label change, no trace re-read. The knobs moved are decision-point-only:
the self-cell threshold shrink `c` (continuous dims) and the discrete null
level `α` (decisions/outcomes) on M1; the family-pool quantile `q` and `α`
on M2. **V44E is preserved as the baseline** (dev_eval_v44.json untouched:
P 0.812 / R 0.667 / F1 0.732); M1-A below is V44E's exact point (c=1.0,
α=0.05) and reproduces it byte-for-byte (TP56/FP13/FN28), and M2-A
reproduces the cached production gate (2,312/16,767).

## M1 candidates — round-2 dev P/R/F1, execution-level benign FPR, M1 incidence

Execution-level benign FPR = the LEP-free 4th-exchangeable-benign probe
row-OR under the SAME decision procedure the candidate uses (fixture-cluster
CI over 903 fixtures; at c=1.0 the continuous channel's 0-firing property is
verified, not assumed). This is the control matched to each candidate's gate
form. (The dev_eval's S2 object — 9.38% at the cross-fixture q98 pooled
threshold, the *stricter* control that scores every dim on the family pool —
is reported alongside for the baseline; the two are different objects and
are never conflated.)

| cand | form | P | R | F1 | dev FP | **exec-level benign FPR** (CI) | M1 incidence |
|---|---|---|---|---|---|---|---|
| **M1-A = V44E baseline** | c=1.0, α=.05 | 0.812 | 0.667 | 0.732 | 13 | **2.43%** ([1.8, 3.1]) | 30.9% |
| M1-E | c=1.0, α=.10 | 0.819 | **0.702** | 0.756 | 13 | **4.96%** ([4.1, 5.9]) | 33.9% |
| M1-B | c=.75, α=.05 | 0.803 | **0.726** | 0.762 | 15 | **2.43%** ([1.8, 3.1]) | 32.7% |
| M1-C | c=.50, α=.05 | 0.797 | **0.750** | 0.773 | 16 | **2.43%** ([1.8, 3.1]) | 35.7% |
| M1-F | c=.50, α=.10 | 0.800 | **0.762** | 0.780 | 16 | **4.96%** ([4.1, 5.9]) | 38.0% |
| M1-G | c=.50, α=.25 | 0.714 | 0.774 | 0.743 | 26 | 8.48% ([7.2, 9.8]) | 40.5% |
| M1-D | c=.25, α=.05 | 0.503 | 0.893 | 0.644 | 74 | 53.80% | 68.0% |
| M1-H | c=.25, α=.25 | 0.507 | 0.905 | 0.650 | 74 | 54.66% | 68.8% |

(dev FPs are the imperfect-label 137-surface, reported not conflated with the
benign object.)

## M2 candidates — production incidence + B2B controls (no dev P/R/F1: M2 has
no reference-labeled surface; judged on incidence and its matched controls)

| cand | form | M2+ (rate) | inter/final | probe-obs FPR | B2B exec row-OR | M3 (v/p) | M4 |
|---|---|---|---|---|---|---|---|
| **M2-A = baseline gate** | q98, α=.05 | **2,312 (13.79%)** | 584/1,826 | 1.16% | 6.70% | 781 (178/603) | 267/781 |
| M2-B | q95, α=.05 | 2,946 (17.57%) | 1,110/1,973 | 3.82% | 16.37% | 896 | 276/896 |
| M2-F | q98, α=.25 | 4,627 (27.60%) | 584/4,176 | 1.16% | 11.60% | 1,029 | 320/1029 |
| M2-C | q90, α=.10 | 7,148 (42.63%) | 3,727/4,328 | 8.42% | 30.05% | 1,411 | 386/1,411 |
| M2-D | q80, α=.10 | 9,964 (59.43%) | 5,463/6,499 | 15.59% | 44.12% | 1,582 | 412/1,582 |
| M2-E | q80, α=.25 | 9,967 (59.44%) | 5,463/6,523 | 15.59% | 44.20% | 1,582 | 412/1,582 |

M2 loosening only *adds* incidence while worsening its B2B execution row-OR
(6.7% → 16–44%); none is specificity-neutral. The q98/α=.05 baseline is the
only M2 candidate with a defensible B2B control.

## Constraints

**An operating point achieving P≥0.70, R≥0.70, F1≥0.65 without
substantially worsening benign specificity: YES — one exists, and it is the
minimal-movement one.**

- **M1-E (c=1.0, α=.10)** keeps V44E's self-cell gate form on the continuous
  dimensions *exactly* and only raises the discrete null level α from .05 to
  .10 (the recorded-integer null on its own family pool — a threshold knob on
  an existing calibrated null, not a new statistic). Result: P 0.819,
  R 0.702, F1 0.756 — all three dev constraints clear — with execution-level
  benign FPR **4.96%, inside the ≤5% target** (fixture-cluster CI
  [4.1%, 5.9%]; the CI straddles 5%: at the point estimate it passes, with
  the uncertainty stated).
- The c<1.0 family does the same job with *more* FPR headroom but less
  precision: M1-B (c=.75) 0.803/0.726/0.762 @ 2.43%; M1-C (c=.50)
  0.797/0.750/0.773 @ 2.43%; M1-F (c=.50, α=.10) 0.800/0.762/0.780 @ 4.96%.
  All meet the three dev constraints AND the 5% target.

**Pareto frontier** (no candidate dominates another on P/R/exec-FPR): the
frontier is the ordered chain M1-D → M1-H → M1-G → M1-C → M1-F → M1-B →
M1-A → M1-E — each step trades recall for precision and FPR. The
constraint-passing knee of the frontier is **M1-B/C/F/E**.

**Candidates failing the 5% FPR target (explicit):**
- M1-G (c=.50, α=.25): 8.48% — fails even though P/R/F1 pass;
- M1-D, M1-H (c=.25): ~54% — the shrink collapses specificity (any in-cell
  variance fires the continuous channels); fail the dev constraints too;
- every M2 candidate except M2-A: B2B row-OR 11.6–44% (M2-F 11.6%, M2-B
  16.4%, M2-C 30.1%, M2-D/E ~44%);
- the baseline V44E/M1-A itself: 2.43% on its own matched control (passes),
  but recall 0.667 fails the dev constraint it was held to.

## What this changes vs the earlier V4.4 verdict

The frozen dev_eval's "no candidate satisfies all dev constraints /
specificity not supported" verdict was a product of holding α at 0.05 and
c at 1.0 in the same object (V44E). This sweep — decision-point moves only
on the same calibrated nulls — shows the constraint region is reachable:
**α=.10 (or c=.75/c=.50 with α=.05) clears P≥0.70, R≥0.70, F1≥0.65 at an
execution-level benign FPR ≤5%** (4.96% at the two α-moves; 2.43% at the
c-moves). The 5%-CI caveat stands for M1-E/F (CI [4.1%, 5.9%] straddles 5%);
M1-B/C sit comfortably inside (2.43%, CI [1.8%, 3.1%]). The cross-fixture
q98 pooled-threshold control (the stricter S2 object, 9.38%) still does not
support 5% — so "≤5% supported" holds under the candidate's own matched
self-cell control, not under the pooled-family control; both are reported
above, never conflated.

No operating point is selected or frozen here — the candidates and their
evidence are reported for the user's decision.
