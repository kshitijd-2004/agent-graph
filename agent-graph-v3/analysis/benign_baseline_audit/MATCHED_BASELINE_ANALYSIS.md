# MATCHED_BASELINE_ANALYSIS.md

## 1. Benign-to-Perturbed Comparability

### 1.1 Reference selection mechanism

Each comparable LEP row carries a `references` field containing 3 benign reference trace IDs. These are selected by the V4.1 BND analysis using the `discover` module, which matches on:

- Same fixture_id
- Same topology
- Same execution variant (`standard`)
- Same propagation mode (where applicable)
- Repetitions 0, 1, 2 (the fixed benign reference set)

The reference selection is **identical across V1/V2/V3/V4/V4.1** — the `run_manifest.json` explicitly states: "Identical to V1/V2/V3/V4 (imported discover); same population."

### 1.2 Reference set sharing

| Run | Comparable LEP rows | Unique reference sets | Unique benign refs | Avg LEP rows per ref set |
|---|---:|---:|---:|---:|
| v1 | 10,057 | 1,615 | 4,845 | ~6.2 |
| v2 | 12,891 | 2,081 | 6,243 | ~6.2 |

Each reference set is shared by approximately 6 LEP rows. This means the benign baseline is **not** computed from independent executions — it is computed from a shared-control design where each benign trace grades once and its result applies to all LEP rows that reference it.

**Implication**: The benign failure rate is trace-level, not cluster-level. A single benign trace graded as `failure` influences the baseline for ~6 LEP rows. If that benign trace is a false negative, it inflates the baseline for all 6 LEP rows.

### 1.3 Are all required benign references present?

**Yes.** The `grade_benign` function loads each reference trace from the staged directory. The v1 staging contains 7,319 benign trace files; the v2 staging contains 8,084 benign trace files. All 4,845 (v1) / 6,243 (v2) unique benign references referenced by comparable LEP rows were successfully loaded and graded.

The `load_trace` function returns `None` only if the trace_id is not found in the `_T2P` index. Since all benign refs are present in staging, this path was never triggered in the benign grading loop.

### 1.4 Do references correspond to the same fixture, topology, and variant?

**Yes.** The reference selection logic ensures that each LEP row's references match its own fixture, topology, and execution variant. This is verified by the `comparable: true` flag in the V4.1 ledger, which asserts that the LEP trace's execution shape matches the references' execution shape.

However, there is a subtlety: **the benign references are graded with the fixture's own spec, not the LEP trace's spec.** Since LEP and benign traces share the same fixture_id, they use the same spec. This is correct.

### 1.5 Survivorship bias in reference selection

**Potential bias exists, but its direction is conservative.**

The benign references are selected from traces that:
1. Were successfully produced in the original production run
2. Passed the admission gate (standard variant, valid structure)
3. Were successfully analyzed by V4.1 (comparable: true)
4. Have a complete 3-reference set

Traces that failed at any of these stages are excluded. This means the benign reference population is **not** a random sample of all possible benign executions — it is a selected subset of successfully completed, analyzable executions.

**Direction of bias**: If failed benign executions are systematically excluded, the benign baseline may **understate** the true benign failure rate. A production run with more failures would have a higher baseline. However, the LEP population undergoes the same selection process, so the comparison may still be valid.

### 1.6 Baseline population vs. M1–M4 population

The benign baseline is computed from the **same population** as M1–M4:

| Dimension | Benign baseline | M1–M4 |
|---|---|---|
| LEP types | N/A (benign) | 4 LEPs |
| Topologies | branch_and_verify, coordinator_workers, review_loop | Same |
| Propagation modes | single_origin, many_to_one (bv), one_to_many (coord) | Same |
| Execution variant | standard | standard |
| Comparable filter | N/A (all graded) | comparable: true |
| Scope | References from comparable rows | Same |

The benign baseline uses **all unique benign references from comparable LEP rows**. This means:
- If a configuration has 0 comparable LEP rows, its benign references are never graded.
- The benign baseline is **conditional on having at least one comparable LEP row**.

This is a minor selection effect: configurations with no comparable LEP rows are excluded from both the LEP analysis and the benign baseline. Since comparability requires matching execution shapes, this exclusion is symmetric.

### 1.7 Equivalent grading criteria

**Yes.** The benign baseline and M4 use the **identical** grading pipeline:

1. Same frozen V4.1 extractor (`extract_features_v4`)
2. Same `task_grade` function
3. Same spec files (`specroot/<fixture>/manifest.json`)
4. Same feature extraction logic

The only difference is the input: benign traces (no LEP) vs. LEP traces. This ensures apples-to-apples comparison within each family.

## 2. Possible Selection Bias

### 2.1 Reference set reuse

The ~6:1 ratio of LEP rows to unique benign references means that each benign trace influences the baseline for ~6 LEP rows. If the benign trace is a false negative (grader error), it inflates the baseline for all 6 LEP rows, making it harder for those LEP rows to show M4 "attribution."

**Direction**: This bias makes M4 more conservative (harder to claim propagation). It does not inflate M4.

### 2.2 Configuration selection bias

The 45-configuration calibration manifest was designed to be representative, but the actual benign baseline uses ALL comparable configurations (not just the 45 selected). The v2 population includes bvrl-regen traces that expand the reference set from 4,845 to 6,243, but the per-family rates remain stable.

### 2.3 Family imbalance

The benign reference population is not evenly distributed across families:

| Family | v1 count | v1 share | v2 count | v2 share |
|---|---:|---:|---:|---:|
| financial_analysis | 1,623 | 33.5% | 2,133 | 34.2% |
| code_review | 1,875 | 38.7% | 2,013 | 32.2% |
| research_synthesis | 1,347 | 27.8% | 2,097 | 33.6% |

The overall 47.5% rate is a **population-weighted average**, not a per-family average. The high code_review rate (70.6%) dominates because code_review represents ~33-39% of the population.

**Implication**: The 47.5% figure should not be interpreted as "the probability that an agent fails any of these tasks." It is the probability that an agent fails a task, weighted by the family distribution in the comparable population. A population with more financial tasks would have a lower baseline.

## 3. Matched Benign-vs-Perturbed Analysis Feasibility

### 3.1 What can be done with existing data

A matched analysis is feasible using the existing per-trace data. For each LEP row with `m4_task_failure: true`, we can:

1. Retrieve its benign references
2. Check the benign references' task grades
3. Compare: did the benign reference(s) also fail?

This would answer: "Among M4-positive LEP rows, how many had benign references that also failed?"

### 3.2 Why this matters

If benign references frequently fail for the same configuration, the M4 attribution is weakened. The current M4 definition does NOT require the benign reference to succeed — it only requires the LEP trace's final deliverable to be a `failure`. This means:

- M4 counts LEP failures regardless of whether the benign reference also failed.
- If benign and LEP both fail for the same fixture, the failure may be due to task difficulty, not the LEP.

### 3.3 Proposed matched analysis

For each M4-positive row:
- Get the 3 benign reference grades
- Compute: `benign_failure_count / 3` (what fraction of benign refs failed?)
- Classify:
  - **Strong attribution**: benign refs all correct, LEP fails → likely LEP-caused
  - **Weak attribution**: some benign refs fail, LEP fails → may be task difficulty
  - **No attribution**: all benign refs fail, LEP fails → likely task difficulty

This analysis would require a new script but no new data — it can be computed from the existing per-trace JSONL and the benign grading cache.

## 4. Summary

| Bias type | Direction | Magnitude | Impact on M4 |
|---|---|---|---|
| Reference sharing (6:1) | Conservative | Moderate | Makes M4 harder to achieve |
| Survivorship (admission gate) | Slightly conservative | Small | May understate true benign rate |
| Family imbalance | Population-weighted | Moderate | 47.5% is not family-neutral |
| No benign-success requirement | Permissive | Moderate | M4 counts LEP failures even when benign also fails |

The benign baseline is a valid null hypothesis for M4 attribution, but it should be interpreted with these caveats:
1. The 47.5% rate is heavily weighted by code_review's 70.6% failure rate.
2. Per-family baselines are more informative than the pooled rate.
3. A matched benign-vs-LEP analysis would strengthen attribution claims.
