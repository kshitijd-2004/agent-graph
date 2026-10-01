# Bounded Result X protocol correction

No live validation, model/API calls, or vLLM operations were performed for this change. The five completed pairs in `live_validation_02` and all existing live artifacts were preserved.

## Observed failures

Read the saved second-run summary and failed handoff payloads:

- `04_research_branch`: the clean synthesizer read both original papers, but its quotation reflowed their line breaks into spaces. Exact-substring sealing correctly rejected it.
- `06_swe_worker`: the clean specialist_a invocation read `django/forms/fields.py`, but the handoff contained conclusion, reasoning and evidence without `intended_use`. Required-field validation correctly rejected it.

These are generation-protocol failures. Neither calls for weaker evidence verification or fabricated content.

## Exact change

`generation/result_x_production.py` now presents an explicit new-derivation JSON schema in its Result X prompt. It requires all four fields: `conclusion`, `reasoning`, `evidence`, and `intended_use`. The three prose fields must be nonempty strings; evidence is a nonempty array of path/quote objects. The prompt explains that intended_use must describe how the receiver should use the result, and prohibits omitted fields and placeholders. Existing exact-excerpt/current-invocation instructions remain in place, including preserving whitespace and code indentation.

A private `ContractError` subtype distinguishes correctable draft-format/quotation failures from provenance, relay, semantic-review, and matching failures. The existing seal checks still reject malformed drafts. `seal_or_repair` catches only that subtype and permits **one correction request per invocation**, only when a turn remains under the existing cap. It provides a validation error and the required schema, without filling fields, changing quotations, reading sources on the producer's behalf, or supplying derived content. The producer must author a corrected candidate through the ordinary handoff tool. Sealing and substantive validation run again on that candidate. A second malformed candidate raises the original failure; unread-source and semantic failures remain terminal without correction.

`generation/stage_runner.py` has one small change inside the existing `result_x` guard: deliver the correction as a result of the rejected handoff tool call and continue the current stage loop. This shared-file hook is needed because only that loop owns the current turn count, tool-call ID, trace dependencies and both native/text histories. The correction consumes the existing turn budget; it does not reset the stage, change the model, extend turn/event limits, reroute the topology, or restart the pair. The rejection and correction are audited; the rejected draft creates no AGENT_HANDOFF boundary or injection origin and is not assigned a Result X identity.

No changes to the four-field contract, exact-substring checks, original-source read provenance, semantic validators, ID instructions, identity hashing, relays, replay/matching, source/tool access, publication, grading, fixtures, or review-loop finalization. Valid handoffs take the same sealing path with no correction message or extra event. Pre-intervention correction turns replay through the existing replay mechanism, with the same input-equality checks. Both arms receive the same protocol/schema instructions. Subsequent model-authored behavior after the intervention remains subject to the existing rules.

## Files

- Modified tracked files: `generation/result_x_production.py`, `generation/stage_runner.py`. Earlier uncommitted evidence-sealing changes were retained.
- Added `tests/test_result_x_protocol_repair.py` (22 deterministic tests).
- Added this report and `protocol_repair/tests.log`, `protocol_repair/legacy_comparison.json`, `protocol_repair/legacy_comparison.log`.
- Other pre-existing tracked/untracked work and all live-run outputs were left intact.

## Deterministic verification

**445 passed in 9.75 seconds**, using the active Python 3.12.9 interpreter. [Full log](protocol_repair/tests.log).

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp python -m pytest \
  tests/test_result_x_protocol_repair.py tests/test_result_x_evidence_sealing.py \
  tests/test_result_x_workflow.py tests/test_result_x_production.py \
  tests/test_result_x_live_review.py tests/test_result_x_live_script.py \
  tests/test_delivery_provenance.py tests/test_injection_count_regression.py \
  tests/test_injection_lifecycle_diagnosis.py tests/test_runner_admission.py \
  tests/test_memory_many_to_one.py tests/test_backend_retries.py \
  tests/test_topology_targeting.py tests/test_fan_out_fan_in.py \
  tests/test_behavioral_anomaly.py tests/test_fixture_aware_propagation.py \
  tests/test_review_cap_finalize.py -q --tb=short
```

New tests reproduce both observed failure classes, verify no automatic field/quote repair, admit producer-authored corrections only after sealing, exhaust the single-correction allowance, refuse retries on provenance/semantic failures, and verify a last-turn failure cannot extend the budget. Deterministic native and text backends exercise the real production stage loop and replay. Across all five topology/mode combinations, corrected candidates produce exactly the same sealed result bytes/IDs and injection-origin counts as valid deterministic controls. No rejected candidate contributes a result or origin.

The existing CPU-only legacy differential test also passed: **30/30 traces identical**, excluding runtime/timestamps and execution IDs. This covers benign, TRC, IPI, MP, HC and legacy ID across all five topology/mode combinations. It uses scripted backends and no API calls. [Results](protocol_repair/legacy_comparison.json), [log](protocol_repair/legacy_comparison.log).

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp python - <<'PY'
from pathlib import Path
p = Path('analysis/result_x_contract/production/compare_legacy.py').resolve()
code = p.read_text().replace("(HERE / 'legacy_comparison.json')", "(HERE.parent / 'protocol_repair/legacy_comparison.json')").replace("(HERE / f'diff_", "(HERE.parent / 'protocol_repair' / f'diff_")
exec(compile(code, str(p), 'exec'), {'__file__': str(p), '__name__': '__main__'})
PY
```

`git diff --check` passed. No live rerun was performed. The correction is bounded recovery from malformed generation, not a guarantee that a live model will produce a compliant second candidate; repeated or substantively invalid output still fails closed.
