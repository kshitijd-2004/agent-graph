# BND Two-Cohort Compatibility — SWE 1–100 vs SWE 101–300

Scope: whether traces from the new 101–300 cohort can be scored under the same
frozen BND rules as the original 1–100 production cohort, and what differs
substantively.

## Verdict
The two cohorts are **compatible at the rule level but not at the reference level**.
They share the BND V4.1 rulebook (`need_at_least_3_benign_references`, the
`benign_references` B1/B2/B3 shape arbiter, and the mode-free structural guard
against O2M↔worker-first-SO mismatches). But cohort B (101–300) has **no frozen
benign references yet**, so its traces are not *comparable* until its own benign
controls are generated.

## Grounding (verified against the repo)
- The V4.1 calibration manifest
  (`analysis/bnd_v41_benign_calibration_manifest/calibration_manifest.json`)
  references **12** SWE fixture IDs, all in the **1–100** range, and **0** in
  101–300. So no frozen reference pool exists for the new cohort.
- Frozen trace pools (`analysis/full_production_consolidated/source/{traces,
  rejected_traces}`) contain code_review references only for 1–100.
- The V4.1 mode-free report (`analysis/full_production_baseline_deviation_v41_mode_free/REPORT.md`)
  documents the structural guard: for `one_to_many` coordinator_workers, 100% of
  the recorded O2M shape is **coordinator-first** while the pre-existing SO benign
  pool is **worker-first**, so the shape arbiter selects **0** compatible refs
  (`need_at_least_3_benign_references:0`). No O2M trace is comparable until
  coordinator-first controls exist.

## Substantive differences

| dimension | cohort A (1–100) | cohort B (101–300) | BND impact |
|---|---|---|---|
| Fixture root | `workspace_fixtures` | `workspace_fixtures_v3` | provenance tracked separately; no historical hash repointed |
| `fixture_manifest_sha256` | original main-root bytes | new v3 bytes | each cohort verifies against its own root |
| Grading representation | keyword-style `required_issues` | `patch-concepts-v1` blocks in `required_issues` | scored answers use a different rubric shape |
| Frozen benign refs | present (12 SWE fixtures in V4.1 manifest) | **none** | B traces not comparable until their 3 benign reps are generated |
| O2M CW comparability | needs coordinator-first CW benigns (structural guard) | same requirement; its new CW benigns must be coordinator-first | matches the scheduler fix (860c820) that made CW schedule coordinator-first in all modes |

## Implication for this launch
1. Generating the 200 scheduled fixtures' 3-rep benigns (1,800 executions) is what
   *creates* cohort B's reference pool. Until those traces exist, no 101–300 BND
   verdict can be produced — this is expected, not a defect.
2. The O2M `coordinator_workers` benign cells in this manifest are the
   single-origin standard benigns (3 reps), **not** the unadopted O2M-matched CW
   benign. The committed scheduler fix (860c820) means those SO benigns now
   schedule coordinator-first, which is the shape the V4.1 arbiter requires for
   O2M comparability. (The separate O2M-matched CW benign that would multiply
   CW benigns by mode is deliberately out of scope here.)
3. Cross-cohort score comparisons (A vs B) should be treated as **not directly
   comparable** because of the grading-representation difference; per-cohort
   comparisons are the intended unit of analysis.

## Residual risk carried from `analysis/swebench_300_repair/repair_notes.md`
Four cross-fixture grading collisions (fixtures 178, 150, 38, 176) remain in the
canonical BND grading workflow. They do not block execution but are a known
grading-representation risk for any cross-fixture comparison touching those IDs.
