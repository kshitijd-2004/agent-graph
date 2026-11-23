# AgentProp Financial-Grader Validation Dataset (Phase 3 deliverable)

Built **2026-11-20**. Repository strictly read-only; every artifact here is under
`/tmp/agrade_audit2/`. No grader implemented, no repo file modified.

## Files
| file | content |
|---|---|
| `FINAL_PROTOCOL.md` | **Pre-registered protocol v1 (locked at annotation start)**: 2-row discrepancy resolution, populations, annotation rules, global gates G1–G6 with power, relaxed per-class gates, order of operations, limitations |
| `HOLDOUT_SUFFICIENCY.md` | Sufficiency verdict: global gates SUFFICIENT (n=775, power ≥97%); 3 rare classes underpowered → pre-relaxed gates; options (a) accept / (b) re-draw split, with recommendation (a) |
| `dataset_records.jsonl` | 6,539 records: all 4,406 deduped financial LEP executions (3 dup IDs flagged, not dropped from population bookkeeping) + 2,133 unique benign reference traces; each with `trace_id`, `trace_file`, `topology`, `propagation_mode`, `lep_code`, `stratum`, `split` (DEV / HELD_OUT_SEALED), `cv_fold`, frozen metric flags, and reconstructed audit flags (`flag_bucket`, `flag_atk_in_final`, `flag_tool_only`) |
| `split_manifest.json` | reproducible split: seed `20261121`, 51 sealed-holdout fixtures / 249 dev fixtures (5×~50 folds), exclusions, dedup list, tier-2 verification outcomes |
| `annotation_tasks_FINAL.jsonl` | **Final task list, 1,375 rows**: full 775-execution sealed acceptance population + 600 seeded dev-tuning executions (5×120 fold-balanced). Supersedes the 678-row `annotation_tasks.jsonl` |
| `spec_correction_manifest.json` | 35 confirmed unit defects + 1 value bug + 2 verified-valid + 2 no-answer exclusions + 20 negative-GT sign notes |
| `verification/EIGHT_SPECS_VERIFICATION.md` | doc-level evidence for the 8 tier-2 candidates |
| `verification/eight_specs_verification.json` | machine-readable per-spec verdicts with doc quotes |
| `verification/two_row_discrepancy.json` | 3,130-vs-3,128 resolution: exactly the 2 duplicated NG rows (`ilmn`→B_SCALE, `lly`→A_STRICT); ledger view reproduces prior 1,514/358/… exactly; no regex drift |
| `verification/gate_power.json` | gate power table + per-class feasibility + min-n (rule-of-three) note |
| `verification/value_audit.json` | all-302 answer-value vs 4-fact operation audit (0 unresolved; flags 219's division-instead-of-multiplication) |
| `verification/spec_dim.json`, `spec_audit.json` | dimensional/unit audit + negative-GT list (rebuilt this session from frozen specs) |

## Key numbers
- Population: 4,409 comparable financial LEP rows → 4,406 unique (3 dup trace IDs:
  `fin_finqa_020_ilmn_2007.bv…TRC.many_to_one`, `fin_finqa_130_awk_2017.bv…IPI.many_to_one`,
  `fin_finqa_280_lly_2008.rl…IPI.single_origin`).
- Frozen grade split (denominator 4,409): 747 correct / 532 failure / 3,130 not_gradeable.
- Rebuilt ungradeable flags (3,130): A_STRICT 1,513 · F_WRONG_VALUE 1,155 · B_SCALE 357 ·
  C_SIGN 59 · G_NO_NUMBER 12 · E_NO_SPEC 32 (±1 A↔B row from regex drift vs prior artifacts).
- Sealed set: 51 fixtures, 775 LEP executions, 372 benign refs, zero trace overlap with dev.
- No trace file missing (0/6,539); sealed/dev disjoint; split↔fixture consistent (0 errors).

## Open items for the annotation session
1. Dual-annotate `annotation_tasks.jsonl` per guide; adjudicate disagreements.
2. Sealed-set ceilings: sign 44 / trc_adopt 34 / omission 20 — report these as
   separate subcounts; do not claim sealed-only quota coverage.
3. `fin_finqa_219_adi_2015` (12 executions, stratum `value_bug_219`): judge against the
   *question*, flag the buggy GT; these rows are the exclusion candidates.
4. `financial_clean` / `financial_version_conflict` (32 executions, stratum
   `no_answer_EXCLUDE`): keep out of numeric grading; optional multi-fact diagnostic only.
