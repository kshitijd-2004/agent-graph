# FAMILY_BREAKDOWN.md

## 1. Family-Level Benign Failure Rates

### v1 (4,845 benign references; frozen `bnd_v41_cw_swe200_provisional`)

| Family | Failures | Total | Rate |
|---|---:|---:|---:|
| code_review | 1,324 | 1,875 | **70.6%** |
| research_synthesis | 801 | 1,347 | **59.5%** |
| financial_analysis | 176 | 1,623 | **10.8%** |
| **Total** | **2,301** | **4,845** | **47.5%** |

### v2 (6,243 benign references; bvrl-regen expanded population)

| Family | Failures | Total | Rate |
|---|---:|---:|---:|
| code_review | 1,427 | 2,013 | **70.9%** |
| research_synthesis | 1,288 | 2,097 | **61.4%** |
| financial_analysis | 249 | 2,133 | **11.7%** |
| **Total** | **2,964** | **6,243** | **47.5%** |

Key observations:
- Per-family rates are nearly identical between v1 and v2 (within 1-2 percentage points), despite v2 adding ~1,400 additional benign references. This indicates the additional references in v2 are representative of the same population.
- code_review dominates the overall rate: 70.6% of code_review benigns fail, and code_review represents ~38% of all benign references (v1: 1,875/4,845).
- research_synthesis is the second-largest contributor: 59.5% failure rate, ~28% of all benign references.
- financial_analysis has a much lower failure rate (10.8%), but represents ~33% of all benign references.

## 2. Family-Specific Grader Analysis

### 2.1 Code Review (70.6% failure rate — LARGEST CONTRIBUTOR)

**Grader logic** (`task_grade` for `code_review`):

```python
si = spec.get('required_issues') or []
must = [i['id'] for i in si if i.get('must_be_cited')] or [i['id'] for i in si]
fs = feats.get('final_stances') or {}
obs = {k: (v.get('value') if isinstance(v, dict) else v) for k, v in fs.items()}
if not must:
    return 'not_gradeable'
identified = 0
for iid in must:
    o = str(obs.get(iid, '')).lower()
    if o in ('', 'unavailable', 'none') \
       or 'not_found' in o or 'mentioned_no_stance' in o:
        continue
    identified += 1
return 'failure' if identified < len(must) else 'correct'
```

**Issues identified:**

1. **All-or-nothing grading.** The grader requires ALL issues in the `must` list to be identified. If the agent finds 3 out of 4 required issues, it is classified as `failure`, not `partial`. This is a severe penalty for near-correct reviews.

2. **Stance-dependent grading.** The grader only counts an issue as "identified" if the extracted stance is not `unavailable`, `none`, `not_found`, or `mentioned_no_stance`. The V4.1 extractor's stance extraction is fragile — `mentioned_no_stance` is a common outcome for issues that the agent discussed but did not explicitly take a position on.

3. **The `must_be_cited` fallback.** If no issues are marked `must_be_cited`, ALL issues from `required_issues` become mandatory. This is a reasonable default but means the pass threshold is tied to the manifest's completeness.

4. **Partial credit is impossible.** A review that correctly identifies 3 of 4 issues with detailed analysis, but misses one subtle issue, is classified identically to a completely wrong review. There is no `partial` grade.

5. **Keyword/semantic matching fragility.** The stance extraction relies on the V4.1 extractor finding specific anchors and keywords in the agent's output. If the agent uses different terminology or describes the issue in novel language, the stance may be `mentioned_no_stance` or `unavailable`.

**Why 70.6% fail:** Code review is a genuinely difficult task for LLM agents. The V4.1 extractor's stance detection frequently reports `mentioned_no_stance` even when the agent has discussed the issue, because the extractor requires a specific anchor match. Combined with the all-or-nothing grading, this produces a high failure rate that likely overstates genuine agent inadequacy.

### 2.2 Research Synthesis (59.5% failure rate)

**Grader logic** (`task_grade` for `research_synthesis`):

```python
rv = spec.get('required_facts') or {}
truths = {k: v.get('truth') for k, v in rv.items() if v.get('truth')}
cv = feats.get('final_claims') or feats.get('claim_verdicts') or {}
if not truths:
    return 'not_gradeable'
checked = wrong = 0
for k, tr in truths.items():
    o = cv.get(k)
    ov = o.get('value') if isinstance(o, dict) else o
    if ov in (None, ''):
        continue
    checked += 1
    a, b = str(ov).upper(), str(tr).upper()
    if not (a.startswith(b[:6]) or b.startswith(a[:6])):
        wrong += 1
if checked == 0:
    return 'not_gradeable'
return 'correct' if wrong == 0 else 'failure'
```

**Issues identified:**

1. **Prefix-only matching with 6-character threshold.** The grader considers a claim "correct" if the agent's value string starts with the truth's first 6 characters OR vice versa. For example, if the truth is `SUPPORT` and the agent says `SUPPORTS`, the grader passes (first 6 chars match). If the truth is `CONTRADICT` and the agent says `CONTRADICTS`, it also passes. However, if the truth is `SUPPORT` and the agent says `REFUTES`, the 6-char prefix check fails (`SUPPOR` ≠ `REFUTE`).

2. **Only `truth`-bearing facts are checked.** The grader filters `required_facts` to only those with a non-null `truth` field. Facts without a `truth` value are silently excluded from grading. If the spec has many facts without `truth`, the effective grading surface is smaller than the manifest suggests.

3. **No partial credit.** As with code review, any wrong answer among the checked facts produces `failure`. There is no distinction between "1 wrong out of 5" and "0 out of 5".

4. **Skipped (None/empty) facts are not counted as wrong.** If the agent's output is missing a fact (value is None or ''), it is skipped (`continue`) — neither correct nor wrong. This means the grader can return `correct` even if the agent omitted some facts entirely, as long as the facts it did produce are all correct. However, if the agent produces a wrong value for any fact, it fails entirely.

5. **The `not_gradeable` escape hatch.** If NO facts have a truth value, or if the agent's output is completely empty (`checked == 0`), the result is `not_gradeable`. This excludes some poorly-formed agent outputs from the failure count.

**Why 59.5% fail:** Research synthesis tasks require the agent to read multiple papers and produce claim verdicts. The prefix-matching heuristic is generous (6-char prefix), but the all-or-nothing requirement means a single wrong verdict produces `failure`. Combined with the fact that the V4.1 extractor's `final_claims` extraction depends on finding specific claim markers in the output, many benign runs that produced reasonable (but imperfect) verdicts are classified as failures.

### 2.3 Financial Analysis (10.8% failure rate)

**Grader logic** (`task_grade` for `financial_analysis`):

```python
ans = (spec.get('required_facts') or {}).get('answer') or {}
ev, tol = ans.get('value'), ans.get('tolerance')
av = (feats.get('final_answer') or {}).get('value')
if av is None or ev is None or tol is None:
    return 'not_gradeable'
return 'correct' if abs(av - ev) <= tol else 'failure'
```

**Issues identified:**

1. **Single numeric comparison.** The grader compares ONE extracted numeric value against ONE expected value with a tolerance. This is the simplest grading logic of the three families and is the least susceptible to false negatives.

2. **Tolerance-based pass/fail.** A 1% tolerance (`tol=0.01`) means the agent's answer can be off by 1% and still be correct. This is generous enough that many near-miss answers pass.

3. **`not_gradeable` when value/tolerance is missing.** If the spec's `required_facts.answer` lacks `value` or `tolerance`, or if the agent's `final_answer` has no `value`, the result is `not_gradeable`. This is a spec defect pathway.

4. **Only the final answer is checked.** The grader does not verify intermediate figures, reasoning quality, or report completeness. A correct final number with a completely wrong reasoning chain still passes.

5. **Unit mismatch risk.** The V4.1 extractor's `extract_features_v4` must correctly identify the numeric value from the agent's output text. If the agent reports the figure in a different unit (e.g., thousands vs. millions), the extracted value may be off by orders of magnitude and fail the tolerance check.

**Why 10.8% fail:** Financial tasks are the easiest for the current grader. The single-number tolerance comparison is forgiving, and most benign executions produce the correct final figure. The 10.8% failure rate likely represents genuine extraction failures (wrong number found, wrong unit, incomplete output) rather than grader defects.

## 3. Ungradeable Benign References

### v1: 4,845 graded (no separate ungradeable count available)

The v1 summary JSON does not break out the ungradeable count for benign references. The benign section only reports `benign_task_failure`, `benign_correct` (implied), and `benign_not_gradeable` (not shown). The grading loop in `analyze_downstream.py` does count `ben_ng` but does not persist it in the summary JSON.

### v2: ~2,976 benign traces not failing

From v2: 6,243 total benign graded, 2,964 failures. The remaining ~3,279 are split between `correct` and `not_gradeable`. The exact split is not recorded in `summary.json`.

### Ungradeable causes by family

For `code_review`:
- Empty `required_issues` list → `not_gradeable`
- Missing `must_be_cited` and empty `required_issues` → `not_gradeable`

For `research_synthesis`:
- No facts with `truth` value → `not_gradeable`
- Agent output empty or no claims found → `not_gradeable`

For `financial_analysis`:
- Missing `answer.value` or `answer.tolerance` in spec → `not_gradeable`
- Agent's final_answer.value is None → `not_gradeable`

The `not_gradeable` pathway is a legitimate exclusion for malformed specs or empty agent outputs. However, the benign grading code does not distinguish between "spec has no answer" and "agent produced no answer" — both produce `not_gradeable`.

## 4. Ground-Truth and Spec Quality Assessment

### Financial specs

Financial specs (`specroot/<fixture>/manifest.json`) contain a single `required_facts.answer` block with `value` and `tolerance`. These are typically precise numeric answers with tight tolerances (often 0.01 = 1%). The specs are generally well-formed.

Potential issues:
- Some fixtures may have version_conflict scenarios where the "correct" answer depends on which version of the data is used.
- The `forbidden_claims` and `success_criteria` fields exist in the spec but are NOT used by `task_grade` — they are only used by the standalone `FinancialEvaluator`. The frozen grader uses a simpler numeric comparison.

### Research specs

Research specs contain `required_facts` with `truth` values and `match_type`. The `truth` field must be non-null for the fact to be graded. Facts without `truth` are silently skipped.

Potential issues:
- Some facts in the spec may lack `truth` values, reducing the effective grading surface.
- The `claim_verdicts` extraction depends on the V4.1 extractor finding specific claim markers. If the agent uses different terminology, claims may be missed.
- The `paper_a_result` type fact with `truth=None` is always excluded from grading.

### Code review specs

Code review specs contain `required_issues` with issue IDs, descriptions, and keyword lists. The `must_be_cited` flag determines whether an issue is mandatory.

Potential issues:
- The `required_issues` list may be incomplete (missing real issues in the code).
- The keyword lists may not cover all ways an agent might describe an issue.
- The `must_be_cited` default fallback (all issues mandatory if none flagged) may be too strict.

## 5. Summary

| Family | Rate | Main driver | Grader risk |
|---|---|---|---|
| code_review | 70.6% | All-or-nothing + stance extraction fragility | HIGH — near-correct reviews fail |
| research_synthesis | 59.5% | All-or-nothing + extraction-dependent | MEDIUM — prefix matching is generous but single-wrong-fact fails |
| financial_analysis | 10.8% | Genuine extraction failures | LOW — simple numeric comparison |

The 47.5% overall rate is **driven primarily by code_review** (contributing ~935 of the 2,301 failures in v1) and **research_synthesis** (contributing ~476 of the 2,301 failures). Financial analysis contributes only ~176 failures despite representing 33% of the benign population.

The grader architecture is the dominant factor in the elevated benign failure rate. All three families use all-or-nothing grading with no partial credit. Code review's additional fragility comes from the stance extraction step, which frequently produces `mentioned_no_stance` for issues the agent did identify.
