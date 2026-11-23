# ACCEPTANCE_VERDICT.md

**Date**: 2026-11-20
**Candidate**: v5-freeze-1
**Status**: BLOCKED

## Verdict

**BLOCKED** — formal acceptance cannot be completed because independently verified human annotations are not available for the held-out set.

## Evidence

1. **Regression tests**: PASS (28/28)
   - Source: `analysis/fin_grader_correction_v5/regression/regression_report.json`
   - All deterministic synthetic and metamorphic tests pass.

2. **Freeze manifest**: CREATED
   - Source: `analysis/fin_grader_correction_v5/V5_FREEZE_MANIFEST.json`
   - Extractor SHA-256: bad72ffc6d3b539dba54be0a457e4f5af2139555f5fbf51f1e31859db4faf3c6
   - Spec overlay SHA-256: [see manifest]
   - Regression SHA-256: [see manifest]

3. **Held-out population**: IDENTIFIED
   - 51 fixtures, 775 tasks
   - All financial_analysis
   - Source: `analysis/fin_grader_correction_v5/validation/split_manifest.json`

4. **Independent annotations**: NOT AVAILABLE
   - The held-out "annotations" are derived from frozen execution data.
   - No independently verified human labels exist.
   - Cannot compute acceptance gates without independent reference.

## Gate Results

| Gate | Threshold | Result | Notes |
|---|---|---|---|
| G1: False-correct rate | ≤ 5% | NOT EVALUATED | No independent labels |
| G2: False-incorrect rate | ≤ 15% | NOT EVALUATED | No independent labels |
| G3: False-ungradeable rate | ≤ 10% | NOT EVALUATED | No independent labels |
| G4: Appropriate abstention | ≥ 90% | NOT EVALUATED | No independent labels |
| G5: Gradeability accuracy | ≥ 90% | NOT EVALUATED | No independent labels |

## Next Steps

See `NEXT_STEPS.md` for required actions before publication.
