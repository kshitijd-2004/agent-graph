# HELDOUT_INTEGRITY.md

## Authoritative Split

**Source**: `analysis/fin_grader_correction_v5/validation/split_manifest.json` (seed: 20261121)

**Original split**:
- Development fixtures: 249
- Held-out fixtures: 51
- Total: 300 financial fixtures

## Held-Out Population

**Total held-out tasks**: 775 (across 51 fixtures, 10-20 tasks each, avg 15.2)
**All held-out fixtures are financial_analysis**: Yes (51/51)

## Exposure Status

**What was inspected**:
- Development traces (249 fixtures): YES — used to build and tune the extractor.
- Sealed held-out traces (51 fixtures): NO — no sealed trace responses were inspected.
- Full-population results (4,409 rows): YES — inspected during development.

**What was NOT inspected**:
- Sealed held-out trace responses.
- Sealed held-out annotations (these are derived from frozen execution data, not independent human labels).
- Held-out grader outcomes.

**Exposure classification**: PARTIALLY EXPOSED

**Evidence**:
- Full-population results (4,409 rows) were inspected during development.
- The sealed held-out set (51 fixtures, 775 tasks) was NOT inspected.
- No held-out annotations were accessed during extractor development.

**Risk**: The extractor may be overfitted to full-population patterns observed during development. The 28/28 regression suite mitigates this risk, but the regression cases are synthetic/dev-derived, not sealed.

## Held-Out Fixtures with Special Status

**Value-bug fixtures**:
- `fin_finqa_219_adi_2015` (frozen GT 56.6926, correct 128,397.8)
  - **Action**: EXCLUDED from scalar acceptance and corrected headline grading.

**No-answer fixtures**: None in held-out set.

**Unit-defect fixtures** (35 total, some in held-out):
- Unit corrections applied via specrev-1 overlay.
- GT values unchanged (only units corrected).

## Evaluation Population

**Total held-out tasks**: 775
**Excluded (value-bug)**: ~10-20 tasks from `fin_finqa_219_adi_2015`
**Scalar-eligible held-out tasks**: ~755-765

## Next Steps

1. Freeze the candidate (COMPLETE — see V5_FREEZE_MANIFEST.json).
2. Prepare sealed evaluation package (this document).
3. Produce independent annotation instructions.
4. Identify which labels require verification.
5. STOP before scoring against unverified labels.

**Do not proceed to formal acceptance until independently verified human annotations are available.**
