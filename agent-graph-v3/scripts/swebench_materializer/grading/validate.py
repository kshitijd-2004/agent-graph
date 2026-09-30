"""Offline grading migration and adversarial validation for the frozen 100.

Run with --write only after all answer probes pass. Source files and prompts
are fingerprinted before/after; only evaluator grading fields are changed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from evaluators.task_evaluators.code_review_evaluator import CodeReviewEvaluator
from generation.runner import ScenarioRunner
from schemas import ScenarioSpec, Trace, TraceEvent, TraceEventType, TraceVariant, WorkflowConfig
from scripts.swebench_materializer.grading.build import with_grading, patch_digest
from scripts.swebench_materializer.grading.curated import ROWS

ROOT = Path(__file__).resolve().parents[3]
DRAFTS = ROOT / 'scripts/swebench_materializer/draft_manifests'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def workspace_fingerprint(directory, manifest):
    return {str(p.relative_to(directory)): digest(p.read_bytes())
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'manifest.json'} | {
                'task_prompt': digest(manifest['task_prompt'].encode()),
                'agent_manifest': digest(json.dumps({k: v for k, v in manifest.items()
                    if k in ScenarioRunner.AGENT_VISIBLE_MANIFEST_KEYS}, sort_keys=True).encode())}


def evaluate_answer(manifest, text):
    evaluator = CodeReviewEvaluator()
    evaluator.manifest = manifest
    trace = Trace(trace_id='grading_probe', execution_id='grading_probe', variant=TraceVariant.BENIGN,
                  events=[TraceEvent(trace_id='grading_probe', event_id='final', event_index=0,
                                     timestamp='2026-09-28T00:00:00Z', event_type=TraceEventType.FINAL_RESPONSE,
                                     agent_role='reviewer', output_text=text)])
    spec = ScenarioSpec(scenario_id='grading_probe', fixture_id=manifest['fixture_id'],
                        task_family='code_review', task_variant='swe_bench_verified', condition='benign',
                        workflow_config=WorkflowConfig())
    return evaluator.evaluate(trace, SimpleNamespace(), spec)


def validate(root, write=False, cross_check=True, cross_sample=None):
    paths = sorted(root.glob('code_review_swe_*/manifest.json'))
    if len(paths) < 1:
        raise ValueError('No fixtures found')
    if len(paths) > max(ROWS):
        raise ValueError(f'Fixture count ({len(paths)}) exceeds available ROWS entries (max {max(ROWS)})')
    results, candidates = [], []
    totals = Counter()
    for path in paths:
        old = json.loads(path.read_text())
        draft = json.loads((DRAFTS / path.parent.name / 'manifest.json').read_text())
        index = draft['provenance']['selection_index']
        row = ROWS[index]
        new = with_grading(old, draft)
        title = draft['task_prompt'].split('Issue:\n', 1)[1].splitlines()[0].strip()
        padding = '\nThis is the reported issue. A review of the supplied repository files is requested to determine the cause and an appropriate correction.'
        probes = {
            'gold': (row['gold'], True), 'paraphrase': (row['paraphrase'], True),
            'title_only': (title + padding, False),
            'incorrect': ('In ' + new['required_issues'][0]['grading']['changed_files'][0] + ': ' + row['incorrect'], False),
            'title_plus_location': (title + '\n' + ' '.join(new['required_files']) + padding, False),
            'prompt_copy': (old['task_prompt'], False),
            'explicit_denial': ('No such defect exists. No changes are needed. ' + row['gold'], False),
        }
        cases = {}
        for name, (answer, expected) in probes.items():
            result = evaluate_answer(new, answer)
            ok = result.task_success == expected
            cases[name] = {'expected': expected, 'actual': result.task_success, 'passed': ok,
                           'details': result.metadata['grading_details']}
            totals['passed' if ok else 'failed'] += 1
        if title.lower() in row['paraphrase'].lower():
            raise ValueError(f'Paraphrase repeats issue title: {path.parent.name}')
        before = workspace_fingerprint(path.parent, old)
        unchanged = before == workspace_fingerprint(path.parent, new)
        # Exercise the actual runner allowlist, not a duplicate stripping rule.
        visible = ScenarioRunner()._strip_ground_truth_from_manifest(path)
        isolated = not any(k in visible for k in ('required_issues', 'oracle', 'success_criteria'))
        issues = old.get('required_issues', [])
        title_based = bool(issues) and all(i.get('keywords') == [title] for i in issues)
        results.append({'fixture_id': old['fixture_id'], 'old_title_only': title_based,
                        'old_required_issues': issues,
                        'gold_patch_sha256': patch_digest(draft['oracle']['gold_patch']),
                        'cases': cases, 'workspace_unchanged': unchanged,
                        'evaluator_fields_isolated': isolated, 'source_fingerprints': before})
        candidates.append((path, new))
    cross_failures = []
    cross_total = 0
    if cross_check:
        # For 300 fixtures, sample cross-checks to keep runtime manageable
        sample_size = cross_sample or min(len(candidates), 30)
        import random
        sampled = random.sample(candidates, min(sample_size, len(candidates)))
        for i, (_, manifest) in enumerate(sampled, 1):
            for j, row in ROWS.items():
                if i == j:
                    continue
                cross_total += 1
                if evaluate_answer(manifest, row['gold']).task_success:
                    cross_failures.append({'fixture': i, 'answer_from': j})
    ok = (not totals['failed'] and not cross_failures
          and all(r['workspace_unchanged'] and r['evaluator_fields_isolated'] for r in results))
    changed = 0
    if write and ok:
        for path, candidate in candidates:
            old = json.loads(path.read_text())
            if old != candidate:
                path.write_text(json.dumps(candidate, indent=2) + '\n')
                changed += 1
        for result, (path, candidate) in zip(results, candidates):
            if workspace_fingerprint(path.parent, candidate) != result['source_fingerprints']:
                raise RuntimeError(f'Workspace changed: {path}')
    return {'fixture_root': str(root), 'fixture_count': len(paths), 'passed': ok,
            'old_title_only_count': sum(r['old_title_only'] for r in results),
            'answer_cases': dict(totals), 'cross_fixture_cases': cross_total,
            'cross_fixture_failures': cross_failures, 'modified_count': changed,
            'unreliable_signals': [r['fixture_id'] for r in results if any(not c['passed'] for c in r['cases'].values())],
            'fixtures': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-root', type=Path, default=ROOT / 'workspace_fixtures_v3')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--report', type=Path, default=Path(__file__).with_name('validation_report.json'))
    args = parser.parse_args()
    report = validate(args.fixture_root.resolve(), args.write)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'fixtures'}, indent=2))
    for fixture in report['fixtures']:
        for name, case in fixture['cases'].items():
            if not case['passed']:
                print(fixture['fixture_id'], name, json.dumps(case['details']))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
