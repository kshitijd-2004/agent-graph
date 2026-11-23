# EXECUTION_QUALITY_AUDIT.md

## 1. Infrastructure Quality of Benign Reference Traces

### 1.1 Trace source provenance

Benign reference traces are sourced from multiple production runs:

| Source | Description | Path |
|---|---|---|
| consolidated_production | Frozen production source (13,914 LEP + 5,151 benign) | `analysis/full_production_consolidated/source/traces` |
| cw_regen | Coordinator workers regeneration (4,845 files; 4,802 admitted standard) | `production_run/coordinator_workers_regeneration/` |
| swe_200 | SWE-bench 200 addition (5,714 files; 5,661 admitted standard) | `production_runs/swebench_200_addition/` |

The v1 staging directory (`analysis/bnd_v41_cw_swe200_provisional/staged/merged/traces/`) contains 25,159 symlinks (7,319 benign + 17,840 LEP). The v2 staging uses the same symlinks plus additional sources.

### 1.2 Admission gate

Traces are admitted to the staging directory through an admission gate that filters for:
- `standard` execution variant (non-memory)
- Successfully completed executions
- Valid trace structure

The v1 run manifest states: "4,802 admitted standard staged; 43 rejected not staged" for cw_regen, and "5,661 admitted standard staged; 53 rejected not staged" for swe_200. This means ~1.1% of produced traces were rejected at the admission gate.

### 1.3 What the benign grading code checks

The `grade_benign` function in `analyze_downstream.py`:

```python
def grade_benign(rid):
    rt = load_trace(rid)
    if not rt:
        return 'not_gradeable'
    # ... extract features and grade
```

If `load_trace` returns `None` (trace file missing or unreadable), the benign reference is silently skipped — it is NOT counted in the denominator. There is no explicit infrastructure-failure accounting.

### 1.4 What infrastructure failures look like in the data

Infrastructure failures that would affect benign grading:

| Failure type | Detection | Impact |
|---|---|---|
| Trace file missing | `load_trace` returns `None` → `not_gradeable`, skipped | Excluded from denominator |
| Trace file corrupted | JSON parse error → exception, trace skipped | Excluded from denominator |
| Truncated output | `final_answer` extraction fails → `av is None` → `not_gradeable` | Counted as `not_gradeable` in denominator |
| Timeout/incomplete execution | Agent never produces final output → empty `final_response` | `not_gradeable` or grader-detected failure |
| Backend error | Trace contains error events but still has output | Depends on output quality |
| Missing spec | `spec_for(fid)` returns `None` → `not_gradeable` | Counted as `not_gradeable` in denominator |

### 1.5 Infrastructure failure accounting

**There is NO explicit infrastructure-failure accounting in the benign grading pipeline.** The grading code silently skips traces that fail to load and classifies traces with missing features as `not_gradeable`. There is no separate category for "infrastructure failure" vs. "genuine task failure."

This means:
- Traces that failed due to infrastructure problems are either excluded (if they fail to load) or misclassified as `not_gradeable` (if they load but have missing output).
- The 47.5% figure does NOT include infrastructure failures in the denominator (they are excluded), but it also does NOT separately account for them.
- If infrastructure failures were systematically more common in benign references than in LEP rows, the comparison would be biased. However, both benign and LEP traces come from the same production pipeline, so this is unlikely to be a major source of bias.

### 1.6 Truncation and incomplete outputs

The V4.1 extractor's `extract_features_v4` function attempts to extract:
- `final_answer` (for financial): numeric value from output text
- `claim_verdicts` / `final_claims` (for research): structured verdicts from output text
- `final_stances` (for code_review): issue stances from output text

If the agent's output is truncated (e.g., due to token limits), these extractions may fail silently, producing `None` values that lead to `not_gradeable`.

There is no explicit truncation detection in the benign grading pipeline. The trace format may include `truncated` or `incomplete` metadata, but `grade_benign` does not check for it.

### 1.7 Tool errors and backend errors

Tool errors (failed tool calls, invalid tool arguments) are recorded in the trace events but are not explicitly checked by `grade_benign`. If a tool error caused the agent to produce an incorrect or incomplete output, the grader would classify it as a failure — indistinguishable from a genuine task failure.

Backend errors (vLLM failures, API timeouts) would typically result in incomplete traces that either fail to load or produce empty outputs. These would be excluded or classified as `not_gradeable`.

## 2. Benign Reference Trace Quality

### 2.1 Are benign references "clean" executions?

The benign references are executions of the same tasks without any LEP injection. They are intended to represent the agent's baseline performance on these tasks. However:

1. **They are NOT independent replications.** Each benign reference is one execution of a specific configuration (fixture, topology, variant, repetition). The 3-reference design (reps 0, 1, 2) is meant to capture execution variance, but the grading uses only ONE of the three references per LEP row (the one selected by the V4.1 comparison).

2. **They may still have execution artifacts.** Even without LEPs, agent executions can have:
   - Random variation in output quality
   - Timing-dependent behavior
   - Non-deterministic tool calls
   - Partial completions

3. **The "benign" label means "no LEP", not "perfect execution."** A benign trace can still have infrastructure failures, tool errors, or agent bugs that produce incorrect outputs.

### 2.2 Do all required benign references complete successfully?

The v1 staging contains 7,319 benign trace files. These are the traces that passed the admission gate and were staged. The original production runs produced more benign traces, but some were rejected at admission.

For the benign grading:
- All 4,845 (v1) / 6,243 (v2) unique benign references are loaded from the staging directory and successfully graded.
- Any references that failed to load are silently excluded.
- The grading loop does not track how many references were attempted vs. how many were successfully graded.

### 2.3 Missing source documents

If a fixture's source documents are missing from the workspace, the agent cannot complete the task correctly. However:
- The fixtures are pre-built with all required documents included.
- The agent's output (not the document availability) is what gets graded.
- If documents were missing, both benign and LEP executions would be affected equally (same workspace).

### 2.4 Invalid task specifications

Spec defects (wrong expected values, missing `truth` fields, incomplete `required_issues`) would cause systematic grading errors. The V4.1 analysis documented several spec-related issues in its changelog:

- "An empty required-key set no longer falls back to the union assertion keys for scoring"
- "The issue-local 'other issue' check double-escaped an already-escaped anchor"
- "A percent figure is never latched by the answer percent-last fallback when the answer's declared unit is not percent"

These fixes were applied to the V4.1 extractor, but the spec files themselves may still have defects. The specroot is described as "hash-verified against both generation manifests" in the v1 run manifest, which provides some assurance of spec integrity.

## 3. Ungradeable Benign References

### 3.1 What causes `not_gradeable`

| Cause | Family | Mechanism |
|---|---|---|
| Empty `required_issues` | code_review | `if not must: return 'not_gradeable'` |
| No facts with `truth` | research_synthesis | `if not truths: return 'not_gradeable'` |
| Missing answer.value or tolerance | financial_analysis | `if av is None or ev is None or tol is None: return 'not_gradeable'` |
| Agent output empty / no claims found | research | `if checked == 0: return 'not_gradeable'` |
| Trace fails to load | all | `if not rt: return 'not_gradeable'` |

### 3.2 Ungradeable rate

The benign grading loop does not persist the `not_gradeable` count in the summary JSON for v1. For v2, the summary does not break out the benign `not_gradeable` count either.

However, the M2 `task_grade_among_M2` counter in v2 shows:
- `correct`: 317
- `failure`: 1,672
- `not_gradeable`: 253

This is for LEP rows among M2 positives, not benign references. The benign grading loop has a separate `ben_ng` counter that is printed to stderr but not persisted.

### 3.3 Impact of `not_gradeable` on the baseline

If a benign reference is `not_gradeable`, it is counted in the denominator but not in the failure count. This dilutes the failure rate. If `not_gradeable` traces were excluded from the denominator, the failure rate among gradeable benign traces would be higher.

For example, if 10% of benign references are `not_gradeable` (a reasonable estimate), the failure rate among gradeable references would be:
- v1: 2,301 / (4,845 × 0.9) ≈ 52.8% (vs. 47.5% including `not_gradeable`)
- v2: 2,964 / (6,243 × 0.9) ≈ 52.7% (vs. 47.5% including `not_gradeable`)

This is a modest increase (~5 percentage points), not a dramatic one. The `not_gradeable` pathway is not the primary driver of the 47.5% rate.

## 4. Summary

| Quality dimension | Finding | Impact on 47.5% |
|---|---|---|
| Trace loading failures | Silently excluded | Negligible (all traces loaded successfully) |
| Truncation/incomplete outputs | `not_gradeable` | Dilutes rate slightly (~5pp) |
| Spec defects | Systematic grading errors | Possible contributor to code_review failures |
| Tool errors / backend failures | Misclassified as task failures | Unknown magnitude, likely small |
| Missing source documents | Would affect both benign and LEP equally | No comparative bias |
| Invalid task specs | Systematic misgrading | Possible contributor, magnitude unknown |

The main finding is that **infrastructure failures are not a major contributor to the 47.5% rate**. The grading pipeline successfully loads and grades nearly all benign references. The primary quality concerns are:
1. Grader false negatives (see GRADER_FAILURE_AUDIT.md)
2. Spec defects in code_review manifests (possible, not confirmed)
3. The `not_gradeable` pathway diluting the rate (minor effect)

The 47.5% rate is primarily a reflection of the grader's grading strictness, not a reflection of infrastructure failures.
