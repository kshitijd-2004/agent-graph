# HELDOUT_VALIDATION.md

## Independent Reference Provenance

**CRITICAL FINDING**: The held-out "annotations" are NOT independently verified human labels.

**Source**: `analysis/fin_grader_correction_v5/validation/annotation_tasks_FINAL.jsonl`
- 775 tasks for 51 held-out fixtures
- All tasks have `list_role = acceptance_sealed`
- **No independent human annotations exist** — labels are derived from frozen execution data (frozen_task_grade, frozen_m1, frozen_m2, etc.)

**Implication**: Formal acceptance gates (G1-G5) CANNOT be computed against independently verified reference labels. The frozen grades are what we're trying to correct, so they cannot serve as ground truth for acceptance.

## Formal Acceptance Status

**BLOCKED** — cannot proceed without independently verified human annotations.

## Validation Package (Prepared, Not Executed)

The following package is ready for independent annotation:

1. **Frozen extractor**: `analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/baseline_deviation_v41.py`
   - SHA-256: [see V5_FREEZE_MANIFEST.json]

2. **Corrected extractor (v5)**: `benchmark/fin_answer_v5.py`
   - SHA-256: [see V5_FREEZE_MANIFEST.json]

3. **Specification overlay (specrev-1)**: `analysis/fin_grader_correction_v5/fin_spec_revised/`
   - SHA-256: [see V5_FREEZE_MANIFEST.json]

4. **Held-out tasks**: 775 tasks across 51 fixtures
   - Source: `analysis/fin_grader_correction_v5/validation/annotation_tasks_FINAL.jsonl`
   - Filter: `list_role = acceptance_sealed`

5. **Annotation instructions**: See `ANNOTATION_GUIDE.md`

6. **Acceptance gates**:
   - G1: False-correct rate ≤ 5%
   - G2: False-incorrect rate ≤ 15%
   - G3: False-ungradeable rate ≤ 10%
   - G4: Appropriate abstention ≥ 90%
   - G5: Gradeability classification accuracy ≥ 90%

## Blockers

1. **No independent human annotations**: The held-out labels are derived from frozen execution data, not independently verified human judgments.
2. **Cannot score acceptance gates**: Without independent labels, we cannot compute false-correct, false-incorrect, or false-ungradeable rates.
3. **Do not manufacture labels**: Model-generated or frozen-execution labels must not be represented as human annotations.

## Required Actions Before Acceptance

1. **Obtain independent human annotations** for the 775 held-out tasks.
2. **Verify annotation quality** (inter-annotator agreement, adjudication process).
3. **Score acceptance gates** against verified labels.
4. **Report PASS/FAIL/INCONCLUSIVE** for each gate.

## Current Status

- Candidate frozen: YES (V5_FREEZE_MANIFEST.json)
- Regression tests passing: YES (28/28)
- Held-out population identified: YES (51 fixtures, 775 tasks)
- Independent annotations available: NO
- Formal acceptance: BLOCKED
