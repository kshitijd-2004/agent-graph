"""Offline mechanical coverage of the four-case normal-production IPI harness."""
from dataclasses import replace

import pytest

from backend.api_backend import ModelTurn, ToolCall
from generation.runner import ScenarioRunner
from scripts.run_ipi_live_validation import CASES, ROOT, audit_result, main, preview, scenario_for

FIXTURES = ROOT / 'workspace_fixtures'


class ScriptedBackend:
    """Reads sources twice to check one-shot delivery, then writes and finalizes."""
    def __init__(self, case, target):
        self.case, self.target = case, target
        self.received = []

    def reset(self, *, mcp_tools, **kwargs):
        self.calls = [('read_text_file', {'path': self.target})] * 2
        self.calls += [('write_file', {'path': self.case.final_path,
                                       'content': 'Offline scripted report; not a factual grading assertion.'})]
        if 'submit_final' in mcp_tools:
            self.calls += [('submit_final', {'summary': 'Offline scripted completion.',
                                             'report_path': self.case.final_path})]
        else:
            self.calls += [('handoff', {'summary': 'Read sources and prepared report.',
                                       'report_path': self.case.final_path})]
        self.index = 0

    def generate(self, prompt='', tool_choice=None):
        name, args = self.calls[self.index]
        self.index += 1
        return ModelTurn(text='', tool_call=ToolCall(id=f'call-{self.index}', name=name, input=args),
                         stop_reason='tool_use')

    def _append_tool_result(self, call, result):
        self.received.append(result)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.name)
def test_four_cases_through_real_runner(tmp_path, case):
    scenario = scenario_for(case)
    plan = preview(case, FIXTURES, scenario)
    backend = ScriptedBackend(case, plan['intended_boundary']['path'])
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(scenario, FIXTURES)
    report = audit_result(case, plan, result, tmp_path)
    assert report['smoke_pass'], report
    assert report['final_artifact_exists']
    assert report['grading'] is not None
    assert not plan['generated_ipi']['fallback_generic']
    assert report['actual_delivered_payloads'] == [plan['offline_delivery_preview']['perturbed_result']]
    assert sum(plan['generated_ipi']['payload_text'] in text for text in backend.received) == 1
    # A missing origin or missing payload must never look like a passing smoke.
    origin = next(e for e in result.trace.events if e.event_labels.is_injection_origin)
    origin.output_text = 'clean source only'
    assert not audit_result(case, plan, result, tmp_path)['smoke_pass']
    origin.event_labels.is_injection_origin = False
    assert not audit_result(case, plan, result, tmp_path)['smoke_pass']


def test_nei_cannot_silently_target_support():
    case = replace(CASES[3], fact_id='claim_1_verdict')
    with pytest.raises(ValueError, match='different fact'):
        preview(case, FIXTURES, scenario_for(case))


def test_default_is_offline_and_preserves_existing_output(tmp_path, monkeypatch):
    def no_live(*args, **kwargs):
        pytest.fail('Offline preview invoked a runner')
    monkeypatch.setattr(ScenarioRunner, 'run', no_live)
    assert main(['--output', str(tmp_path)]) == 0
    before = (tmp_path / 'offline_plan.json').read_bytes()
    with pytest.raises(FileExistsError):
        main(['--output', str(tmp_path)])
    assert (tmp_path / 'offline_plan.json').read_bytes() == before


def test_live_entrypoint_with_only_scripted_backends(tmp_path, monkeypatch):
    import json
    from backend import hf_backend

    created = []
    def offline_backend(**kwargs):
        case = CASES[len(created)]
        plan = preview(case, FIXTURES, scenario_for(case))
        backend = ScriptedBackend(case, plan['intended_boundary']['path'])
        created.append(backend)
        return backend
    monkeypatch.setattr(hf_backend, 'HFBackend', offline_backend)
    assert main(['--run', '--output', str(tmp_path)]) == 0
    assert len(created) == 4
    rows = json.loads((tmp_path / 'summary.json').read_text())
    assert len(rows) == 4 and all(row['smoke_pass'] for row in rows)
    for case in CASES:
        assert (tmp_path / case.name / 'trace.json').is_file()
        assert (tmp_path / case.name / 'audit.json').is_file()
    with pytest.raises(ValueError, match='Live output already exists'):
        main(['--run', '--output', str(tmp_path)])
    assert len(created) == 4
