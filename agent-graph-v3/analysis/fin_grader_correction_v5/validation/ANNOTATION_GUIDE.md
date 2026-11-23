# Financial-Grader Validation Set — Annotation Protocol & Guide

Isolated temp dir: `/tmp/agrade_audit2/validation_dataset/` (repository is read-only; nothing here modifies the repo).
Reproducible inputs: `split_manifest.json` (seed, fixture split, folds), `dataset_records.jsonl` (all records),
`annotation_tasks.jsonl` (the 678-task quota-guaranteed list), `verification/` (spec evidence).

## 1. What to annotate and why

Two task lists exist:
- **`annotation_tasks.jsonl`** — 678 executions: 300 seeded from the sealed held-out set, 300 from the dev CV pool, top-uped to quotas. **Annotation happens on the full list; acceptance metrics are computed ONLY on the subset whose `split == 'HELD_OUT_SEALED'`.** Dev-list annotations are for rule debugging and are discarded before lock.
- Every record carries `trace_id` → `trace_file` (the JSON trace), `stratum`, and `sample_flags` (sign / scale / multi_cand / omission / trc_adopt).

Sealed-set flag ceilings (documented limitation from C7): sign 44, scale 102, trc_adopt 34, omission 20, multi_cand 558. The sealed set alone cannot fill the sign/trc/omission quotas; those quotas are met by the pooled list (dev + sealed). Consequence: **acceptance thresholds for the rare classes (sign accuracy ≥95%, omission abstention ≥90%) must be read against the pooled annotation with the sealed-only subcount reported separately** — do not claim sealed-only coverage for them.

## 2. Annotation fields (one row per execution)

| field | values / notes |
|---|---|
| `trace_id` | as given |
| `requested_quantity` | free text: what the task asked for (copy the `answer.label` question; for the 2 excluded fixtures, the multi-fact instruction) |
| `expected_type_unit` | one of: `dimensionless_ratio`, `percent`, `currency_usd_scaled` (thousands/millions), `share_count`, `signed_delta`, `negative_quantity`, `count`, `n/a` |
| `answer_present_in_final` | `yes` / `no` / `present_only_in_intermediate_or_tools` — based on the **final response text only** (`event_type == 'final_response'`) for "in final"; check handoffs/tool results for the third option |
| `annotator_extracted_value` | the number **a careful human would take as the agent's answer to the requested quantity**, with sign and a unit tag (e.g. `48.10`, `-3155 thousands`, `2.62 dimensionless`). Empty if none identifiable. |
| `answer_complete` | `yes` / `no` (no = the agent stated inputs but never stated the requested quantity) |
| `correct_vs_gt` | `correct` / `incorrect` / `ungradeable_no_answer` / `ambiguous` — judge against `specroot/<fid>/manifest.json` `required_facts.answer.{value,tolerance,unit}`, applying the **verified unit interpretation** for defect fixtures (see `spec_correction_manifest.json`; e.g. for `fin_finqa_002_cb_2008` the GT 2.6183 is dimensionless, so "2.62" is correct even though the spec says "USD millions") |
| `sign_handling` | `n/a` / `agent_sign_correct` / `agent_sign_missing` (magnitude stated, sign absent) / `agent_sign_wrong` |
| `ambiguity_reason` | none / multiple_competing_answers / units_unclear / version_conflict_in_docs / truncated / other (free text) |
| `frozen_grader_outcome` | read LAST, after committing the above: `correct` / `failure` / `not_gradeable` (from `frozen_task_grade` in the record — do not re-derive) |
| `frozen_grader_was_right` | `yes` / `no` (does the frozen outcome match `correct_vs_gt`? map: frozen `correct` ↔ `correct_vs_gt=correct`; frozen `failure` ↔ `incorrect`; frozen `not_gradeable` ↔ `ungradeable_no_answer` **or** `ambiguous`) |

### Anchor rule
Annotators must NOT see the frozen grader's extracted value or any prior audit's per-trace classification. `flag_bucket`, `class_precise3`-style fields, and `frozen_grader_outcome` are only revealed after `correct_vs_gt` is committed (the `frozen_grader_outcome` field is pre-filled in the task file — annotators copy it into the form at the end, not the beginning).

### Known-fixture caveats (from verification)
- `fin_finqa_219_adi_2015`: the spec's GT (56.6926) is a generation bug — the correct answer to the question is 128,397.8 thousand. Annotators must use the **question** ("total fair value …") to judge, not the buggy GT; mark `correct_vs_gt = correct_if_uses_question` and note it. These rows are tagged `stratum = value_bug_219`.
- `fin_finqa_051_dre_2008`: label says "2007" but the facts are the 2008 column; the intended GT value (1.5633 USD/share) is unaffected.
- The 35 confirmed unit-defect fixtures: judge against the **corrected dimension** in `spec_correction_manifest.json`, not the declared unit.

## 3. Adjudication
- Two annotators per row (minimum); disagreements on `correct_vs_gt` or `answer_present_in_final` go to a third adjudicator; ties broken by the written task question, never by the grader.
- Single-annotator cells are flagged `confidence: single` and excluded from acceptance metrics.
- Track per-cell inter-annotator agreement; report it in the validation report.

## 4. What the dataset does NOT support
- Population-level proportions: the set is quota-sampled, not probability-sampled. Report it as "annotated sample of N=678 (sealed subset N=300+topups)" with per-class n.
- M1/M2 claims: behavioral metrics are computed from the frozen feature ledger, not these records (C6 invariant).
