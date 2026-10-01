# live_validation_04: missing final artifacts

The three failed trajectories never wrote a final artifact. This is not evidence of an earlier valid write being lost during forced finalization. After this finding, the user explicitly selected: **“Document and test only; leave publication unchanged.”** No production implementation or publication behavior was changed in this follow-up.

## Saved evidence

Inspected the clean-arm `result_x_audit.json` files, model tool calls, executed tool records, final-stage tool availability and private/materialized workspace files. [observations.json](final_artifact/observations.json) preserves audit hashes, counts, configured paths and filesystem findings.

| Pair | Invocations | Executed reads | Handoffs | write_file calls | Final invocation | Configured artifact |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 01_financial_forward | 11 | 22 | 10 | 0 | analyst, index 10 | `output/financial_summary.md` |
| 02_swe_reverse | 11 | 11 | 10 | 0 | reviewer, index 10 | `output/code_review_report.md` |
| 06_swe_worker | 10 | 10 | 9 | 0 | coordinator, index 9 | `output/code_review_report.md` |

Each trajectory made one submit_final call, naming the correct configured path. Neither the model-call audit nor the executed-tool audit contains any write_file call. No configured artifact exists anywhere in these saved trajectories. The failed clean runs stopped the pairs before ID execution.

The last invocation in each case offered submit_final without handoff, consistent with forced finalization. It also offered write_file and normal source tools. Its system prompt explicitly instructed writing the complete final artifact at the configured path. The model submitted a textual summary without first creating that file. Thus:

- The error is not an omitted or wrong report_path.
- It is not a write from an earlier stage hidden by invocation-private workspaces.
- It is not an evidence-sealing regression or loss of Result X identity/provenance.
- Prior handoff conclusions and final summary text do not constitute a written task artifact.

Recognizing an earlier authoritative file could not repair these runs: none exists. Synthesizing a report from their summaries or admitting an absent file would violate the required publication contract. Existing fail-closed behavior remains appropriate. Earlier-write reuse was not implemented following the user's clarification.

## Regression coverage and files

Added `tests/test_result_x_final_artifact.py` with three deterministic production-runner cases: review_loop forward targeting, review_loop reverse targeting, and coordinator_workers single_origin. Scripted agents read original sources and emit valid Result X handoffs but never write a file. They continue until the scheduler forces finalization and then submit the configured path.

Each case verifies that the final invocation offers submit_final without handoff, no executed write exists anywhere in the trajectory, the missing-artifact error occurs, the run remains unsuccessful/ineligible, and no configured final artifact is published. The tests reproduce the actual failure class; they do not force eligibility or fabricate reports.

Only this test file, this report and the `final_artifact/` diagnostic/test outputs were added. No production files, fixtures, grading, topology, ID semantics, replay/matching, source access, Result X behavior or review-loop finalization were modified. All pre-existing changes and saved live outputs were preserved.

## CPU validation

The initial three targeted regressions passed. The full requested deterministic suite then passed: **473 tests**, including all Result X/validator tests and focused origin, topology and generation-readiness regressions. [Full log](final_artifact/tests.log).

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

**30/30 CPU legacy differential traces matched** after excluding timestamps/runtime/execution IDs. [Results](final_artifact/legacy_comparison.json), [log](final_artifact/legacy_comparison.log). The existing comparator was executed with only output destinations redirected to preserve historical files:

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python - <<'PY'
from pathlib import Path
p = Path('analysis/result_x_contract/production/compare_legacy.py').resolve()
code = p.read_text().replace("(HERE / 'legacy_comparison.json')", "(HERE.parent / 'final_artifact/legacy_comparison.json')").replace("(HERE / f'diff_", "(HERE.parent / 'final_artifact' / f'diff_")
exec(compile(code, str(p), 'exec'), {'__file__': str(p), '__name__': '__main__'})
PY
```

`git diff --check` passed. No live validation, model/API calls, or vLLM operations were performed. The three saved runs remain failed; this investigation does not retroactively repair or reclassify them.
