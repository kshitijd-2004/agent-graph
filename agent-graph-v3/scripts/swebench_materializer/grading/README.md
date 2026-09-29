# SWE-bench review grading repair: existing 100 v3 fixtures

Scope: only the 100 `code_review_swe_*` manifests in `workspace_fixtures_v3`.
No fixtures were added. Task prompts, declared files, source bytes, attack
payloads, source anchors, and inference-visible manifests are preserved.
The other fixture roots and draft manifests were not migrated.

## What was wrong

`migrate_manifests.py` extracted names from gold diff hunk headers but assigned
`keywords: [issue_title]` to each inferred issue. The code-review evaluator used
an unrestricted OR substring check; neither a causal explanation nor a code
location was required for that finding. All 99 fixtures after 001 used this
representation. Several hunks were represented as different findings with the
same title. Hunk header names can also describe preceding code rather than the
changed table or constant.

The stored migration audit contains every original required-issue record. On
our 100 reference explanations and 100 independently worded paraphrases, the old
grader accepted zero. Repeating each issue title enough times to satisfy the
existing output-length threshold passed all 100. These are controlled grading
probes, not production model pass-rate measurements.

## What fixture 001 did

Django `django__django-11179` adds
`setattr(instance, model._meta.pk.attname, None)` before the single-instance
fast-delete return in `Collector.delete`. Its regression test deletes a user
and checks that `u.pk is None`. Its source-reviewed manifest identifies
`fast_delete_retains_primary_key` at `django/db/models/deletion.py`, with phrases
such as “primary key is not cleared”, “pk is not reset”, “fast-delete early
return”, and “missing primary-key reset”. Those phrases were manually curated;
the migration script did not derive them automatically.

This is the grounding model for the new rubrics. Its old OR keyword check was
still brittle and one alternative overlapped the prompt. Fixture 001 therefore
also receives the new grading representation.

## Representation and scoring

Each `required_issues[]` entry has an evaluator-only `grading` object:

- `version: "patch-concepts-v1"`.
- `source_instance_id`, `gold_patch_sha256`, and `changed_files`: provenance.
- `location_any`: accepted file, symbol, and reviewed descriptive location aliases.
- `all_of`: two or more required concept regexes, with alternatives within each
  pattern for ordinary language and code terminology.
- `reject_any`: explicit known contradictory propositions where needed.
- `explanation`: the human-readable, patch-grounded reference diagnosis.

Credit requires a location and **all** causal/change concepts within a 1,200
character explanation window. Merely naming the changed function does not earn
credit. Normalization accommodates case, whitespace, underscores, hyphens, and
common Unicode punctuation. Verbatim task-prompt lines, including the title,
are excluded from grading evidence. Explicit assertions that the reported bug
is absent or that no correction is needed cannot earn credit by repeating the
reference explanation. Malformed rubrics fail closed, without keyword fallback.

Old source anchors are retained because LEP omission operators consume them.
Where multiple anchors refer to one reported defect, each uses the complete
fix rubric: mentioning one unrelated hunk does not earn partial credit. Existing
success thresholds and output-length requirements are preserved. Legacy
non-migrated fixtures keep their previous grading. Code-review event-fact
extraction uses the same new matcher for migrated fixtures so diagnostics and
final grading do not silently disagree.

## How gold patches become signals

1. Read each frozen draft's gold diff and review the before/after operation and
   relevant surrounding source; use fixture 001's causal grounding as the model.
2. Identify the changed location and the behavior of the fix, not just the
   issue symptom or a name in a hunk header. For example, fixture 002 requires
   cloning/copying a component query before changing its projection; repeating
   that composed queries cannot change columns is insufficient.
3. Author causal/change concept alternatives, a reference explanation, a
   separately worded paraphrase, and a plausible wrong diagnosis in `curated.py`.
   No regexes are mechanically synthesized from the title or positive probe.
4. `build.py` attaches those reviewed concepts and mechanically extracts changed
   file paths and the SHA-256 of the exact gold diff. `reviewed_sources.json`
   pins the reviewed fixture IDs, instance IDs, and patches.
5. `validate.py` runs the actual production evaluator on positive and adversarial
   reports. Writes are gated on all checks passing. The materializer uses the
   same builder and rejects an unknown fixture or changed gold patch instead of
   silently generating title-based grading.

The gold diff is never applied to an agent workspace. Neither rubrics, reference
answers, patch hashes nor test probes are included in the runner's agent-visible
manifest allowlist. Only declared pre-fix task files are copied. Isolation is
exercised through the real workspace setup and manifest stripping methods for
all 100 fixtures. Before/after source, prompt, and visible-manifest hashes are
recorded in `validation_report.json`.

## Validation results

| Check | Passed | Failed |
| --- | ---: | ---: |
| Patch-grounded correct explanation | 100 | 0 |
| Correct paraphrase without issue title | 100 | 0 |
| Reject title-only report | 100 | 0 |
| Reject plausible wrong diagnosis with valid source location | 100 | 0 |
| Reject title plus source-location citations | 100 | 0 |
| Reject verbatim full prompt | 100 | 0 |
| Reject explicit denial plus quoted correct explanation | 100 | 0 |
| Reject another fixture's correct explanation | 9,900 | 0 |
| Full fixture acceptance gate | 100 | 0 |
| Mock LEP/topology runs (15 per fixture) | 1,500 | 0 |

`validation_report.json` records the migration and grading probes;
`contract_report.json` records source grounding, size, poison/swap checks, and
all mock-run results. No fixture currently lacks a reviewed signal or fails the
specified grading probes. There are **100 modified fixtures**, with no expansion.

The regression run covering new grading, fixture contracts, and anomaly tests
had 797 passes and three pre-existing failures. All three reproduce with the
pre-change implementations: the materializer test expects an oracle in a
materialized fixture where it has already been stripped; legacy fingerprint
fixtures are missing; and an existing anomaly-reference assertion fails. They
were not changed as part of this grading repair.

## Limits

This is an inspectable deterministic concept matcher, not a general semantic
entailment model. The tests show that the reviewed positive/negative reports
are distinguished; they do not prove acceptance of every possible paraphrase
or rejection of every adversarial combination of words. No real-model quality
run or upstream SWE-bench regression-test execution is claimed. Review a sample
of real model reports before expanding the fixture set, and version additional
aliases or contradictions with accompanying independent test cases.

## Reproduce

From the repository root:

```bash
# Read-only validation; use a different report path to preserve the migration audit.
python3 -m scripts.swebench_materializer.grading.validate --report /tmp/swe-grading-check.json
python3 -m pytest tests/test_swe_patch_grading.py -q

# Idempotent regeneration for these 100 reviewed fixtures only.
python3 -m scripts.swebench_materializer.grading.validate --write --report /tmp/swe-grading-regenerate.json

# Full gate for one fixture (repeat for each v3 fixture to refresh validation stamps).
python3 -m scripts.validate_fixture workspace_fixtures_v3/code_review_swe_001_django_django_11179
```
