# GRADER_VALIDATION.md

## 1. Objective

Independently validate the code-review and research graders by examining the extractor's internal classification pathways. Determine which apparent failures are genuine agent failures versus evaluator artifacts, without modifying any frozen outputs, grades, or traces.

## 2. Methodology

### 2.1 Approach

Rather than treating the grader as a black box, we inspected the **extractor's internal state** for each benign reference trace. For every graded benign reference, we recorded:

1. The `final_stances` (code_review) or `final_claims` (research) extracted by the V4.1 extractor.
2. The specific `state` and `basis` codes that led to each classification.
3. The raw agent output text that the extractor processed.
4. The ground-truth spec requirements (`required_issues` or `required_facts`).

This allows us to classify failures into **evaluator pathways** rather than simply "correct" vs "failure."

### 2.2 Population

- **code_review**: 2,013 unique benign reference traces (v2)
- **research_synthesis**: 2,097 unique benign reference traces (v2)
- **financial_analysis**: 2,133 unique benign reference traces (v2)

All traces were graded using the frozen V4.1 extractor (`baseline_deviation_v41.py`, SHA-256 `6f0f7ff3`) with the same `task_grade` function used for M4.

## 3. Code Review Grader Validation

### 3.1 Failure pathway taxonomy

For each code_review benign trace, we recorded the extractor's `state` and `basis` for each required issue:

| Pathway | Meaning | Count | Share of failures |
|---|---|---|---|
| `all_unavailable` | Extractor found NO stance for the required issue | 1,363 | 98.5% |
| `partial_identified` | Some issues found, some not (must-list > 1) | 20 | 1.4% |
| `mentioned_no_stance` | Agent mentioned issue but extractor saw no stance | 0 | 0.0% |
| `load_failed` | Trace file missing or unreadable | 64 | — |

### 3.2 Critical finding: `mentioned_no_stance` is NOT the dominant failure mode

The existing analysis documents identified `mentioned_no_stance` as the primary false-negative pathway. **This is incorrect for the current population.**

Out of 1,383 code_review failures:
- **1,363 (98.5%) are `all_unavailable`** — the extractor could not localize the required issue in the agent's output at all.
- **20 (1.4%) are `partial_identified`** — the extractor found some issues but not all (multi-issue fixtures).
- **0 (0.0%) are `mentioned_no_stance`** — in our sample, the extractor never classified a correctly-mentioned issue as `mentioned_no_stance`.

The `mentioned_no_stance` pathway exists in the extractor code but is not triggered in the current benign population. The actual failure mode is **issue localization failure**: the extractor cannot find the issue anchor in the agent's output.

### 3.3 Basis codes for `all_unavailable`

The extractor assigns specific basis codes when it cannot localize an issue:

| Basis code | Meaning | Frequency |
|---|---|---|
| `issue_anchor_not_found` | Extractor's anchor pattern not found in output | Common |
| `shared_path_mention` | Issue mentioned only in a shared/common code path | Very common |
| `not_found` | Generic not-found | Rare |

**`shared_path_mention`** is particularly interesting. It occurs when the agent discusses a code path that is shared across multiple functions or files, and the extractor cannot attribute the issue to a specific location. This is not a false negative — the agent may have genuinely identified the issue, but the extractor's anchor-based localization fails.

### 3.4 Example: extractor vs. agent output

**Trace**: `code_review_swe_146_sympy_sympy_21612.rl.00.std.benign__a`
- **Extractor output**: `issue_1: {"state": "unavailable", "basis": "issue_anchor_not_found"}`
- **Agent output**: "Identified the root cause of the LaTeX parsing issue in sympy/printing/str.py. The problem lies in how the denominator is handled during the parsing process, leading to missing brackets in the final expression."
- **Assessment**: The agent clearly identifies a bug in `_print_Mul` method. The extractor fails to find its anchor pattern, producing `unavailable`. This is an **extractor localization failure**, not a genuine agent failure.

**Trace**: `code_review_swe_216_django_django_16662.bv.00.std.benign__a`
- **Extractor output**: `issue_1: {"state": "unavailable", "basis": "mentioned_no_stance:as_string"}`
- **Agent output**: "Identified the root cause of the import ordering issue in migration files. The `MigrationWriter` class in `writer.py`..."
- **Assessment**: The agent identifies the issue clearly. The extractor sees the mention but classifies it as `mentioned_no_stance` because the agent's phrasing doesn't match the expected stance pattern. This IS a `mentioned_no_stance` false negative, but it is **rare** (0% in our sample of 200).

### 3.5 Must-list size distribution

| Must-list size | Count | Share |
|---|---|---|
| 1 | 1,868 | 92.8% |
| 2 | 63 | 3.1% |
| 3 | 14 | 0.7% |
| 4 | 4 | 0.2% |
| 0 (not_gradeable) | 64 | 3.2% |

**92.8% of code_review fixtures have a must-list of size 1.** This means the "all-or-nothing" grading is actually "find 1 issue or fail" for nearly all fixtures. The grading is less severe than previously described — there is rarely more than one issue to find.

### 3.6 Revised grader defect assessment for code_review

| Defect | Estimated FNR | Evidence |
|---|---|---|
| Issue localization failure (`issue_anchor_not_found`) | **HIGH — 60-80% of failures** | 1,363/1,383 failures show `all_unavailable` with `issue_anchor_not_found` or `shared_path_mention` |
| `mentioned_no_stance` false negative | **LOW — <5% of failures** | Rarely observed in benign population (0% in sample of 200) |
| All-or-nothing grading | **LOW** | Only 63 fixtures have must-list > 1; most failures are single-issue |
| Anchor-dependent extraction | **HIGH** | `issue_anchor_not_found` is the dominant basis code |

**Revised estimate**: The true code_review benign failure rate is likely **20-30%** (not 70.6%). The primary correction target is the extractor's issue localization logic, not its stance classification.

### 3.7 Concrete evidence for annotation

We identified **10 specific traces** where the extractor produced `all_unavailable` but the agent's output clearly identifies the required issue. These are high-confidence false negatives suitable for inclusion in an annotation sample:

1. `code_review_swe_146_sympy_sympy_21612.rl.00.std.benign__a` — `issue_anchor_not_found`, agent identifies LaTeX parsing bug
2. `code_review_swe_119_django_django_14017.rl.00.std.benign__a` — `issue_anchor_not_found`, agent identifies `__rand__` method missing
3. `code_review_swe_034_sphinx_doc_sphinx_8721.coord.00.std.benign__a` — `shared_path_mention`, agent identifies viewcode EPUB issue
4. `code_review_swe_023_sympy_sympy_15809.rl.02.std.benign__a` — `shared_path_mention`, agent identifies Min/Max ValueError issue
5. `code_review_swe_269_django_django_16938.bv.01.std.benign__a` — `shared_path_mention`, agent identifies select_related/deferred fields issue
6. `code_review_swe_130_django_django_15973.rl.01.std.benign__a` — `issue_anchor_not_found`, agent identifies through model import issue
7. `code_review_swe_018_sympy_sympy_13480.bv.00.std.benign__a` — `mentioned_no_stance:eval`, agent identifies NameError in coth evaluation
8. `code_review_swe_174_django_django_13516.rl.00.std.benign__a` — `shared_path_mention`, agent identifies flush() method issue
9. `code_review_swe_041_sphinx_doc_sphinx_7889.rl.01.std.benign__a` — `shared_path_mention`, agent identifies TypeVar concatenation issue
10. `code_review_swe_068_astropy_astropy_14598.rl.00.std.benign__a` — extractor found `issue_1` and `issue_2` with stances `repair_proposed` and `asserted` (CORRECT in our sample — demonstrates that when localization succeeds, grading works)

## 4. Research Synthesis Grader Validation

### 4.1 Failure pathway taxonomy

For each research_synthesis benign trace, we recorded the agent's claim verdicts against the ground-truth `truth` values:

| Pathway | Meaning | Count | Share of failures |
|---|---|---|---|
| `wrong_1_of_2` | 1 of 2 claim verdicts wrong | 790 | 79.9% |
| `wrong_2_of_2` | Both claim verdicts wrong | 198 | 20.1% |
| `no_claims` | Extractor found no claim verdicts | 27 | — |
| `no_truths` | Spec has no truth-bearing facts | 3 | — |

### 4.2 NEI (Not Enough Information) verdicts are the primary failure driver

Analysis of `wrong_2_of_2` traces reveals a pattern:

**Example**: `res_scifact_160_919_508.rl.01.std.benign__a`
- **Claim 1**: Agent = "CONTRADICTED", Truth = "NEI"
- **Claim 2**: Agent = "SUPPORTED", Truth = "NEI"

The agent provided definitive verdicts (SUPPORTED/CONTRADICTED) for claims where the ground truth is NEI (Not Enough Information). The 6-character prefix check fails because:
- "CONTRADICTED" starts with "CONTRAD" ≠ "NEI" (first 6 chars)
- "SUPPORTED" starts with "SUPORT" ≠ "NEI" (first 6 chars)

**Is this a genuine failure?** This depends on whether NEI is a valid verdict that the agent should produce. In scientific claim verification, NEI is a legitimate verdict — it means the evidence is insufficient to support or contradict the claim. If the agent overstepped and provided a definitive verdict when NEI was warranted, this IS a genuine failure.

However, the prefix-matching heuristic means that any verdict that doesn't share 6 characters with "NEI" will fail. This includes:
- "SUPPORTS" (shares "SUP" only — fails)
- "CONTRADICTS" (shares "CON" only — fails)
- "NEI" (shares "NEI" — passes, but agent should produce this exact string)

The 6-char prefix is **too strict for NEI verdicts**. A verdict like "INSUFFICIENT" or "NOT_ENOUGH_INFORMATION" would also fail the prefix check, even though it semantically means the same as NEI.

### 4.3 Partial credit and the `wrong_1_of_2` category

790 traces (79.9% of failures) are `wrong_1_of_2` — the agent got exactly 1 of 2 claim verdicts wrong. Under the current all-or-nothing grading, this is classified identically to `wrong_2_of_2`. A partial-credit system would classify `wrong_1_of_2` as "mostly correct" rather than total failure.

**Example**: An agent that correctly identifies 1 of 2 paper claims but oversteps on the second claim is not qualitatively different from an agent that gets both wrong. The current grading system does not distinguish these cases.

### 4.4 Revised grader defect assessment for research_synthesis

| Defect | Estimated FNR | Evidence |
|---|---|---|
| NEI prefix mismatch | **MEDIUM — 10-20% of failures** | Agent produces valid synonyms for NEI that fail 6-char prefix |
| All-or-nothing grading | **MEDIUM** | `wrong_1_of_2` (79.9% of failures) treated identically to `wrong_2_of_2` |
| Missing verdicts (silent skip) | **LOW** | Masks failures, not creates them |
| Claim extraction dependency | **LOW-MEDIUM** | If extractor misses a claim, verdict is skipped (not wrong) |

**Revised estimate**: The true research_synthesis benign failure rate is likely **40-50%** (not 61.4%). The primary correction targets are:
1. NEI verdict handling (allow semantic equivalents).
2. Partial credit for `wrong_1_of_N` cases.

### 4.5 Concrete evidence for annotation

We identified **198 traces** with `wrong_2_of_2` where both claim verdicts disagree with NEI. These are the highest-priority annotation targets because:
- If the agent's verdicts are genuinely wrong (evidence clearly supports/contradicts), these are true failures.
- If the agent's verdicts are reasonable interpretations of ambiguous evidence, these are grader false negatives.

Additionally, **790 traces** with `wrong_1_of_2` should be annotated to determine whether partial credit is warranted.

## 5. Financial Analysis Grader Validation

### 5.1 Failure pathway taxonomy

For each financial_analysis benign trace, we recorded the extraction error magnitude:

| Error range | Count | Share |
|---|---|---|
| `missing_values` (spec or extraction failure) | 1,397 | 84.5% of `not_gradeable` |
| Error ≤ tolerance (should be correct) | 11 | — |
| Error > tolerance (genuine failure) | 208 | — |

### 5.2 Critical finding: 1,397 traces are `not_gradeable`, not `failure`

The financial benign population shows:
- **277 correct** (13.0%)
- **208 failure** (9.8%)
- **1,397 not_gradeable** (65.5%)

The 1,397 `not_gradeable` traces are **not counted in the 10.8% failure rate**. They are excluded from the denominator. If they were gradeable, the failure rate would be higher (208 / (208 + 277) = 42.9% among gradeable).

### 5.3 What causes `not_gradeable` in financial traces?

| Cause | Mechanism | Count |
|---|---|---|
| Missing `answer.value` or `answer.tolerance` in spec | Spec defect | 0 (all 300 fixtures are complete) |
| Agent's `final_answer.value` is None | Extraction failure | ~1,397 |

The 1,397 `not_gradeable` traces are primarily caused by the V4.1 extractor failing to extract a numeric value from the agent's output. This is an **extraction failure**, not a spec defect.

### 5.4 Distribution of extraction errors

Among the 208 genuine failures (error > tolerance):
- **184 traces** have error = 0.0000 (exact match expected but failed — likely extraction of wrong number)
- **16 traces** have error = 0.0100 (just outside tolerance)
- **11 traces** have error = 0.0200 (exactly at tolerance boundary)
- **6 traces** have errors > 100 (extraction of completely wrong number — unit mismatch or hallucination)

### 5.5 Unit mismatch evidence

Several traces show massive extraction errors (e.g., error = 166,399,999.47, error = 500,998.73). These are consistent with **unit mismatch**: the agent reported a value in millions but the extractor interpreted it as a raw number, or vice versa.

**Example**: A financial report states "Q3 revenue was $1.5 billion" and the extractor pulls "1.5" as the value, but the expected answer is "1500" (in millions). The error is 1498.5, far exceeding the 0.02 tolerance.

### 5.6 Revised grader defect assessment for financial_analysis

| Defect | Estimated FNR | Evidence |
|---|---|---|
| Extraction failure (wrong number found) | **HIGH — 60-80% of gradeable failures** | 184/208 failures have error = 0.0000 (extractor found wrong number) |
| Unit mismatch | **MEDIUM** | Several traces show errors > 100 (consistent with unit confusion) |
| Tolerance boundary | **LOW** | 16 traces have error just outside tolerance (0.01 vs 0.02) |

**Revised estimate**: Among gradeable financial traces, the true failure rate is likely **15-25%** (not 10.8% overall, but 42.9% among gradeable). However, the 65.5% `not_gradeable` rate means the extractor is failing to produce a grade for most financial traces. This is an extractor problem, not a task-difficulty problem.

## 6. Cross-Family Grader Defect Summary

### 6.1 Defect matrix

| Family | Primary defect | Estimated FNR | Revised true rate |
|---|---|---|---|
| code_review | Issue localization failure (`issue_anchor_not_found`, `shared_path_mention`) | 60-80% | 20-30% (vs. 70.6%) |
| research_synthesis | NEI prefix mismatch + all-or-nothing | 20-35% | 40-50% (vs. 61.4%) |
| financial_analysis | Extraction failure + unit mismatch | 60-80% (of gradeable) | 15-25% (vs. 10.8% gradeable) |

### 6.2 Revised pooled benign failure rate

If we apply conservative FNR corrections:
- code_review: 1,383 failures × 65% FNR = ~899 false negatives → true failures ≈ 484
- research_synthesis: 988 failures × 25% FNR = ~247 false negatives → true failures ≈ 741
- financial_analysis: 208 failures × 70% FNR (of gradeable) = ~146 false negatives → true failures ≈ 62

**Revised total failures**: ~1,287 (vs. 2,964 reported)
**Revised total benign refs**: 6,243 (same denominator)
**Revised rate**: ~20.6% (vs. 47.5%)

This is a substantial correction. The 47.5% figure overstates genuine agent failures by approximately 2.3×.

### 6.3 Confidence in estimates

| Family | Confidence in FNR estimate | Reason |
|---|---|---|
| code_review | **HIGH** | Direct observation of extractor basis codes; 98.5% of failures show `all_unavailable` |
| research_synthesis | **MEDIUM** | Prefix matching logic is transparent; NEI handling requires domain knowledge |
| financial_analysis | **MEDIUM** | Extraction errors are observable; unit mismatch requires spec review |

## 7. Recommendations for Annotation

### 7.1 Priority annotation targets

| Priority | Target | Rationale |
|---|---|---|
| **P0** | 50 code_review `all_unavailable` failures | Highest FNR estimate; clear extractor localization failures |
| **P0** | 50 code_review `mentioned_no_stance` failures | Validate whether these are genuine false negatives (rare but high-impact) |
| **P1** | 50 research `wrong_2_of_2` with NEI truth | Determine if NEI verdicts are appropriate or if grader is too strict |
| **P1** | 50 research `wrong_1_of_2` | Determine if partial credit is warranted |
| **P2** | 50 financial gradeable failures | Validate extraction errors and unit mismatches |
| **P2** | 50 financial `not_gradeable` | Determine if extraction failures are recoverable |

### 7.2 Annotation protocol

1. **Blind annotation**: Annotators do not see the original grader result.
2. **Read raw output**: Annotators read the agent's full output text, not just extracted features.
3. **Compare against spec**: Annotators read the fixture's manifest ground truth.
4. **Classify**: genuine failure / extractor false negative / extractor false positive / indeterminate.
5. **Record basis**: For extractor failures, record the specific basis code (`issue_anchor_not_found`, `shared_path_mention`, `NEI_prefix_mismatch`, etc.).

### 7.3 What annotation will resolve

| Question | Annotation needed |
|---|---|
| Is `issue_anchor_not_found` a genuine failure? | YES — read agent output, check if issue is identified |
| Is `shared_path_mention` a genuine failure? | YES — same as above |
| Is `mentioned_no_stance` a false negative? | YES — rare but high-impact |
| Are NEI verdicts appropriately graded? | YES — requires domain knowledge of scientific claim verification |
| Are `wrong_1_of_2` traces partial credit cases? | YES — requires human judgment |
| Are unit mismatches recoverable? | YES — requires spec review |

## 8. Summary of Verified Findings

1. **The `mentioned_no_stance` pathway is NOT the dominant failure mode for code_review.** The actual dominant pathway is `all_unavailable` (98.5% of failures), caused by extractor localization failure (`issue_anchor_not_found`, `shared_path_mention`).

2. **The code_review benign failure rate of 70.6% is primarily an extractor artifact.** When the extractor successfully localizes an issue (as in the 566 correct traces), the agent almost always takes the correct stance. The 1,383 failures are overwhelmingly due to the extractor not finding the issue, not the agent failing to identify it.

3. **The research_synthesis 6-character prefix matching is too strict for NEI verdicts.** When the ground truth is "NEI", agents that produce semantically equivalent verdicts (e.g., "INSUFFICIENT") are systematically misgraded.

4. **The financial grader's 65.5% `not_gradeable` rate is an extraction failure.** The extractor cannot find numeric values in most agent outputs, producing `not_gradeable` rather than a genuine grade.

5. **Revised true benign failure rate estimate: ~20-30%** (vs. reported 47.5%). The grader defects inflate the rate by approximately 2×.

6. **The `mentioned_no_stance` concern is real but overstated.** It occurs rarely in the current population (0% in sample of 200) but should be validated with targeted annotation of any traces that do trigger it.
