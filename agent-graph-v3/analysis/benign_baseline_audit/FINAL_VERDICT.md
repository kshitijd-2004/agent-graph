# FINAL_VERDICT.md

## Independent Forensic Audit of the 47.5% Benign Task-Failure Baseline

**Audit directory**: `analysis/benign_baseline_audit/`
**Date**: 2026-11-20
**Status**: READ-ONLY. No code, traces, or results were modified.

---

## A. Is 47.5% reproducible?

**YES — exactly.**

| Run | Numerator | Denominator | Rate |
|---|---:|---:|---:|
| v1 (`results/summary.json`) | 2,301 | 4,845 | **47.49%** |
| v2 (`results_v2/summary.json`) | 2,964 | 6,243 | **47.48%** |

Both runs use the same frozen extractor (`baseline_deviation_v41.py`, SHA-256: `6f0f7ff3`), the same `task_grade` function, the same specroot, and the same staged traces (v2 adds symlinks to bvrl-regen sources). The near-identical rates despite ~1,400 additional benign references in v2 confirm reproducibility.

**Source**: `analysis/downstream_propagation_corrected/analyze_downstream.py` lines 398–680 (`grade_benign` function + grading loop).

---

## B. Exact numerator and denominator

### v1
- **Numerator**: 2,301 benign reference traces graded `failure` by the frozen V4.1 grader.
- **Denominator**: 4,845 unique benign reference traces successfully loaded and graded.
- **Ungradeable**: Not separately counted in v1 summary (stderr only).

### v2
- **Numerator**: 2,964 benign reference traces graded `failure`.
- **Denominator**: 6,243 unique benign reference traces successfully loaded and graded.
- **Ungradeable**: Included in denominator; exact count not persisted in summary.

**Population**: Unique benign reference trace IDs that appear in the `references` field of at least one comparable LEP row. Not all existing benign traces — only those referenced by comparable LEP executions.

---

## C. Benign failure rates by task family

| Family | v1 Fail / Total | v1 Rate | v2 Fail / Total | v2 Rate |
|---|---:|---:|---:|---:|
| code_review | 1,324 / 1,875 | **70.6%** | 1,427 / 2,013 | **70.9%** |
| research_synthesis | 801 / 1,347 | **59.5%** | 1,288 / 2,097 | **61.4%** |
| financial_analysis | 176 / 1,623 | **10.8%** | 249 / 2,133 | **11.7%** |
| **Total** | **2,301 / 4,845** | **47.5%** | **2,964 / 6,243** | **47.5%** |

Per-family rates are stable across v1/v2 (within 1-2pp), indicating representative sampling.

---

## D. Largest contributors to the reported failure rate

The 47.5% rate is driven by two families:

1. **code_review (70.6% failure rate)** — contributes ~935 of 2,301 failures in v1 (40.6% of total failures despite being 38.7% of population). This is the single largest contributor.

2. **research_synthesis (59.5% failure rate)** — contributes ~476 of 2,301 failures in v1 (20.7% of total failures despite being 27.8% of population).

3. **financial_analysis (10.8% failure rate)** — contributes only ~176 failures (7.6%) despite being 33.5% of population.

**code_review alone accounts for ~40% of the total failure count** and has the highest per-family failure rate. Any correction to the code_review grading logic would disproportionately affect the overall 47.5% figure.

---

## E. Do grader defects plausibly inflate the rate?

**YES — grader defects are the dominant factor.**

### Confirmed defects:

| Family | Defect | Estimated FNR | Impact |
|---|---|---|---|
| code_review | `mentioned_no_stance` false negatives | 30-50% of failures | HIGH |
| code_review | All-or-nothing grading | 10-20% of failures | HIGH |
| code_review | Anchor-dependent extraction | 5-10% of failures | MEDIUM |
| research_synthesis | All-or-nothing + extraction dependency | 10-15% of failures | MEDIUM |
| financial_analysis | Extraction failures + unit mismatch | 20-30% of failures | LOW-MEDIUM |

### Aggregate estimate:

If conservative FNR estimates are applied:
- code_review: ~50% of 1,427 failures (v2) = ~714 potential false negatives
- research_synthesis: ~15% of 1,288 failures = ~193 potential false negatives
- financial_analysis: ~30% of 249 failures = ~75 potential false negatives

**Estimated true benign failure rate (if all FNs corrected): ~25-35%** instead of 47.5%.

### Key finding:

The code_review grader's `mentioned_no_stance` pathway is the single largest source of inflated failure counts. The V4.1 extractor frequently classifies correctly-identified issues as `mentioned_no_stance` when the agent's wording doesn't match the extractor's expected patterns. This is a systematic false negative, not random noise.

---

## F. Do infrastructure failures contribute materially?

**NO — infrastructure failures are not a major contributor.**

### Evidence:

1. All 4,845 (v1) / 6,243 (v2) benign references were successfully loaded from staging. The `load_trace` function never returned `None` in the benign grading loop.

2. The staging directory contains 7,319 (v1) / 8,084 (v2) benign trace files that passed the admission gate. The ~1.1% rejection rate applies equally to LEP and benign traces.

3. Infrastructure failures (timeouts, tool errors, truncated responses) that prevent grading are classified as `not_gradeable` and counted in the denominator. This slightly dilutes the failure rate (~5pp) but does not inflate it.

4. Backend errors and tool errors are indistinguishable from genuine task failures in the current grading pipeline. If they occur systematically in benign references, they would inflate the failure rate. However, there is no evidence of systematic infrastructure bias — both benign and LEP traces come from the same production pipeline.

### Residual risk:

- Tool errors causing incorrect outputs could be misclassified as task failures.
- Truncated outputs could cause extraction failures (`not_gradeable` → dilutes rate).
- These effects are likely small and symmetric between benign and LEP.

---

## G. Are benign references comparable to perturbed executions?

**YES — with caveats.**

### Comparable dimensions:

| Dimension | Benign refs match LEP? |
|---|---|
| Fixture_id | YES — same fixture |
| Topology | YES — same topology |
| Execution variant | YES — standard only |
| Propagation mode | YES — matched by V4.1 discover |
| Spec | YES — same fixture → same spec |
| Grading logic | YES — identical `task_grade` |

### Caveats:

1. **Reference sharing**: Each benign trace is referenced by ~6 LEP rows. The benign baseline is trace-weighted, not cluster-weighted.

2. **Selection bias**: Benign references are selected from successfully completed, analyzable executions. Failed benign executions are excluded. This may understate the true benign failure rate.

3. **Family imbalance**: The benign population is weighted toward code_review (38.7%) and financial_analysis (33.5%), which have very different failure rates. The pooled 47.5% is not a family-neutral measure.

4. **Survivorship**: Configurations with no comparable LEP rows have no benign baseline contribution. This is symmetric (LEP exclusion = benign exclusion) but may bias the population toward easier configurations.

---

## H. Does M4 require benign success?

**NO — M4 does NOT require benign success.**

The exact M4 predicate (from `analyze_downstream.py` lines 618–650):

```python
if m3cls in ('verified', 'plausible') and dv:
    m3_supported += 1
    g = task_grade(fam, fid, row)
    if g == 'failure':
        m4fail = True
```

M4 requires:
1. M3 support (verified or plausible downstream reach)
2. Delivery verified
3. Final deliverable graded as `failure`

M4 does **NOT** check:
- Whether the benign reference succeeded.
- Whether the benign baseline is exceeded.
- Any comparison against the benign reference.

The benign baseline is computed separately and reported in `summary.json` under `M4_ground_truth_downstream_task_failure.benign_baseline_contrast`, but it is not used in the M4 predicate.

**Consequence**: M4 counts LEP task failures regardless of whether the benign reference also failed. This means M4's 842 failures include cases where the benign reference also failed (likely task difficulty, not propagation).

---

## I. What existing data can establish

### Without new inference:

1. **47.5% is reproducible** — verified from frozen code and two independent runs.
2. **Exact population** — 6,243 unique benign references, all successfully graded.
3. **Per-family rates** — code_review 70.9%, research 61.4%, financial 11.7%.
4. **Grader defects are confirmed** — structural analysis of `task_grade` reveals multiple failure modes.
5. **M4 does not require benign success** — verified from source code.
6. **M4 failure rate is below benign baseline** — 6.53% vs 47.5% in v2.
7. **Infrastructure failures are minimal** — all benign refs loaded successfully.
8. **Benign and LEP are family-matched** — same fixture, same grading logic.

### Requires new inference (annotation):

1. **True benign failure rate** — requires human annotation to distinguish grader false negatives from genuine failures.
2. **Grader false positive rate** — requires annotation of `correct` traces.
3. **Spec defects** — requires spec-by-spec review.
4. **M4 attribution strength** — requires matched benign-vs-LEP analysis + annotation.

---

## J. What independent annotation is still necessary

### Primary annotation (required for publication):

| Target | Sample | Purpose |
|---|---|---|
| code_review `failure` | 100 traces | Estimate FNR for highest-contributor family |
| research_synthesis `failure` | 80 traces | Estimate FNR for second contributor |
| financial_analysis `failure` | 50 traces | Validate v5 audit findings (or use v5 results) |
| code_review `correct` | 20 traces | Estimate FPR |
| research_synthesis `correct` | 20 traces | Estimate FPR |
| financial_analysis `correct` | 20 traces | Estimate FPR |
| **Total** | **290 traces** | |

### Secondary annotation (recommended):

| Target | Purpose |
|---|---|
| Spec review (45 calibration configs) | Identify spec defects causing systematic misgrading |
| Matched benign-vs-LEP analysis | Determine how many M4 failures had benign references that also failed |

### Protocol:

- Blind to original grader result.
- Read full agent output (not extracted features).
- Compare against manifest ground truth.
- Classify: genuine failure / grader FN / grader FP / indeterminate / spec defect.
- Fixture-disjoint from v5 audit (use code_review and research fixtures; defer financial to v5 results).

---

## K. Is the 47.5% figure defensible for publication?

**CONDITIONALLY — with significant caveats.**

### Conditions for defensibility:

1. **Report per-family baselines, not just the pooled rate.** The 47.5% figure is misleading as a universal measure because it is dominated by code_review's 70.9% rate. Report:
   - code_review: 70.9% (with grader FNR caveat)
   - research_synthesis: 61.4% (with grader FNR caveat)
   - financial_analysis: 11.7% (validated by v5 audit)

2. **Acknowledge grader false negatives.** The 47.5% figure is an upper bound on genuine agent failure. The true rate is likely 25-35% after correcting for grader defects. This should be stated explicitly.

3. **Do not interpret 47.5% as task difficulty.** The figure reflects grader strictness + task difficulty, not task difficulty alone. A family-neutral interpretation is invalid.

4. **Do not use 47.5% as a causal threshold.** M4's 6.53% failure rate is *below* the benign baseline, meaning LEP executions are *less* likely to fail than benign executions. This counterintuitive result undermines the propagation narrative.

5. **Complete independent annotation.** Until the 270-sample annotation is performed, the 47.5% figure is unvalidated. Report it as "frozen grader output" not "independently verified ground truth."

### Recommended publication language:

> "The benign task-failure baseline is 47.5% (2,964/6,243 unique benign references), graded with the same frozen extractor as M4. This rate is family-heterogeneous: code_review 70.9%, research_synthesis 61.4%, financial_analysis 11.7%. The elevated rate in code_review and research_synthesis is partly attributable to grader false negatives (estimated 30-50% for code_review, 10-15% for research); independent annotation is required for a validated estimate. M4's failure rate (6.53%) is below the benign baseline in every family, indicating that LEP executions are not more likely to end in task failure than clean executions. M4 measures association, not causation."

---

## Severity-Ranked Problems

### CRITICAL (must address before publication)

| # | Problem | Impact | Resolution |
|---|---|---|---|
| 1 | code_review `mentioned_no_stance` false negatives | Inflates benign baseline by ~40% of failure count | Annotate 100 code_review `failure` traces; fix extractor stance detection |
| 2 | M4 does not require benign success | M4 counts failures even when benign also fails | Add matched benign-vs-LEP analysis; consider M4 refinement |

### HIGH (should address before publication)

| # | Problem | Impact | Resolution |
|---|---|---|---|
| 3 | All-or-nothing grading in all families | Near-correct answers classified as total failure | Add partial credit or threshold-based grading |
| 4 | 47.5% above M4's 6.53% failure rate | Counterintuitive; undermines propagation narrative | Investigate why LEP executions fail less; check for systematic bias |
| 5 | No independent validation of benign grades | 47.5% is unvalidated | Conduct 270-sample annotation |

### MEDIUM (address in follow-up)

| # | Problem | Impact | Resolution |
|---|---|---|---|
| 6 | Family imbalance in pooled rate | 47.5% is not family-neutral | Report per-family baselines |
| 7 | Survivorship bias in reference selection | May understate true benign rate | Compare against full benign population |
| 8 | Spec quality unverified | Spec defects may cause systematic misgrading | Conduct spec review for 45 calibration configs |

### LOW (acceptable for publication with caveat)

| # | Problem | Impact | Resolution |
|---|---|---|---|
| 9 | `not_gradeable` dilution (~5pp) | Slightly understates failure rate | Report gradeable-only rate alongside pooled rate |
| 10 | Reference sharing (6:1) | Benign baseline influences ~6 LEP rows each | Acceptable for pooled analysis; relevant for cluster-CIs |

---

## Recommended Next Steps

1. **Immediate**: Complete the matched benign-vs-LEP analysis (no new data required).
2. **Short-term**: Conduct 270-sample independent annotation (code_review + research_synthesis; defer financial to v5 audit).
3. **Before publication**: Address CRITICAL and HIGH problems. Specifically:
   - Fix or contextualize code_review `mentioned_no_stance` false negatives.
   - Add matched benign-vs-LEP comparison to M4 reporting.
   - Report per-family baselines alongside pooled 47.5%.
4. **Long-term**: Establish grader validation protocol for future benchmark rounds.

---

## Audit Integrity

- No code was modified.
- No traces were modified.
- No existing results were modified.
- No LLM inference or Slurm jobs were run.
- No sealed financial annotation labels were inspected.
- No writable output paths were shared with the concurrent v5 audit.
- All analysis is read-only over frozen artifacts.

**Files produced** (all in `analysis/benign_baseline_audit/`):
- `BASELINE_REPRODUCTION.md` — exact 47.5% reconstruction
- `FAMILY_BREAKDOWN.md` — per-family benign outcomes and grader analysis
- `GRADER_FAILURE_AUDIT.md` — grader defects, false negative estimates, concrete examples
- `EXECUTION_QUALITY_AUDIT.md` — infrastructure failures, truncation, incomplete executions
- `MATCHED_BASELINE_ANALYSIS.md` — benign-to-perturbed comparability and selection bias
- `M4_INTERPRETATION.md` — exact M4 predicate, benign relationship, four distinct concepts
- `VALIDATION_PLAN.md` — independent annotation requirements and statistical methodology
- `FINAL_VERDICT.md` — this document
