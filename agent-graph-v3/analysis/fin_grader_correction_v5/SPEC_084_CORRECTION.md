# SPEC-084-CORRECTION: Inverse-Ratio Value Bug

**Fixture**: `fin_finqa_084_bdx_2018`
**Date**: 2026-11-20
**Status**: CORRECTED in specrev-1 overlay (frozen spec untouched)

## Question
> "in 2018 what was the ratio of the service cost to the interest cost?"

## Source Facts (from frozen spec)
- `fact_2` (service cost, pension plans 2018): **136.0 USD millions**
- `fact_1` (interest cost, pension plans 2018): **90.0 USD millions**

## Frozen GT (INCORRECT)
- **Value**: 0.6618
- **Operation**: interest_cost / service_cost = 90.0 / 136.0 = 0.6618
- **Unit**: USD millions (also incorrect for a ratio)

## Corrected GT
- **Value**: 1.5111 (rounded to 4 decimal places)
- **Operation**: service_cost / interest_cost = 136.0 / 90.0 = 1.5111...
- **Unit**: dimensionless (corrected in specrev-1 confirmed_unit_defects)

## Evidence
1. The question explicitly asks for "ratio of the service cost to the interest cost" which is `service_cost / interest_cost`.
2. The frozen GT 0.6618 is the reciprocal: `interest_cost / service_cost`.
3. Development traces show agents consistently compute `136 / 90 ≈ 1.51` and report "the ratio of the service cost to the interest cost is approximately 1.51".
4. The frozen grader marks these as incorrect (|1.51 - 0.6618| = 0.8482 > tolerance 0.02).

## Impact
- All 084 executions are misgraded by the frozen extractor.
- Under v5 with corrected GT, these flip from failure → correct.
- The correction overlay applies the corrected GT (1.5111) and unit (dimensionless).

## Regression Test
- **B5_ratio_dimensionless** in `regression_cases.jsonl` tests this correction.
- GT updated from 1.51 (natural reading) to 1.5111 (exact calculation).
- Test passes with corrected spec overlay.

## Original Audit Blind Spot
The 302-fixture audit verified arithmetic relationships but did NOT verify semantic direction. It treated the spec as a mathematical object rather than a semantic contract. This is why the inverse-ratio bug was missed.

## Correction Record
- **Frozen spec**: UNCHANGED
- **Overlay**: `value_corrections.fin_finqa_084_bdx_2018` in `corrections.json`
- **Implementation**: `load_corrected_spec()` applies value corrections when `apply_value_corrections=True`
- **Version**: specrev-1
