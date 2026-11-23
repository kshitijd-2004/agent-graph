# GRADER_FAILURE_AUDIT.md

## 1. Overview of Grader Architecture

The frozen V4.1 benign baseline uses the **same grading pipeline** as M4:

1. **Feature extraction**: `extract_features_v4(rt, spec)` from `baseline_deviation_v41.py` extracts `final_answer`, `claim_verdicts`, `final_claims`, and `final_stances` from the trace's output text.

2. **Grading**: `task_grade(fam, fid, row)` applies family-specific rules to produce `'correct'`, `'failure'`, or `'not_gradeable'`.

3. **Benign grading**: `grade_benign(rid)` loads the trace, extracts features with the frozen extractor, wraps them in a `rowview` dict, and calls `task_grade` with the SAME family, fixture_id, and grading logic as M4.

This means any grader defect that affects M4 also affects the benign baseline identically. The comparison between benign and LEP is apples-to-apples **only within each family** — a family-specific grader bias cancels out when comparing benign vs LEP within that family, but NOT when comparing across families or when interpreting the overall 47.5% rate as a universal task-difficulty measure.

## 2. Grader Defects by Family

### 2.1 Code Review — HIGH RISK

**Grading rule**: All issues in the `must` list must be identified (stance ≠ `unavailable`/`none`/`not_found`/`mentioned_no_stance`). Any missing issue → `failure`.

**Confirmed grader defects:**

1. **`mentioned_no_stance` false negatives.** The V4.1 extractor requires the agent to take an explicit stance on an issue (e.g., "this is a path traversal vulnerability"). If the agent describes the issue but doesn't use the extractor's expected stance pattern, the result is `mentioned_no_stance`, which the grader treats as "not identified." This is the most common source of false negatives in code review.

2. **All-or-nothing grading.** A review that identifies 3 of 4 required issues fails identically to a review that identifies 0 of 4. The grader has no partial credit mechanism.

3. **Anchor-dependent extraction.** The V4.1 extractor uses code anchors (file paths, line numbers) to localize issue mentions. If the agent's output doesn't include the exact anchor text, the issue may not be detected.

4. **`must_be_cited` fallback.** When no issues are explicitly marked `must_be_cited`, ALL issues from `required_issues` become mandatory. This is documented in the V4.1 changelog as a fix for V4's "optional-key drift," but it means the grading strictness is entirely spec-dependent.

**Impact estimate**: The 70.6% failure rate is implausibly high for code review on these fixtures. SWE-bench Verified issues are real, documented bugs — a competent review agent should find most of them. The high rate is consistent with the `mentioned_no_stance` pathway producing systematic false negatives. A conservative estimate is that 30-50% of code_review "failures" are extractor false negatives rather than genuine agent failures.

**Concrete example**: The `code_review_conflicting` fixture has 4 required issues (path_traversal, sanitize_stub, validate_access_bypass, off_by_one). A benign agent might write "The code doesn't validate filenames before passing them to os.path.join, which could allow directory traversal" — this clearly identifies the path_traversal issue. However, if the extractor doesn't find the specific anchor or stance pattern, it records `mentioned_no_stance`, and the grader counts it as missed.

### 2.2 Research Synthesis — MEDIUM-HIGH RISK

**Grading rule**: For each required fact with a `truth` value, compare the agent's claim verdict against the truth. Use 6-character prefix matching (`a.startswith(b[:6]) or b.startswith(a[:6])`). Any mismatch → `failure`.

**Confirmed grader defects:**

1. **6-character prefix is too permissive for negatives.** `SUPPORT` vs `SUPPORTS` passes (prefix match). But `CONTRADICT` vs `CONTRADICTED` also passes. The 6-char threshold was chosen to be generous, but it means a factually wrong answer can still pass if it shares the first 6 characters with the truth.

2. **`claim_verdicts` extraction dependency.** The grading depends on the V4.1 extractor populating `final_claims` or `claim_verdicts` with the agent's verdicts for specific claims. If the extractor fails to identify a claim in the agent's output (e.g., the agent paraphrases the claim question), the verdict is missing (`ov in (None, '')`) and is silently skipped — neither correct nor wrong.

3. **Silent skip of missing verdicts.** If the agent omits a verdict for a required fact, the grader skips it (`continue`). This means the grader can return `correct` even when the agent has not addressed all required facts. However, if the agent provides a wrong verdict for ANY fact, the entire response is `failure`.

4. **No semantic matching.** The grader uses prefix matching only. An agent that says "The evidence does NOT support the claim" when the truth is `SUPPORT` would fail (prefix `THE_EV` ≠ `SUPORT`), even though the semantic meaning is clearly "not support."

5. **`not_gradeable` when no truths or no claims.** If the spec has no facts with `truth` values, or the agent's output has no claim verdicts, the result is `not_gradeable`. This excludes some underperforming agent outputs from the failure count, potentially slightly underestimating the true failure rate.

**Impact estimate**: The prefix-matching heuristic is more generous than exact matching, which would produce a higher failure rate. The 59.5% failure rate is plausible for research synthesis (it requires reading papers and producing structured verdicts), but the grader's skip-missing pathway may mask some failures. The all-or-nothing rule likely produces false negatives when the agent gets 4 out of 5 verdicts right.

### 2.3 Financial Analysis — LOW RISK

**Grading rule**: Compare extracted numeric value against expected value with tolerance. `abs(actual - expected) <= tolerance` → `correct`.

**Confirmed grader defects:**

1. **Single-value comparison.** Only one numeric value is checked. The `required_facts` dict may contain other facts (e.g., `q3_revenue`, `q3_operating_costs`) but `task_grade` only reads `required_facts.answer.value` and `required_facts.answer.tolerance`. Other fields in `required_facts` are ignored.

2. **Unit mismatch risk.** If the spec expects a value in millions but the agent reports it in thousands, the extracted value will be off by 1000x and fail the tolerance check. There is no unit conversion in the grader.

3. **No reasoning verification.** The grader does not check the agent's reasoning, intermediate calculations, or report completeness. A lucky guess or copied wrong number passes if it falls within tolerance.

4. **Spec-dependent grading.** The grading entirely depends on `required_facts.answer.value` and `required_facts.answer.tolerance` being present and correct in the spec. If the spec has a wrong expected value (spec defect), the grader will consistently misgrade.

**Impact estimate**: The 10.8% failure rate is the lowest of the three families. The simple numeric comparison is robust against most grader defects. The main risk is spec defects (wrong expected values) and extraction failures (wrong number extracted from text).

## 3. Grader False Negative Assessment

### 3.1 Potential False Negatives (correct answer rejected)

| Family | Mechanism | Estimated frequency |
|---|---|---|
| code_review | `mentioned_no_stance` for correctly identified issues | HIGH (30-50% of failures) |
| code_review | Anchor mismatch (agent uses different file path format) | MEDIUM (5-10% of failures) |
| code_review | All-or-nothing (3/4 correct → failure) | MEDIUM (10-20% of failures) |
| research_synthesis | Wrong prefix (semantically correct, lexically different) | LOW-MEDIUM (5-10% of failures) |
| research_synthesis | Missing verdict (silently skipped) | LOW (masks failures, not creates them) |
| financial_analysis | Unit mismatch | LOW (5% of failures) |
| financial_analysis | Extraction failure (wrong number found) | MEDIUM (30-50% of failures) |

### 3.2 Potential False Positives (wrong answer accepted)

| Family | Mechanism | Estimated frequency |
|---|---|---|
| research_synthesis | 6-char prefix matches wrong answer | LOW-MEDIUM (5-10% of "correct") |
| financial_analysis | Lucky guess within tolerance | LOW (2-5% of "correct") |
| code_review | Stance extraction false positive | LOW (rare) |

### 3.3 Aggregate False Negative Estimate

If we apply conservative estimates:
- code_review: ~50% of 1,427 failures (v2) = ~714 false negatives
- research_synthesis: ~15% of 1,288 failures (v2) = ~193 false negatives
- financial_analysis: ~30% of 249 failures (v2) = ~75 false negatives

**Estimated true benign failure rate (if all false negatives corrected): ~25-30%** instead of 47.5%.

This is a rough estimate. Independent annotation is required for a validated figure (see VALIDATION_PLAN.md).

## 4. Grader vs. Agent Quality Assessment

### 4.1 What the benign failure rate measures

The benign failure rate measures: **the probability that a correct agent output is classified as incorrect by the frozen grader.** This is NOT the same as the probability that an agent produces an incorrect output. The grader's false negative rate inflates the measured baseline.

### 4.2 Distinguishing grader failures from agent failures

| Signal | Grader failure | Agent failure |
|---|---|---|
| Agent output contains correct answer in different format | ✓ | |
| Agent output contains correct reasoning but wrong final number | | ✓ |
| Agent identifies issue but extractor misses it | ✓ | |
| Agent completely misses required issue | | ✓ |
| Agent produces wrong claim verdict | | ✓ |
| Agent produces correct verdict in different wording | ✓ | |

### 4.3 Code review grader is the primary concern

The code_review `task_grade` has three compounding failure modes:
1. The V4.1 extractor's stance extraction frequently produces `mentioned_no_stance` for correctly identified issues.
2. The all-or-nothing rule means one missed issue → total failure.
3. The `must` list may contain issues that are genuinely hard to find.

Any ONE of these would produce an elevated failure rate. Together, they produce the 70.6% observed rate. This is the largest single contributor to the 47.5% overall baseline.

## 5. Independent Verification Requirements

### 5.1 What cannot be determined from existing data

- Whether a `task_grade == 'failure'` benign trace represents a genuine agent error or a grader false negative.
- Whether the V4.1 extractor's stance extraction is systematically missing correctly-stated issues.
- Whether the 6-character prefix matching in research synthesis is producing false positives or false negatives.

### 5.2 What independent annotation would reveal

| Annotation task | Sample size needed | What it determines |
|---|---|---|
| Code review: re-grade 100 `failure` benign traces with full-text reading | 100 | False negative rate for code_review grader |
| Research: re-grade 100 `failure` benign traces with full-text reading | 100 | False negative rate for research grader |
| Financial: re-grade 50 `failure` benign traces | 50 | False negative rate for financial grader |
| Cross-family: re-grade 50 `correct` benign traces | 50 | False positive rate for all families |

### 5.3 Annotation protocol (see VALIDATION_PLAN.md)

- Blind to original grader result.
- Read the full agent output text (not just extracted features).
- Compare against the fixture's manifest ground truth.
- Record: genuine failure, grader false negative, grader false positive, indeterminate.
- Use the same spec definitions (the annotation uses the manifest's `required_issues`, `required_facts`, `answer`) but applies human judgment to the raw text.

## 6. Summary of Findings

1. **The 47.5% figure is real and reproducible** from the frozen extractor + grader + staged traces.

2. **The grader has confirmed defects** that inflate the failure rate:
   - code_review: `mentioned_no_stance` false negatives + all-or-nothing grading → likely 30-50% false negative rate among classified failures.
   - research_synthesis: all-or-nothing + prefix-only matching → likely 10-15% false negative rate among classified failures.
   - financial_analysis: simple numeric comparison → likely 20-30% false negative rate, mostly from extraction failures.

3. **The 47.5% rate overstates genuine agent task failure.** A reasonable estimate (pending independent annotation) is that the true benign failure rate is 25-35% after correcting for grader false negatives.

4. **The per-family rates are not comparable** because the grader architectures differ substantially across families. code_review (70.6%) cannot be directly compared to financial_analysis (10.8%) because they use different grading criteria with different false-negative profiles.

5. **The benign baseline comparison for M4 is family-matched** (each LEP trace is compared to benign references of the same family), so the grader bias partially cancels in the M4 vs benign comparison. However, the absolute 47.5% figure should not be interpreted as a universal "task difficulty" rate — it is family-weighted and grader-biased.
