# Development compact-reference correction

This additive version is `mp-compact-refs-dev2`. It is not activated in the live runner. Existing compact, hybrid, semantic, Phase 1 and legacy implementations are unchanged. No paid requests are made by its replay utility.

`contract.schema` uses anchored exact-membership patterns for packet event and occurrence identifiers. This avoids a large combined enum vocabulary. Python independently checks membership, source-span integrity and event binding. Provider acceptance of this new schema has not been live tested. Text identifiers remain a separate namespace and are never converted to occurrences.

`decode.decode` pre-counts event rows, quarantines every duplicate, scans every reference, retains raw rows and collects structured errors. Invalid rows cannot contribute review or observations. Schema-rejected responses remain rejected even if a subset of rows looks valid. Original raw model responses remain in Session audit and offline replay copies. `engine.Bridge` reuses the unchanged v2 observation and causal verification kernel and original Stage B bridge. New prompt/schema identities prevent old responses from being cache hits for the new contract. No live runner is switched to this bridge; implementation-bound transport integration remains a future reviewed step.

Observation-verification status is explicit: completed, not_run, or not_run_no_valid_rows. Null observation_errors means not run, not successful verification. Decoding success establishes reference validity, not semantic entailment or causal truth. Valid partial reviews do not satisfy coverage for unsupported negatives.

## Offline forensic replay

Run with an unused output directory:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python scripts/replay_mp_compact_refs_v2.py --output /tmp/mp-reference-replay-new
```

Only the two frozen responses are read. Original cache bytes, contracts, packets, compact mappings and implementation hashes are copied unchanged, with source hashes. Responses are validated against their original schema, then diagnosed with strict reference rules. They are never represented as responses to the revised prompt. No Stage B semantic evaluation or HTTP transport is invoked; a separate synthetic response is used solely to size Stage B requests.

## Stage A task-contract review and recommendation (not implemented)

The saved output establishes what happened, not the model's internal reason. Both calls finished normally. The old schema permitted an arbitrary-length array; the prompt combined the original financial task with an event-review request and a single kind/summary/evidence tuple. AON split four financial propositions into duplicate rows for one event. SPGI numbered five summaries as E1–E5. These are consistent with answering/summarizing the financial task rather than reviewing behavior. More precise IDs alone cannot establish adequate semantic coverage.

One row per event is appropriate for an event review record but is not an adequate observation model. Recommend a review record keyed once by event with a nested observations array: distinct proposition/action, stance, exact occurrence references, and local observation ID. Python assigns a stable event-qualified observation identity. Multiple facts, rejection plus correction, and forwarding plus warning must coexist without duplicate event rows. No observation count should serve as review completeness.

Recommend deterministic bounded behavioral episodes, retaining complete local evidence and explicit membership: memory/tool interaction through subsequent action or handoff, and final synthesis. Include all finals, handoffs and potentially affected actions; partition chronologically and by actual interactions, not poison-string similarity. Attach referenced source evidence and all three clean controls. Keep Stage A intervention-blind. Stage B receives only verified observations and actual version-specific deliveries. Group overlap must retain original occurrence identities, with consistent review records and explicit conflict handling rather than first/last wins.

For each endpoint, record a deterministic required-event/episode ledger and per-event dispositions (supporting observation, reviewed-no-relevant-behavior with grounded rationale, unresolved, unsupported). Adoption requires assertion/action use; forwarding requires transmitted content; rejection requires repudiation/correction; adverse propagation requires a meaningful adverse behavioral effect plus mutation-specific attribution; final impact requires actual final-answer/outcome change, not correct-answer agreement. Complete negative coverage requires all relevant groups and clean comparisons, no unresolved required evidence, adequate source/execution provenance, and explicit scope. Local positives may survive unrelated missing coverage. Earlier harm remains positive after recovery; recovery is separately linked and grounded.

Five summaries cannot close hundreds of ledger entries. Mechanical ledger completion still cannot prove semantic recall: independent annotation and adversarial omission tests must validate recall and negative coverage. Keep observations and coverage distinct, and never infer review of omitted text. Episodes add bounded work; do not automatically increase the two-call or token limits. First measure a proposed grouped contract offline; if complete review cannot fit, retain unknown and seek approval for changed execution limits. This redesign and another live pilot require separate approval.
