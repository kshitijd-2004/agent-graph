# Production propagation CLI

Run the ordinary offline analysis directly on saved execution directories. No replay ledger, frozen metric file, external model, or benchmark execution is required.

From `agent-graph-v3/`, using the installed Python environment:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.analyze_propagation \
  --source-dir analysis/full_production_consolidated/source \
  --output-dir analysis/full_production_propagation \
  --detector-version agentprop-hc-ipi-v1
```

The output directory must be new or empty and separate from the inputs. The current shell's system `python3` is too old; use the existing environment above, or a supported environment containing the repository dependencies. Analysis uses CPU only and starts no agent executions, GPU jobs, model servers, or external LLM calls.

## Versioned dispatch

`agentprop-hc-ipi-v1` is the default, explicitly pinned detector suite:

| LEP | Detector | Endpoint |
| --- | --- | --- |
| HANDOFF_CORRUPTION | `hc-baseline-relative-v1.0` | Baseline-relative propagation candidate |
| INDIRECT_PROMPT_INJECTION | `hc-ipi-evidence-v2.0` | Evidence-based propagation candidate |
| TOOL_RESULT_CORRUPTION | `legacy` | Existing behavioral detection |
| INPUT_DISREGARD | `legacy` | Existing behavioral detection |
| MEMORY_POISONING | `legacy` | Existing behavioral detection |

`--detector-version legacy` explicitly runs the original detector for all five LEPs. Unknown suite names fail. The implementation reuses `PropagationAnalyzer` admission, grouping, analysis, and attempt accounting. HC and IPI are dispatched directly to the unchanged detector functions; their native reports are retained. The legacy analyzer's existing Python API and defaults remain compatible. The original frozen HC/IPI evaluation scripts are unchanged and optional.

Programmatic entry point:

```python
from benchmark.production_propagation import ProductionPropagationAnalyzer

summary = ProductionPropagationAnalyzer(
    source_dir="analysis/full_production_consolidated/source",
    output_dir="analysis/full_production_propagation",
    detector_version="agentprop-hc-ipi-v1",
).analyze()
```

## Discovery, admission, and references

`--source-dir` accepts a consolidated directory with `traces/` and/or `rejected_traces/`, or a root containing numeric `node_*` shards (also `node_original_*` and `node_rerun_*`). It reads JSON files in both execution folders. It does not traverse workspace artifacts or failed-shard archive directories. The caller supplies the completed, immutable source population; analysis does not query Slurm or infer completion from filenames. The existing consolidated production source already contains the selected original and rerun traces.

Attempts are identified by recorded `attempt_id`, falling back to `trace_id`. Identical JSON content is deduplicated regardless of file formatting or folder. Conflicting content for the same identity aborts the run before metrics are produced, including when one copy cannot deserialize. Different attempts for the same experimental slot remain different attempts.

The unchanged `generation.admission.validate_trace` revalidates every readable distinct attempt. Placement in `traces/` is not proof of eligibility; placement in `rejected_traces/` does not bypass checks. Infrastructure failures, ineffective mutations, partial injections, unverifiable evidence, and invalid graph dependencies remain excluded. Available attempt-start/record ledgers preserve attempts without readable traces. When every selected shard has a frozen plan, the original plan identity checks apply; conflicting plan entries fail. Without a complete plan, planned coverage remains undefined.

References retain the existing deterministic policy: first admitted attempt in sorted source-path order for each fixture/topology/execution variant/repetition. HC/IPI additionally require matching task family and three identified distinct repetitions and use the first three selected references. Additional benign repetitions are logged but not substituted based on outcomes. Legacy detectors retain their original all-distinct-repetition reference policy. Fewer than three matched references leave an admitted attempt **unmeasured**, not negative. Standard and memory-enabled controls remain separate. Experimental protocols are not pooled.

`--fixture-root PATH` overrides the default repository `workspace_fixtures/` for controlled tests or alternate installed fixture trees. Missing manifests produce recorded analysis failures, not negative classifications. A nonzero exit status is returned when any admitted analysis fails.

## Outputs and interpretation

- `propagation_metrics.jsonl` and `.csv`: unified metrics for every measured trace, detector/suite version, native detector status, normalized positive/negative/unknown classification, source hashes, exact selected clean references, recovery, final-answer endpoint, and graph repairs.
- `detector_evidence.jsonl`: complete native HC/IPI reports, including boundaries, all event decisions, graph paths, and lifecycle endpoints. HC positive witnesses include source paths. Legacy entries retain their anomaly IDs/details.
- `legacy_metrics.jsonl`: original metric dictionaries for unaffected LEPs, without schema additions, for exact comparisons. With the legacy suite, this contains all measured LEPs.
- `propagation_summary.json`: combined and per-LEP classifications, descriptive detector-positive fractions, admission accounting, and task/configuration breakdowns.
- `by_lep_type.csv`, `by_task_family.csv`, `by_topology.csv`, `by_propagation_mode.csv`, `by_lep_x_topology.csv`, `by_configuration.csv`, `by_cell.csv`: unified breakdowns. Configuration crosses LEP, task family, topology, and propagation mode.
- `admission_attempts.jsonl`, `clean_admissions.jsonl`, `clean_references.jsonl`: admission decisions and selected reference provenance.
- `input_inventory.jsonl`, `run_manifest.json`, `output_hashes.json`: input identity, resolved paths, hashes, duplicate copies, detector/code versions, reference policy, fixture hashes, and output integrity.

V2's native `no_detected_effect` is mapped to unified `negative`, while its original status is preserved. HC's native three-way classification is unchanged. A negative means no effect detected within the supported evidence, not proof of a causal negative. Unknown is never silently converted into negative: its unified `propagation_occurred` compatibility field is null. `detector_positive_fraction` divides positives by **all measured traces, including unknowns**, with separate counts for all three classifications.

HC/IPI positives are **research candidates**. `independently_validated_propagation_probability` is null, and no independent accuracy validation is claimed. Legacy results are also detector outputs. Execution admission failures and insufficient-reference attempts are counted separately from measured detector unknowns.

HC/IPI use exactly the graph paths supported by their respective detectors, including verified artifact repairs. No additional legacy reachability filter discards a valid repaired path. Candidate depth summarizes supported evidence paths, while unaffected legacy depth calculations remain unchanged. Recovery and final-answer impact are separate endpoints; neither changes an earlier candidate classification. Final-answer impact measures detected deviation, not correctness. Unavailable legacy lifecycle endpoints are `not_assessed`.

## Preflight and regression reproduction

To check a consolidated population without running detectors:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.analyze_propagation \
  --source-dir analysis/full_production_consolidated/source \
  --output-dir analysis/full_production_preflight \
  --detector-version agentprop-hc-ipi-v1 \
  --inventory-only
```

To reproduce the previous analysis from its source files alone:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.analyze_propagation \
  --source-dir analysis/prelim_oct25_combined/source \
  --output-dir analysis/production_reproduction_fresh \
  --detector-version agentprop-hc-ipi-v1
```

Only the following **optional verification** step reads frozen evaluation outputs:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.verify_production_propagation \
  --integrated analysis/production_reproduction_fresh \
  --output analysis/production_reproduction_verification.json
```

It checks the exact 9,880-attempt measured population; every native HC/IPI report, event decision, graph repair/path, and endpoint; the original three-reference pairing; and every legacy metric dictionary for the 5,833 unaffected traces. The verifier reports exact matches separately from matches that differ only in diagnostic reason-list order: the unchanged legacy ID detector iterates over sets, so that order can vary across Python processes. Only reason-list order is normalized for comparison; multiplicities, reason text, event order, classifications, and all metrics must match. Native output files are not rewritten. It also verifies that HC, IPI, legacy detector code, and graph-building code match the frozen evaluation hashes. The production CLI never imports or reads these comparison inputs.

Tests use temporary source directories with saved synthetic trace artifacts, actual admission, all five detector routes, duplicates/conflicts, missing references, lifecycle/status preservation, direct/sharded layouts, and CLI exit codes. No benchmark backend executes during these tests.
