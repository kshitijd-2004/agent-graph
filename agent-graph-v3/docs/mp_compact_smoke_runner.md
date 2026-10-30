# Compact v2 MP smoke runner (development only)

The new runner is `scripts/smoke_mp_compact.py`. It selects only the frozen AON/SPGI MP traces and exactly three matched clean references each. It uses the v2 packet builder, compact codec and bridge, v2 endpoint-specific coverage, and unchanged original verification kernel. It does not call the original hybrid evaluator directly or activate production analysis.

## Approved limits

- 160,000 conservative input units per request; exact outgoing serialized JSON bytes plus 4,096 framing allowance.
- 16,384 maximum output tokens per request, sent as `max_completion_tokens`.
- Four paid attempts total; no more than stages A and B once per trace. Stage B is skipped without verified target behavior.
- $0.40 estimated-spending cap, including unresolved reservations. Interactive confirmation before the first paid request. No automatic retries, redirects or fallbacks.

The new configuration class preserves existing transport checks while permitting the specifically approved larger input bound. It cannot be configured above the approved call, input, output or spending caps. It does not change model context limits, evidence, thresholds, admission, clean-reference requirements or semantic verification.

Actual Stage B size is measured after Stage A returns. Oversized evidence is never truncated. Any rejected/failed request halts subsequent paid execution in that invocation; cached evidence remains usable. Pending/failed cache records block silent retries across invocations. Missing usage retains the entire reservation. Spend is an estimate at configured rates, not an invoice guarantee.

## Commands

From the repository root:

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
export MP_COMPACT_ENDPOINT=https://api.openai.com/v1
export MP_COMPACT_MODEL=gpt-4.1-mini-2025-04-14
```

Preview (no credentials, requests or output writes):

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_compact \
  --max-input-tokens 160000 --max-output-tokens 16384 \
  --max-calls 4 --budget-usd 0.40
```

With `OPENAI_API_KEY` or `MP_COMPACT_API_KEY` set through your secret mechanism, execute yourself:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_compact \
  --execute --output analysis/mp_compact_smoke/smoke_001 \
  --max-input-tokens 160000 --max-output-tokens 16384 \
  --max-calls 4 --budget-usd 0.40
```

The exact terminal confirmation is `PAY MP COMPACT 4 0.40`. There is no noninteractive bypass. If everything is cached no payment confirmation or key is needed.

Cache-only replay, with a new output directory:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.smoke_mp_compact \
  --execute --cache-only --output analysis/mp_compact_smoke/replay_001 \
  --max-input-tokens 160000 --max-output-tokens 16384 \
  --max-calls 0 --budget-usd 0.40
```

The default cache is `analysis/mp_compact_smoke/cache`. Cache/output paths must remain separate and inside this dedicated namespace. Output directories must be new. Generation settings must remain unchanged for cache reuse. Request identities include exact model, endpoint, prompt, schema, input, temperature/output setting, transport version and a hash of implementation dependencies. Old failed-smoke caches cannot be reused under this identity. Changing implementation invalidates the identity; old artifacts remain intact.

Environment defaults use `MP_COMPACT_MAX_INPUT_TOKENS`, `MP_COMPACT_MAX_OUTPUT_TOKENS`, `MP_COMPACT_MAX_CALLS`, `MP_COMPACT_BUDGET_USD`, `MP_COMPACT_TIMEOUT`, and configured per-million input/output rates (default .40 and 1.60). Temperature stays 0; snapshot remains pinned. API credentials are excluded from identities and artifacts.

## Saved diagnostics

Every execution preserves plan and implementation hashes, complete v2 packets, compact occurrence mappings, request bodies, raw responses, usage, pending/failure cache records, decisions, endpoint coverage, verification errors and accepted event findings. Progress prints trace, stage, response count, paid attempts, cache hits, elapsed time and cumulative estimated cost.

Health reports transport completion, Stage A verified-observation count/errors, Stage B eligibility/skipping, each semantic endpoint's state/status/usability, and overall semantic success separately. A supported local positive does not require unrelated global coverage. Negative decisions retain v2 endpoint-specific review requirements. Overall success requires a usable primary adverse-propagation endpoint without reported stage/verification defects; unknown secondary endpoints remain explicit. A skipped Stage B can have successful transport but not overall semantic success. Successful HTTP responses alone never establish semantic success or scientific accuracy.

The cache preview cannot know a cold Stage B request. It displays that limitation and uses configured caps for a conservative plan. Saved size-only scenarios predict the following exact outgoing bounds with the new output setting:

| Case | Stage | JSON bytes | Input bound | Approx tokens | Reservation USD |
|---|---|---:|---:|---:|---:|
| AON | A | 83,166 | 87,262 | 20,792 | .0611192 |
| AON | B scenario | 132,079 | 136,175 | 33,020 | .0806844 |
| SPGI | A | 72,665 | 76,761 | 18,167 | .0569188 |
| SPGI | B scenario | 113,846 | 117,942 | 28,462 | .0733912 |

Total scenario reservations: $0.2721136. Absolute four-call configured ceiling: $0.3608576, below $0.40. Stage B figures are placeholders for sizing, not new model judgments or guarantees; the real request must pass its own check. Output adequacy and live semantic accuracy remain untested.

Offline test command:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest -q tests/test_mp_compact_smoke.py
```

No paid API calls were made during preparation. This runner is ready for review, not enabled for production.
