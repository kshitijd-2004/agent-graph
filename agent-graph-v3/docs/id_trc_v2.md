# AgentProp ID/TRC v2 offline measurement

`agentprop-id-trc-v2` selects corrected INPUT_DISREGARD and TOOL_RESULT_CORRUPTION detectors, HC `hc-baseline-relative-v1.0`, IPI `hc-ipi-evidence-v2.0`, and legacy MEMORY_POISONING. HC/IPI/MP criteria and per-attempt metric rows are unchanged. The earlier `agentprop-hc-ipi-v1` suite reproduces legacy ID/TRC with the current HC/IPI; `legacy` selects the original detector for all five LEPs. Frozen evaluations remain optional and unchanged.

These outputs are mixed-detector candidate classifications. They are not human-validated propagation probabilities. Unknown and incomparable remain in the measured denominator and have a null binary compatibility value.

## Input Disregard

The legacy omission profiler pooled all causal ancestors of a semantic slot. Serial clean runs therefore made earlier workers' events mandatory prerequisites for later workers. Applying this to coordinator-first parallel fan-out conflated the execution schedule with omitted behavior.

The corrected detector first compares the recorded scheduling skeleton: initial roles, cross-role transition links, and fan-out receivers. Event ordering between independent arms is irrelevant. All matched clean repetitions must support the same observed skeleton as the perturbed run. An incompatible comparison is `incomparable`, even if branch filtering would eliminate the legacy positive. Shape matching is a necessary comparison guard, not proof of equivalent workflows.

For comparable controls, required actions are restricted to input/output operations in the same role visit, connected by actual clean dependencies. The prerequisite must occur in every occurrence of the compared slot in every clean repetition. Pure reasoning events and other roles' actions are never required by this pathway. The perturbed event must correspond to the clean slot and omit that prerequisite in its own branch visit. Stable benign witnesses, operation/object, branch visit, source paths and event IDs are recorded. These are empirical required-action candidates, not proof that the task could not be completed any other way.

Attribution additionally requires a recorded disregard instruction, a verified recorded mutation when available, actual receiver input exposure in that role, strict origin exclusion, and a downstream path. A supported omission is counted at the first affected action on a path. An omission evidenced before the intervention is not attributed to it. If an earlier visit of the same role already performed the prerequisite operation, the need to repeat it is unresolved and the comparison is unknown. Comparable legacy unexpected-operation/target evidence remains a separate action pathway, with the same exposure and reachability checks. Unsupported legacy differences become unknown, rather than being relabeled negative. No new execution or scheduling logic is introduced.

## Tool Result Corruption

Two evidence pathways are explicitly distinguished:

1. `boundary_provenance_adoption`: independent of clean-slot availability. A uniquely paired `read_text_file` result must have differing recorded before/after bytes, valid hashes, matching call arguments and actual emitted content. A named task assertion must change at that boundary and reappear as an active authored assertion, write or forwarding action. This excludes the corruption boundary and passive exposure. The output records the exact before/after assertion, observed excerpt, source path, receiver exposure, transport and downstream graph path.
2. `legacy_matched_slot_effect`: retains the existing matched-slot behavioral endpoint where its evidence survives boundary verification and the new explicit ambiguity/rejection guards. It is labeled as a legacy behavioral effect, not silently relabeled as proven corrupted-value adoption. Its adoption endpoint may remain unresolved. It cannot override known independent sources, ambiguous assertions, failed assertion transport, or attribution after correction.

Financial extraction recognizes named task labels and tables only when a unique explicit year/qualifier selects the column. It never chooses columns using the fixture answer or numeric substitution list. Research verdicts and code-review decisions reuse the established assertion grammar. Merely repeating a number, an unrelated date, a quotation, rejected instruction or fenced example is insufficient. Unrecognized boundaries and conflicting assertions preserve unknown.

Source attribution examines recorded upstream assertions for the same key and corrupted value. Independent or ambiguous source availability prevents attribution. Cross-agent transfer requires a directly linked, identical handoff payload or a successful same-path, byte-identical artifact write/read. A failed/intervening overwrite invalidates the older version. An ancestor adoption alone cannot prove transport. Graph reconstruction uses the existing verified artifact-edge routine; it does not add chronology edges.

## Endpoints and limitations

- Delivery: verified boundary content appears in receiver input.
- Adoption: active authored use of the specific boundary assertion.
- Forwarding: a supported assertion in a handoff or file/memory write. A write is an attempted forwarding action; it does not itself prove successful storage or another agent's receipt.
- Cross-agent transmission: a subsequent supported assertion after a recorded content transfer to another role.
- Recovery: a downstream authored assertion restores an adopted key to its original boundary value. A clean reread without authored use is not recovery evidence.
- Transient: adoption occurred, and all recognized changed boundary assertions are restored in the final answer.
- Persistent/final impact: a supported corrupted assertion remains in the final answer. Absence of such evidence is not automatically a clean final answer; omitted or unparsed facts produce unknown.

Recovery can coexist with later persistence (e.g. partial correction or reintroduction). Restoring original assertions does not establish overall answer correctness. ID recovery remains unassessed; matching final wording does not prove a previously omitted action was restored. There is no external judge or independently labeled ground truth.

The extraction grammar is deliberately bounded. Derived calculations, complex scientific paraphrases, code semantics, merged or abbreviated handoffs, unrecorded memory transport and incomplete source provenance can remain unknown. Legacy matched-slot effects are retained as a separate endpoint and should be reviewed separately from boundary-proven adoption. Identical shapes can still hide workflow differences; the recorded guard is necessary, not sufficient for full experimental exchangeability.

Required future controls for incompatible coordinator-worker cases: three admitted benign repetitions for the same fixture and execution variant using the coordinator-first decomposition, the same parallel fan-out receiver set, and the same merge schedule as the one-to-many perturbed workflow. Existing serial worker-first controls cannot establish absence of an ID effect. This task does not generate those executions.

## Reproduction from the repository root

Use Python 3.10+ with repository dependencies (the tested environment is shown):

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.analyze_propagation \
  --source-dir analysis/full_production_consolidated/source \
  --output-dir analysis/full_production_id_trc_v2 \
  --detector-version agentprop-id-trc-v2

/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.compare_id_trc_v2 \
  --before analysis/full_production_propagation \
  --after analysis/full_production_id_trc_v2
```

The analyzer requires a fresh output directory. For repetition after the saved run, choose a new directory and use the same `--after` path in the comparison. Discovery, canonical deduplication, conflict rejection, admission and clean-reference construction are unchanged. No replay ledger or frozen baseline metric file is required by detection; the comparison script reads the two saved metric populations only after analysis.

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest \
  tests/test_id_trc_v2.py tests/test_production_propagation.py \
  tests/test_behavioral_anomaly.py tests/test_hc_baseline_relative.py \
  tests/test_evidence_propagation.py tests/test_analysis_admission.py -q
```

Production trace regressions load existing artifacts and skip only when those optional artifacts are absent. The detector contains no trace-specific IDs.
