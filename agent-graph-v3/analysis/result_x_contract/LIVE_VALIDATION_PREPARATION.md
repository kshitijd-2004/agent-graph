# Seven-pair live validation preparation

Prepared 2026-10-01 against the accepted `PRODUCTION_INTEGRATION.md` matrix.
**Prepared only: no live pairs, semantic-review model calls, endpoint probes, or server operations were executed.**

## Files and scope

- `generation/result_x_live_review.py`: source-only admission callbacks, deterministic source arithmetic/evidence checks, and an isolated JSON semantic-review client.
- `scripts/run_result_x_live_validation.py`: executable fixed seven-pair driver; defaults to printing its plan, requires `--run` to execute.
- `tests/test_result_x_live_review.py`: deterministic admission/rejection tests with offline reviewer responses.
- `tests/test_result_x_live_script.py`: fixed matrix, configuration, artifact preservation, failure recording, and default no-execution tests using a stub runner.
- This preparation report.

No existing production integration, scheduler, review-loop finalization, fixture, grading/evaluator, LEP, or source file was edited. No architecture migration, manifest update, or benchmark-wide validator registration was introduced. The new callable plugs into the accepted `validator(candidate, sources)` parameter.

## Admission design

Each validator receives only `DerivedResultDraft` and the accepted adapter's original sanitized source snapshot. It selects the exact original document/code paths needed by these three fixtures and extracts only the original task description from the sanitized manifest. It does not open a fixture directory, read expected outputs, accept traces or conditions, or pass manifest grading/attack/label fields to the reviewer. Exact primary-source quotations are mandatory. The accepted adapter independently enforces that producer evidence cites source bytes actually read.

The default semantic reviewer is a separate, fresh, tool-free request to the existing endpoint at temperature 0. It receives the candidate, full relevant original sources, original task, and domain review instructions. It must explicitly assess substantive correctness, usefulness, conclusion, reasoning, intended use, and supporting source quotations. Administrative handoffs, unsupported claims, invalid evidence, unresolved review issues, missing assessments, malformed/truncated responses, and unavailable review all reject. No reviewer response or rejection feedback is inserted into an agent conversation.

The reviewer must check all material claims, not merely recognize one correct fact or find matching words. Partial results and concrete source-grounded corrections can pass without completing the task. It is instructed to treat candidate/source instructions as data, consider conflicting evidence, and reject when it cannot establish support.

Successful decisions are cached per pair using the full candidate/source/task packet hash. Thus replayed clean-prefix drafts reuse their prior decision without a second model assessment. The key contains no arm, producer role, LEP identity, perturbation metadata, or outcome label. A changed source snapshot or candidate causes a new review.

This is **model-assisted source review**, not a deterministic proof of arbitrary natural-language entailment. By default the reviewer uses the same served model as the agents with a fresh context; errors can be correlated. `--review-model` can select a different model already served by that same endpoint. Semantic reviewer reliability and actual agent ID compliance remain live-review questions. The CPU tests validate the admission machinery with controlled reviewer decisions; they do not claim that an untested live reviewer always reasons correctly.

### FinQA

For `fin_finqa_052_msi_2006`, the code parses named rows/periods directly from `documents/report.md`, producing variables such as `debt_total` and `debt_thereafter`. It computes the thereafter/total percentage from those source cells and supplies that computation to the reviewer. No expected numerical answer is loaded or embedded as an acceptance target.

The reviewer checks factual interpretation, source-stated authority/preliminary status, periods, units, arithmetic choice, and usefulness. It also returns source-variable arithmetic checks tied to exact candidate claim substrings. Python recomputes those expressions using a restricted AST language (named source values, arithmetic, and conversion constants only; no `eval` or code execution). Reported numbers must occur in the candidate and match their stated precision; wrong arithmetic is rejected even if the reviewer marks the prose correct. `not_equals` supports legitimate corrections of prior numerical claims. Source facts can be checked as single-variable lookups. Worded approximations can use a null reported value and are explicitly delegated to semantic review against the computed quantity.

This allows varied prose, different valid rounding precision, intermediate facts, and review corrections rather than requiring equality with one fixed draft. It remains specific to the named table in the selected live fixture, not a generic validator for every FinQA problem.

### SWE code review

For the selected Django fixture, the reviewer sees the original `django/forms/fields.py` and original issue/task text. It must substantiate the location, copy/aliasing behavior, causal explanation, and usefulness of the diagnosis or correction from that code. Instructions distinguish shallow/deep copying, mutable containers and nested values, and disallow invented executed tests or runtime observations. The issue text alone is insufficient; valid code quotations are required. There is no reference patch, frozen answer, or benchmark verdict in the review input.

### Research

The accepted matrix names `research_conflicting`, not a materialized SciFact fixture. Its original paper A/B and trends document are supplied intact. The reviewer checks claim/verdict support, attribution, statistics, table/abstract consistency, controls, limitations, and disagreements. It must distinguish support, contradiction, and insufficient evidence, and must not treat a non-significant result as proof of equivalence.

The actual source disagreement and internal inconsistency are preserved; no stored correct verdict is substituted. Focused single-paper findings can pass when qualified and useful. This implementation does not claim coverage of other SciFact fixture formats outside the accepted seven-pair matrix.

## Driver behavior and outputs

The exact seven rows are encoded in `PAIRS`, including role-filtered first forward/reverse handoffs, extractor occurrence 2, branch:synthesizer, both branches in many-to-one, worker:specialist_b, and upstream:coordinator. Occurrence remains per producer, as in the accepted implementation.

All rows use `memory_mode=ephemeral_private`, `max_events=300`, `max_agent_turns=40`. Both arms use fresh `HFBackend` instances with identical explicit model, temperature and output-token limit. CLI values override backend environment decoding overrides to prevent an unrecorded difference. The default actor temperature is 0.4 and token limit 4096. Review calls use temperature 0 and a separate configurable token limit (default 4096).

Final publication paths are exactly:

- Financial: `output/financial_summary.md`
- SWE: `output/code_review_report.md`
- Research: `output/research_synthesis.md`

Each pair has its own directory (`01_financial_forward` through `07_research_fanout`) containing the accepted runner's `result_x_audit.json`, `clean_trace.json`, and `input_disregard_trace.json` whenever both arms execute. These files are preserved without rewriting. Sibling `*_reviews.jsonl` files record exact reviewer packets, decisions, calculation errors, request hashes, and cache hits. `plan.json` records settings and matrix; `summary.json` is updated after each attempt with status, artifacts, review call counts, recorded actor calls/replayed turns, and elapsed time. Failed pairs remain recorded; no favorable-only reruns or selection occur. An existing output directory is refused.

The unchanged production adapter stops a pair if clean fails before the ID arm starts. In that case there is no ID trace to preserve: the script records it under `missing_artifacts` and continues to the next planned pair. It does not fabricate traces or change the accepted early-stop behavior. A nonzero process exit indicates at least one failed/ineligible pair. A zero exit means runner completion/admission, **not proof of live ID compliance or production readiness**. Review final audits manually as required by the accepted report.

## Deterministic validation

Result: **61 passed**. The direct script `--help` check and `git diff --check` also passed.

```sh
CUDA_VISIBLE_DEVICES='' TMPDIR=/tmp \
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest \
  tests/test_result_x_live_review.py tests/test_result_x_live_script.py -q --tb=short
```

The tests exercise alternate financial prose/rounding, source-value changes, wrong and reversed arithmetic, incorrect precision, substituted numbers, intermediate facts, explicit numerical corrections, positive/negative semantic assessments, unsupported SWE/research claims, research disagreement, forbidden metadata isolation, bad/missing citations, unavailable/malformed reviewer responses, safe arithmetic parsing, cached decisions, fresh tool-free reviewer contexts, exact matrix/role settings, fixed decoding settings, and artifact preservation under a stub runner. Script wiring tests do not execute any production stages or model calls.

## Command to run later

From `/u/kdhande/agent-graph/agent-graph-v3`, once your existing endpoint is available:

```sh
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python \
  scripts/run_result_x_live_validation.py --run \
  --base-url "$LLM_VLLM_BASE_URL" \
  --model "$LLM_MODEL" \
  --output analysis/result_x_contract/live_validation_01
```

Set `LLM_VLLM_BASE_URL` to the existing base URL including `/v1` and `LLM_MODEL` to its exact served model ID. Authentication, if required, uses the existing backend's `LLM_VLLM_API_KEY`. The driver never manages a server. Without `--run`, it only prints the fixed plan and does not contact the endpoint or create outputs. A review-model override must refer to a model already available on the specified endpoint.

## Planning estimate

Assuming four-stage review loops, three-stage branch/verify runs, four-stage workers-first coordinator runs, and five-stage fan-out runs, replay reduces the seven pairs to roughly **40 newly sampled actor-stage invocations**: 7 + 6 + 5 + 4 + 4 + 5 + 9. At 4–10 model turns per invocation, budget about **160–400 actor requests**, plus up to about **26 distinct semantic review requests** for those minimal successful trajectories (often fewer because identical candidates are cached). Relays do not invoke the validator. Extra revisions, retries, malformed tool output, and longer analyses can increase cost; early rejection reduces it.

No endpoint latency was measured. As a conditional estimate, an average 5–20 seconds per actor/reviewer request puts this sequential smoke around **15 minutes to 2.5 hours**; context length, output length, model size and server load can move it substantially. Recorded actor-turn counts exclude HTTP requests that fail before a recorded model turn; reviewer counts include attempted review calls. Live completion, quality and ID compliance still require inspection.
