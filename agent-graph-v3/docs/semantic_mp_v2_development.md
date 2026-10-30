# MP semantic development correction v2

This is an offline development correction, not a validated replacement for official
propagation results. It is separate from `benchmark/semantic_mp` (dev1), the
Phase 1 evaluator, and the legacy evaluator. Those implementations are preserved.
No live v2 CLI is enabled. The OpenAI-compatible adapter defaults to offline;
its online branch has been tested with an injected mock opener only.

## Contract and grounding

The model receives observable text, the pinned model configuration, prompt and
schema. It receives no intervention label, origin IDs, expected outcome or separate
known-poison-value hint. It returns propositions with exact quotes and supporting
quotes, not offsets. Python locates each quote uniquely in the original text and
records character spans. Missing/repeated quotes stay unresolved; old guessed
positions never disambiguate them. No fuzzy correction is performed.

The prompt/schema/validator share a versioned contract. Facts require values;
actions have a nullable scalar value. Applicability is a separate proposition kind.
Pure punctuation and pure greetings can be genuinely proposition-free. A nonempty
uninterpreted passage cannot become a complete empty extraction merely because a
model says so. `Not applicable` is interpreted as an applicability judgment.

Validation has distinct structural, quote-location and bounded semantic-support
stages. Quantity validation binds the subject, surface number, unit and scope in
the same clause, then normalizes units with Decimal. A unit or rejection cue in an
unrelated sentence cannot validate a claim. Explicit scopes cannot be discarded.
Stance-bearing evidence is necessary for quoted/rejected/hypothetical/uncertain
claims. Rejection is not assertion of its opposite and is not adverse propagation.
Simple declarative relations, claim verdicts, code-defect statements and explicit
actions are supported. General contextual entailment, implicit negation and table
header/cell interpretation are **not yet certified capabilities** of this checker.
They are reported as capability gaps, not failures of agent resistance.

Normalization is explicit and inspectable: casing/punctuation, constrained quantity
predicate synonyms, surface units/scales, fiscal years, calendar dates and levels.
Maturity dates retain their temporal role. Entity aliases are accepted only from
unambiguous explicit source appositives (e.g. Acme Corporation (ACME)); no guessed
company equivalences. Missing scope does not become an unrestricted wildcard.
This is not a learned semantic matching model; paraphrases outside the supported
rules remain unsupported. Independent alignment precision/recall is unmeasured.

Partial extraction retains grounded propositions plus per-item issue codes/statuses.
It is never relabeled complete. Endpoint negatives require relevant coverage;
positives require a grounded mutation/delivery/behavior chain, and adverse effects
also require existing clean-reference support and authoritative contradiction.
Coverage failures are endpoint-specific. Source failures do not erase an explicit
rejection. Unsupported added/removed matching is not conflated with API failure.

## Mutation and delivery

Full pre/post stored memory versions are compared, identifying additions, removals
and alterations. This handles AON's non-applicable record replaced by financial
assertions. Partial extraction cannot prove absence/removal.

The 300-character boundary is real in `generation/stage_runner.py:951`:
`read_memory` returns `rec.value[:300]`. V2 does not invent visibility beyond that
boundary. It uses full stored versions for change identification and separately
requires the changed proposition in the payload verified in recorded model context.
The pre-mutation write is no longer arbitrarily cut to 300 characters. The existing
record-version delivery reconstruction is reused unchanged.

Removed propositions are detected, but a downstream omission alone is not enough
to establish an adverse effect. Requirement/omission causality remains an explicit
unsupported capability. Added propositions can support adverse effects when their
contradiction with authoritative evidence and stable clean behavior is grounded.

## Reproducibility and deduplication

`tests/fixtures/semantic_mp_v2/recorded_smoke.json` contains the original 74 raw
responses, inputs and source hashes. Replay verifies its frozen SHA256 and the
original smoke artifacts. Its migration discards old proposed offsets, adds empty
support-quote lists, and preserves all original responses. It does not pretend
that the new prompt generated those responses.

Extraction results are memoized by exact text, model, temperature, prompt, schema,
validator version and transport settings. Adapter disk caches additionally include
endpoint/body identity. V1 raw caches are never overwritten/rebranded. Every
occurrence keeps its event, role, record, time and clean-run provenance; event
references point to a deduplicated extraction catalog. Three identical clean
contents still constitute three distinct clean executions, not one.

## Offline commands

```bash
python3 -m scripts.replay_semantic_mp_v2 \
  --output analysis/semantic_mp_v2/my_offline_replay

python3 -m pytest -q tests/test_semantic_mp_v2.py tests/test_semantic_mp.py \
  tests/test_semantic_mp_pilot.py tests/test_mp_retrospective.py \
  tests/test_behavioral_anomaly.py tests/test_analysis_admission.py \
  tests/test_fixture_aware_propagation.py
```

The output must be a new directory under `analysis/semantic_mp_v2`. Replay never
imports the network adapter and uses only the two frozen smoke traces and their
matched controls. Tests use authored semantic cases, ten assistant-authored
recorded-response reviews, and mocked adapter responses. These are development
regressions, **not independent human ground truth or held-out accuracy estimates**.

## Pilot gate

The existing runner still targets dev1, not v2. Its summary now separates transport
completion from semantic success and reports extraction/endpoint coverage.
Semantic incompleteness exits with status 3. Before a pilot, it recomputes semantic
health from smoke decisions, so an old transport-only `completed` summary cannot
bypass the gate. The historical smoke correctly fails that gate. This is an
operational gate, not a substitute for frozen scientific validation criteria.

No additional paid smoke run, production activation or full-dataset analysis is
authorized. The corrected prompt's live behavior and independent scientific
accuracy remain untested; changing the prompt means old raw responses cannot
establish those properties.

## Existing environment after shell resume

The final verification used `/u/kdhande/.conda/envs/agentgraph-vllm/bin/python`
(Python 3.12.14). If your shell's `python3` resolves to system Python 3.6, use that
absolute interpreter in the commands above. No new environment is required.
The final bounded report is `analysis/semantic_mp_v2/report.md`; its authoritative
outputs are in `analysis/semantic_mp_v2/validated_replay/`.
