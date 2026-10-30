# Event-bound hybrid MP adjudicator v2 (development only)

This is a separate package, `benchmark/mp_adjudicator_v2`. No original evaluator, production registration, trace, plan, admission rule, threshold, or result is changed. There is no new live runner or API invocation. `evaluate(packet, session, enabled=True)` accepts an injected session; by default the original Session has no live transport. New prompts and schemas have distinct request hashes and must never reuse old responses as new judgments.

## Evidence and interpretation

The unchanged MP packet builder reconstructs mutations, delivered versions, actual context exposure and three matched clean references. V2 binds its immutable emitted-content projection to blocks. Each block ID is a deterministic 80-bit digest of run, event, field, Unicode start/end offsets and SHA256 of the full source text. Collisions within a packet fail closed. The local block record retains all identity components and exact text. `resolve` rechecks every component against the source and reconstructs the exact citation. IDs are not freely relocatable aliases.

The field `observable_text` names the existing packet's emitted-content projection, not a claimed raw trace JSON field. The original event identity, type, arguments and source identity remain available in the base packet. Blocks refer to full content or exact nonempty lines (including original line endings), never generated paraphrases. Selecting multiple blocks yields separate citations, never a stitched quote. Exact location does not establish semantic entailment.

Stage A receives named event objects with source text and event-local span IDs. No target/control labels, origins, intervention labels or expected answers are provided. Source, action, memory write, acknowledgement, handoff and final-response event types remain visible. Its schema has an explicit review/interpretation slot for every event, with IDs limited to that event's blocks. Python assigns observation IDs from event identity; there is no model-generated O/C namespace.

Stage B receives only verified behavioral observations as observations; the full evidence context remains available for source/control comparisons. Run roles are explicit. Python computes effect/record-version/delivery options from actual provenance, restricted to target behavior. Selecting an option supplies the event, origin, delivery and version without asking the model to reproduce those identifiers. Python verifies global evidence-block membership; global ID catalogs are not repeated in every schema citation position. Options are eligibility, not proof of adoption or harm. If Stage A has no verified target behavior, Stage B is skipped and all semantic endpoints report unknown/no_verified_target_behavior.

All five semantic endpoints are required object keys. Missing keys fail contract validation and yield explicit unknown states. Supported local findings survive unrelated incomplete reviews. Unsupported or missing review remains explicit. The model reports reviewed/unresolved/unsupported per event rather than enumerating free-form reviewed/unresolved ID lists.

## Strict attribution and coverage

The original hybrid verification kernel is reused unchanged for grounding, prior behavior, clean support threshold, source identity, arithmetic, origin/version attribution, cross-agent relay evidence, ambiguous identical delivered origins, recovery, and graph depth. Rejection remains distinct from adverse effects; clean agreement does not constitute a positive final-answer change. Graph gaps do not become invented edges. Calculations retain the original limitations.

Trace-wide negative adoption, forwarding, rejection and adverse endpoints conservatively require all behavior candidates in every run reviewed with evidence, including pre-exposure behavior. Final-answer-impact negatives require all final responses and source-document events reviewed, plus target/control citations, explicit scope completion and intact execution/provenance. A final-answer negative does not establish no intermediate harm. These coverage scopes are explicit, auditable design choices requiring review; semantic review remains a model attestation, not deterministic proof of recall.

Each positive is checked by the original kernel. V2 resolves blocks into exact quotations solely as an internal compatibility bridge; the model never writes quotes. This bridge conservatively retains the original unique-quote restriction, so repeated identical text within one event may remain unsupported despite span-specific block identity. A malformed stage-wide schema currently invalidates that stage. Locally valid positive findings survive other individually rejected findings under the original aggregation rules.

## Offline validation and limitations

The frozen fixture `tests/fixtures/mp_adjudicator_v2/failed_smoke.json` contains the four original-contract smoke responses and source hashes for diagnosis only. Twenty-nine parameterized regressions reproduce every absent-quote event binding failure; none is repaired by moving text to another event. Additional tests cover clean target errors, invalid options/blocks, missing endpoints, old-response rejection, coverage, local positives, unchanged-answer agreement, ambiguous origins, and recovery after intermediate harm. Synthetic responses demonstrate mechanics, not model accuracy or independent ground truth.

Run offline checks:

```bash
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m pytest -q tests/test_mp_adjudicator_v2.py
/u/kdhande/.conda/envs/agentgraph-vllm/bin/python -m scripts.validate_mp_adjudicator_v2 --output analysis/mp_adjudicator_v2_dev1/my_review
```

The second command verifies frozen smoke hashes, resolves every block, and writes new packet/size diagnostics only. It makes no semantic model requests. The directory must be new.

Maximum expected semantic calls remain two per admitted trace, one if Stage A supplies no verified target behavior, zero for failed admission. Exact Stage B size depends on the new Stage A response. No live compatibility or model accuracy is claimed. Event-bound evidence and required per-event schema slots materially increase input/output size. Before approving a live pilot, review schema-provider limits, output-cap sufficiency and token budgets; the old 100,000 conservative byte-based input guard will reject these larger requests. Do not bypass it or silently truncate evidence. Additional compact presentation/schema work may be required. Two calls is a ceiling, not a promise these packets fit the prior live limits.

Independent annotation, held-out validation, model precision/recall, false positives, endpoint coverage, and reproducibility remain outstanding. No new production classification or propagation rate is produced.
