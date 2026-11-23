# MATCHED_DEGRADATION_ANALYSIS.md

## 1. Objective

Design a matched benign-versus-perturbed analysis that asks whether task quality **deteriorates** following a perturbation, rather than simply whether a downstream execution failed. This metric would complement M4 by distinguishing propagation-associated task failures from failures that would plausibly have occurred without perturbation.

## 2. Design Principles

### 2.1 Preserve M4 unchanged

The existing M4 definition is preserved exactly for reproducibility. Any new metric is introduced as a **separate, explicitly defined measure** with a distinct name. M4 remains:
- M4 = M3-supported AND final deliverable graded as `failure`

### 2.2 Matched comparison, not pooled comparison

Instead of comparing LEP failure rate against the pooled benign baseline (47.5%), the matched analysis compares each LEP execution against its **own benign references**. This controls for fixture difficulty, spec strictness, and family effects.

### 2.3 Task quality deterioration, not just task failure

The metric measures whether the LEP execution performed **worse** than its matched benign references, not just whether it failed. This requires:
1. Grading the benign references (already done in `grade_benign`).
2. Comparing the LEP grade against the benign grades.
3. Classifying the relationship.

## 3. Proposed Metric: M4-matched

### 3.1 Definition

```
M4-matched = LEP execution is M3-supported
             AND LEP task grade is 'failure'
             AND at least one benign reference task grade is 'correct'
```

### 3.2 Interpretation

M4-matched answers: "Among M3-supported LEP executions that end in task failure, how many had benign references that succeeded?"

This is stricter than M4 because it requires:
- The LEP to fail (same as M4).
- At least one benign reference to succeed (stricter than M4).

If all benign references also fail, the failure may be due to task difficulty rather than the LEP.

### 3.3 Relationship to M4

| Scenario | M4 | M4-matched | Interpretation |
|---|---|---|---|
| LEP fails, benign correct | YES | YES | **Strong attribution** — LEP likely caused failure |
| LEP fails, benign mixed | YES | YES | **Moderate attribution** — LEP likely contributed to failure |
| LEP fails, benign all fail | YES | NO | **Weak attribution** — failure may be task difficulty |
| LEP correct, benign correct | NO | NO | No failure |
| LEP correct, benign failure | NO | NO | LEP improved outcome |

M4-matched is a **strict subset** of M4. Every M4-matched row is also an M4 row, but not vice versa. This preserves M4's definition while adding a stricter attribution test.

## 4. Extended Classification: Four Outcome Categories

For each M3-supported LEP execution, classify into one of four categories:

| Category | Condition | Meaning |
|---|---|---|
| **Degraded** | LEP = failure, benign ≥ 1 correct | Task quality deteriorated; LEP likely causal |
| **Persistent failure** | LEP = failure, benign all failure | Task was already failing; LEP attribution weak |
| **Persistent correct** | LEP = correct, benign all correct | No degradation; baseline maintained |
| **Improved** | LEP = correct, benign ≥ 1 failure | Task quality improved; LEP may have helped |

### 4.1 Degraded (the key category)

A "Degraded" classification requires:
1. The LEP execution is M3-supported (delivery verified, downstream reach evidence).
2. The LEP execution's final deliverable is graded `failure`.
3. At least one benign reference for the same configuration is graded `correct`.

This is the strongest evidence that the LEP caused task failure. The benign reference(s) demonstrate that the agent **can** succeed on this task without the LEP.

### 4.2 Persistent failure

A "Persistent failure" classification occurs when:
1. The LEP execution is M3-supported and graded `failure`.
2. All benign references for the same configuration are also graded `failure` (or `not_gradeable`).

This means the failure cannot be attributed to the LEP — the task is too difficult for the agent even without perturbation.

### 4.3 Persistent correct

A "Persistent correct" classification occurs when:
1. The LEP execution is M3-supported and graded `correct`.
2. All benign references are also graded `correct`.

This means the LEP did not affect task quality.

### 4.4 Improved

An "Improved" classification occurs when:
1. The LEP execution is M3-supported and graded `correct`.
2. At least one benign reference is graded `failure`.

This is counterintuitive but possible — the LEP may have changed the agent's behavior in a way that bypassed the grader's strict criteria, or the benign reference may have been unlucky.

## 5. Reference Set Aggregation Rules

### 5.1 Multiple benign references

Each LEP row has 3 benign references (reps 0, 1, 2). The aggregation rule determines how to combine them:

| Rule | Description | Recommended |
|---|---|---|
| **Any-correct** | At least 1 benign reference is `correct` → classify as `correct` | YES |
| **Majority** | ≥2 benign references agree → use majority grade | Alternative |
| **All-correct** | All 3 benign references must be `correct` → stricter | Too strict |
| **Any-failure** | At least 1 benign reference is `failure` → classify as `failure` | NO (too lenient) |

**Recommendation**: Use **Any-correct** for the primary analysis. This is the most conservative attribution rule — it requires only one benign success to establish that the task is solvable.

### 5.2 Handling `not_gradeable` benign references

`not_gradeable` benign references are **neutral** — they neither support nor weaken attribution. The aggregation rules apply only to `correct` and `failure` grades.

| Aggregation | `not_gradeable` treatment |
|---|---|
| Any-correct | Ignored; at least 1 of the remaining refs must be `correct` |
| Majority | Ignored; majority of gradeable refs must agree |
| All-correct | Counted as failure (makes the rule stricter) |

### 5.3 Reference set sharing

As noted in MATCHED_BASELINE_ANALYSIS.md, each benign reference is shared by ~6 LEP rows. This means:
- A benign `correct` grade benefits all 6 LEP rows that reference it.
- A benign `failure` grade weakens attribution for all 6 LEP rows.

This is acceptable for the pooled analysis but should be noted when interpreting individual M4-matched claims.

## 6. Feasibility with Existing Data

### 6.1 Data requirements

All required data is already present in the existing artifacts:

| Data | Source | Already computed? |
|---|---|---|
| LEP task grades | `results_v2/per_trace.jsonl` (`task_grade` field) | YES |
| M3 classification | `results_v2/per_trace.jsonl` (`m3_reach_class` field) | YES |
| Benign reference grades | Computed by `grade_benign` in `analyze_downstream.py` | YES (in memory, not persisted) |
| Reference sets | `results_v2/per_trace.jsonl` (`reference_set` field) | YES |

### 6.2 What needs to be computed

The benign reference grades are computed during the `analyze_downstream.py` run but are **not persisted** in the output JSONL. To perform the matched analysis, we need to:

1. Re-run `grade_benign` for all unique benign references (6,243 traces).
2. Join the benign grades to each LEP row by `reference_set`.
3. Apply the aggregation rule (any-correct).
4. Classify each LEP row into Degraded / Persistent failure / Persistent correct / Improved.
5. Count M4-matched = Degraded rows.

### 6.3 Computational cost

- Grading 6,243 benign traces: ~5 minutes (CPU-only, uses frozen extractor).
- Joining and classifying 12,891 LEP rows: <1 minute.
- No GPU, no Slurm, no LLM inference.

This is a **read-only analysis** that can be run locally without any new infrastructure.

## 7. Proposed Implementation

### 7.1 Script design

Create a new script: `analysis/benign_baseline_audit/compute_matched_degradation.py`

Inputs:
- `results_v2/per_trace.jsonl` (LEP rows with reference sets)
- `staged_v2/merged/traces/` (benign trace files)
- `bnd_v41_cw_swe200_provisional/specroot/` (ground-truth specs)
- `bnd_v41_cw_swe200_provisional/frozen_run/benchmark/baseline_deviation_v41.py` (frozen extractor)

Outputs:
- `matched_degradation.jsonl` (per-LEP-row classification)
- `matched_degradation_summary.json` (aggregate counts)
- `matched_degradation_report.md` (human-readable report)

### 7.2 Classification logic

```python
def classify_matched(lep_grade, benign_grades):
    if lep_grade != 'failure':
        if all(g == 'correct' for g in benign_grades if g != 'not_gradeable'):
            return 'persistent_correct'
        else:
            return 'persistent_correct'  # LEP correct is always "no degradation"

    # LEP is failure
    gradeable_benigns = [g for g in benign_grades if g != 'not_gradeable']
    if not gradeable_benigns:
        return 'ungradeable_benign'  # Cannot determine attribution

    if any(g == 'correct' for g in gradeable_benigns):
        return 'degraded'
    else:
        return 'persistent_failure'

def m4_matched(lep_grade, benign_grades, m3_class):
    if m3_class not in ('verified', 'plausible'):
        return False
    if lep_grade != 'failure':
        return False
    gradeable_benigns = [g for g in benign_grades if g != 'not_gradeable']
    if not gradeable_benigns:
        return False
    return any(g == 'correct' for g in gradeable_benigns)
```

### 7.3 Aggregate metrics

| Metric | Definition |
|---|---|
| M4-matched rate | Degraded / all M3-supported |
| Degradation rate | Degraded / M3-supported with lep = failure |
| Persistent failure rate | Persistent failure / M3-supported with lep = failure |
| Benign success rate | Any benign correct / all reference sets |

## 8. Expected Results (Hypothesis)

Based on the existing data:

| Metric | Expected value | Rationale |
|---|---|---|
| M4 (current) | 842 / 12,891 = 6.53% | Already computed |
| M4-matched (projected) | ~200-400 / 12,891 = 1.5-3.1% | Stricter: requires benign success |
| Degraded fraction of M4 | ~25-50% | Many M4 failures have benign references that also fail |
| Persistent failure fraction of M4 | ~50-75% | High benign failure rates in code_review and research |

**Key hypothesis**: The majority of M4-positive rows will be "Persistent failure" rather than "Degraded." This would mean that most downstream task failures are **not** attributable to the LEP — they reflect task difficulty that persists even without perturbation.

## 9. Implications for AgentProp

### 9.1 If Degraded < 25% of M4

This would mean that **most M4 claims are weak attribution**. The benign baseline of 47.5% would be validated as a real phenomenon — these tasks are genuinely difficult, and LEP executions fail at similar rates to clean executions.

**Publication implication**: M4 should be reported as "association" not "attribution." The propagation narrative needs significant qualification.

### 9.2 If Degraded > 50% of M4

This would mean that **most M4 claims are strong attribution**. Even though the benign baseline is high, the LEP still pushes some configurations from success to failure.

**Publication implication**: M4 is a valid attribution measure, but the absolute rate is low (1.5-3.1% of all comparable executions). The propagation effect is real but rare.

### 9.3 Family-stratified results

The matched analysis would reveal family-specific patterns:

| Family | Benign success rate | Expected Degraded / M4 |
|---|---|---|
| financial_analysis | ~88% | High — most M4 failures have benign successes |
| research_synthesis | ~39% | Medium — many benign failures dilute attribution |
| code_review | ~29% | Low — most benign references also fail |

This would show that financial analysis M4 claims are the most defensible (high benign success rate), while code_review M4 claims are the weakest (low benign success rate).

## 10. Relationship to Concurrent v5 Audit

The v5 audit is focused on financial grading defects. The matched degradation analysis should:

1. **Use the frozen grader for LEP grades** (same as M4) — ensures reproducibility.
2. **Use the v5-corrected grades for benign references** (if available) — provides a sensitivity analysis.
3. **Report both**: "Using frozen grader: X% Degraded. Using v5-corrected grades: Y% Degraded."

This allows the reader to see how grader correction affects attribution strength, without modifying M4 or the frozen outputs.

## 11. Summary

| Design choice | Rationale |
|---|---|
| New metric name: M4-matched | Preserves M4 for reproducibility; M4-matched is explicitly stricter |
| Aggregation: Any-correct | Most conservative attribution rule |
| `not_gradeable` = neutral | Does not bias toward or against attribution |
| Family-stratified reporting | Reveals which M4 claims are most defensible |
| v5 sensitivity analysis | Shows impact of financial grader correction |

The matched degradation analysis answers a different question than M4:
- **M4**: "Did the LEP execution fail?"
- **M4-matched**: "Did the LEP execution fail when the benign reference succeeded?"

The second question is more relevant to causal attribution. It is feasible to compute with existing data and requires no new inference or infrastructure.
