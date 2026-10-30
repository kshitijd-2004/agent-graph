# Bounded hybrid MP smoke runner

The new runner is `scripts/smoke_mp_adjudicator.py`; its transport is isolated in `benchmark/mp_adjudicator/smoke_transport.py`. It uses the unchanged hybrid adjudicator. Selection is fixed to the frozen AON and SPGI MP traces, with exactly three matched clean references each. Existing admission, packet construction, semantic prompts, thresholds and decision rules are unchanged.

No live smoke execution was performed while preparing this runner. Offline tests mock all HTTP responses. They establish transport mechanics, not live semantic accuracy.

## Preview

Run from the repository root:

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
export MP_HYBRID_ENDPOINT=https://api.openai.com/v1
export MP_HYBRID_MODEL=gpt-4.1-mini-2025-04-14
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_adjudicator \
  --max-calls 4 --max-input-tokens 100000 --max-output-tokens 6000 \
  --budget-usd 0.25
```

Preview is cache-only, does not require credentials, and creates no output directory. It reports the actual known request estimates, cached assessments where available, a maximum remaining call count, and a conservative cost ceiling. Exact Stage B inputs depend on Stage A's actual response. A cold preview cannot know their size; it explicitly reports that limitation rather than substituting authored judgments. At the default caps the four-call conservative ceiling is $0.1984 using the configured rates; actual per-request reservations use actual request sizes and generally are smaller.

## Execute yourself

Set `OPENAI_API_KEY` or `MP_HYBRID_API_KEY` through your existing secret mechanism. If needed, Bash can read it without echo or command-history exposure:

```bash
read -r -s -p 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
```

Then use a **new** output directory:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_adjudicator \
  --execute --output analysis/mp_hybrid_smoke/smoke_001 \
  --max-calls 4 --max-input-tokens 100000 --max-output-tokens 6000 \
  --budget-usd 0.25
```

At the first uncached request, an interactive terminal requires the exact confirmation:

```text
PAY MP HYBRID 4 0.25
```

There is no noninteractive confirmation bypass. A declined confirmation halts further paid attempts. No confirmation or API key is needed when all responses are cached. Progress is flushed after responses, cache hits and failures and includes trace, stage, completed API responses, paid attempts, cache hits, elapsed time and cumulative estimated cost. Reservations also print before sending.

To replay the same cache with paid calls forbidden:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_adjudicator \
  --execute --cache-only --max-calls 0 \
  --output analysis/mp_hybrid_smoke/replay_001 \
  --max-input-tokens 100000 --max-output-tokens 6000 --budget-usd 0.25
```

Keep output-token settings unchanged to reuse the exact cache identity. Each output directory must be new. The default shared cache is `analysis/mp_hybrid_smoke/cache`; `--cache` may select another dedicated subdirectory in that same namespace. Outputs and cache cannot overlap.

## Limits and failure handling

- At most one paid request for behavioral interpretation (A), then one for attribution (B), per trace. Global cap cannot exceed four. No hidden verification, retries, redirects, fallback model, API token-count calls or evidence truncation.
- Before each request, the runner checks global call count, input-token bound and dollar reservation. The output cap is sent as `max_completion_tokens`. The new transport uses strict JSON-schema Chat Completions requests, as documented in the [official API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
- The local input guard uses UTF-8 serialized request byte count plus 4,096 framing tokens, a deliberately conservative bound for the pinned byte-level model tokenizer. The display estimate uses bytes/4. These are not exact provider token counts. If the bound exceeds the configured cap, the request is stopped without truncation. Increase caps only after reviewing the saved diagnostics.
- Dollar reservations use that input bound and maximum output tokens. Reported usage replaces a reservation with a usage-based estimate when valid; no provider cache discount is assumed. Unknown usage or ambiguous network failure retains the full reservation. Unexpected provider usage above the bound halts further requests and records the larger known estimate. Budget enforcement concerns estimated spend at configured prices; it cannot guarantee the provider's invoice or undo an unexpected charge.
- A durable pending cache record is written before each request. A timeout, HTTP error, pending/crashed attempt, refusal or incomplete response is never silently retried on rerun. Review those artifacts before separately authorizing any retry. The runner deliberately provides no automatic retry switch. Do not delete failed/pending records merely to bypass this protection.
- HTTP/transport failures halt further uncached requests in the run. Already-cached evidence may still be evaluated. Request/response contract failures remain explicit unknown decisions under the unchanged adjudicator.

`MP_HYBRID_MAX_CALLS`, `MP_HYBRID_MAX_INPUT_TOKENS`, `MP_HYBRID_MAX_OUTPUT_TOKENS`, `MP_HYBRID_BUDGET_USD` supply CLI defaults. `MP_HYBRID_TIMEOUT` defaults to 120 seconds. Temperature is fixed at 0. Model identity must remain the approved snapshot in this runner. The underlying transport is OpenAI-compatible and accepts an HTTPS endpoint; different endpoint identities never share cache entries. API keys are never persisted in requests or artifacts.

Configured rate assumptions are `MP_HYBRID_INPUT_USD_PER_MILLION=0.40` and `MP_HYBRID_OUTPUT_USD_PER_MILLION=1.60`, inherited from the prior smoke pricing scenario, not asserted as a newly verified price quote. Review/update rates for the endpoint before paid execution. They are saved in the plan. Generation-affecting request configuration, endpoint, prompt, schema and input all participate in cache identity; local spending limits and price assumptions do not invalidate unchanged semantic responses.

## Outputs and interpretation

Every execution writes only to a new directory under `analysis/mp_hybrid_smoke/`:

- `plan.json`, `implementation_hashes.json`: settings, preview and code snapshot before requests.
- `packet_0.json`, `packet_1.json`: full evidence packets and original event/trace identity mappings.
- `requests.jsonl`: hashes, exact requests, raw returned response objects, usage, cost estimates, cache hits, reservations and failures.
- `decisions.jsonl`: unchanged adjudicator outputs with per-endpoint states, supporting spans and provenance.
- `event_findings.jsonl`: full accepted findings, including origin, affected agent, supporting event IDs/spans, relation, cross-agent transfer, recovery where present, recorded paths and depth availability. An empty file means no accepted findings; it does not imply no propagation.
- `validation_0.json`, `validation_1.json`: rejected proposals, failure reasons and coverage diagnostics.
- `summary.json`: transport completion and semantic success reported separately.

Shared cache records preserve raw response text/objects, usage and failed or pending attempts. Cache hits are copied into the new request audit as raw response objects. Failed/malformed HTTP bodies remain in the cache record identified by the request hash.

Transport completion requires four returned/cached stage responses without request failures. Semantic success requires a usable primary adverse-propagation judgment, complete review, and no reported observation-validation or finding-verification failures for each trace. Other unknown endpoints remain visible. An all-unknown result is a semantic failure, not a successful smoke test. A partly usable result may be inconclusive. These checks are runner health criteria, not changes to propagation definitions or independent accuracy validation.

Exit codes: 0 = transport and semantic health success; 2 = transport incomplete/failed (also argparse errors); 3 = transport completed but semantic health failed. Even exit 0 does not establish scientific validity.

## Offline tests

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest -q tests/test_mp_adjudicator_smoke.py
```

The tests cover two-stage cache replay without paid calls, config/input cache separation, call/token/dollar limits, explicit confirmation, crash/timeout records, refusal and truncation, missing usage, redirect rejection, schema failures, all-unknown semantic failure, progress and event-level outputs. Synthetic packets and mocked responses are used; the real smoke workflow is not executed by these tests.
