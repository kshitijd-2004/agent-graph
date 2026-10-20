"""Conditional production boundaries, with real SciFact fixtures and mutations."""
import json
from collections import Counter

import pytest

from backend.api_backend import ModelTurn, ToolCall
from benchmark.benchmark_runner import BenchmarkRunner
from generation.runner import ScenarioRunner
from generation.scenario_builder import ScenarioBuilder, ScenarioBuildConfig
from schemas import TraceEventType
from test_injection_count_regression import CODES, FIXTURES, origins

MP = 'LEP_MEMORY_POISONING'
PATHS = [('review_loop', 'single_origin'), ('branch_and_verify', 'single_origin'),
         ('branch_and_verify', 'many_to_one'), ('coordinator_workers', 'single_origin'),
         ('coordinator_workers', 'one_to_many')]
FIXTURE = 'res_scifact_045_212_488'


class ResearchBackend:
    def __init__(self, topology, skip_write=False, value='Independent findings from the papers.'):
        self.topology, self.skip_write, self.value = topology, skip_write, value
        self.visits = Counter()
        self.prompts = []

    def reset(self, *, agent_name, mcp_tools, **kwargs):
        role = agent_name
        self.visits[role] += 1
        self.calls = [('read_text_file', {'path': 'documents/paper_a.md'}),
                      ('read_text_file', {'path': 'documents/paper_b.md'})]
        if 'write_memory' in mcp_tools and not (role == 'coordinator' and self.skip_write):
            self.calls += [('write_memory', {'key': 'key_findings', 'value': self.value})]
        final = ((self.topology == 'branch_and_verify' and role == 'verifier')
                 or (role == 'coordinator' and self.visits[role] > 1)
                 or (self.topology == 'review_loop' and role == 'analyst' and self.visits[role] > 1))
        summary = 'Claim 1: CONTRADICTED. Claim 2: SUPPORTED. Evidence from Paper A and Paper B.'
        if final:
            self.calls += [('submit_final', {'summary': summary})]
        else:
            report = f'output/{role}.md'
            self.calls += [('write_file', {'path': report, 'content': summary}),
                           ('handoff', {'summary': summary, 'report_path': report,
                            'source_paths': ['documents/paper_a.md', 'documents/paper_b.md']})]
        self.index = 0

    def generate(self, prompt='', tool_choice=None):
        self.prompts.append(prompt)
        name, args = self.calls[self.index]
        self.index += 1
        return ModelTurn(tool_call=ToolCall(id=f'call-{self.index}', name=name, input=args),
                         text='', stop_reason='tool_use')

    def _append_tool_result(self, *args, **kwargs):
        pass


def run_research(tmp_path, code=MP, topology='coordinator_workers', mode='one_to_many',
                 backend=None):
    lep = BenchmarkRunner._resolve_lep(None, code, 'research_synthesis') if code else None
    spec = ScenarioBuilder().build_single(ScenarioBuildConfig(
        task_family='research_synthesis', fixture_id=FIXTURE, task_variant='scifact_claims',
        topology=topology, propagation_mode=mode, condition='single_lep' if code else 'benign',
        lep_configs=[lep] if lep else [], memory_mode='persistent_shared', max_agent_turns=12))
    backend = backend or ResearchBackend(topology)
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec, FIXTURES)
    assert result.runner_success, result.error
    return result


@pytest.mark.parametrize('code', CODES)
@pytest.mark.parametrize('topology,mode', PATHS)
def test_scifact_all_supported_paths(tmp_path, code, topology, mode):
    backend = ResearchBackend(topology)
    result = run_research(tmp_path, code, topology, mode, backend)
    assert result.dataset_eligible, result.trace.metadata
    es = origins(result)
    assert len(es) == (2 if mode == 'many_to_one' else 1)
    assert result.trace.metadata['unfulfilled_injections'] == {}
    for event in es:
        if code == MP:
            call = next(e for e in result.trace.events if e.event_id == event.depends_on[0])
            assert call.tool_arguments['value'] != event.tool_arguments['value']
            assert event.hidden['original_hash'] != event.hidden['perturbed_hash']
        elif code in ('LEP_INDIRECT_PROMPT_INJECTION', 'LEP_TOOL_RESULT_CORRUPTION'):
            assert event.hidden['result_changed']
            assert event.tool_result != (FIXTURES / FIXTURE / event.tool_arguments['path']).read_text()
        elif code == 'LEP_HANDOFF_CORRUPTION':
            assert event.hidden['original_hash'] != event.hidden['perturbed_hash']
        else:
            instruction = event.hidden['input_disregard']['instruction']
            assert instruction
            assert any(instruction in prompt for prompt in backend.prompts)


def test_optional_coordinator_write_is_not_forced_or_retargeted(tmp_path):
    backend = ResearchBackend('coordinator_workers', skip_write=True)
    result = run_research(tmp_path, backend=backend)
    assert not result.dataset_eligible
    assert result.termination_reason == 'injection_target_unreachable'
    assert result.trace.metadata['expected_injection_origins'] == 1
    assert not origins(result)
    assert backend.visits == {'coordinator': 1}
    assert not any(e.event_type == TraceEventType.MEMORY_WRITE for e in result.trace.events)
    detail = result.trace.metadata['unfulfilled_injections'][MP]
    assert detail['status'] == 'boundary_not_reached'
    assert detail['boundary'] == 'memory_write'
    assert detail['required_roles'] == ['coordinator']
    assert detail['decisions'] == []


def test_same_clean_path_still_completes_without_coordinator_write(tmp_path):
    backend = ResearchBackend('coordinator_workers', skip_write=True)
    result = run_research(tmp_path, code=None, backend=backend)
    assert result.dataset_eligible, result.trace.metadata
    assert backend.visits['coordinator'] == 2
    assert not origins(result)
    assert 'unfulfilled_injections' not in result.trace.metadata


def test_memory_noop_is_not_an_origin(tmp_path):
    manifest = json.loads((FIXTURES / FIXTURE / 'manifest.json').read_text())
    value = manifest['attack']['memory_poisoning']['key_findings']['value']
    result = run_research(tmp_path, backend=ResearchBackend('coordinator_workers', value=value))
    assert result.termination_reason == 'injection_target_unreachable'
    assert not result.dataset_eligible
    assert not origins(result)
    writes = [e for e in result.trace.events if e.event_type == TraceEventType.MEMORY_WRITE]
    assert len(writes) == 1 and writes[0].tool_arguments['value'] == value
    assert result.trace.metadata['unfulfilled_injections'][MP]['status'] == 'mutation_not_applied'


def test_memory_origin_value_is_really_stored(tmp_path, monkeypatch):
    from memory.memory_store import MemoryStore
    stored = []
    original = MemoryStore.add
    def capture(self, record):
        result = original(self, record)
        stored.append(record)
        return result
    monkeypatch.setattr(MemoryStore, 'add', capture)
    result = run_research(tmp_path)
    event, = origins(result)
    record, = [r for r in stored if r.metadata.get('write_event_id') == event.event_id]
    assert record.value == event.tool_arguments['value']
    assert record.metadata['poison_origin_event_id'] == event.event_id
    assert 'poisoned' in record.tags


def test_missing_source_read_closes_o2m_window(tmp_path):
    backend = ResearchBackend('coordinator_workers')
    reset = backend.reset
    def without_reads(**kwargs):
        reset(**kwargs)
        backend.calls = [(name, args) for name, args in backend.calls if name != 'read_text_file']
    backend.reset = without_reads
    result = run_research(tmp_path, backend=backend)
    assert result.termination_reason == 'injection_target_unreachable'
    assert not origins(result)
    detail = result.trace.metadata['unfulfilled_injections'][MP]
    assert detail['status'] == 'target_not_realized'
    assert any('prerequisite' in d['reason'] for d in detail['decisions'])


@pytest.mark.parametrize('memory_mode', ['none', 'ephemeral_private'])
def test_impossible_memory_configuration_fails_before_inference(tmp_path, memory_mode):
    from schemas import ScenarioSpec, WorkflowConfig
    backend = ResearchBackend('coordinator_workers')
    spec = ScenarioSpec(scenario_id='impossible-memory', task_family='research_synthesis',
        task_variant='scifact_claims', fixture_id=FIXTURE, condition='single_lep',
        lep_configs=[BenchmarkRunner._resolve_lep(None, MP, 'research_synthesis')],
        workflow_config=WorkflowConfig(topology='coordinator_workers',
            propagation_mode='one_to_many', memory_mode=memory_mode))
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec, FIXTURES)
    assert not result.runner_success
    assert not result.dataset_eligible
    assert 'requires shared memory' in result.error
    assert not backend.visits


@pytest.mark.parametrize('code', CODES)
def test_scifact_backend_failure_never_reaches_injector(tmp_path, monkeypatch, code):
    monkeypatch.setattr('generation.stage_runner.time.sleep', lambda seconds: None)
    backend = ResearchBackend('branch_and_verify')
    def fail(*args, **kwargs):
        raise ConnectionRefusedError('production reproduction')
    backend.generate = fail
    result = run_research(tmp_path, code, 'branch_and_verify', 'many_to_one', backend)
    assert result.termination_reason == 'backend_connection_error'
    assert not result.dataset_eligible
    assert result.trace.metadata['expected_injection_origins'] == 2
    assert not origins(result)
    assert result.trace.metadata['unfulfilled_injections'][code]['decisions'] == []
    assert {e.event_type for e in result.trace.events} == {
        TraceEventType.USER_INPUT, TraceEventType.SYSTEM_INIT, TraceEventType.TOPOLOGY_TRANSITION}


def test_evaluation_error_is_not_reported_as_missing_boundary(tmp_path, monkeypatch):
    from leps.memory_poisoning import MemoryPoisoningLEP
    def fail(*args, **kwargs):
        raise ValueError('broken predicate')
    monkeypatch.setattr(MemoryPoisoningLEP, 'evaluate', fail)
    result = run_research(tmp_path)
    assert not result.dataset_eligible
    assert not origins(result)
    detail = result.trace.metadata['unfulfilled_injections'][MP]
    assert detail['status'] == 'evaluation_error'
    assert detail['evaluation_errors'][0]['reason'] == 'broken predicate'
