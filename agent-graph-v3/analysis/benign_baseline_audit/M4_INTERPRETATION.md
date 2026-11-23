# M4_INTERPRETATION.md

## 1. Exact M4 Predicate

M4 is defined in `analyze_downstream.py` (lines 618–650) as:

```
M4 = M3-supported AND final deliverable is a ground-truth failure
```

Where:
- **M3-supported** means the LEP trace is M2-positive AND delivery-verified AND has positive downstream reach evidence (either `verified` or `plausible`).
- **Final deliverable is a ground-truth failure** means `task_grade(fam, fid, row) == 'failure'` for the LEP trace's final output.

### 1.1 What M4 does NOT require

M4 does **NOT** require:
- The benign reference to succeed.
- The LEP trace to have a higher failure rate than the benign baseline.
- Any comparison against the benign baseline.
- Any attribution logic — M4 is purely descriptive.

### 1.2 What M4 measures

M4 measures: "Among M3-supported LEP executions, how many end in a ground-truth task failure?"

This is a descriptive statistic, not a causal claim. The downstream analysis script's comments make this explicit:

```python
# M4 (M3-supported AND final deliverable is a ground-truth failure)
m4fail = False
if m3cls in ('verified', 'plausible') and dv:
    m3_supported += 1
    g = task_grade(fam, fid, row)
    if g == 'failure':
        m4fail = True
```

## 2. M4 and the Benign Baseline

### 2.1 The benign baseline is NOT part of M4

The benign baseline (47.5%) is computed **separately** from M4. It appears in the output summary under `M4_ground_truth_downstream_task_failure.benign_baseline_contrast`, but it is not used in the M4 predicate itself.

The benign baseline serves as a **null hypothesis** for interpretation: "M4 counts failures, but 47.5% of benign executions also fail. So M4 is only interesting if it EXCEEDS this baseline."

### 2.2 How the benign baseline affects M4 interpretation

The v2 results show:
- M4: 842 failures out of 12,891 comparable (6.53%)
- Benign baseline: 47.5%

This means M4's failure rate (6.53%) is **well below** the benign baseline (47.5%). In other words, LEP executions are **less likely** to end in task failure than benign executions.

This counterintuitive result has several possible explanations:

1. **LEPs change agent behavior in ways that reduce task failure.** Some LEPs (e.g., HANDOFF_CORRUPTION) may cause agents to produce different outputs that happen to pass the grader more often.

2. **The benign baseline is inflated by grader false negatives.** If the benign baseline of 47.5% overstates genuine failures (due to grader defects), then the comparison with M4 is misleading. The true benign rate might be 25-35%, making M4's 6.53% even more anomalous.

3. **M4's M3 gate filters out easy failures.** M4 requires M3 support (delivery-verified + downstream reach evidence). This gate may exclude LEP executions that fail for reasons unrelated to the LEP (e.g., the agent was already failing before the LEP took effect).

4. **The comparison is family-mismatched.** The 47.5% pooled rate blends code_review (70.6%), research (59.5%), and financial (10.8%). M4's 842 failures are also family-blended, but the family distribution may differ between benign and LEP populations.

### 2.3 Family-adjusted M4 vs benign

From v2 summary:
- M4 by family: code_review 147, financial_analysis 27, research_synthesis 391
- M4 denominators by family: code_review 4,107, financial_analysis 4,409, research_synthesis 4,375

Per-family M4 rates:
- code_review: 147 / 4,107 = 3.58%
- financial_analysis: 27 / 4,409 = 0.61%
- research_synthesis: 391 / 4,375 = 8.94%

Per-family benign baselines:
- code_review: 70.9%
- financial_analysis: 11.7%
- research_synthesis: 61.4%

In every family, M4's failure rate is **below** the benign baseline. This is consistent across families and suggests the result is not an artifact of family mixing.

## 3. Four Distinct Concepts

### 3.1 Downstream-associated task failure (M4)

A task failure that occurs in an execution where:
1. A downstream deviation was observed (M2)
2. The LEP's mutation was delivered to a downstream agent (M3)
3. The final deliverable is graded as incorrect

This is what M4 measures.

### 3.2 Task failure absent a perturbation (benign baseline)

A task failure that occurs in a clean execution with no LEP. This is the 47.5% rate.

### 3.3 Perturbation-associated degradation relative to matched benign

The difference between M4's failure rate and the benign baseline, matched by configuration (fixture, topology, variant). This requires:
1. For each M4-positive LEP row, retrieve its benign references' grades.
2. Compute the benign failure rate for that configuration.
3. Compare: did the LEP increase the failure rate relative to benign?

This analysis has **not** been performed. The current reporting only shows the pooled benign baseline, not a matched comparison.

### 3.4 Causally attributable failure

A task failure that would NOT have occurred if the LEP had not been injected. This requires a counterfactual: the same agent, same configuration, same execution, but without the LEP. This counterfactual does not exist in the current population.

The strongest evidence for causal attribution is:
- **Verified reach** (M3): the LEP's introduced content appears in the downstream agent's own authored output. This is correlation, not causation.
- **M4 failure + benign success**: if the benign reference for the same configuration succeeds, the LEP failure is more likely attributable to the LEP.
- **M4 failure + benign failure**: if the benign reference also fails, the failure may be due to task difficulty, not the LEP.

## 4. What M4 Does Not Establish

M4 does **NOT** establish:

1. **Causation.** M4 is a descriptive statistic. It counts co-occurrences of M3 evidence and task failure. It does not prove that the LEP caused the failure.

2. **Attribution.** The benign baseline is provided for attribution context, but M4 itself does not compute an attribution score. The report's language ("ground-truth downstream task failure") is descriptive, not causal.

3. **Propagation effectiveness.** M4 measures whether the final deliverable is wrong, not whether the LEP's content propagated through the agent graph. M3 measures propagation; M4 measures consequences.

4. **Agent degradation.** M4 does not measure whether the agent performed worse than it would have without the LEP. It only measures whether the final output is incorrect.

## 5. Consequences of Benign Failures for M4

### 5.1 If benign baseline is 47.5%

The benign baseline of 47.5% means that nearly half of all clean executions end in task failure. This has several implications for M4:

1. **Task difficulty dominates.** The high benign rate suggests these tasks are genuinely difficult for the agent. M4's 6.53% failure rate is actually *lower* than the benign rate, suggesting that LEP executions may be *less* likely to fail than clean executions.

2. **M4 as "avoidance of failure"** — LEP executions might be failing *less* because the LEP changes the agent's behavior in ways that bypass the grader's strict criteria. This is not propagation — it is a grader artifact.

3. **The 47.5% baseline sets a high bar for "attribution."** For an M4 claim to be meaningful, the LEP must increase the failure rate *above* 47.5%. Currently, it does not.

### 5.2 If benign baseline is corrected to ~25-35% (after grader fixes)

If independent annotation reveals that the true benign failure rate is 25-35% (correcting for grader false negatives), then:
- M4's 6.53% is still below the corrected baseline.
- The conclusion remains: LEP executions are not more likely to fail than benign executions.
- However, the margin is smaller, and some family-level comparisons might show LEP elevation.

### 5.3 If M4 required benign success

If M4 were redefined to require the benign reference to succeed (i.e., "LEP failure minus benign success"), the M4 count would drop significantly. From the existing data:

- code_review benign success rate: ~29.1% (v2)
- research_synthesis benign success rate: ~38.6% (v2)
- financial_analysis benign success rate: ~88.3% (v2)

For configurations where the benign reference fails, an M4 claim would be impossible. This would disproportionately affect code_review (where 70.6% of benigns fail) and research_synthesis (59.5%).

## 6. Feasible Matched Analysis

A matched benign-vs-LEP analysis is feasible using existing data:

1. For each M4-positive LEP row, retrieve its 3 benign references.
2. Grade the benign references (already done in `grade_benign`).
3. Compute:
   - `benign_all_fail`: all 3 benign refs are `failure`
   - `benign_some_fail`: 1-2 benign refs are `failure`
   - `benign_all_pass`: all 3 benign refs are `correct` or `not_gradeable`
4. Cross-tab: M4 failure vs. benign outcome.

This would reveal:
- How many M4-positive rows have benign references that also fail.
- Whether the "strong attribution" category (benign all pass, LEP fails) exists.
- Whether M4 failures are concentrated in configurations where benign also fails.

This analysis does NOT require new data, new grading, or new inference. It is a straightforward computation from the existing per-trace JSONL and the benign grading cache.

## 7. Summary

| Question | Answer |
|---|---|
| Does M4 require benign success? | **NO** — M4 does not check benign references |
| Does M4 compare against benign baseline? | **NO** — baseline is reported separately |
| What does M4 measure? | M3-supported LEP executions with task failure |
| Is M4 causal? | **NO** — it is descriptive, not causal |
| Is M4 failure rate above benign baseline? | **NO** — 6.53% vs 47.5% |
| What would strengthen attribution? | Matched benign-vs-LEP analysis + independent annotation |

The current M4 definition is valid as a descriptive statistic, but it should not be interpreted as evidence of causal propagation. The benign baseline context reveals that M4's failure rate is actually *below* the no-perturbation failure rate, which complicates the propagation narrative.
