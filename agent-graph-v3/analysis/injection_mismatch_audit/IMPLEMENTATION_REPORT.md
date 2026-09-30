# Financial IPI semantic repair and historical impact

The approved repair is implemented. No production benchmark, model inference,
historical trace rewrite, fixture modification, or dataset re-admission was run.
The former diagnosis report is retained as a historical snapshot in REPORT.md.

## Final semantics

Financial IPI uses occurrence=1: the first eligible canonical-target read of each
configured origin. The default single-origin stage identities are:

| Topology | Default origin | Financial runtime role |
|---|---|---|
| review_loop | researcher stage (producer) | extractor |
| branch_and_verify | researcher stage (branch A) | researcher |
| coordinator_workers | security_worker stage (worker A) | specialist_a |

An explicit topology_target overrides these single-origin defaults. Missing the
configured origin's target read does not allow another agent to substitute for it.

For many_to_one, intended origins are distinct handoff sources into the exit
stage. In branch_and_verify they are researcher and analyst. Each can fire once;
the verifier cannot supply a missing origin. The expected count remains **2**.
Trigger occurrence and idempotency now use the same per-role scope, so one branch
cannot advance another branch's trigger counter.

For one_to_many, the intended origin is the upstream coordinator. Its first
outgoing handoff closes the injection window. IPI must fire **strictly before**
that boundary. Worker injections and later coordinator injections are invalid.
Handoff-corruption and input-disregard LEPs may mutate that initial handoff itself:
the mutation is applied before its payload is delivered to downstream consumers.
There must be an actual fan-out handoff; a coordinator that injects and immediately
finalizes without fan-out is not a valid one-to-many realization.

**Workflow scheduling is unchanged.** The existing topology can perform preliminary
worker passes before its coordinator visit. These are not substituted for the
required upstream origin. The final invariant concerns the origin and the initial
coordinator fan-out; it does not introduce a new prohibition on preliminary work.
An initially considered scheduler change was removed from the final implementation.

## Strict admission and state invariant

Admission now requires all three:

1. Clean execution completion.
2. Exact equality of actual labeled origins and expected origins.
3. Correct per-LEP origin identity, distinct intended M2O roles, and O2M ordering.

An otherwise completed count-correct trace with invalid structure receives
`termination_reason=injection_structure_mismatch` and dataset_eligible=false.
Existing count mismatches retain `injection_count_mismatch`. Other termination
failures remain failures. No origin is synthesized, inferred from exposure, or
counted for an unchanged mutation.

Every active LEP requires an explicit budget and topology binding before the first
model call. Missing configuration raises an error instead of creating max_origins=1
or warning and skipping. Reset clears budgets, counts, target bindings, and the
fan-out state. Re-registering a LEP invalidates its prior budget/binding.

The existing production path already configured the same orchestrator correctly:
fresh construction → registration → topology binding → budget setup → stage calls.
No intermediate reset caused the 116 failures. This hardening protects the contract;
it is not presented as the historical cause.

Structural checks consume existing private origin labels/attribution. Their result
is stored only in trace-level `origin_validation` admission metadata. No detector
feature, event observable, task prompt, anomaly definition, or propagation metric
was changed. A graph-feature regression changes this metadata and verifies identical
node features, edge features, and edges.

## Exact implementation changes

| File / functions | Change |
|---|---|
| tasks/registry.py: financial DEFAULT_LEPS | Financial IPI occurrence 2 → 1, with the canonical-target rationale |
| schemas/trigger_matcher.py: TriggerMatcher.evaluate | Occurrence counts keyed by the same trigger/role scope as idempotency |
| generation/injection_origins.py: intended_origin_roles | Shared origin identity contract, including explicit overrides and stage-ID resolution |
| generation/injection_origins.py: remap_task_roles | Extracts the existing role mapping without changing it, shared by execution and auditing |
| generation/injection_origins.py: origin_structure_errors | Per-LEP counts, distinct intended roles, supported modes, upstream identity, and first-fan-out ordering |
| leps/registry.py: set_topology / evaluate_for_boundary | Bind intended roles, filter boundaries, close O2M window at initial upstream handoff |
| leps/registry.py: validate_firing_configuration / mark_fired_origin / reset | Fail fast for missing/wrong budgets or bindings; reject duplicate/unintended origins; clear stale state |
| generation/runner.py: run / _execute_scenario | Preflight validation; structural admission; shared role remapping; compare stage_id with exit_stage |
| generation/stage_runner.py: run_stage | Resolve M2O exit by stable stage identity; record Input Disregard's sender as origin, not recipient |
| leps/topology_target.py: resolve_target_stage | Resolve upstream:coordinator through its stable stage identity after role remapping |
| leps/indirect_prompt_injection.py: evaluate | Import the previously missing TriggerState; reject failed reads as injection surfaces |
| generation/scenario_builder.py | Carry propagation_mode through ScenarioBuildConfig into WorkflowConfig |
| scripts/fixture_mock.py: FixtureBackend | Test backend explicitly hands off on the first coordinator visit instead of finalizing before fan-out |
| scripts/audit_origin_semantics.py | Full accepted/rejected audit using the same structural validator; exact minimal and overlap manifests |

`expected_injection_origins()`, TRC target selection and mutation operator, fixture
grading, task prompts, detector features, propagation metrics, and review-cap logic
are unchanged. TRC receives the same general propagation-mode identity/order checks
as other LEPs; its 16 historical M2O incomplete mutations were not rescued.

The only Input Disregard accounting correction records the handoff **source** in
the orchestrator's fired-target set, matching its existing origin event label. This
does not relabel historical events or change the disregard instruction/payload.

## Historical diagnosis: all 116 mismatches

All are financial_analysis, STANDARD, and injection_count_mismatch.

| Topology | Mode | LEP | Expected→actual | Count |
|---|---|---|---|---:|
| branch_and_verify | many_to_one | IPI | 2→1 | 90 |
| branch_and_verify | many_to_one | IPI | 2→0 | 3 |
| branch_and_verify | many_to_one | TRC | 2→1 | 16 |
| coordinator_workers | single_origin | IPI | 1→0 | 5 |
| coordinator_workers | one_to_many | IPI | 1→0 | 2 |
| **Total** | | | | **116** |

The financial IPI occurrence=2 setting predates canonical-file filtering. After
that filter was introduced, report + transcript contributed only one eligible read.
In every 2→1 IPI trace, researcher contributed occurrence 1 and analyst fired at
the shared occurrence 2; verifier was excluded. There were no accepted financial
M2O IPI traces. For TRC, occurrence=1 permitted 78 accepted two-origin runs.

The three zero-origin M2O IPI traces lack an analyst target read. The five
single-origin coordinator IPI traces have only one canonical read, by worker A.
The two O2M IPI traces also have only that worker read, not an upstream origin.
Neither O2M mismatch involves role remapping: financial defaults have two agents
and coordinator_workers has four stages, so this topology was not remapped.

The sixteen TRC mismatches remain genuine incomplete attempts: four analysts did
not read the canonical target; twelve read content on which the actual operator
produced no material change after the researcher overwrote the file. No rerun is
mandated for these sixteen by this repair, and rejection remains enforced.

See historical.json for all 116 exact paths and boundary-level evidence.

## Complete accepted and rejected audit

Scanned **12,900** historical files: **11,801 accepted**, **1,099 rejected**.

* **1,625 previously accepted traces are invalid under corrected structure.**
* **275 rejected traces use affected configurations.** Of these, **100** are the
  completed IPI count mismatches addressed here; **175** were already rejected for
  other execution failures and are separated to avoid duplicate recovery work.
* **11,000 traces are outside the affected configuration/structure set.**

The 1,625 accepted invalidations are:

* **269 financial single-origin IPI:** review_loop 90, branch_and_verify 93,
  coordinator_workers 86. Origins were outside the now-bound intended stage.
* **1,356 one-to-many traces across all families and all five LEPs:** wrong upstream
  origin identity or late origin. This conclusion does **not** rely on the order
  of preliminary worker passes. All remain invalid with only identity and
  first-fan-out timing checks.

The latter includes the specifically requested **89 accepted financial O2M IPI**:
77 specialist_b, 2 synthesizer, 10 coordinator; all after initial fan-out.

Financial M2O IPI had **zero** accepted traces. All **93** completed mismatches need
rerunning; seven additional executions were already rejected as protocol_violation.
Accepted M2O IPI in other families remains valid: **99 code_review**, **86
research_synthesis**. These are not in the rerun manifest.

**Non-IPI impact:** 1,091 accepted and 109 already-rejected O2M traces are affected
by upstream-origin enforcement. This is a propagation-structure repair, not a
change to their perturbation operators. No accepted non-IPI trace outside O2M was
invalidated by this audit.

## Exact minimal rerun matrix

The minimum defect/semantic replacement set is **1,725 executions**: 1,625 accepted
invalidations plus 100 completed IPI mismatches. Every row below is a perturbation
run at its original repetition (0) and original execution variant. MP uses the
historical memory-enabled variant; the other LEPs use STANDARD. No benign runs
are added.

Abbreviations: IPI indirect prompt injection; TRC tool-result corruption;
MP memory poisoning; HC handoff corruption; ID input disregard.

| Task family | Topology | Mode | LEP | Accepted replacements | Completed rejected | Minimal reruns |
|---|---|---|---|---:|---:|---:|
| code_review | coordinator_workers | one_to_many | HC | 88 | 0 | 88 |
| code_review | coordinator_workers | one_to_many | IPI | 86 | 0 | 86 |
| code_review | coordinator_workers | one_to_many | ID | 91 | 0 | 91 |
| code_review | coordinator_workers | one_to_many | MP | 98 | 0 | 98 |
| code_review | coordinator_workers | one_to_many | TRC | 88 | 0 | 88 |
| financial_analysis | branch_and_verify | many_to_one | IPI | 0 | 93 | 93 |
| financial_analysis | branch_and_verify | single_origin | IPI | 93 | 0 | 93 |
| financial_analysis | coordinator_workers | one_to_many | HC | 93 | 0 | 93 |
| financial_analysis | coordinator_workers | one_to_many | IPI | 89 | 2 | 91 |
| financial_analysis | coordinator_workers | one_to_many | ID | 92 | 0 | 92 |
| financial_analysis | coordinator_workers | one_to_many | MP | 88 | 0 | 88 |
| financial_analysis | coordinator_workers | one_to_many | TRC | 95 | 0 | 95 |
| financial_analysis | coordinator_workers | single_origin | IPI | 86 | 5 | 91 |
| financial_analysis | review_loop | single_origin | IPI | 90 | 0 | 90 |
| research_synthesis | coordinator_workers | one_to_many | HC | 92 | 0 | 92 |
| research_synthesis | coordinator_workers | one_to_many | IPI | 90 | 0 | 90 |
| research_synthesis | coordinator_workers | one_to_many | ID | 91 | 0 | 91 |
| research_synthesis | coordinator_workers | one_to_many | MP | 82 | 0 | 82 |
| research_synthesis | coordinator_workers | one_to_many | TRC | 93 | 0 | 93 |
| **Total** | | | | **1,625** | **100** | **1,725** |

There are **175 additional affected rows already rejected elsewhere**: 155 labeled
protocol_violation in the historical files, 14 max_review_cycles, 6 max_events_reached.
This audit does not reclassify transport errors disguised as protocol violations.
If replacing every failed execution as well, use the union of both manifests:
**1,900**, not 1,725 + all 1,099 rejected executions. The 175 are overlap with
separate execution-failure recovery work, not newly invalid accepted data.

Artifacts:

* semantic_impact.json — all 12,900 rows, private structural errors, affected and
  rerun flags, original condition/variant/repetition/scenario identity, full matrix.
* rerun_manifest.json — **exact 1,725 original trace paths/scenarios** for the minimal set.
* already_rejected_overlap.json — exact 175 affected, independently rejected executions.

Reproduce without models or benchmark execution:

```sh
python3 -m scripts.audit_origin_semantics /u/kdhande/agent-graph/traces/agentprop48 --output analysis/injection_mismatch_audit/semantic_impact.json
```

## Regression tests and limits

The invalid previous-agent test file was replaced with production-path tests that
use the benchmark's family-specific LEP resolver, explicit perturbed condition and
propagation_mode, real ScenarioRunner/StageRunner/tool dispatch/mutation/admission,
and deterministic model responses. They cover all five LEPs across the five
supported topology/mode cells, all three financial IPI modes, explicit origin
overrides, missing targets, repeated target reads, duplicate/unintended origins,
late O2M origins, missing fan-out, remapped M2O/O2M exit identities, unconfigured
budgets, structurally impossible handoff origins before inference, reset,
independent occurrence counters, TRC no-ops, and benign execution.

Test/helper changes:

* tests/test_injection_count_regression.py — replacement production-path regressions.
* tests/test_injection_lifecycle_diagnosis.py — current first-read expectations;
  still proves budget and orchestrator identity survive stage execution.
* tests/test_runner_admission.py — synthetic traces now carry valid origin roles
  and actual fan-out; unsupported modes cannot pass admission solely by count.
* tests/test_memory_many_to_one.py — test coordinator explicitly writes and hands
  off before finalizing on its return pass.
* tests/test_topology_targeting.py — real topology identities and explicit budgets.
* tests/test_o2m_m2o_propagation.py — missing state must raise, not fabricate cap 1.
* tests/test_fixture_contract.py — retain exact hashes for unaffected legacy cells;
  approved changed cells must pass real structural admission/count checks.
* tests/test_detector_anomaly_targets.py — metadata-isolation assertion for the
  training encoder, runnable when its existing dependencies are installed.

**Focused suite: 162 passed.** See regression_results.xml. It includes a passing
graph-feature isolation check requiring torch but not torch_geometric.

**Broader comparison: 284 passed, 27 failed, 11 errors (322 total).** All 38
failure/error identities are the same as the pre-edit baseline (232 passed,
27 failed, 11 errors; 270 total). There are **zero newly failing test identities**.
test_comparison.json records every baseline/final failure name. Existing failures
include obsolete fixture-count assumptions, materializer/curation checks affected
by parallel work, review-loop/protocol/export expectations, and eleven old tests
that request unsupported coordinator_workers + many_to_one. These unrelated
failures were not hidden, skipped, or fixed by changing benchmark semantics.

The separate training-level leakage test could not collect in this environment
because torch_geometric is absent. No dependency installation was attempted; the
graph-level metadata-isolation regression passes. A full repository suite pass is
not claimed.

All **75 deterministic fixture mock cells** passed. Exactly **46 unaffected cells**
retain their old injection fingerprints; the **29 changed cells** are precisely
O2M cells plus financial IPI single-origin/M2O cells under the approved repair.
Fixture grading, fixture manifests, and stored legacy fingerprint data were not
changed. The previous agent's fifteen broken regression tests are no longer left
as the repair's verification suite.

## Operational limits

The fix guarantees valid origin selection and rejects invalid realization; it
does not compel an LLM to read a file. A rerun can still correctly be rejected if
an intended origin omits the canonical read, finalizes without fan-out, or a
mutation is a no-op. The three historical zero-origin M2O IPI trajectories would
still lack an analyst origin if replayed without a new eligible analyst read.

No review-cap code, SWE-bench fixtures, grading rules, selection/curation files,
materialization scripts, detector implementation, or propagation metrics were
edited for this repair. The shared repository HEAD advanced during this work and
included some already-applied changes; no git commit or history rewrite was made
by this task.

No production rerun has been launched. The manifests are review artifacts only.
