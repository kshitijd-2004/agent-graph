# Semantic measurement contract v2

The offline production runner and coverage diagnosis are documented in
[semantic_production_measurement.md](semantic_production_measurement.md).
Version 2 adds explicit verified-nondelivery decisions, a bounded document
extractor with uncovered spans, source bundles, and semantic-mutation-backed
exact-record forwarding evidence. It does not certify general production prose.
Historical v1 artifacts and the historical v2 evaluator are unchanged.

This opt-in development module is separate from the production propagation
analyzer, semantic MP dev1, and the historical semantic MP v2 evaluator. It uses
the v2 extractor without changing its prompt, schema, cache, or frozen responses.
No independent labels, validation results, or general semantic capability are
claimed. No network calls are required by the measurement or evaluation modules.

## API and capability boundary

```python
from benchmark.semantic_mp_v2.extraction import Extractor
from benchmark.semantic_mp_v2.measurement import evaluate_trace
from benchmark.semantic_mp_v2.measurement_contract import EntityRegistry
from benchmark.semantic_mp_v2.measurement_reporting import summarize_measurements

# Build and record this neutral identity artifact with the task fixture.
registry = EntityRegistry.build(task_source_text)
result = evaluate_trace(
    perturbed_trace, clean_traces, authoritative_text=task_source_text,
    extractor=Extractor(), registry=registry, enabled=True,
)
summary = summarize_measurements([result])
```

The real-trace adapter is MP-only and preserves admission, distinct repetition,
matched fixture/topology, memory-enabled control, and verified version/delivery
requirements. The core is task-independent but accepts *trusted prepared*
provenance, primarily for authored tests. Never use model-proposed exposure IDs or
context completeness as trusted core inputs. Other LEPs require separate adapters
and mechanism validation; this change does not enable them in production.

The supported attribution relation is direct adoption of an explicitly aligned
changed proposition. General entailment, derived semantic decisions, and omission
attribution remain unknown. Final-answer impact certifies only the supported
adverse subset; nonadverse changes remain unassessed. The separate arithmetic
extension in the production detector is unchanged.

## Claim assessment and denominators

Every matched clean execution has an independent run ID and a comparison record:

* Extraction: complete, partial, failed.
* Alignment: resolved, ambiguous, unassessed.
* Relation: supports, contradicts, absent, mixed, unresolved.
* Supporting claim IDs, source IDs, and unresolved reasons.

An explicit negative proposition is not missing content. Rejecting a proposition
does not assert its opposite. Quotation without a verdict does not establish
support or contradiction. Conflicting assertions produce mixed. Missing output
scope, failed extraction, and uncertain identity produce unresolved, never absence.

Only full bounded deterministic parsing currently certifies extraction coverage.
A contextual response's `complete=true` is insufficient. Exact quote checks prove
location, not general entailment. This conservative rule reduces coverage and must
not be advertised as independent scientific validation of the bounded parser.

All distinct clean runs remain in the denominator. With S supporting runs, U
unresolved runs, and N total runs, support is bounded by [S/N, (S+U)/N]. Conditional
agreement S/(N-U) is diagnostic only. Eligibility requires at least three runs and
the lower bound >= 0.8. For 2 supporting + 1 unresolved, observed support is 2/3,
coverage is 2/3, conditional agreement is 1, and eligibility is false.

An all-supporting set is a strong invariant; lower bound >= 0.8 is stable; an
interval straddling the threshold is insufficient evidence; resolved variation
below the threshold is variable. Eligibility and descriptive tier are separate:
two supporting controls still fail the minimum-run requirement.

## Identity and source attribution

Identity excludes value and stance, so present/absent remains an aligned
contradiction. Only explicit source-attested aliases and existing deterministic
normalizations are accepted. Unequal subjects, including disjoint-token
paraphrases and near-miss endpoint names, are unresolved unless their identity can
be established. Explicit incompatible scopes/units can establish different slots.
Candidate comparison is exhaustive within the prepared scope; no top-k retrieval
miss establishes absence. The registry contains source definitions and a source
hash, not expected verdicts, and is never passed to the extractor.

Claim evidence records agent commitment separately from attributed source and
modality. The current extractor does not resolve source attribution: the field is
explicitly unresolved, never guessed from a quoted stance. Direct primitive
assertions can be assessed under the bounded grammar; complex source-reporting or
endorsement constructions remain unsupported by that grammar.

## Positive evidence and mandatory checks

Every candidate records supporting dependencies and required context checks.
Both have a transitive closure over serializable evidence nodes. Missing nodes,
unknown evidence, refuted checks, and cycles block the positive. Source text,
quote spans, claim IDs, clean run assessments, delivery IDs, and node states remain
available for offline replay of the decision basis.

Supporting roots include changed-before/after content, actual delivery,
downstream assertion, provenance, and (for adverse effects) authoritative
contradiction plus stable clean support. The rules, not the model, select context:
recorded ancestors and earlier events of the same actor. Relevant incomplete
context, pre-exposure repetition/independent sources, contradictory commitments,
and post-exposure correction prevent an automatic adverse positive. Same-actor
history is conservatively included even when dependency recording is sparse.
Unrelated branches do not enter a candidate's closure. Later recovery cannot erase
an earlier supported positive. Verified delivery reconstruction handles record
versions and overwrites; a storage mutation alone never establishes exposure.

Rejection/forwarding need their local grounded stance and delivery, without an
authoritative correctness reference. They do not inherit the adverse endpoint's
clean support gate. Unsupported general inference remains unknown.

Baseline equivalence, response to delivered information, intervention attribution,
and task consequence are separate fields. Baseline-equivalent adoption does not
establish a new adverse effect. A new claim is not false merely because the
fixture omits it. Required omissions need an applicable output obligation and
coverage; this version does not assign adverse omission positives.

## Reporting and reproduction

`summarize_measurements` returns separate development endpoint counts, unknown
counts, assessment coverage, per-claim support tiers, gap reasons, registry
snapshots, and extractor/alignment/contract versions and hashes. It refuses to
pool different extractor/alignment configurations. It does not write or alter
production `propagation_summary.json`, and does not report a headline propagation
probability. Claim profiles are counts of candidate comparisons, not independent
executions or independent accuracy measurements.

Raw contextual responses stay in the existing extractor/transport artifacts;
measurement results preserve parsed extractions and source text. Replaying these
artifacts establishes reproducibility of recorded decisions, not fresh-model
agreement or semantic correctness.

## Frozen evaluation and independent review protocol

Rubric version: `semantic-alignment-review-v1`. The bundled development cases are
assistant-authored regression examples, explicitly not independent ground truth.
Run the offline tool to produce predictions, stratified counts, source hashes, and
a blinded review packet in a new directory:

```bash
python3 -m scripts.evaluate_semantic_measurement --output /tmp/semantic-contract-review
python3 -m pytest tests/test_semantic_measurement.py -q
```

The packet omits predictions and authored labels. Before held-out evaluation,
freeze the rubric, source/corpus hashes, split, acceptance thresholds, and reviewer
assignments. Two reviewers independent of rule development annotate identity,
scope, extraction accuracy/coverage, stance, commitment, source attribution, and
supporting spans. Preserve their original annotations; a third reviewer
adjudicates disagreements. Reviewers may select unresolved when context cannot
establish identity. Do not force a binary label.

Identity means the same subject, relation, and applicable entity/time/version/
conditional scope, even when the value differs. Different means explicit evidence
establishes different entities or incompatible scopes. Lexical dissimilarity alone
does not establish different. Task sources must establish aliases in ambiguous
examples; annotators must not guess them from anticipated model outputs.

Mandatory strata across financial, code-review, and research tasks:

* Disjoint-token paraphrases and changed relational wording (false-split pressure).
* Near-identical entities/endpoints (false-merge pressure).
* Same entity, different version/date/conditional scope.
* Aligned opposite values, quotation versus endorsement, explicit rejection.
* Incomplete extraction, omitted required facts, and legitimate optional absence.

Report false merges, confident false splits, missed matches including abstentions,
and coverage separately by task and failure stratum. Do not optimize on a pooled
accuracy number. The automated bundled check isolates alignment on authored
propositions; the review packet also requests extraction annotations from raw text.
Extraction accuracy, held-out accuracy, source-attribution accuracy, and independent
key-stability estimates remain unmeasured until that review is completed.
