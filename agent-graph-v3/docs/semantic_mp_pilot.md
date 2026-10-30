# Bounded MP semantic development pilot

Run from the repository with its existing Python environment (`python3`, no new packages).
This workflow does not establish independent validation or change official results.
Only the frozen Phase 1 development sample is used. Smoke: AON 270 and SPGI 082.
Pilot: those same two (cache reuse), HST 165, SciFact 045, matplotlib 061.
Thus there are five unique real perturbed traces, not seven, each with three matched controls.

```bash
cd /u/kdhande/agent-graph/agent-graph-v3
export MP_SEMANTIC_ENDPOINT=https://api.openai.com/v1
export MP_SEMANTIC_MODEL=gpt-4.1-mini-2025-04-14
export MP_SEMANTIC_TEMPERATURE=0
export MP_SEMANTIC_MAX_TOKENS=4096
export MP_SEMANTIC_CACHE=/tmp/agentprop-semantic-cache
# Set OPENAI_API_KEY securely in your shell if not already configured.

# Offline estimates: no credentials or confirmation required.
python3 -m scripts.pilot_semantic_mp smoke --output analysis/mp_semantic_pilot/smoke --max-calls 74 --budget-usd 0.70
# Reprints estimate, then requires typing PAY smoke before any paid call.
python3 -m scripts.pilot_semantic_mp smoke --output analysis/mp_semantic_pilot/smoke --max-calls 74 --budget-usd 0.70 --execute

# Inspect smoke decisions, coverage gaps, and summary before proceeding.
python3 -m scripts.pilot_semantic_mp pilot --output analysis/mp_semantic_pilot/pilot --max-calls 104 --budget-usd 1.00
# Requires completed smoke summary and typing PAY pilot.
python3 -m scripts.pilot_semantic_mp pilot --output analysis/mp_semantic_pilot/pilot --max-calls 104 --budget-usd 1.00 --smoke-summary analysis/mp_semantic_pilot/smoke/summary.json --execute
```

Keep model/settings/cache unchanged between phases. Existing adapter caches are compatible.
A durable private cache path may replace `/tmp/agentprop-semantic-cache`; keeping the existing
path reuses any prior entries. Cache content is also copied into each run's request ledger.
Outputs must be new subdirectories of `analysis/mp_semantic_pilot`; existing runs are never overwritten.
For a retry choose a new output name and explicitly authorize a new budget. Limits are per invocation,
not an account-wide spending cap. The two commands above authorize at most $1.70 in total at the
configured rate table. There are no automatic retries, batch submissions, or alternate model fallbacks.

## Estimates and accounting

Empty-cache smoke: 74 calls, approximately 44,560 input tokens, $0.503 including maximum output
for every call, $0.678 conservative reservation total. Five-trace empty-cache pilot: 178 calls,
112,380 estimated input tokens, $1.212 with maximum output, $1.638 conservative reservation.
After successful smoke: at most 104 new requests, approximately 67,820 input tokens,
$0.709 with maximum output and $0.961 conservative reservation. Actual estimates are printed
from current cache/configuration before execution. Output usage is usually below the maximum,
but do not assume that before running. Truncation is a failure, never a negative decision.

Standard rates: $0.40 input / $0.10 provider-cached input / $1.60 output per million tokens,
from https://developers.openai.com/api/docs/models/gpt-4.1-mini (checked 2026-10-16).
Approximate input count uses serialized UTF-8 bytes / 4, not a tokenizer.
Budget admission reserves all serialized request bytes plus 4096 framing tokens at uncached
input price, plus the full output cap. Each reservation is persisted before network access.
Reported usage settles the reservation; ambiguous failures retain it and stop the run.
No further request is sent when the call cap or dollar reservation cap would be exceeded.
This is a client-side cost cap under the recorded rate/token-accounting assumptions, not a
provider-enforced billing cap. Provider pricing changes require review of the rate table.
API responses report usage, not invoice dollars; cost fields are computed estimates.

## Evidence and limitations

The runner plans all extraction inputs offline, deduplicates exact requests, uses deterministic
extraction where already supported, and submits only the existing extractor's observable text.
Matching, intervention labels, clean-role comparisons and causal attribution stay local.
All clean reference behavior is retained. Authoritative context comprises unique read_text_file
results observed in clean controls, without attack manifests or answer labels. No new clipping,
poison-string filtering, model batching, or semantic extractor changes are introduced.
The unchanged evaluator currently uses 300-character mutation prefixes: this pre-existing
capability limitation remains, and this pilot is not a remedy or independent validation of it.

Artifacts: plan.json (pins, rates, input/code hashes and estimates), semantic_inputs.json
(exact model inputs), requests.jsonl (reservations, cache hits, raw responses including usage,
model fingerprints and failures), decisions.jsonl (endpoint decisions, exact proposition spans,
clean-reference and delivery evidence, authoritative source text), summary.json (spend and stop status).
Every response/cache entry is preserved even if extraction validation subsequently fails.
If a request fails or a budget stops execution, available cached evidence is evaluated offline;
missing extraction responses remain explicit evaluation failures/unknowns. Positive endpoints
may still be supported by other evidence, following the unchanged evaluator's existential rule.
Inspect endpoint status and gaps; a completed transport run does not imply semantic completeness.

Offline regression command:
```bash
python3 -m pytest -q tests/test_semantic_mp_pilot.py tests/test_semantic_mp.py tests/test_mp_retrospective.py
```

## Development correction after the failed smoke

The historical smoke's `completed` status described API transport, not semantic
success. The runner now distinguishes these, exits 3 for semantic incompleteness,
and recomputes smoke semantic health before allowing pilot execution. The existing
smoke is blocked from progressing. Existing artifacts are preserved unchanged.

The corrected v2 evaluator is separately implemented and **offline-only at the CLI**.
The live commands above still select the original development evaluator; do not
use them to test v2. See `docs/semantic_mp_v2_development.md` for the offline replay.
A new paid test requires separate approval; none was performed for this correction.
