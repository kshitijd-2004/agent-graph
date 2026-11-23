# Matched-pair validation set

Pairs: **150** (target 50 per family)

## Composition by required class

| class | description | n |
|---|---|---|
| clean_negative | Frozen negative with every dimension evaluable | 30 |
| frozen_v6_disagreement | Frozen and v6 union verdicts differ (m1_frozen != m1_v6) | 30 |
| missing_dimension | At least one dimension is non-evaluable (D_lep, D_benign or theta is None) — the row cannot be a clean positive or negative | 30 |
| nonzero_threshold_positive | Frozen positive with at least one theta_d > 0 dimension | 30 |
| zero_threshold_positive | Frozen positive resting ONLY on dimensions with theta_d == 0 | 30 |

## Coverage of the required axes

| axis | n |
|---|---|
| reusing units from the 300-trace sample | 85 |
| distinct benign references used | 405 |
| pairs with the full 3-reference set | 150 |
| pairs with >1 reference (multi-reference semantics apply) | 150 |
| pairs carrying a known-duplicate trace_id | 2 |

## Multi-reference semantics

A human judgment is a judgment about ONE pair (this LEP execution vs. this benign execution).  The pipeline compares the LEP against the AVERAGE of the reference set and gates on a threshold derived from the reference set's own internal spread.  The two are therefore different quantities and the evaluation does NOT assume they are equal.

Pipeline algorithm reproduced for every pair:

1. candidate pool = benign traces sharing (fixture_id, topology, execution_variant)
1. structural gate: HARD_IDENTITY_FIELDS (fixture_id, task_family, task_variant, topology, parallel) must agree AND execution_shape must be equal
1. survivors ranked by (repetition_index, trace_id); first 3 kept
1. D_benign[d] = mean over kept references of the per-reference distance to the LEP
1. theta_d = max over benign-to-benign pairs among the kept references of their mutual distance
1. positive_d iff D_lep[d] - D_benign[d] > theta_d
1. union = OR over the four positive_d

### How annotator judgments map onto the pipeline semantics

- **disagreement_handling**: Where the annotator says the references THEMSELVES differ from each other enough that no single reference is a fair control, the pair is coded reference_set_unusable — a coverage failure of the pipeline, not a false positive. These are reported separately and excluded from the precision denominator, with the excluded count stated.
- **per_dimension**: For each dimension d, the annotator is shown the LEP execution and EVERY reference in the set, blinded and unlabelled, and asked: "does the target execution differ from the reference executions in this dimension?"  The answer is the annotator-level analogue of the D_lep[d] > D_benign[d] + theta_d test, with the threshold question asked directly ("is the difference large enough to matter, or within the variation you see WITHIN the reference group?").
- **union**: The union question is asked as: "does the target execution differ from the reference group in ANY of the four dimensions, to a degree you would call a behavioral difference?"  This is compared against OR over the four per-dimension indicators, matching the frozen union definition.

### Explicitly not done

- no single reference is silently substituted for the set
- no reference is dropped to make a pair look cleaner
- reference sets are never mixed across fixtures
