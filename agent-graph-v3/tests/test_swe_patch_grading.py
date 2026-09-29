"""Regression coverage for evaluator-only grading of the frozen SWE 100."""
import json

import pytest

from benchmark.behavioral_anomaly import extract_code_review_issue_presence
from evaluators.task_evaluators.patch_review import match_patch_review, rubric_errors
from generation.runner import ScenarioRunner
from schemas import Trace, TraceEvent, TraceEventType, TraceVariant
from scripts.swebench_materializer.grading.build import with_grading
from scripts.swebench_materializer.grading.curated import ROWS
from scripts.swebench_materializer.grading.validate import DRAFTS, ROOT, evaluate_answer

FIXTURES = sorted((ROOT / 'workspace_fixtures_v3').glob('code_review_swe_*/manifest.json'))


@pytest.mark.parametrize('path', FIXTURES, ids=lambda p: p.parent.name)
@pytest.mark.parametrize('case', ['gold', 'paraphrase', 'title', 'incorrect', 'prompt_copy'])
def test_all_fixture_answers(path, case):
    manifest = json.loads(path.read_text())
    index = manifest['provenance']['selection_index']
    row = ROWS[index]
    title = manifest['task_prompt'].split('Issue:\n', 1)[1].splitlines()[0]
    answers = dict(gold=row['gold'], paraphrase=row['paraphrase'],
                   title=(title + '\n' + ' '.join(manifest['required_files'])) * 3,
                   incorrect='In ' + manifest['required_files'][0] + ': ' + row['incorrect'],
                   prompt_copy=manifest['task_prompt'])
    assert 'grading' in manifest['required_issues'][0]
    result = evaluate_answer(manifest, answers[case])
    assert result.task_success == (case in ('gold', 'paraphrase')), result.metadata
    if case == 'paraphrase':
        assert title.lower() not in answers[case].lower()


@pytest.mark.parametrize('path', FIXTURES, ids=lambda p: p.parent.name)
def test_generation_is_pinned_and_idempotent(path):
    manifest = json.loads(path.read_text())
    draft = json.loads((DRAFTS / path.parent.name / 'manifest.json').read_text())
    assert with_grading(manifest, draft) == manifest
    draft['oracle']['gold_patch'] += '\n'
    with pytest.raises(ValueError, match='needs review'):
        with_grading(manifest, draft)


@pytest.mark.parametrize('path', FIXTURES, ids=lambda p: p.parent.name)
def test_real_workspace_isolation(path, tmp_path):
    manifest = json.loads(path.read_text())
    runner = ScenarioRunner(output_dir=tmp_path / 'output')
    workspace = tmp_path / 'workspace'
    runner._setup_workspace(workspace, path.parent)
    visible = runner._strip_ground_truth_from_manifest(path)
    assert not {'oracle', 'required_issues', 'success_criteria', 'validation'} & visible.keys()
    assert visible['task_prompt'] == manifest['task_prompt']
    allowed = {name for key in ('required_files', 'optional_files', 'distractor_files', 'sensitive_files')
               for name in manifest.get(key, [])}
    assert {str(p.relative_to(workspace)) for p in workspace.rglob('*') if p.is_file()} == allowed
    for name in allowed:
        assert (workspace / name).read_bytes() == (path.parent / name).read_bytes()


@pytest.mark.parametrize('answer,expected', [
    ('Collector.delete can exit through its fast-delete branch before reaching the ordinary primary-key cleanup. Clear the primary key on the in-memory object on this exit too.', True),
    ('The query projection mutates the shared query inside get_combinator_sql. Clone the query prior to setting the selected columns to prevent the projection leaking into the next compilation.', True),
])
def test_additional_independently_worded_explanations(answer, expected):
    path = FIXTURES[0 if 'Collector' in answer else 1]
    manifest = json.loads(path.read_text())
    assert evaluate_answer(manifest, answer).task_success is expected


def test_missing_rubric_fields_fail_closed():
    manifest = json.loads(FIXTURES[0].read_text())
    issue = manifest['required_issues'][0]
    issue['grading']['all_of'] = []
    issue['keywords'] = ['primary key']
    assert rubric_errors(issue['grading'])
    assert not evaluate_answer(manifest, ROWS[1]['gold']).task_success


def test_legacy_keyword_grading_unchanged():
    manifest = json.loads(FIXTURES[0].read_text())
    issue = manifest['required_issues'][0]
    issue.pop('grading')
    issue['keywords'] = ['custom legacy finding']
    assert evaluate_answer(manifest, 'custom legacy finding ' * 10).task_success


def test_event_fact_detection_uses_same_rubric():
    manifest = json.loads(FIXTURES[0].read_text())
    for answer, expected in [(ROWS[1]['gold'], True), (ROWS[1]['incorrect'], False)]:
        event = TraceEvent(trace_id='t', event_id='e', event_index=0,
                           timestamp='2026-09-28T00:00:00Z', event_type=TraceEventType.FINAL_RESPONSE,
                           agent_role='reviewer', output_text=answer)
        trace = Trace(trace_id='t', execution_id='x', variant=TraceVariant.BENIGN, events=[event])
        facts = extract_code_review_issue_presence(trace, manifest)
        assert facts and facts[0].value is expected
