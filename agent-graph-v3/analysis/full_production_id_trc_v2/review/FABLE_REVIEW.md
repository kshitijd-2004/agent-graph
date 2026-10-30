# Fable independent-review package — AgentProp ID/TRC v2

## Scope and status

Offline analyzer correction only; no benchmark/workflow/injector/trace changes and no agent executions, Slurm jobs, GPUs or external model calls. Detector suite: `agentprop-id-trc-v2`. The final run ingested all 37,761 stored files and retained the same 10,395 measured attempts. All 6,159 HC/IPI/MP metric rows are exactly equal to the frozen prior analysis. All 19 pre-existing output files and the source inventory were hash-verified unchanged. `tests.log` records 309 passing tests. These are implementation/regression checks, not independent human ground-truth validation.

Start with [comparison.md](comparison.md), [verification.json](verification.json), [protected_artifacts_verification.json](protected_artifacts_verification.json), and [methodology](../../../docs/id_trc_v2.md).

## Exact implementation surface

- `benchmark/id_trc_v2.py`: `shape`, `comparability`, `sessions`, `branch_reference`, `analyze_id`; `assertions`, `verified_tool_boundary`, `recorded_transports`, `analyze_trc`; shared result/path helpers. Entire file is new.
- `benchmark/production_propagation.py`: `CORRECTED_VERSION`, `ProductionPropagationAnalyzer.__init__`, `_analyze_single_trace`, `_write_outputs`, `_write_provenance`, and `summarize`. New dispatch, explicit incomparable/null handling, lifecycle/provenance fields, and unchanged-row compatibility for HC/IPI/MP.
- `scripts/analyze_propagation.py`: `main` accepts the new suite. Original suite/default and legacy remain available.
- `scripts/compare_id_trc_v2.py`: `classification_table`, `main` and output helpers produce complete-population comparisons and evidence deltas; not used by detection.
- `tests/test_id_trc_v2.py`: synthetic causal/branch, source/provenance, adoption/quotation/recovery, and twelve real-trace regression cases.
- `tests/test_production_propagation.py`: new-suite CLI isolation test and full-population comparison test with temporary saved artifacts.
- `docs/id_trc_v2.md`: criteria, limits and commands.

The two edited integration files have pre-task snapshot diffs alongside this note. `changed_files.json` and `../run_manifest.json` contain source hashes. Existing `behavioral_anomaly.py`, `propagation_analysis.py`, HC/IPI implementations and `event_graph_builder.py` are byte-identical to the pre-task snapshots.

## Methodological changes

ID requires compatible recorded workflow skeletons across all controls before supporting a comparison. Required predecessors are same-role-visit task input/output operations, witnessed across every corresponding slot occurrence and every clean repetition. Independent sibling actions and pure reasoning are excluded. Delivery, own-role input exposure, strict origin exclusion, downstream reachability, pre-existing omission exclusion and earlier-visit availability guards apply. Earlier-visit operations cannot be assumed to need repetition; those cases remain unknown.

TRC adds a separate boundary-provenance pathway that can detect active use in unmatched slots. It binds named assertions to verified before/after bytes and a receiver's recorded exposure; cross-agent attribution needs an identical directly linked handoff or a successful latest-version artifact transfer. Upstream independent/conflicting sources veto attribution. Quotes, fences, rejection and unrelated numbers cannot support adoption. Legacy matched-slot effects remain separately labeled and cannot override an explicit attribution failure. They are not counted as proven adoption solely because legacy classified them positive.

No new thresholds were tuned to the production positive fraction. Three stable repetitions, actual graph paths, content equality and fixture assertion identity govern the rules. Development checks exposed and fixed legacy fallback quotation revival and repeated-visit omission ambiguity; intermediate development outputs are not the final analysis.

## Priority results and endpoint coverage

All 459 coordinator-worker one-to-many ID traces are incomparable, with zero supported positives and zero negatives in that cohort. Every perturbed run starts with the coordinator; all 1,377 corresponding benign references start with specialist_a. The earlier audit's partial fan-out heuristic (452/459) did not establish equivalence of the complete start/fan-out/merge skeleton. Actual shape evidence for every trace is retained; do not interpret the new zero supported positives as an estimated zero causal-effect rate.

TRC has 301 traces with 452 boundary-proven adoption events. Of these, 158 events were absent from the old anomalous-event sets, across 129 traces; 93 adoption events occur in newly positive traces. 62 adoption events have no clean slot. There are also 349 positive traces supported only by retained legacy matched-slot effects: do not conflate these with proven adoption.

TRC lifecycle: 301 traces / 380 authored forwarding actions; 47 traces / 99 cross-agent supported events; 229 traces with some assertion recovery (259 adoption events subsequently restored); 123 traces with adoption and complete restoration of recognized changed assertions in the final answer; 36 traces with supported final contamination. These endpoints overlap and are not an exclusive partition. Unparsed/omitted final facts remain unknown. Restoring the original boundary values does not establish overall answer correctness.

ID recovery/persistence are unassessed. Final impact for ID denotes a supported omission at the final event, not a semantic correctness judgment.

## Classification and event evidence

- [All-LEP and complete ID/TRC configuration comparisons](comparison.md)
- [Classification transitions](classification_transitions.csv)
- [Every added/removed positive trace](changed_trace_evidence.jsonl), including original details, revised events, exact boundaries, clean witnesses and graph paths
- [Every newly identified TRC adoption event](new_trc_adoption_event_evidence.jsonl)
- [Lifecycle, coverage, depth and affected-agent distributions](lifecycle_and_coverage.json), also CSV
- [Complete native evidence](../detector_evidence.jsonl)

`event_depth` is the minimum supported origin-to-effect path length, preserving the unified metric convention. `max_supported_path_depth` records the furthest supported effect along its shortest evidence path. Handoff depth counts role changes along supported paths. A write attempt alone is forwarding, not proof of successful storage or receipt; cross-agent counts require verified transport.

## Real-trace review cases

Full exact native records and source/control paths: [real_trace_regression_evidence.json](real_trace_regression_evidence.json).

| Trace | Classification | Supported events | Final impact | Review focus |
| --- | --- | --- | --- | --- |
| code_review_swe_006_django_django_14493.coord.LEP_INPUT_DISREGARD.one_to_many.00.std__b | incomparable | — | unknown | serial/parallel code controls incomparable |
| code_review_swe_053_matplotlib_matplotlib_13989.coord.LEP_INPUT_DISREGARD.single_origin.00.std__b | positive | 34,35 | detected | retained legitimate same-branch report-write omission |
| fin_finqa_053_hst_2018.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | positive | 10 | not_detected | new adoption and complete recognized-assertion recovery |
| fin_finqa_053_hst_2018.coord.LEP_TOOL_RESULT_CORRUPTION.single_origin.00.std__b | positive | 10 | not_detected | retained matched-slot adoption |
| fin_finqa_076_ms_2013.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | positive | 10,20,47,48 | not_detected | recorded cross-agent transmission with recovery |
| fin_finqa_079_re_2016.rl.LEP_INPUT_DISREGARD.single_origin.00.std__b | unknown | — | unknown | high-risk false positive: earlier role visit may already have written report |
| fin_finqa_084_bdx_2018.coord.LEP_INPUT_DISREGARD.one_to_many.00.std__b | incomparable | — | unknown | serial/parallel financial controls incomparable |
| fin_finqa_146_mo_2014.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | unknown | — | unknown | high-risk false negative: worker assertion with unresolved transport |
| fin_finqa_169_mas_2010.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | unknown | — | unknown | high-risk false positive: 10-K numeric collision / unresolved boundary grammar |
| fin_finqa_228_awk_2018.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | negative | — | not_detected | high-risk false positive: date numeric collision |
| fin_finqa_242_c_2008.coord.LEP_TOOL_RESULT_CORRUPTION.one_to_many.00.std__b | negative | — | not_detected | high-risk false positive: derived-number collision |
| res_scifact_289_826_410.coord.LEP_INPUT_DISREGARD.one_to_many.00.std__b | incomparable | — | unknown | serial/parallel research controls incomparable |

## Highest-risk review questions

**False positives:** confirm that retained same-branch omission candidates represent required task actions, especially report writes; slot identities can be coarse (e.g. generic tool targets). Check retained legacy-only TRC effects separately from boundary-proven adoption. Verify every claimed cross-agent effect against the recorded source transfer. Repeated numbers, quoted/fenced claims, rejected values and dates are covered by explicit regressions but the bounded grammar is not a semantic oracle.

**False negatives / coverage:** the current grammar does not prove arbitrary derived-value adoption, scientific paraphrases or code semantics. Source tables without unique label/year/qualifier mapping are unresolved. Abbreviated/merged handoffs and unrecorded memory/artifact relationships cannot prove cross-agent transport. Financial fin_146 demonstrates active worker reuse that remains unknown under the recorded transport requirement. Many TRC unknowns reflect unrecognized boundary assertions rather than evidence of no effect. These are coverage limitations, not adjudicated failures.

**Controls:** ID cannot be resolved for the incompatible fan-out population with the current controls. Generate, in a separate task, three benign repetitions per matched fixture/variant using coordinator-first decomposition, the identical parallel receiver set and merge schedule. No such executions were generated here. The shape guard is necessary but does not by itself prove experimental exchangeability. Other shape-incompatible cases and earlier-visit ambiguities are enumerated in the full evidence.

## Reproduction

From the repository root, use the commands in [docs/id_trc_v2.md](../../../docs/id_trc_v2.md). The analyzer requires a fresh output directory; choose another directory when repeating the saved run. Then run `python -m scripts.compare_id_trc_v2 --before analysis/full_production_propagation --after <new-output-dir>`. Full-source detection does not depend on any frozen replay ledger or prior metrics. The comparison alone reads the frozen analysis.

No independent reviewer has yet labeled these cases. Fable should assess the evidence and unresolved provenance directly before interpreting candidate fractions as probabilities.
