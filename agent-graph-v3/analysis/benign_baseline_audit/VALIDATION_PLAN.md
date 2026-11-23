# VALIDATION_PLAN.md

## 1. Purpose

This plan specifies the independent annotation required to validate the benign task-failure baseline (47.5%) and the grader's classification decisions. The goal is to estimate:
- False negative rate (FN): benign traces graded `failure` that are actually `correct`
- False positive rate (FP): benign traces graded `correct` that are actually `failure`
- Grader reliability vs. ground truth

## 2. What Cannot Be Determined Without Annotation

| Question | Existing data sufficient? | Requires annotation? |
|---|---|---|
| Is 47.5% reproducible? | YES — verified from frozen code | NO |
| What is the exact denominator? | YES — 6,243 unique benign refs | NO |
| What are the per-family rates? | YES — 70.6% / 59.5% / 10.8% | NO |
| Do grader defects inflate the rate? | PARTIAL — mechanisms identified | YES — quantify |
| Are infrastructure failures present? | PARTIAL — load failures tracked | YES — characterize |
| Is M4's attribution valid? | NO — requires matched analysis | YES — see below |
| What is the true benign failure rate? | NO — grader may be wrong | YES — primary goal |

## 3. Annotation Requirements

### 3.1 Primary annotation: Benign task quality

**Goal**: Determine whether `task_grade == 'failure'` benign traces represent genuine agent failures or grader false negatives.

**Sample design**:
- Stratified by family and grade outcome.
- Oversample `failure` grades (the class of interest).
- Include a control sample of `correct` grades to estimate false positives.

| Stratum | Population | Sample size | Rationale |
|---|---|---|---|
| code_review `failure` | ~1,427 | 100 | Highest failure rate, highest uncertainty |
| research_synthesis `failure` | ~1,288 | 80 | Medium failure rate, medium uncertainty |
| financial_analysis `failure` | ~249 | 50 | Lowest failure rate, most certain |
| code_review `correct` | ~586 | 20 | False positive check |
| research_synthesis `correct` | ~809 | 20 | False positive check |
| financial_analysis `correct` | ~1,884 | 20 | False positive check |
| **Total** | | **290** | |

**Annotation protocol**:
1. **Blind annotation**: Annotators do not see the original grader result.
2. **Read raw output**: Annotators read the agent's full output text (not just extracted features).
3. **Compare against spec**: Annotators read the fixture's manifest ground truth.
4. **Classify**: genuine failure / grader false negative / grader false positive / indeterminate / spec defect.
5. **Document reason**: For each classification, record the specific reason (e.g., "agent identified issue but used different terminology").

### 3.2 Secondary annotation: Spec quality

**Goal**: Identify spec defects that cause systematic misgrading.

**Sample design**:
- One manifest per fixture for all 45 calibration configurations.
- Focus on `required_issues` (code_review) and `required_facts` (research, financial).

**Annotation protocol**:
1. Read each manifest's `required_issues` or `required_facts`.
2. Verify that each required item is:
   - Actually present in the fixture's source code or documents.
   - Unambiguously stated in the ground truth.
   - Gradable from agent output (i.e., the expected answer can be extracted from reasonable output).
3. Flag items that are:
   - Missing from the fixture.
   - Ambiguously specified.
   - Ungradeable (no clear correct answer).

### 3.3 Tertiary annotation: Financial v5 coordination

The concurrent v5 audit is focused on financial grading defects. This audit must NOT:
- Use sealed financial annotation labels.
- Modify the v5 audit's samples or conclusions.
- Share writable output paths.

**Coordination protocol**:
- The v5 audit's findings on financial grader defects are treated as **provisional sensitivity evidence**.
- This audit references v5 results where relevant but does not duplicate them.
- If the v5 audit identifies financial grader defects, this audit incorporates them as confirmed findings (with attribution).
- If the v5 audit's findings conflict with this audit's analysis, both are reported with the conflict noted.

## 4. Statistical Methodology

### 4.1 Confidence intervals

For the false negative rate (FNR) and false positive rate (FPR), compute Wilson score intervals:

```
Wilson(k, n, z=1.96):
    p = k / n
    den = 1 + z^2 / n
    c = (p + z^2 / (2*n)) / den
    m = z * sqrt((p*(1-p) + z^2/(4*n)) / n) / den
    return (max(0, c-m), min(1, c+m))
```

Report 95% CIs for each stratum and for the overall rate (pooled across strata).

### 4.2 Corrected baseline estimate

From the annotation sample, estimate the true benign failure rate:

```
corrected_rate = (annotated_failures - annotated_false_negatives) / total_annotated
```

Compute 95% CI using Wilson interval.

### 4.3 Minimum detectable effect

With n=290 annotations:
- For FNR estimation: ±5.8% precision at 95% CI (assuming p=0.5).
- For FPR estimation: ±5.8% precision at 95% CI (assuming p=0.5).
- For overall rate correction: ±3.4% precision at 95% CI (assuming p=0.47).

## 5. Fixture-Disjoint Strategy

### 5.1 Avoid v5 audit overlap

The v5 audit uses a specific set of financial fixtures for its annotation. This audit must select disjoint fixtures:

| Audit | Fixtures used |
|---|---|
| v5 (financial) | Sealed — do not inspect |
| This audit (benign) | Use non-financial fixtures for primary annotation; use v5-published results for financial |

### 5.2 Recommended approach

1. **Primary annotation**: Use code_review and research_synthesis fixtures (no overlap with v5).
2. **Financial inference**: Reference v5 audit's published findings on financial grader defects. Do not independently annotate financial fixtures.
3. **Cross-family validation**: If v5 publishes corrected financial grades, incorporate them into the overall baseline estimate.

## 6. Practical Validation Sample

### 6.1 Recommended sample sizes (revised)

| Family | Grade | Sample | Rationale |
|---|---|---|---|
| code_review | failure | 100 | Highest uncertainty, highest contribution |
| code_review | correct | 20 | False positive check |
| research_synthesis | failure | 80 | Medium uncertainty |
| research_synthesis | correct | 20 | False positive check |
| financial_analysis | failure | 50 | Lowest uncertainty, but use v5 results |
| **Total** | | **270** | |

### 6.2 Sampling method

1. Randomly sample from the `failure` and `correct` strata within each family.
2. Stratify by fixture to ensure coverage across different task specifications.
3. For code_review, include fixtures from all three topologies (bv, coord, rl).

## 7. Acceptance Criteria

### 7.1 For the baseline figure

The 47.5% figure is **defensible for publication** if:

1. The false negative rate for code_review is <30% (currently estimated 30-50%).
2. The false negative rate for research_synthesis is <15% (currently estimated 10-15%).
3. The false positive rate for all families is <10%.
4. The corrected baseline (after FNR adjustment) is within ±5pp of 47.5%.

If the FNR is >30% for code_review, the 47.5% figure overstates genuine failures and should be corrected or contextualized.

### 7.2 For M4 attribution

M4's attribution claims are **defensible for publication** if:

1. A matched benign-vs-LEP analysis shows that M4 failures exceed benign failures for the same configuration.
2. The benign baseline is validated (not purely grader artifact).
3. M4 is reported as "association" not "causation" — the current report language is appropriate.

## 8. Uncertainty Reporting

### 8.1 What to report

- Point estimates with 95% CIs for all rates.
- Stratum-specific CIs (by family, by grade).
- Sensitivity analysis: what if FNR is at the upper/lower bound of the CI?
- Limitation statement: "Independent annotation of N samples was not performed; grader false negative rates are estimated from structural analysis."

### 8.2 What NOT to claim

- Do not claim human verification where none occurred.
- Do not present the 47.5% figure as independently validated.
- Do not treat the grader as ground truth in the annotation.
- Do not select only obvious grader failures for annotation (introduce sampling bias).

## 9. Recommended Next Steps

1. **Immediate**: Complete the MATCHED_BASELINE_ANALYSIS using existing per-trace data. This requires no annotation and provides immediate insight into benign-vs-LEP comparability.

2. **Short-term** (weeks): Conduct the primary annotation (270 samples, code_review + research_synthesis). Use the v5 audit's published financial findings.

3. **Medium-term** (months): If annotation reveals high FNR, correct the benign baseline and recompute M4. Report both the original and corrected figures with appropriate caveats.

4. **Long-term**: Establish a grader validation protocol for future benchmark rounds, including:
   - Grader calibration on known-correct/known-incorrect outputs.
   - Periodic false negative audits.
   - Human-in-the-loop grading for edge cases.
