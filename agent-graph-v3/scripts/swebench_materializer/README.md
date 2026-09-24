# AgentProp SWE-bench fixture materializer

This bundle contains the frozen 100-instance selection, the draft per-fixture
manifests, and a materializer/validator.

## Setup

```bash
python -m pip install datasets
```

Git must be available and the machine needs network access to GitHub and
Hugging Face.

## Recommended first run: one fixture

```bash
python materialize_swebench_fixtures.py --limit 1
```

Inspect the new directory under the repository's `workspace_fixtures/` and
`validation_report.json` in the invocation directory.

The default limit is one. Increase it explicitly only after reviewing candidate
answer keys and poison claims and passing the representative fixture gate.

The script clones each unique upstream repository once, resolves the official
SWE-bench Verified `base_commit`, copies task-relevant files from that exact
pre-fix commit, enriches each manifest, and checks that the gold patch applies.

It deliberately does **not** apply the gold patch to the agent workspace.

## After materialization

Manifests follow `docs/fixture_schema.md`: `required_issues` contains source
functions and issue keywords, and `attack.memory_poisoning` maps family memory
keys to concrete `value`/`description` entries. Input Disregard has no block.

The materializer runs `scripts.validate_fixture` and writes the resulting
`validation` block. It checks grounding, contradictions, swap entities, size,
distinct base source IDs, and all five LEPs in three propagation modes using a
deterministic mock backend through the production runner. This is not a real
model quality test. Unverifiable claims and oversized source files fail the gate.
Draft issue anchors and keywords still require source review; a migrated draft
is not an accepted fixture. The materializer refuses to overwrite existing data.

To revalidate an existing fixture from the repository root:

```bash
python -m scripts.validate_fixture workspace_fixtures/code_review_swe_001_django_django_11179
```
