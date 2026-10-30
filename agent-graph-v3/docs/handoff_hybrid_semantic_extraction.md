# LLM-Assisted Semantic Extraction — Audit & Test Report

Read-only audit of the existing LLM extraction infrastructure and the semantic
production measurement pipeline, with bounded offline test results and a cost
estimate for paid execution. No production data, traces, or results were
modified. No paid API calls were made. The only new artifact is this document.

## 1. What already exists (reused, not reimplemented)

- **`benchmark/semantic_mp_v2/adapter.py` — `Transport`.** Production-ready
  OpenAI-compatible adapter. Offline by default (`allow_live=False`). A live
  call requires `allow_live=True` *and* an API key. Content-addressed disk
  cache keyed on the full request identity (endpoint + model + prompt + schema
  + input text); even invalid/refused responses are saved for reproduction.
- **`benchmark/semantic_mp_v2/extraction.py` — `Extractor`, `validate`,
  `deterministic`.** The v2 extractor. The deterministic grammar runs first
  (zero model calls); only unrecognized free-form text escalates to the pinned
  LLM (`gpt-4.1-mini-2025-04-14`, temperature 0, strict JSON schema).
- **`benchmark/semantic_mp_v2/production_extraction.py` —
  `ProductionExtractor`.** The production runner's offline extractor.
  Deterministic, document-scoped, with exact character offsets and an
  on-disk exact-input cache. Preserved unchanged in this audit.
- **`benchmark/semantic_mp_v2/measurement.py`, `measurement_contract.py`,
  `measurement_reporting.py`, `sources.py`, `production_rules.py`.** The
  evidence contract, endpoint decisions, source bundles, and deterministic
  delivery/nondelivery rules. Unchanged.
- **`scripts/measure_semantic_propagation.py`.** The production CLI runner.
  Unchanged.

## 2. The key structural constraint (verified in test A)

The extraction layer (LLM or deterministic) only produces `Result` objects.
The `Result` type (`extraction.py`) has **no provenance field** — no
`response_id`, no `system_fingerprint`, no `usage`. The `Transport` *does*
return `response_id`, `system_fingerprint`, `usage`, `input_sha256`
(`adapter.py`), but `Extractor.extract` discards them: it builds the `Result`
via `validate(text, json.loads(response['content']))` and never threads the
transport provenance into it.

So for reproducible LLM-assisted extraction the caller must **record the
provenance at the runner level** (wrap `extract`, or read it back from the
transport cache by `input_sha256`), because it is not part of the `Result`
today. This is the one integration gap. It is the only place a new field
would be needed, and it is deliberately left out of scope for this audit.

Test A confirms the rest of the chain already works end to end offline:
deterministic-first (0 network calls for recognizable text), identical
replay from cache, and rejection of a fabricated quote span while retaining
the correctly-grounded proposition.

## 3. Test results (all offline; fake/cached transports only)

### Test A — `scripts/evaluate_semantic_measurement.py` + `Transport`
Uses `Extractor()` with the deterministic path and an injected opener.
- Recognizable deterministic text → `network_calls == 0` (deterministic wins
  before any model call). ✓
- Contextual text escalated to the fake transport → 1 network call. ✓
- Fresh extractor, same disk cache → identical result, `disk_hits == 1`,
  `network_calls` total unchanged. ✓ (replay without extra model calls)
- A proposition whose `quote`/`support_quotes` do **not** occur verbatim in
  the source (`ZZZ_ABSENT_SPAN`) → `semantic_support` raises
  `absent_quote` → that proposition is dropped as an `absent_quote`/
  `grounding_unverified` issue, while the correctly-quoted proposition is
  retained. So **fabricated spans are rejected deterministically**; the LLM
  cannot assert a claim whose cited evidence is not a verbatim source
  substring.
- Injection-attempt text in the input is treated purely as untrusted data:
  the model's content is JSON-parsed and each span is re-verified against the
  original text; nothing in the input can add a claim that fails span
  grounding.

### Test B — `Transport` model-pinning
Live responses are validated against the pinned model. A response whose
`model` field is anything other than `gpt-4.1-mini-2025-04-14` is rejected
(`model pin mismatch`); only the approved snapshot is accepted.

### Test C — `Extractor.validate` robustness (invalid/refused inputs)
Invalid JSON, an empty/None `choices` array, a refusal, a truncated
`finish_reason != stop`, a truncated `content`, a `null` content, and a
wrong model each produce a well-formed `Result` with status
`invalid_model_output` (or `capability_unsupported` / `execution_failed` as
appropriate) and **zero propositions**. Nothing is fabricated: malformed or
refused model output never yields a usable extraction, so downstream
endpoints cannot become positive on a bad response.

## 4. How LLM-derived propositions satisfy the evidence contract
(Explanatory — no code.)

The contract's safeguard is in `extraction_status`
(`measurement_contract.py`): a result only counts as extraction
`complete` when `result.complete and result.method.startswith("deterministic")`.
A context/model result is therefore always at least `partial`, and a `Result`
with no usable propositions is `failed`. The consequences for the endpoints:

- **Positive decisions** (adoption, forwarding, rejection, adverse_propagation,
  final_answer_impact) require `satisfied` closures whose supporting
  dependencies include `grounded_claim` nodes. Those nodes are `supported`
  only when (a) every cited span is a verbatim source substring **and**
  (b) the proposition's grounding is `deterministic_supported`. A
  context/model proposition is not `deterministic_supported`, so on its own
  it cannot single-handedly satisfy a positive. This is the exact mechanism
  by which "an LLM's `complete=true` is not trusted": model proposals are
  retained as *inspectable* partial evidence (with spans, stance, scope,
  source attribution) but they do not certify completeness or, by themselves,
  unlock a positive.
- **Unresolved/UNKNOWN** is preserved wherever coverage or identity cannot be
  certified deterministically. Missing scope, failed extraction, and
  unresolved claim identity all map to `unresolved`, never `absent` or a
  positive.
- **Negative decisions** come from the deterministic
  `apply_delivery_decisions` rules (verified-nondelivery) and from a
  fully-covered, complete, deterministic comparison; they are not derived
  from model guesses.

In short: the LLM **proposes** interpretations (subject, predicate, value,
stance, unit, scope, source, with quoted evidence). Deterministic
verification (verbatim span checks, alignment keys, support bounds, delivery
reconstruction) is the **final authority** on whether those proposals can
support, contradict, or are simply unresolved. The LLM never directly labels
causal propagation.

## 5. Cost estimate for paid execution (gpt-4.1-mini, 2025 pricing)

- The deterministic grammar handles most structured lines for free; only
  genuinely free-form, unrecognized text triggers a paid call.
- Approximate published list pricing for `gpt-4.1-mini`: ~$0.40 / 1M input
  tokens, ~$1.20 / 1M output tokens.
- Per-LLM-call order of magnitude: ~300–1500 input tokens (prompt + text),
  ~100–600 output tokens (strict JSON schema). Budget ~$0.001–$0.006 per
  call, plus retries/failures.
- The production population is 1,296 eligible MP traces, but a *full* paid
  rerun would extract every observed event, clean-run event, and source
  document for every trace — that is orders of magnitude more than 1,296
  calls (each trace contributes many extraction inputs). A realistic paid
  full-production estimate is in the low **thousands** of LLM calls, i.e.
  roughly **single-digit to low-tens of dollars** before retries, rate-limit
  overhead, and failure re-tries. A small validation sample (e.g. 1–5 traces
  with the full event set) is well under ~$0.50.

Exact figures require the real text lengths and the actual cache hit-rate;
the above is a planning band, not a bill. No call has been executed.

## 6. Exact commands (to be run only with explicit approval)

Run from the repository root in the existing Python environment. The
production CLI is `scripts.measure_semantic_propagation`; the extractor is
selected by `--extractor` (`offline` default, or `hybrid`). A cache-only
replay is the same command after a live run, pointed at the existing
`extraction_cache/` so no network is used.

**Small paid validation (bounded):**
```
python3 -m scripts.measure_semantic_propagation \
  --source-dir production_run --rerun-dir rerun_production_oct19 \
  --completed-shards /tmp/agentprop_completed_mp_shards.txt \
  --output analysis/semantic_mp_hybrid_validation \
  --mechanism LEP_MEMORY_POISONING \
  --extractor hybrid --max-traces 5
```

**Cached replay (no network):** re-run the same command against a directory
whose `extraction_cache/` is already populated, or with
`allow_live=False`; `Transport` then serves entirely from cache and any
miss raises instead of calling the API.

**Full production run (future, separate approval):**
```
python3 -m scripts.measure_semantic_propagation \
  --source-dir production_run --rerun-dir rerun_production_oct19 \
  --completed-shards /tmp/agentprop_completed_mp_shards.txt \
  --output analysis/semantic_mp_hybrid_full \
  --mechanism LEP_MEMORY_POISONING --extractor hybrid
```

**Reproducibility verification:** after any run, re-run with `--resume`
against the same frozen input snapshot; the `run_manifest.json` identity hash
must match, and the per-attempt result cache checksums must verify, so the
outputs are bit-identical.

## 7. Limitations

- The LLM extractor's provenance (`response_id`, `system_fingerprint`,
  `usage`) is not attached to the `Result`; it must be captured at the
  runner/transport layer (the one real integration gap, out of scope here).
- Cost and call counts above are planning estimates from approximate
  per-token pricing and must be re-estimated against real text lengths.
- LLM-derived propositions remain **partial** evidence by design; they
  cannot by themselves produce a positive endpoint decision. The general
  semantic-entailment gap (why exposed traces still abstain) is a
  certification/alignment limitation, not one a paid call resolves.
