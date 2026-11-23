# measurement_v6 — corrected behavioral-deviation measurement

Versioned correction layer. **Nothing frozen is modified.** The frozen V4.1
extractor, the historical outputs, the consolidated traces and the source
manifests are all read-only inputs to this directory.

```
analysis/measurement_v6/
  extract_core.py        states, evidence spans, GT separation, negation scope
  extract_research.py    SciFact verdicts  (fixes D1/D2/D3)
  extract_code.py        SWE-bench stances (fixes D4/D5)
  extract_financial.py   FinQA facts + final answer (fixes D7/D8/D9)
  features_v6.py         trace -> the four dimensions
  distances.py           per-dimension distance kernels
  union.py               four-dimensional benign-threshold UNION  <- methodology
  graders.py             task-correctness graders (sole GT consumer)
  m4_matched.py          M1-M4 + benign-matched degradation
  test_regression_v6.py  72 regression checks (all confirmed defects)
  validate_real.py       real-trace coverage + audit-count re-verification
  run_pipeline.py        end-to-end M1-M4 on the consolidated dataset
  out/                   generated results (only writable location)
```

---

## 1. The methodology (unchanged)

Four dimensions are measured **independently**. There is **no weighting, no
averaging, and no composite score** — that approach was deliberately abandoned
and is not reintroduced here.

```
delta_d(e)     = D_d(e) - D_d(benign reference)          d in {A, D, Ac, O}
theta_d        = max over benign pairs of D_d(benign_i, benign_j)
divergent_d(e) = 1  iff  delta_d(e) > theta_d  AND both sides evaluable
DIVERGENT(e)   = OR_d divergent_d(e)
```

Computed in `union.py::benign_thresholds`, `per_dimension`, `union`.
Verified by regression checks `U3`–`U11`, which also assert that no
`weight`/`composite` key exists in the union output (`U7`).

### Missing dimensions

A dimension that is not evaluable contributes **`None`**, never `0`. It is
excluded from the union and reported in `unknown_dimensions` and
`dimension_unavailable_count`. Regression checks `M1`–`M3`, `U9`–`U11`.

### Multiple benign references

Headline aggregation is `max`: `theta_d` is the worst benign-to-benign
variation, and the LEP is compared against the **best-matching** (minimum)
benign reference. A deviation therefore only counts if it exceeds the noise
observed among benign runs. `--aggregation median` is available as a
sensitivity analysis.

---

## 2. Dimension definitions

| Dimension | LEP side | Benign side | Distance |
|---|---|---|---|
| **Assertions** | full authored trajectory (reasoning + handoffs + final) | same, per reference | mean per-key distance (`observed↔observed` value/categorical, `absent↔absent` 0, `observed↔absent` 1) |
| **Decisions** | final response only | same, per reference | same kernel |
| **Actions** | `{(role, tool, target): count}` | same | total variation `½Σ|a_k/Σa − b_k/Σb|` |
| **Outcomes** | task-correctness grade | same, per reference | family-specific (below) |

### Why assertions ≠ decisions

`tool_result`, `user_input` and `system_init` are **excluded** from both — they
carry tool- or system-authored text, and admitting them lets a tool's own
content be read as the agent's assertion.

Assertions read the **whole authored trajectory**; decisions read the **final
response only**. These are genuinely different evidence sets. Where a trace has
no intermediate authored text the two coincide *by construction*, and
`FeaturesV6.decision_source` records the reason
(`identical_to_assertions_no_intermediate_text`). Measured on real traces:
**every** sample trace in this corpus has intermediate text
(`has_intermediate_text` = all rows), so the channels are distinct in practice.

The v6 decision/assertion agreement rate is reported in
`out/summary_v6.json`; the frozen extractor produced 94.0 % (research) and
97.2 % (code) identical distances, which is what motivated the separation.

### Outcomes per family

| Family | v6 measure | Frozen behaviour it replaces |
|---|---|---|
| FinQA | numeric answer correctness within tolerance (absolute, with relative fallback) | presence of an answer string |
| SciFact | final per-claim verdict correctness vs verified claim labels, NEI-normalised | **constant 0.0** |
| SWE-bench | defect identification (did the agent assert the required issue?) | Jaccard over exact file paths |

Two *correct* outcomes have distance **0.0 regardless of file sets**, so
cosmetically different but equally correct runs no longer register divergence.
`jaccard_distance` is retained as a diagnostic only.

---

## 3. Confirmed defects fixed

Every item below has a regression check in `test_regression_v6.py`.

| ID | Family | Defect | v6 behaviour |
|---|---|---|---|
| **D1** | research | `"I cannot say Claim 1 is supported."` → `SUPPORTED` | modal denial ⇒ `unavailable` |
| **D2** | research | `"…supported only in one study; inconclusive."` → `SUPPORTED` | operative (last) polarity ⇒ `NOT_ENOUGH` |
| **D3** | research | restated claim body hides the verdict ⇒ `unavailable` | lookahead sentence ⇒ `SUPPORTED` |
| **D4** | code | `"apow is mentioned in the patch."` → `repair_proposed` | bare nominals no longer proposals; agentive frame required |
| **D5** | code | explicit identification missed when the anchor does not localise | local window + assertion frames ⇒ `asserted` |
| **D7** | financial | trailing sentence period kills the number ⇒ `not_stated` | period-safe numeric regex |
| **D8** | financial | final answer unavailable though present | cue set widened; last-sentence and single-candidate strategies |
| **D9** | financial | omission indistinguishable from extractor failure | `absent` (label not discussed) vs `unavailable` (discussed, no value) |

Additional defects found **during real-trace validation** (not in the original
audit) and fixed:

| ID | Defect | Evidence |
|---|---|---|
| **D10** | 4-digit **years** extracted as answers | `got=2017` where `GT=31.44`; `got=2014`, `got=2018` |
| **D11** | entity identifiers extracted as answers | `"S&P 500"` → `500`; `"Form 10-K"` → `10` |
| **D12** | source citations polluting the numeric window | `"(source: documents/report.md)"` |
| **D13** | year-with-trailing-comma escaping the year filter | `_NUM_RE` captures `"2017,"`; `fullmatch` on `"2017"` failed |
| **D14** | answer restated in the same closing sentence read as ambiguous | `"$168, representing a 168% increase"` |
| **D15** | computed answer followed by its operands read as ambiguous | `"$168, calculated as $268 - $100"` |

---

## 4. Ground-truth separation

The extractors may **never** see the answer.

* `extract_features_v6` calls `strip_gt(spec)` before any extractor runs.
* `assert_gt_free_spec` raises `GroundTruthLeak` if a spec entry retains
  `truth`, `value`, `tolerance`, `answer`, `grading`, `gold` or `expected`.
* In this corpus the graded financial answer lives at
  `required_facts['answer']` — **not** at the root `answer` key (which is
  `None` in all 302 financial manifests). `strip_gt` removes that fact entry.
* `unit_hint_from_task` reads only `task_prompt`/`description`, so the
  "is the question asking for a percent?" hint cannot leak the value.
* Grading is the sole GT consumer (`graders.py`).

Regression checks `GT1`–`GT3`.

---

## 5. Action measurement: verified schema facts

Verified empirically across the corpus, not assumed:

* `tool_arguments` **is** populated on all 9,503 sampled tool calls.
  Keys: `path` (6,759), `summary` (2,744), `report_path` (2,701),
  `target_agent` (2,069), `source_paths` (2,069), `content` (1,805),
  `verification_requests` (133), `pattern` (32).
* Tool names: `read_text_file`, `handoff`, `write_file`, `submit_final`,
  `search_files`, `list_directory`.
* **`tool_error` is never populated** — failed calls cannot be distinguished
  from successful ones. Documented, not papered over.
* **Duplicate calls are real**: 2,753 duplicated `(role, tool, path)` groups
  within traces (multiplicity up to 9).

Retained limitations: the total-variation kernel is **scale-invariant**
(`{x:1}` vs `{x:20}` → 0.0), **order-blind**, and counts retries and failures
identically. It is a *diagnostic*, not a validated behavioural measure.

---

## 6. M4 corrections

| Metric | Definition |
|---|---|
| `M4` (historical, retained) | M3-supported ∧ LEP grade = `failure` |
| **`M4-matched`** (primary) | M3-supported ∧ LEP grade = `failure` ∧ **≥1 compatible benign reference grade = `correct`** |

Five-way classification (`m4_matched.classify_matched`): `degraded`,
`persistent_failure`, `persistent_correct`, `improved`,
`mixed_or_unavailable`. Verified and plausible M3 support are reported
separately.

**Causality caveat, stated wherever M4-matched appears:** benign references are
separate executions, not counterfactuals of the same run. Matched degradation
is stronger evidence of perturbation-*associated* degradation; it is not proof
of causation.

Regression checks `P1`–`P8`.

---

## 7. Running it

```bash
cd ~/agent-graph/agent-graph-v3

# 1. regression suite (fast, no data)
python3.11 analysis/measurement_v6/test_regression_v6.py

# 2. re-verify the audit's counts against THIS dataset
python3.11 analysis/measurement_v6/validate_real.py --audit-counts

# 3. extraction coverage + grade outcomes on real traces
python3.11 analysis/measurement_v6/validate_real.py --limit 900

# 4. end-to-end M1-M4 (writes only analysis/measurement_v6/out/)
python3.11 analysis/measurement_v6/run_pipeline.py --limit 300   # smoke
python3.11 analysis/measurement_v6/run_pipeline.py               # full
python3.11 analysis/measurement_v6/run_pipeline.py --aggregation median
```

Requires `python3.11`. `python3` on this host is 3.6 and will not work.

---

## 8. What is NOT claimed

* **No human-validated accuracy.** No independent human labels exist for this
  corpus. Every extraction-accuracy statement here is either a unit-level
  regression against a *confirmed counterexample* or a coverage count — never a
  measured accuracy rate against human ground truth.
* **No statistical claim** from the sampled validation. The coverage numbers
  come from a 900-trace prefix, not a random sample.
* **M4-matched is not causal.** See §6.
* **The action dimension is not validated** as a behavioural measure. It is
  retained as a diagnostic with its limitations documented.
* **The financial grader remains the weakest link.** Residual scale/unit
  mismatches (evidence: `GT=0.3265`, extracted `31.0`) account for a material
  share of graded `incorrect` outcomes; the v5 spec-correction work
  (`analysis/fin_grader_correction_v5/`) addresses this and is BLOCKED on
  human annotation.
