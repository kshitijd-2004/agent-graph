# NEXT_STEPS.md

## Immediate Actions (Before Publication)

### 1. Obtain Independent Human Annotations (BLOCKING)

**Action**: Commission independent human annotators to grade the 775 held-out tasks.

**Requirements**:
- Annotators must be blind to the frozen grades and v5 extractor outputs.
- Annotation instructions must specify the grading criteria (correct/incorrect/ungradeable) with examples.
- Inter-annotator agreement must be measured (target Cohen's kappa ≥ 0.80).
- Disagreements must be adjudicated by a third expert.

**Deliverables**:
- Annotated held-out dataset (775 tasks with independent labels).
- Inter-annotator agreement metrics.
- Adjudication log.

### 2. Run Formal Acceptance Gates (PENDING)

**Prerequisite**: Independent human annotations (Step 1).

**Actions**:
1. Run v5 extractor on held-out tasks (frozen implementation, no changes).
2. Compare v5 outputs against independent human labels.
3. Compute acceptance gates:
   - G1: False-correct rate ≤ 5%
   - G2: False-incorrect rate ≤ 15%
   - G3: False-ungradeable rate ≤ 10%
   - G4: Appropriate abstention ≥ 90%
   - G5: Gradeability classification accuracy ≥ 90%
4. Report PASS/FAIL/INCONCLUSIVE for each gate.
5. Give overall acceptance verdict.

### 3. Update Paper with Qualified Claims (PENDING)

**Prerequisite**: Formal acceptance (Step 2).

**Actions**:
1. Replace frozen M4 (842) with corrected M4 (818) if acceptance passes.
2. Qualify the 71% ungradeability claim: "80.5% of frozen ungradeable executions were extractor artifacts."
3. Report benign baseline as 32.5% (v5), not 11.67% (frozen).
4. Acknowledge the 084 spec correction and its impact.
5. State that full-population regrading is exploratory pending acceptance.

## Optional Actions (After Acceptance)

### 4. Apply 084 Correction to Full Population

If acceptance passes:
- Regrade all 084 executions with corrected GT (1.5111).
- Update corrected M4 from 818 to 817 (1 additional flip).
- Update full-population regrade report.

### 5. Semantic Direction Audit

Audit all ratio/rate/margin/percentage fixtures (~10-15) for semantic direction bugs similar to 084.

### 6. Sign Convention Audit

Audit all negative-GT fixtures (20) for sign convention mismatches with question wording.

## What NOT to Do

1. **Do not update the paper before acceptance** — the corrected results are not yet validated.
2. **Do not manufacture annotations** — model-generated labels must not be represented as human annotations.
3. **Do not modify the frozen extractor** — v5 is frozen for acceptance.
4. **Do not modify the spec overlay** — specrev-1 is frozen for acceptance.
5. **Do not run new LLM inference or Slurm jobs** — not needed for acceptance.

## Timeline Estimate

- Independent annotation: 2-4 weeks (depending on annotator availability)
- Acceptance testing: 1 week (after annotations complete)
- Paper update: 1-2 weeks (after acceptance)

**Total**: 4-7 weeks to publication-ready state.
