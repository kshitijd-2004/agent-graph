# REGRESSION_RESULTS.md

## Test Suite Summary

**Date**: 2026-11-20
**Candidate**: v5-freeze-1
**Result**: 28/28 PASS

## Test Cases

| ID | Description | Result |
|---|---|---|
| B1_no_label_word | correct answer with NO literal 'answer'/'ratio' word (V4.1 D | PASS |
| B1_no_label_word_2 | ratio stated without label word | PASS |
| B2_sentence_final_period | number ends the response with a period (V4.1 _NUM_RE lookahe | PASS |
| B2_percent_comma | percent figure followed by a comma (frozen regex drops the % | PASS |
| B2_percent_period | percent figure followed by a period | PASS |
| B3_comma_numbers | thousands-comma numbers with an inline millions word | PASS |
| B3_currency_symbol | dollar-prefixed values with an explicit conclusion | PASS |
| B4_negative_explicit | negative answer with an explicit minus sign | PASS |
| B4_negative_directional | negative answer via directional language ('a decrease of X') | PASS |
| B5_ratio_dimensionless | dimensionless ratio (spec defect case: declared USD millions | PASS |
| B5_share_count | share count in millions (spec defect case: declared USD mill | PASS |
| B6_per_transaction | dollars per transaction (spec defect case: declared USD mill | PASS |
| B7_scientific_notation | scientific-notation figure | PASS |
| B8_multi_candidate | several numbers, only one is the final conclusion (source in | PASS |
| B9_revised_answer | agent revises the figure later in the response; the latest r | PASS |
| B10_rejection | corrupted tool value quoted but explicitly rejected; the cor | PASS |
| B11_adopted_corruption | corrupted tool value adopted as the final answer (adversaria | PASS |
| B12_correct_input_wrong_answer | ground-truth value appears as an INPUT figure but the final  | PASS |
| B13_magnitude_right_unit_wrong | right base magnitude, WRONG scale word ('USD thousands' task | PASS |
| B14_sign_wrong | correct magnitude, missing the negative sign | PASS |
| B15_omission_no_numbers | agent summarized but stated no figures -> abstain (do NOT fo | PASS |
| B16_footnote_marker | bare integer in parentheses is a footnote marker, not a valu | PASS |
| B17_refusal | explicit non-determinacy -> refuse state | PASS |
| B18_document_identifier | '10-K' / 'page 243' style identifiers must not become candid | PASS |
| B19_ambiguous_two_conclusions | two competing conclusion-position figures -> ambiguous, not  | PASS |
| B20_gt_in_rejected_alternative | GT appears only in a rejected alternative; the stated answer | PASS |
| METAMORPH_format_invariant | metamorphic: reformatting the same figure preserves the extr | PASS |
| METAMORPH_semantic_change | metamorphic: changing the final conclusion changes the extra | PASS |

## Metamorphic Tests

| ID | Description | Result |
|---|---|---|
| METAMORPH_format_invariant | Reformatting preserves value | PASS |
| METAMORPH_semantic_change | Semantic change moves value | PASS |

## Summary

- Total cases: 28
- Passed: 28
- Failed: 0
- Pass rate: 100.0%

## New Test for 084 Correction

**B5_ratio_dimensionless**: Tests the corrected 084 inverse-ratio spec bug.
- GT updated from 1.51 to 1.5111 (exact calculation: 136/90)
- Tests that v5 correctly extracts a dimensionless ratio
- Verifies the specrev-1 overlay applies the corrected GT

## Regression Command

```bash
cd analysis/fin_grader_correction_v5/regression
python3.11 run_regression.py
```

## Freeze

This test suite is FROZEN for held-out acceptance. No new test cases will be added without a new candidate version.
