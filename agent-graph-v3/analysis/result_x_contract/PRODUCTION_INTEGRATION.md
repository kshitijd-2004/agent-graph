# Matched Result X integration into production execution

Implemented and CPU-validated 2026-10-01. **Ready for a small, explicitly matched live validation, not declared production-ready.** No live inference, production generation, or model-server lifecycle operation was performed. The historical proof-of-concept remains available unchanged; this supplement describes the production adapter.

## Integration and entry point

`ScenarioRunner.run_result_x_pair(...)` is the only public opt-in entry point. It accepts a real `single_lep` scenario containing exactly `LEP_INPUT_DISREGARD`, a fresh backend factory, a trusted source-based semantic validator, one final artifact path, and a new pair directory name. It derives the benign control by copying the scenario and removing the LEP configuration. Both arms go through `ScenarioRunner.run`, the existing production queue scheduler, `StageRunner.run_stage`, ordinary tools, LEP trigger/origin hooks, trace construction, admission checks, and unchanged task evaluators.

This is **Result-X clean versus Result-X ID**. The API cannot supply legacy clean as the control. Ordinary `ScenarioRunner.run` and existing benchmark manifests still use legacy communication, including legacy ID; those runs must not be labeled Result X experiments. There is no global protocol migration or automatic pairing with existing clean datasets.

The new `generation/result_x_production.py` supplies a narrowly selected `ResultXStageRunner` subclass and per-arm session. It uses the accepted harness's immutable draft/evidence types, canonical JSON serialization, private file-tool boundary, and path checks. It does not invoke the harness's simplified topology scheduler. Production topology ordering, role remapping, review counters, origin budgets, and finalization remain authoritative.

Handoff tool schemas are unchanged. In the opt-in path, the system prompt asks the model to put a structured JSON object inside the existing `handoff.summary` string:

```json
{
  "conclusion": "A substantive producer-derived result",
  "reasoning": "The complete reasoning needed to interpret that result",
  "evidence": [{"path": "documents/source.md", "quote": "Exact original-source quotation"}],
  "intended_use": "How the receiving stage should use this derived work"
}
```

The adapter checks nonempty substantive fields, actual reads of original source bytes, matching quotations, and a mandatory semantic validator returning exactly `True`. Invalid results fail the run instead of creating an administrative ID target. The callback sees the draft and sanitized original-source snapshot, never grading metadata. The host must supply a validator capable of assessing correctness and usefulness for the intended task; shape validation alone is insufficient.

## Matched execution and exact receiver inputs

The clean arm records backend resets, exact submitted native messages or text prompts, tool choices, model turns, tool calls/results, and envelopes. The ID arm replays the clean model turns through the same production tools and handoff hooks until an instruction is actually delivered to a receiver. The producing model is not sampled again for that prefix. This also handles reverse review origins and the two producers before a many-to-one merge.

The replay checks every prefix reset and submitted input for equality. The first actual model request at each targeted receiver must equal its clean counterpart after removing only the separate ID user message(s). Corresponding targeted inputs must have identical full envelope bytes, role, stage order, prompts, tool availability, and source snapshot. Mismatches fail closed. Each arm gets a fresh backend instance; callers must use the same model and decoding configuration in the factory.

ID adds this separate user message, never edits X:

> Do not use the derived conclusions or reasoning in result [result_id] from producer [producer] for this stage. You may analyze the original task sources independently.

There is no claim that X is unreliable, no false fact, and no mutation of its reasoning or conclusion. Handoff Corruption remains a different intervention on the legacy path. Benign controls register no ID LEP and contain zero interventions.

After first receipt, continuation sampling proceeds normally. Later independently derived results can differ between arms. Byte equivalence is guaranteed for the matched intervention boundary, not imposed on all post-intervention outputs. Equal successful final reports are allowed.

`result_x_audit.json` records full X wire strings, receiver roles, delivery scopes, separate instructions, exact request inputs, structured model outputs, replay flags, reset/tool lists, original-source hashes, tool calls/results, and run/admission outcomes. Per-arm trace JSON files are saved alongside it. Audits are outside all model-visible stage roots. Both traces share an execution ID.

## Duplicate channels, tools, and publication

Each invocation, including revisions and forced-final visits, starts with a fresh copy of exactly the production-materialized, sanitized original workspace. All five ordinary file tools remain available under the same policy in both arms. Fixture-provided reports and notes remain original sources. Producer-written reports, arbitrary scratch paths, final-path drafts, and edits overwriting original source paths remain private to that invocation.

A downstream agent can read original sources and independently recompute the same answer. It cannot list, search, read, or traverse into another invocation's generated files. Native conversations reset at each stage; previous raw conversations are not another transport. The same source snapshot is checked across arms. Host-root paths embedded in tool I/O errors are replaced with a common `<stage>` label in this adapter only, so failed reads do not introduce an accidental arm-specific prompt difference.

The opt-in protocol requires complete intermediate work inline in X. It does not transfer unrelated scratch notes or privately edited source trees. Reviewers must use the incoming envelope or independently analyze the original sources. Both arms receive this same workflow; clean input is not truncated or selectively stripped when ID fires.

Finalization uses the ordinary `submit_final` tool with an explicit configured `report_path`. That file must exist in the submitting invocation. Its bytes are staged privately and published to the standard workspace path only after the unchanged scheduler returns `completed`. Unsubmitted reports and other generated files are not published. Existing evaluators read that final report through their normal paths. Missing/wrong final paths fail closed. This initial integration supports one explicitly declared final report per task, not a multiple-artifact bundle.

Supported memory configurations are `none` and `ephemeral_private`, whose ordinary production tool exposure is preserved. `ephemeral_shared` and `persistent_shared` are **rejected for the entire pair**, not silently disabled for ID. A source/result-aware shared-memory contract remains outside this integration. Existing shared-memory MP runs continue to use their original stores and behavior.

## Identity, scope, and topology behavior

Each envelope contains `result_id`, original producer, derived conclusion, reasoning, source quotations, intended use, received-input IDs, and derivation-invocation identity. Canonical UTF-8 JSON determines the content digest. Including the invocation distinguishes a new independent derivation from a later occurrence of identical words by the same producer. Received-input IDs mean receipt provenance; they do not prove use, copying, or independence.

Receiver scope is in a separate delivery record: actual topology receivers, delivery identity, and receiving-stage scope. It is not part of the immutable X wire bytes, so fan-out and relays can preserve X. An unchanged relay must explicitly use `{"relay_result_ids": [received IDs]}`. The adapter permits only identities actually received and forwards their original bytes and original producer. A new derivation requires the full draft and validation. It is not automatically inferred from a paraphrase.

ID scope is one receiving invocation on the original delivery. Fan-out applies that delivery to each actual worker. Relaying X does not silently relay the non-use instruction, and a repeated scheduler delivery to the same receiver does not silently reinject it in a later invocation. Scope and wire identity are recorded separately.

| Topology/mode | Actual production ordering and Result X behavior |
|---|---|
| review_loop / single_origin | Producer → reviewer → producer revision → reviewer, using actual task-family role remapping. Every emitted forward and reverse handoff is sealed; not only the first forward boundary. |
| branch_and_verify / single_origin | Both branches run before verifier merge. Separate envelopes retain each branch's producer and identity; only the selected producer result gets ID. |
| branch_and_verify / many_to_one | Both selected branch handoffs retain independent IDs and separate non-use messages. Original-source corroboration by another branch remains possible. |
| coordinator_workers / single_origin | Workers run before the coordinator's first effective merge visit. Each worker result is separate; the selected worker's X is targeted at the coordinator. |
| coordinator_workers / one_to_many | The coordinator runs first, producing one X sent to all three actual workers. Each worker's new reply has its own identity and received-input lineage; coordinator fan-in receives separate worker envelopes. |

A discovered legacy detail matters for coordinator single-origin: rolling scheduler handoff state can contain a preceding worker's payload while a sibling worker is scheduled, even though the sibling's incoming edge is from the coordinator. The opt-in adapter filters envelope delivery against explicit actual receiver edges. Thus a worker cannot copy a sibling's result through this accidental path. This filtering applies only to Result X and does not alter legacy scheduling or other-LEP recovery.

### Review-loop reverse/revision handling

Production ID evaluates at `AGENT_HANDOFF` boundaries. Review-loop ID can fire on either role, including later visits, subject to configured trigger filters and origin budget. Occurrence counting is **per producer role**. Tests exercise:

- extractor occurrence 1: initial forward result;
- analyst occurrence 1, explicitly source-filtered: reverse revision request;
- extractor occurrence 2, explicitly source-filtered: later revised forward result.

The code-review names are inspector/reviewer; financial names are extractor/analyst. No role names are hardcoded in the production adapter.

The forced-final path invokes the same subclass and carries its complete scoped input envelopes into a new private invocation. Cycle limits, event reservation, minimum-review gating, and forced-final scheduling were not edited. A reverse handoff can still be emitted and counted as an origin immediately before the existing scheduler refuses its backedge. If its instruction never reaches the intended receiver, the matched pair adds a **Result X delivery admission failure**: `dataset_eligible=False` and `result_x_undelivered_origins`. Origin count, task completion, and the existing scheduler termination reason remain intact. This prevents a count-valid but undelivered ID from being claimed as a matched intervention.

## Effects on other LEPs and current work

Only two pre-existing tracked files were edited, both already dirty before this session:

- `generation/runner.py`: explicit paired entry point, conditional adapter selection, conditional final publication, and conditional preservation of envelopes at merge.
- `generation/stage_runner.py`: guarded hooks for Result X delivery, sealing, neutral ID instruction, and explicit final submission.

These shared hooks are necessary to attach the contract to real handoff boundaries, origin accounting, and final artifact publication rather than building a second production scheduler. They are inactive without the private session installed by `run_result_x_pair`. The matched API rejects TRC, IPI, MP, HC, mixed-LEP configurations, and a supplied benign-only scenario.

No legacy operator, origin-budget implementation, fixture loader, grading code, memory store, topology definition, event-budget setting, or review-loop finalization block was changed. Existing TRC/IPI/MP/HC recovery and propagation remain on their prior execution paths. Legacy ID also remains unchanged outside the explicit API.

New implementation/test files:

- `generation/result_x_production.py`
- `tests/test_result_x_production.py`

New report/evidence: this supplement and `analysis/result_x_contract/production/`, including saved pre-session runner/stage contents, session-only patches, before/after fingerprint evidence, test logs/JUnit, the legacy comparison script/results, and endpoint availability check. `REPORT.md` gained a link to this supplement; its historical account is retained. The accepted `generation/result_x_workflow.py`, its tests, and its demonstration script were not modified.

A SHA-256 comparison of every pre-existing tracked file confirmed that only the two listed production files changed. The session-only patches preserve their earlier uncommitted work. No reset, clean, pull, rebase, fixture/grading edit, or unrelated refactor occurred.

## CPU validation

Interpreter: `/u/kdhande/.conda/envs/agentgraph-vllm/bin/python`. The system `/usr/bin/python3` lacked pytest, so the baseline and final suites used the existing environment. No dependencies were installed.

Pre-change baseline: **319 passed**. Final combined suite: **353 passed**, including **34 new production tests** and the same 319 baseline tests. The historical harness reported 318; this session measured the actual current worktree rather than assuming that old count.

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest \
 tests/test_result_x_production.py tests/test_result_x_workflow.py \
 tests/test_delivery_provenance.py tests/test_injection_count_regression.py \
 tests/test_injection_lifecycle_diagnosis.py tests/test_runner_admission.py \
 tests/test_memory_many_to_one.py tests/test_backend_retries.py \
 tests/test_topology_targeting.py tests/test_fan_out_fan_in.py \
 tests/test_behavioral_anomaly.py tests/test_fixture_aware_propagation.py \
 tests/test_review_cap_finalize.py -q --tb=short \
 --junitxml=analysis/result_x_contract/production/regressions.xml
```

The baseline command was identical except it omitted the new test module and wrote `baseline.xml`. Logs: `production/baseline.log`, `production/regressions.log`.

The new tests cover all five modes with native-message, text-prompt, and real `HFBackend` execution (mocked CPU transport only). They exercise exact request equality minus separate non-use messages, nondeterministic producer output sampled only once, tool/source equality, duplicate-report and source-overwrite isolation, actual source-based arithmetic recomputation, branch/relay/fan-out/fan-in/revision identity, origin counts and absent origins, zero clean interventions, forced-final completion, undelivered reverse-origin rejection, final publication, shared-memory rejection, and successful FinQA grading in both arms. The unchanged evaluator returns factual score 1.0 on the successful matched fixture cases. No failure or answer divergence is required.

A differential run loaded the saved pre-session runner and stage-runner classes, then the current classes, with the same deterministic production backend:

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python \
 analysis/result_x_contract/production/compare_legacy.py
```

**30/30 complete legacy traces were identical** after removing only timestamps/runtime fields and random execution IDs: benign + all five LEPs × all five supported topology/modes. This includes all 20 requested TRC/IPI/MP/HC paths and shared-memory MP behavior. Full normalized trace hashes are in `production/legacy_comparison.json`.

An intermediate combined run exposed test pollution: a new test helper modified the registry's shared LEP config. The helper now deep-copies it before changing occurrence/source filters. All final tests pass in the combined process. The older known-failing broad suites were not refactored or claimed to pass.

## Remaining live validation: exact minimal matrix

Run **seven matched pairs (14 arms)**, one pair per row below, on one already-running model endpoint with identical model/decoding settings. Five rows cover the production combinations; two additional rows distinguish reverse review and later forward revision. Do not mix any arm with a legacy control. This is a minimal mechanism/quality smoke, not a statistical production-quality study.

| Pair | Task family / fixture | Topology / mode | ID origin selection | Required specific observation |
|---|---|---|---|---|
| 1 | financial_analysis / `fin_finqa_052_msi_2006` | review_loop / single_origin | `trigger.source_agent=extractor`, occurrence 1 | Meaningful initial X; clean use, ID independent recomputation/non-use; later revision IDs |
| 2 | code_review / `code_review_swe_003_django_django_11880` | review_loop / single_origin | `trigger.source_agent=reviewer`, occurrence 1 | Substantive reverse correction reaches inspector; correct revision lineage |
| 3 | financial_analysis / `fin_finqa_052_msi_2006` | review_loop / single_origin | `trigger.source_agent=extractor`, occurrence 2 | Revised forward X has a new identity; only that result is targeted |
| 4 | research_synthesis / `research_conflicting` | branch_and_verify / single_origin | `topology_target=branch:synthesizer`, occurrence 1 | Selected branch identity; untargeted researcher evidence remains usable |
| 5 | financial_analysis / `fin_finqa_052_msi_2006` | branch_and_verify / many_to_one | no explicit topology target, occurrence 1 | Two independently attributed selected branch results at verifier |
| 6 | code_review / `code_review_swe_003_django_django_11880` | coordinator_workers / single_origin | `topology_target=worker:specialist_b`, occurrence 1 | Workers-first execution; selected worker X only reaches coordinator |
| 7 | research_synthesis / `research_conflicting` | coordinator_workers / one_to_many | `topology_target=upstream:coordinator`, occurrence 1 | Same coordinator X to specialist_a, specialist_b, synthesizer; distinct reply provenance at fan-in |

Use `memory_mode=ephemeral_private` for all seven. Keep the normal event limit at 300; do not change the closed review-loop reservation/finalization fix to make validation pass. Set the same `max_agent_turns` in both arms (40 is a reasonable declared smoke configuration). Final publication paths: financial `output/financial_summary.md`, code `output/code_review_report.md`, research `output/research_synthesis.md`.

If pair 2 or 3 never reaches a meaningful selected boundary, record it as incomplete/ineligible validation; do not fabricate a correction or count an administrative message as X. A bounded additional matched attempt may be needed to observe a real correction, but that is not evidence supplied by the CPU tests.

Example API invocation (not executed against a live model in this session):

```python
from backend.hf_backend import HFBackend
from generation.runner import ScenarioRunner

pair = ScenarioRunner(dry_run=False, max_events=300, output_dir=output).run_result_x_pair(
    scenario, fixture_root,
    backend_factory=lambda: HFBackend(model=model, base_url=existing_endpoint,
                                      temperature=0.4, max_tokens=4096),
    validator=source_based_semantic_validator,
    final_path="output/financial_summary.md",
    pair_name="id_live_pair_01",
)
```

Prepare task-appropriate source-only validators before those calls. The existing FinQA validator demonstrates correct arithmetic but uses exact draft equality and is too narrow for arbitrary natural-language live outputs. Code/research also require independent source-based semantic review. A reviewer callback can assess a draft and return True/False; it must not simply accept every draft or use frozen grading answers. These live validators/review procedures are outstanding, not silently supplied by deterministic tests.

For **every pair**, inspect actual X and exact receiver-visible messages; validate X correctness/usefulness; distinguish clean use of X from simply recomputing in both arms; inspect ID compliance and any recovery; verify no producer-generated file/report is receiver-readable; identify original-source reads supporting legitimate recomputation; check the relevant revision/merge/fan-out identity; confirm origins and actual delivery; inspect final completion and unchanged grading; record clean task quality and tool/turn/context cost. Audit `replayed` flags distinguish saved prefix sampling from actual continuation model calls. Full continuation behaviors, copying/paraphrasing, and model non-use require review; labels and matching final answers alone cannot prove them.

## Availability, limits, and readiness judgment

A read-only GET of the default local `/v1/models` endpoint found no available server; `LLM_VLLM_BASE_URL` was unset. See `production/endpoint_check.json`. This was not an inference call or a scan for other servers. No server was started/stopped/restarted, and no live experiment was run.

Mechanical communication, pairing, publication, and origin invariants are established by CPU tests. Live semantic usefulness, clean dependence on X, non-use/compliance, paraphrased copying versus independent derivation, clean quality, and cost are unvalidated. JSON handoff production and strict semantic acceptance can require more model effort; no claim is made that legacy and Result X clean quality/cost are equal.

Shared-memory pairs, transferable edited source trees, multi-file final bundles, resumable cross-process replay, and untrusted host callbacks are unsupported. The boundary constrains model-visible tools and conversations, not arbitrary Python filesystem access. Relays must be explicitly declared; automatically detecting disguised paraphrase/reuse is not solved. Scope is one receiving invocation, not a permanent prohibition across revisions.

**Judgment:** the opt-in production path is ready for the bounded live matrix once an existing endpoint and source-based validators/review are supplied. It is not ready for unattended production generation on CPU evidence alone. Stop here after implementation, CPU validation, and reporting.
