# Fixture contract conformance audit — 2026-09-24

Authority: `docs/fixture_schema.md`, read in full before implementation. No
changes were made to that contract or to the five legacy fixture manifests.

## What was already correct

- The runner passed private fixture context to the LEP orchestrator and selected
  `task_prompt` before the legacy description fallback.
- Agent-visible manifest fields were allowlisted, and stage prompts listed
  manifest `required_files`.
- Tool corruption already supported manifest target/operator selection, and
  prompt injection supported an explicit target.
- The SWE-bench materializer retrieved pre-fix source by `base_commit` and
  checked the gold patch without applying it to the fixture.
- Fixture discovery and propagation references were already fixture-specific.

## Corrections required by the final contract

- Memory poisoning now takes the exact `value` and `description` from a mapping
  of family memory keys. The `key`/`strategy`/`seed_from` synthesis path is gone.
  The legacy value pool is unchanged.
- Input Disregard's fixture-specific additions were reverted; its file is back
  to the committed implementation.
- Tool corruption validates its required-file target, uses configured source
  conclusions/markers, and can omit manifest-named methods/classes as well as
  functions. New fixture definitions do not alter legacy omission constants.
- Handoff omission uses `critical_terms`; attribution swaps use simultaneous,
  entity-bounded `swap_pairs`. Missing fields retain the legacy operators.
- Prompt injection defaults to the first required file for new fixtures.
- Base task prompts/files, runner task text, and built-in mock read targets
  accept fixture data while retaining legacy fallbacks.
- Code review grading and anomaly detection use issue keywords from the same
  manifest entries. Financial grading and detection use manifest numeric
  patterns and answer `value_strings`, with legacy per-field fallback.
- New workspaces copy only declared task files. Private manifest fields remain
  excluded. Legacy notebook checkpoints and nested ground-truth artifacts are
  excluded from workspace copies as well.
- All 100 SWE-bench drafts were structurally migrated to `required_issues` and
  explicit `bug_findings` value/description entries. The selection itself was
  already distinct and was not changed. Drafts contain no validation block.
- The materializer defaults to one fixture, refuses to overwrite existing
  fixtures, rejects missing required files/duplicate source IDs, and invokes
  the acceptance gate. Its temporary patch-check worktree is uniquely allocated.

## Files changed in this correction

- Engine/task: `generation/runner.py`, `tasks/base_task.py`.
- LEPs: `leps/tool_result_corruption.py`, `leps/memory_poisoning.py`,
  `leps/handoff_corruption.py`, `leps/indirect_prompt_injection.py`,
  `leps/silent_omission.py`; `leps/input_disregard.py` was restored as described.
- Evaluation: `evaluators/task_evaluators/code_review_evaluator.py`,
  `evaluators/task_evaluators/financial_evaluator.py`,
  `benchmark/behavioral_anomaly.py`.
- Pipeline: `scripts/fixture_mock.py`, `scripts/validate_fixture.py`,
  `scripts/swebench_materializer/migrate_manifests.py`,
  `scripts/swebench_materializer/materialize_swebench_fixtures.py`,
  `scripts/swebench_materializer/README.md`.
- Data: all 100 `scripts/swebench_materializer/draft_manifests/*/manifest.json`
  files and
  `workspace_fixtures/code_review_swe_001_django_django_11179/manifest.json`.
- Tests: `tests/test_fixture_contract.py`,
  `tests/data/legacy_fixture_injections.json`,
  `tests/test_memory_many_to_one.py` (explicit legacy fixture selection).
- This audit. Unrelated pre-existing trace, training, and detector changes were
  preserved. Diagnostic artifacts are isolated under `.cache/fixture-contract-*`.

## Verification

The legacy regression captured injection fingerprints before changing engine
behavior. All 75 cells match afterward: five fixtures × five LEPs × three
propagation modes. Fingerprints cover origin event index/type/role and
output/tool-result/argument payloads; random execution IDs and timestamps are
excluded. This is a deterministic mock backend exercising the production
runner, not a real model quality measurement.

The representative Django fixture passed all gate checks: source grounding,
contradictory poison, real swap entities, size, distinct source ID, and all
15 mock execution cells. Counts per LEP are 1 for single-origin, 2 for the
branch-and-verify many-to-one topology, and 1 for coordinator-worker one-to-many.
Every cell completed and was dataset-eligible. The gate estimates 5,345 tokens
using UTF-8 bytes/4, including private fixture metadata but excluding its own
generated validation block. The source code remains the unmodified pre-fix
file. Its `Collector.delete` fast path returns before the normal-path primary
key reset; the poison falsely asserts that the fast path performs that reset.

Focused tests cover manifest payloads, new-file routing, evaluator/detector
agreement, hidden metadata, gate rejection cases, source-ID uniqueness, all
100 draft shapes, materializer integration, and exact legacy fingerprints.
The final combined run passed all 172 relevant tests.
The relevant suite consists of `test_fixture_contract.py`,
`test_memory_many_to_one.py`, `test_fixture_aware_propagation.py`,
`test_behavioral_anomaly.py`, `test_task_evaluator_integration.py`, and
`test_runner_admission.py`.

The broader `test_e2e_integration.py` suite has 38 passes and 20 failures.
All 20 failure names reproduce on isolated committed baseline `0f62cc8`.
They concern existing review-loop completion, prompt/protocol expectations,
prefix export, and event-content assertions. They were not changed as part
of this fixture refactor. JUnit comparison artifacts are under
`.cache/fixture-contract-baseline-ST2Cqb/`.

Python compilation and `git diff --check` pass. An initial test run hit a full
`/tmp`; reruns use isolated scratch storage on the workspace filesystem.

## Remaining limits before bulk acceptance

- The other 99 drafts have candidate function anchors and keyword/poison text
  derived from their source issue and patch metadata. They still need source
  review, materialization, and their own acceptance gate. They are not marked
  accepted. Large full source files or oracle metadata may fail the size gate.
- Contradiction/grounding checks are deterministic and conservative. They are
  not a general semantic proof for arbitrary natural-language claims; source
  review remains necessary, and unsupported wording can require refinement.
- Token size is an estimate, not an exact Llama tokenizer count.
- No optional real clean-model run was performed. No full 100-fixture
  materialization or benchmark was launched.
