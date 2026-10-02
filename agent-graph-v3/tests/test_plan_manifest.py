"""Saved-plan selection tests: no backend creation or model execution."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from benchmark.benchmark_runner import BenchmarkManifest, BenchmarkRunner
from benchmark.plan_manifest import load_plan_manifest
from generation.runner import ScenarioRunner

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / 'analysis/readiness_50/plan_manifest.json'


@pytest.fixture
def data():
    return json.loads(SAVED.read_text())


def load(tmp_path, data):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(data))
    return load_plan_manifest(path, BenchmarkManifest(fixture_root=ROOT / 'workspace_fixtures'))


def test_readiness_resolves_exact_entries_and_settings(tmp_path, data):
    manifest, entries = load(tmp_path, data)
    assert len(entries) == 50
    assert entries == data['scenarios']
    for key, value in data['settings'].items():
        assert getattr(manifest, key) == value
    runner = BenchmarkRunner(manifest)
    for entry in entries:
        workflow = runner._build_workflow_config(entry)
        assert workflow.seed == 42 and workflow.temperature == 0.4
        assert workflow.topology == entry['topology']
        assert workflow.propagation_mode == entry['propagation_mode']
        assert (workflow.memory_mode == 'ephemeral_shared') == (entry['execution_variant'] == 'memory_enabled')


@pytest.mark.parametrize('mutation', [
    lambda d: d['scenarios'].append(deepcopy(d['scenarios'][0])),
    lambda d: d['scenarios'][1].update(run_id=d['scenarios'][0]['run_id']),
    lambda d: d['scenarios'][0].pop('fixture_id'),
    lambda d: d['scenarios'][0].update(seed=123),
    lambda d: d['scenarios'][0].update(fixture_id='missing_fixture'),
    lambda d: d['scenarios'][0].update(task_family='unknown'),
    lambda d: d['scenarios'][0].update(task_variant='unknown'),
    lambda d: d['scenarios'][0].update(topology='unknown'),
    lambda d: d['scenarios'][0].update(propagation_mode='one_to_many'),
    lambda d: d['scenarios'][0].update(lep_code='UNKNOWN', lep_codes=['UNKNOWN']),
    lambda d: d['scenarios'][0].update(lep_codes=[]),
    lambda d: d['scenarios'][0].update(execution_variant='memory_enabled'),
    lambda d: d['scenarios'][0].update(repetition_index=1),
    lambda d: d['scenarios'][0].update(repetition_index=False),
    lambda d: d['scenarios'][0].update(pair_tag='wrong'),
    lambda d: d['scenarios'][0].update(scenario_id='wrong'),
    lambda d: d.update(scenarios=[]),
    lambda d: d['settings'].pop('temperature'),
    lambda d: d['settings'].update(temperature=float('nan')),
    lambda d: d['settings'].update(seed='42'),
    lambda d: d['settings'].update(extra_setting=True),
    lambda d: d['fixture_manifest_sha256'].update({d['scenarios'][0]['fixture_id']: 'wrong'}),
])
def test_invalid_selections_fail_closed(tmp_path, data, mutation):
    mutation(data)
    with pytest.raises((ValueError, OSError)):
        load(tmp_path, data)


@pytest.mark.parametrize('text', ['{', '[]', '{"schema":"a","schema":"b"}'])
def test_malformed_json(tmp_path, text):
    path = tmp_path / 'bad.json'
    path.write_text(text)
    with pytest.raises(ValueError):
        load_plan_manifest(path, BenchmarkManifest(fixture_root=ROOT / 'workspace_fixtures'))


def stub_outputs(monkeypatch):
    monkeypatch.setattr(BenchmarkRunner, '_persist_record', lambda *a: None)
    monkeypatch.setattr(BenchmarkRunner, '_summarize', lambda self: {'total_runs': len(self.results)})
    monkeypatch.setattr(BenchmarkRunner, '_write_summary', lambda *a: None)
    monkeypatch.setattr(BenchmarkRunner, 'audit_injection_counts', lambda *a: None)


def test_cli_exact_50_and_no_execution_expansion(tmp_path, monkeypatch, data):
    import benchmark.__main__ as cli
    import benchmark.plan_manifest as loader
    original_load = loader.load_plan_manifest
    def validate_then_forbid_expansion(*args):
        result = original_load(*args)
        monkeypatch.setattr(BenchmarkManifest, 'build_plan', lambda *a: pytest.fail('Expanded after selection'))
        return result
    monkeypatch.setattr(loader, 'load_plan_manifest', validate_then_forbid_expansion)
    captured = []
    original_execute = BenchmarkRunner._execute
    class StopBeforeInference(BaseException):
        pass
    def capture(self, scenario, fixture_root, execution_id=None):
        captured.append(asdict(scenario))
        raise StopBeforeInference()
    def execute(self, entry):
        self.llm_backend = object()
        try:
            original_execute(self, entry)
        except StopBeforeInference:
            return SimpleNamespace(error=None)
        pytest.fail('Scenario did not reach the normal runner')
    monkeypatch.setattr(ScenarioRunner, 'run', capture)
    monkeypatch.setattr(BenchmarkRunner, '_execute', execute)
    monkeypatch.setattr(BenchmarkRunner, '_default_backend', lambda *a: pytest.fail('Backend created'))
    stub_outputs(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--plan-manifest', str(SAVED), '--output-dir', str(tmp_path),
                                    '--real-model', '--backend', 'vllm', '--model', data['settings']['model_name'],
                                    '--vllm-url', data['settings']['vllm_url']])
    assert cli.main() == 0
    assert len(captured) == 50
    assert [s['scenario_id'] for s in captured] == [e['scenario_id'] for e in data['scenarios']]
    for scenario, entry in zip(captured, data['scenarios']):
        for key in ('fixture_id', 'task_family', 'task_variant', 'condition', 'repetition_index'):
            assert scenario[key] == entry[key]
        assert [c['code'] for c in scenario['lep_configs']] == entry['lep_codes']
        assert scenario['workflow_config']['temperature'] == 0.4
        assert scenario['workflow_config']['seed'] == 42


def test_cli_without_manifest_uses_legacy_expansion(tmp_path, monkeypatch):
    import benchmark.__main__ as cli
    called, selected = [], []
    original = BenchmarkManifest.build_plan
    def build(self):
        result = original(self)
        called.append(result)
        return result
    monkeypatch.setattr(BenchmarkManifest, 'build_plan', build)
    monkeypatch.setattr(BenchmarkRunner, '_execute', lambda self, e: selected.append(e) or SimpleNamespace(error=None))
    stub_outputs(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--output-dir', str(tmp_path), '--benign-repetitions', '0'])
    assert cli.main() == 0
    assert len(called) == 1 and selected == called[0]
    assert len(selected) == 17575


@pytest.mark.parametrize('extra', [['--model', 'different'], ['--mod=different'], ['--dry-run'], ['--repetitions', '2'],
                                 ['--propagation-modes', 'single_origin']])
def test_cli_conflicts_cannot_execute(tmp_path, monkeypatch, extra):
    import benchmark.__main__ as cli
    monkeypatch.setattr(BenchmarkRunner, 'run', lambda *a, **k: pytest.fail('Execution started'))
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--plan-manifest', str(SAVED), '--output-dir', str(tmp_path)] + extra)
    assert cli.main() == 1


def test_fixture_incompatible_lep_rejected(tmp_path, data):
    import hashlib
    data['scenarios'] = data['scenarios'][:1]
    entry = data['scenarios'][0]
    fixture = json.loads((ROOT / 'workspace_fixtures' / entry['fixture_id'] / 'manifest.json').read_text())
    fixture['supported_leps'] = ['LEP_TOOL_RESULT_CORRUPTION']
    directory = tmp_path / entry['fixture_id']
    directory.mkdir()
    raw = json.dumps(fixture).encode()
    (directory / 'manifest.json').write_bytes(raw)
    data['fixture_manifest_sha256'][entry['fixture_id']] = hashlib.sha256(raw).hexdigest()
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Cannot resolve'):
        load_plan_manifest(path, BenchmarkManifest(fixture_root=tmp_path))


def test_cli_bad_manifest_never_runs(tmp_path, monkeypatch):
    import benchmark.__main__ as cli
    path = tmp_path / 'bad.json'
    path.write_text('{')
    monkeypatch.setattr(BenchmarkRunner, 'run', lambda *a, **k: pytest.fail('Execution started'))
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--plan-manifest', str(path)])
    assert cli.main() == 1
