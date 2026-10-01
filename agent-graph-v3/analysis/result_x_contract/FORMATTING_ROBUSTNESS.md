# Result X formatting robustness after live_validation_03

No live validation, model/API calls, or vLLM operations were performed. Existing live runs and unrelated work were preserved.

## Exact observed causes

Inspected the saved `live_validation_03` audits and trace termination/origin metadata. [Extracted observations](formatting_robustness/observations.json) include original audit hashes, exact failed quotations, restored source spans, read sets, failed response summaries, and publication observations. Source excerpts were checked against the original snapshot SHA-256 hashes recorded in the audit. Grading answers were not used to design or admit evidence.

| Pair | Observed failure | Cause and correction |
| --- | --- | --- |
| 02_swe_reverse | Both arms `protocol_violation`, `dataset_eligible=False` | Clean inspector revision (stage 2) produced two identical 7,249-character truncated text-form handoff calls with `stop_reason=length`, no parsed tool call, and runaway escaped newlines. ID reviewer (stage 3) recovered once to write a report, then produced two identical 7,237-character truncated text-form submit_final calls. The existing generic nudge did not recover those calls. Added Result-X-specific guidance to that same one-retry mechanism, with concise native calls, single JSON serialization for handoff, and plain-language final summaries. Truncated output is never fabricated into an accepted call. |
| 04_research_branch | Quote absent, even after one correction | Stage 0 researcher read both original papers, then reflowed line breaks into spaces in both first and corrected quotations. Deterministic whitespace matching now restores their exact original source spans. |
| 06_swe_worker | Missing configured report_path | Stage 3 coordinator successfully wrote `output/code_review_report.md` (1,953 bytes), then called submit_final with only `summary`. The adapter now uses its authoritative configured path when omitted. |
| 07_research_fanout | Quote absent after correction | Stage 0 coordinator read both papers and submitted the same reflowed quotations twice. Both now match original spans through the same narrow whitespace rule. |

02 was not an injection-origin problem: expected/actual counts were 0/0 for clean and 1/1 for ID, with valid ID origin validation and no undelivered-origin flag. Completion failed, so ineligibility was appropriate. There is no eligibility override. Its truncated outputs were incomplete, not safely reparsable complete tool calls. The new nudge targets the demonstrated failure but cannot guarantee live model recovery; repeated truncation still produces protocol_violation and remains ineligible.

## Deterministic evidence matching

Added `generation/result_x_evidence.py`, used only by the opt-in production Result X adapter. The producer's source-read check still runs first, against the current invocation's read set. Matching then works as follows:

1. Preserve an already exact, nonempty source substring unchanged.
2. Otherwise, tolerate differences only in existing runs of ASCII space, tab, CR, LF, form-feed and vertical-tab; incidental leading/trailing ASCII whitespace may be stripped while locating the source span. A whitespace run must still separate the same neighboring non-whitespace characters. Whitespace cannot be inserted into or removed from a token.
3. Preserve every non-whitespace character, including case, digits, signs, punctuation, Markdown and Unicode characters. No case folding, Unicode normalization, word substitution, ellipsis expansion, or fuzzy matching occurs.
4. For Python sources, do not normalize whitespace inside string-literal tokens: that whitespace is data. If the original Python source cannot be tokenized, formatting fallback fails closed; exact quotes still work.
5. Return the first qualifying **actual original source substring**, not normalized/reconstructed text. Record submitted/restored quotations and the matching rule in stage audit metadata. Pass the restored evidence to the unchanged substantive validator and identity hashing.

Thus a reflowed quotation can identify supported source content without trusting the model's whitespace. The sealed X always contains exact original evidence. Changes of words/facts/punctuation, missing original-source reads, generated-artifact citations, and semantic-review failures remain rejected. The same transformation runs in both arms before sealing; matched X bytes remain identical. Previously valid exact quotations retain their original bytes and identity. Relays are unchanged.

The historical standalone harness remains strict; this production-only fix does not replace the legacy protocol or benchmark evaluators. The production semantic reviewer receives canonical exact quotations, so its existing source checks need no relaxation.

## Publication and truncation behavior

An absent, null, or empty report_path selects the one configured final path. An explicitly different path is rejected. The configured path must contain a file in the current private invocation workspace; it cannot name an original source, and previous-stage files are not inherited. The runner publishes only after the scheduler accepts final completion, as before. No final artifact is synthesized from summary text, and the malformed JSON-like prose inside the live-06 summary is not parsed as a handoff contract.

The Result X subclass overrides only the existing retry nudge for missing/truncated tool responses. It asks for a fresh, concise native call, no printed tool wrapper or repeated escaping, all four handoff fields, and a short plain-language submit_final summary. Retry count, model/decoding settings, replay, stage scheduling, event/turn limits, admission, and other LEP behavior remain unchanged. The normal Result X prompt carries the same concise serialization guidance. No extra resampling loop or automatic completion was added.

## Files changed for this follow-up

- New `generation/result_x_evidence.py`.
- Modified `generation/result_x_production.py`: canonical evidence before validation/sealing; configured publication-path fallback; scoped retry/prompt guidance.
- New `tests/test_result_x_formatting.py` (27 deterministic cases).
- Updated `tests/test_result_x_evidence_sealing.py`: removed two old whitespace-rejection expectations, now covered as accepted source-span recovery; substantive paraphrase/Markdown rejection tests remain.
- Added this report and `formatting_robustness/observations.json`, `regressions.log`, `legacy_comparison.json`, `legacy_comparison.log`.

Previously pending `generation/stage_runner.py` changes were retained without further modification. No fixtures, grading, evaluators, topology, origin semantics, ID instructions, source access, or review-loop finalization changes.

## CPU verification

**185 focused tests passed in 6.64s**, including the exact live research quotations, provenance rejection before a local read, restored evidence reaching the validator, changed-fact/token/Markdown rejection, Python literal safety, current-invocation publication checks, and five production topology/mode comparisons proving canonical result bytes/IDs, clean/ID matching, origin counts and publication equal valid controls. Native truncation tests exercise the actual production runner with `length` responses and escaped text wrappers, verifying both bounded recovery and appropriate continued ineligibility.

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python -m pytest \
  tests/test_result_x_formatting.py tests/test_result_x_evidence_sealing.py \
  tests/test_result_x_protocol_repair.py tests/test_result_x_workflow.py \
  tests/test_result_x_production.py tests/test_result_x_live_review.py \
  tests/test_result_x_live_script.py -q --tb=short
```

**285 additional tests passed in 8.34s**; [log](formatting_robustness/regressions.log). **470 total passed**.

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python -m pytest \
  tests/test_delivery_provenance.py tests/test_injection_count_regression.py \
  tests/test_injection_lifecycle_diagnosis.py tests/test_runner_admission.py \
  tests/test_memory_many_to_one.py tests/test_backend_retries.py \
  tests/test_topology_targeting.py tests/test_fan_out_fan_in.py \
  tests/test_behavioral_anomaly.py tests/test_fixture_aware_propagation.py \
  tests/test_review_cap_finalize.py -q --tb=short
```

**30/30 legacy differential traces identical**, excluding timestamp/runtime/execution-ID fields; [results](formatting_robustness/legacy_comparison.json), [log](formatting_robustness/legacy_comparison.log). The existing CPU-only comparator covers benign and all five LEPs across five topology/mode combinations. It was executed unchanged except redirecting output files to `formatting_robustness` to preserve historical results:

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/sw/user/python/miniforge3-pytorch-2.10.0/bin/python - <<'PY'
from pathlib import Path
p = Path('analysis/result_x_contract/production/compare_legacy.py').resolve()
code = p.read_text().replace("(HERE / 'legacy_comparison.json')", "(HERE.parent / 'formatting_robustness/legacy_comparison.json')").replace("(HERE / f'diff_", "(HERE.parent / 'formatting_robustness' / f'diff_")
exec(compile(code, str(p), 'exec'), {'__file__': str(p), '__name__': '__main__'})
PY
```

`git diff --check` passed. Live rerun was not performed. These results establish deterministic behavior, not guaranteed model completion or production readiness.
