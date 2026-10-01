# Forced-finalization write-before-submit protocol

This follows the live_validation_04 diagnosis: the three failed trajectories never wrote their configured final artifacts. Publication remains strict and unchanged; no artifact is synthesized, copied from a prior invocation, or derived automatically from model text.

## Implementation

`generation/result_x_production.py` identifies a forced-finalization invocation using the existing scheduler state: the current stage can finalize but cannot hand off, remaining reviews are zero, and the original topology stage could hand off. This excludes ordinary terminal stages such as a branch verifier. No scheduler changes were made.

The forced invocation's system prompt now starts with an explicit ordered instruction:

1. Call `write_file` with the configured report path and the complete final answer.
2. After the write succeeds, call `submit_final` for that same path.

A missing-artifact exception remains a `ContractError`, with its existing message. A forced invocation may receive exactly one missing-artifact correction. The correction states that the artifact has not been written and nothing was published, names the configured path, and directs a model-authored write followed by submission. A second invalid submission raises the error. Explicitly wrong paths are not retried. Non-forced invocations retain their prior behavior.

The correction is allowed only if at least two turns remain under the existing invocation cap, enough for a write and a subsequent submission. The counter resets per invocation, independently of handoff draft repairs. It does not extend event or turn limits, reset the backend, resample a matched prefix, or change model settings.

`generation/stage_runner.py` adds a guarded Result X hook at submit_final. The loop must own this hook because it supplies the remaining turn budget and continues the existing invocation. A rejected submit produces an audited tool-result correction in both native and text histories, with a dependency on its tool call. It creates no FINAL_RESPONSE, publication, handoff boundary, or injection origin. Only a later valid submission takes the existing final-response/publication path.

Publication still reads an actually existing artifact at the one configured path in the current private workspace and waits for scheduler acceptance. Result X evidence, identities, delivery instructions, replay/matching, source access, topology, fixture grading and other LEPs are unchanged. Both arms receive the same forced-finalization prompt and deterministic correction policy; pre-intervention calls remain subject to the existing replay checks.

## Files changed in this follow-up

- `generation/result_x_production.py`: forced-stage instruction, missing-artifact error subtype and one-correction policy.
- `generation/stage_runner.py`: Result-X-only submission correction hook.
- `tests/test_result_x_final_artifact.py`: expanded to 14 cases.
- `tests/test_result_x_evidence_sealing.py`: its test-only stage stub now supplies normal finalize/handoff capability fields.
- This report and `forced_final_protocol/` test/differential outputs.

All earlier tracked/untracked work and saved live outputs were preserved.

## Deterministic validation

**484 tests passed in 11.72s.** [Full log](forced_final_protocol/tests.log).

The final-artifact tests cover review_loop forward, review_loop reverse and coordinator_workers single_origin. Native and text scripted backends exercise premature submit → correction → write_file → submit_final through the real production runner. They verify successful completion, eligibility, unchanged origin counts, exactly one final response, identical configured artifact bytes in both arms, and delivery of the correction to the backend. Repeated noncompliance remains rejected in all three cases. Additional tests cover wrong paths, non-forced invocations and actual forced stages with too little remaining budget for write plus submit.

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python -m pytest \
  tests/test_result_x_final_artifact.py tests/test_result_x_formatting.py \
  tests/test_result_x_evidence_sealing.py tests/test_result_x_protocol_repair.py \
  tests/test_result_x_workflow.py tests/test_result_x_production.py \
  tests/test_result_x_live_review.py tests/test_result_x_live_script.py \
  tests/test_delivery_provenance.py tests/test_injection_count_regression.py \
  tests/test_injection_lifecycle_diagnosis.py tests/test_runner_admission.py \
  tests/test_memory_many_to_one.py tests/test_backend_retries.py \
  tests/test_topology_targeting.py tests/test_fan_out_fan_in.py \
  tests/test_behavioral_anomaly.py tests/test_fixture_aware_propagation.py \
  tests/test_review_cap_finalize.py -q --tb=short
```

**30/30 legacy differential traces matched**, excluding timestamps/runtime/execution IDs. [Results](forced_final_protocol/legacy_comparison.json), [log](forced_final_protocol/legacy_comparison.log). The existing scripted comparator was executed with only its output destinations redirected to preserve earlier results:

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python - <<'PY'
from pathlib import Path
p = Path('analysis/result_x_contract/production/compare_legacy.py').resolve()
code = p.read_text().replace("(HERE / 'legacy_comparison.json')", "(HERE.parent / 'forced_final_protocol/legacy_comparison.json')").replace("(HERE / f'diff_", "(HERE.parent / 'forced_final_protocol' / f'diff_")
exec(compile(code, str(p), 'exec'), {'__file__': str(p), '__name__': '__main__'})
PY
```

`git diff --check` passed. No live model/API calls, live validation, or vLLM operations were performed. Live compliance with the new instruction is untested; repeated failure to write still fails closed.
