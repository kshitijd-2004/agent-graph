# Production propagation pipeline integration

Implemented `python -m scripts.analyze_propagation` with the pinned `agentprop-hc-ipi-v1` suite. It reads saved source directories, revalidates admission, builds clean references, and runs all five detector routes without frozen replay ledgers or baseline metric files. No benchmark agents, GPU workloads, model servers, or external LLM calls were launched.

## Frozen-population verification

**Passed.** The integrated run independently discovered the exact previous **9,880 measured attempts** from **37,146 saved trace files**. Admission found 22,870 perturbed attempts, 15,610 admitted perturbed traces, and 4,023 clean-reference groups. Of the admitted attempts, 5,730 lacked sufficient matched benign references and remained unmeasured. There were zero analysis failures.

| LEP | Measured | Positive | Negative | Unknown |
| --- | ---: | ---: | ---: | ---: |
| HANDOFF_CORRUPTION | 2208 | 51 | 780 | 1377 |
| INDIRECT_PROMPT_INJECTION | 1839 | 240 | 232 | 1367 |
| INPUT_DISREGARD | 2212 | 620 | 1592 | 0 |
| MEMORY_POISONING | 1802 | 21 | 1781 | 0 |
| TOOL_RESULT_CORRUPTION | 1819 | 542 | 1277 | 0 |

The 51 HC and 240 IPI positives are candidate classifications, not independently validated propagation probabilities. Unknowns remain explicit and have null binary compatibility outputs. Negative means no detected effect under the supported evidence rules.

All **2,208 native HC reports**, **1,839 native IPI reports**, and **4,047 selected three-run pairings** match the frozen results exactly. This comparison includes every event decision, assertion, intervention boundary, graph repair/path, recovery record, and final-answer endpoint. The complete measured population matches, with no added or missing attempt IDs.

All **5,833 unaffected legacy metric rows** match semantically. **5,398** match as exact dictionaries; **435 ID rows** differ only in diagnostic reason-list order because the unchanged legacy implementation iterates over sets. The verifier sorts only those lists for comparison, preserving reason text and multiplicity. Event IDs/order, classifications, numeric metrics, and every other field match. Native outputs remain untouched.

HC, IPI, legacy detector code, and the graph-builder code match the frozen SHA-256 hashes. The frozen scripts and original outputs were not modified. The only change to the existing analyzer is two overridable input-reading/discovery hooks; its default behavior remains compatible.

HC final-answer endpoints remain 28 detected, 976 not detected, and 1,204 unknown; six traces show assertion recovery. IPI retains 65 detected final-answer effects, 572 not detected, 1,202 unknown, and 129 traces with recovery.

## Tests and artifacts

**247 distinct tests passed** across the regression run and final focused run: 236 existing HC/IPI, behavioral, fixture, and admission tests; plus 11 end-to-end/integration tests. Temporary source trees exercise all five routes, real admission, identical and conflicting duplicates (including malformed conflicts), three-run selection, standard/memory variants, missing controls/manifests, unknowns, source layouts, failure exit codes, native report preservation, and legacy equivalence.

- [Frozen verification](frozen_verification.json)
- [Integrated metrics](reproduction/propagation_metrics.jsonl)
- [Complete detector evidence](reproduction/detector_evidence.jsonl)
- [Unified summary](reproduction/propagation_summary.json)
- [Unchanged legacy metric schema](reproduction/legacy_metrics.jsonl)
- [Run provenance](reproduction/run_manifest.json)
- [Regression tests](tests.xml) and [final integration tests](final_integration_tests.xml)

## Command for the consolidated full-production dataset

Run from the repository root (`agent-graph-v3/`) with a new output directory:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.analyze_propagation \
  --source-dir analysis/full_production_consolidated/source \
  --output-dir analysis/full_production_propagation \
  --detector-version agentprop-hc-ipi-v1
```

The installed environment is used because the current shell’s system `python3` is too old. The CLI also supports `--fixture-root`, `--inventory-only`, and an explicit all-legacy suite. [CLI documentation](../../docs/production_propagation_cli.md) describes outputs, source selection, references, and optional regression verification.

## Consolidated dataset preflight

The same CLI successfully completed `--inventory-only` on `analysis/full_production_consolidated/source`: **37,761 files**, **23,348 perturbed attempts**, **16,072 admitted perturbed traces**, and **4,023 clean-reference groups**. No conflicting duplicates or ingestion errors occurred. Resolved input provenance covers **29,949 files from all 160 original shards** and **7,812 files from all 100 October 19 rerun shards**. The expanded population has not been run through the detectors; the command above performs that complete analysis.

[Preflight summary](full_production_preflight/inventory_summary.json) · [Preflight manifest](full_production_preflight/run_manifest.json) · [Artifact verification](artifact_verification.json).
