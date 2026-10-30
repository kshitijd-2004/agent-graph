# Compact MP evidence codec — development only

`benchmark/mp_adjudicator_compact` is a separately versioned model-facing codec and session bridge around the unchanged MP hybrid v2 evaluator. It adds no live runner, HTTP client, production registration or non-MP reconstruction path. Use the original MP-only v2 builder for new packets. The measurement tool reads the saved AON/SPGI MP packets only.

## What is preserved

The complete local v2 packet is unchanged. The measurement artifacts include byte-identical copies of the input packets with source SHA256 values. Base trace events (including those outside the model review set), original metadata, source texts, blocks, span offsets, original event identity maps, memory versions, delivery contexts and graph provenance remain intact.

Every v2 selected event and every evidence block appears in the compact view. Text is stored once per exact distinct string. Behavior events, source-document events, and other observations occupy separate named groups. Each event retains its run, type, role, tool, source path and a list of `[occurrence_id, text_id]` pairs. Full block and line block text may overlap, but identical strings are deduplicated. There is no lexical, poison-string, temporal or graph-descendant selection filter and no source text omission.

Short IDs such as `b1a` are deterministic aliases assigned to sorted original block IDs within an immutable packet. They are scoped by the packet SHA256, not globally meaningful IDs. Every alias maps to the original v2 block ID, which resolves run, event, `observable_text` field, exact Unicode offsets and full source SHA256. The resolver verifies that original binding before acceptance. Text IDs share text only: they confer no event identity, adoption, exposure or review status.

Stage A remains blind to treatment labels and origins. It selects occurrence IDs, not text IDs, quotations or offsets. A reusable row schema replaces the large per-event repeated schema. Python checks event existence, uniqueness, block membership and event-local binding. Omitted event reviews become explicit unresolved entries, never automatic reviewed or negative entries. Invalid event/block references fail closed. This changes the wire contract but not the semantic evidence requirements.

Stage B receives the same full evidence view, verified Stage A behavior expressed through selected occurrence IDs, compact attribution-option IDs, actual mutation/delivery metadata, run roles, and unchanged endpoint coverage requirements. Option and evidence aliases are resolved deterministically before the unchanged v2 assessment and original hybrid verification kernel. The bridge maps only schema evidence fields, not prose. No text-equality matching is used to move evidence between events. All five endpoint keys remain mandatory.

The two-stage call ceiling remains two per admitted trace; Stage B is skipped if no verified target behavior exists. Shared text never implies shared review. Cross-agent relay checks, clean-run restrictions, competing identical origins, event depths, intermediate harm and later correction are inherited unchanged. Missing origin paths remain unknown depth.

## Offline measurement

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.measure_mp_compact --output analysis/mp_compact_dev1/my_review
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest -q tests/test_mp_adjudicator_compact.py
```

The output directory must be new. No network is used. Stage A request sizes are measured directly. Stage B sizes use an explicitly authored **size-only** scenario: every event reviewed, one full block per event, short placeholder summaries. This scenario is not a semantic result, accuracy test, estimate of actual coverage, or bound on a future model response. The measurement script captures requests and deliberately supplies no usable Stage B judgment. Source packet copies, codec mapping, requests and metrics are saved separately from previous results.

Costs use the unchanged runner's configured historical rates ($0.40/M input, $1.60/M output), bytes/4 token estimates and 6,000 output tokens per request. Conservative input reservations use wire bytes +4,096. These are not tokenizer measurements or invoice guarantees.

## Remaining limitations

Both measured Stage A requests pass the existing conservative 100,000 input guard. Both Stage B size scenarios fail it. No token, output, spending or call guard has been changed. A future live adapter must continue checking each actual generated Stage B request. Do not authorize a new live smoke test on the assumption these scenarios fit.

Full per-event reviews may also exceed the old 6,000-output-token cap; no complete-output guarantee is claimed. Provider structured-schema support, model event-binding accuracy, semantic recall, real output lengths and independent accuracy validation remain untested. The representation still requires following explicit text references; unlike the original flat table, each occurrence is attached to a named event and deterministic event-local membership is enforced. This may help but has not been shown empirically to improve the model.

Existing v2 conservative limitations remain: full/line span granularity, repeated-quote uniqueness in the original verifier, complex calculations, ambiguous origins, and whole-stage failure for invalid schema. Reduction in size does not establish reduction in semantic error.
