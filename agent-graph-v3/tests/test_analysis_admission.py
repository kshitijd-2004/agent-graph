"""Real execution artifacts cross the same admission gate at runtime and ingestion."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from generation.admission import validate_trace, mutation_evidence
from benchmark.propagation_analysis import PropagationAnalyzer
from test_injection_reachability import run_research, MP, ResearchBackend
from schemas import TraceEventType


@pytest.fixture
def valid(tmp_path):
    return run_research(tmp_path / 'run').trace


def save(root, trace, folder='traces', name=None):
    path = root / folder / ((name or trace.trace_id) + '_trace.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace.to_dict()))


def origin(trace):
    return next(e for e in trace.events if e.event_labels.is_injection_origin)


def controls(root):
    clean = run_research(root / 'clean', code=None).trace
    for i in range(3):
        trace = deepcopy(clean)
        trace.trace_id = f'clean-{i}'
        trace.metadata.update(repetition_index=i, execution_variant='memory_enabled')
        save(root, trace)


def plan_for(trace, sid=None):
    m = trace.metadata
    return {k: m[k] for k in ('scenario_id', 'fixture_id', 'task_family', 'condition',
                              'topology', 'propagation_mode', 'lep_codes')} | {
                                  'scenario_id': sid or m['scenario_id'], 'valid_trace_quota': 1}


@pytest.mark.parametrize('kind', ['unreachable', 'backend', 'noop', 'wrong_role', 'late',
                                 'forged_count', 'duplicate_event', 'bad_dependency'])
def test_misplaced_rejection_never_becomes_negative(tmp_path, valid, kind):
    trace = valid
    e = origin(trace)
    if kind in ('unreachable', 'backend', 'forged_count'):
        for event in trace.events:
            event.event_labels.is_injection_origin = False
            event.hidden.pop('mutation_evidence', None)
        trace.metadata.update(actual_injection_origins=1, expected_injection_origins=1)
        if kind != 'forged_count':
            trace.metadata.update(termination_reason='injection_target_unreachable' if kind == 'unreachable'
                                  else 'backend_connection_error', dataset_eligible=False)
    elif kind == 'noop':
        call = next(x for x in trace.events if x.event_id in e.depends_on)
        e.tool_arguments['value'] = call.tool_arguments['value']
        mutation_evidence(e, MP, call.tool_arguments['value'], call.tool_arguments['value'])
    elif kind == 'wrong_role':
        e.agent_role = 'specialist_a'
    elif kind == 'late':
        late = next(x for x in reversed(trace.events) if x.event_type == TraceEventType.MEMORY_WRITE
                    and x.agent_role == 'coordinator')
        e.event_labels.is_injection_origin = False
        e.hidden.pop('mutation_evidence')
        late.event_labels.is_injection_origin = True
        late.hidden['lep_type'] = MP
        before = late.tool_arguments['value']
        late.tool_arguments['value'] = 'Different poisoned finding'
        mutation_evidence(late, MP, before, late.tool_arguments['value'])
    elif kind == 'duplicate_event':
        trace.events.insert(2, deepcopy(e))
    else:
        e.depends_on = ['missing']
    save(tmp_path, trace)  # Deliberately put bad traces in the accepted directory.
    analyzer = PropagationAnalyzer(tmp_path, plan=[plan_for(trace)])
    result = analyzer.analyze()
    assert result['overall']['started_perturbed_attempts'] == 1
    assert result['overall']['valid_admitted_perturbed_traces'] == 0
    assert result['overall']['propagation_probability'] is None
    assert not (tmp_path / 'propagation_metrics.jsonl').read_text()
    assert analyzer.attempts[0]['propagation_occurred'] is None
    if kind == 'backend':
        assert analyzer.attempts[0]['reason'] == 'infrastructure_failure'
    if kind == 'unreachable':
        assert analyzer.attempts[0]['reason'] == 'injection_target_unreachable'
    if kind == 'noop':
        assert analyzer.attempts[0]['reason'] == 'ineffective_mutation'


def test_partial_many_to_one_cannot_enter_metrics(tmp_path):
    trace = run_research(tmp_path / 'run', topology='branch_and_verify', mode='many_to_one').trace
    e = origin(trace)
    e.event_labels.is_injection_origin = False
    e.hidden.pop('mutation_evidence')
    assert validate_trace(trace).reason == 'partial_injection'
    save(tmp_path, trace)
    result = PropagationAnalyzer(tmp_path).analyze()
    assert result['overall']['propagation_probability'] is None
    assert result['overall']['rejected_by_reason']['partial_injection'] == 1


def test_valid_unretrieved_storage_mutation_is_measurable_zero(tmp_path, valid):
    assert not any(e.event_type == TraceEventType.MEMORY_RETRIEVAL for e in valid.events)
    assert validate_trace(valid).admitted
    controls(tmp_path)
    save(tmp_path, valid)
    result = PropagationAnalyzer(tmp_path, plan=[plan_for(valid)]).analyze()['overall']
    assert result['valid_admitted_perturbed_traces'] == 1
    assert result['measurable_propagation_outcomes'] == 1
    assert result['valid_traces_without_downstream_propagation'] == 1
    assert result['propagation_probability'] == 0
    assert result['injection_yield'] == result['analysis_coverage'] == 1


def test_detector_exception_is_unknown(tmp_path, valid, monkeypatch):
    controls(tmp_path)
    save(tmp_path, valid)
    def fail(*args, **kwargs):
        raise RuntimeError('detector unavailable')
    monkeypatch.setattr('benchmark.propagation_analysis.detect_behavioral_anomalies', fail)
    analyzer = PropagationAnalyzer(tmp_path)
    result = analyzer.analyze()['overall']
    assert result['valid_admitted_perturbed_traces'] == 1
    assert result['analysis_failures'] == result['unknown_outcomes'] == 1
    assert result['propagation_probability'] is None
    assert result['analysis_coverage'] == 0
    assert analyzer.attempts[0]['propagation_occurred'] is None
    assert analyzer.attempts[0]['analysis_status'] == 'analysis_failed'


def test_plan_keeps_rejected_and_unattempted_cells(tmp_path, valid):
    a = plan_for(valid)
    b = {**a, 'scenario_id': 'unstarted', 'fixture_id': 'another-fixture'}
    valid.metadata.update(dataset_eligible=False, termination_reason='injection_target_unreachable')
    save(tmp_path, valid, 'rejected_traces')
    result = PropagationAnalyzer(tmp_path, plan=[a, b]).analyze()
    assert len(result['by_cell']) == 2
    assert result['overall']['planned_perturbed_slots'] == 2
    assert result['overall']['any_valid_cell_coverage'] == 0
    assert result['overall']['quota_coverage'] == 0
    assert result['overall']['slot_fulfillment'] == 0
    attempted = next(c for c in result['by_cell'].values() if c['started_perturbed_attempts'])
    assert attempted['injection_yield'] == 0
    assert attempted['propagation_probability'] is None
    unstarted = next(c for c in result['by_cell'].values() if not c['started_perturbed_attempts'])
    assert unstarted['injection_yield'] is None


def test_legacy_storage_evidence_remains_valid(tmp_path, valid):
    valid.metadata.pop('admission_contract')
    valid.metadata.pop('admission')
    for e in valid.events:
        e.hidden.pop('mutation_evidence', None)
    assert validate_trace(valid).admitted


def test_legacy_tool_mutation_without_original_is_unverifiable(tmp_path):
    trace = run_research(tmp_path / 'run', code='LEP_TOOL_RESULT_CORRUPTION').trace
    trace.metadata.pop('admission_contract')
    for e in trace.events:
        e.hidden.pop('mutation_evidence', None)
    assert validate_trace(trace).status == 'unverifiable'


def test_copied_attempt_is_not_counted_twice(tmp_path, valid):
    save(tmp_path, valid)
    save(tmp_path, valid, 'rejected_traces')
    summary = PropagationAnalyzer(tmp_path).analyze()
    assert summary['overall']['started_perturbed_attempts'] == 1


def test_failed_attempt_without_trace_is_counted(tmp_path):
    record = dict(scenario_id='failed', fixture_id='fixture', condition='single_lep',
                  lep_code=MP, topology='coordinator_workers', propagation_mode='one_to_many',
                  termination_reason='backend_connection_error', trace_id='lost')
    (tmp_path / 'benchmark_records.jsonl').write_text(json.dumps(record)+'\n')
    result = PropagationAnalyzer(tmp_path).analyze()['overall']
    assert result['started_perturbed_attempts'] == 1
    assert result['rejected_by_reason']['infrastructure_failure'] == 1
    assert result['propagation_probability'] is None


def test_missing_plan_does_not_invent_coverage(tmp_path, valid):
    save(tmp_path, valid)
    overall = PropagationAnalyzer(tmp_path).analyze()['overall']
    assert overall['planned_cells'] is None
    assert overall['any_valid_cell_coverage'] is None
    assert overall['slot_fulfillment'] is None
    assert overall['coverage_fraction'] is None
    assert overall['observed_cells'] == 1


def test_wrong_plan_and_unknown_contract_fail_closed(tmp_path, valid):
    save(tmp_path, valid)
    bad = {**plan_for(valid), 'fixture_id': 'wrong-fixture'}
    summary = PropagationAnalyzer(tmp_path, plan=[bad]).analyze()
    assert summary['overall']['valid_admitted_perturbed_traces'] == 0
    valid.metadata['admission_contract']['version'] = 'future-version'
    assert validate_trace(valid).status == 'unverifiable'


def test_missing_fixture_is_analysis_failure(tmp_path, valid, monkeypatch):
    controls(tmp_path)
    save(tmp_path, valid)
    def fail(*args):
        raise FileNotFoundError('missing fixture definition')
    monkeypatch.setattr(PropagationAnalyzer, '_load_fixture_spec', staticmethod(fail))
    overall = PropagationAnalyzer(tmp_path).analyze()['overall']
    assert overall['analysis_failures'] == 1
    assert overall['propagation_probability'] is None


def test_frozen_plan_start_ledger_and_retry_artifacts(tmp_path, monkeypatch):
    from benchmark.benchmark_runner import BenchmarkRunner, BenchmarkManifest, BenchmarkRecord
    import uuid
    root = tmp_path / 'run'
    runner = BenchmarkRunner(BenchmarkManifest(output_dir=root))
    calls = []
    def execute(entry):
        assert (root / 'benchmark_plan.json').exists()
        assert entry['attempt_id'] in (root / 'attempt_starts.jsonl').read_text()
        calls.append(entry)
        return BenchmarkRecord(run_id='r', scenario_id=entry['scenario_id'], fixture_id='f',
            attempt_id=entry['attempt_id'], task_family='research_synthesis', condition='single_lep',
            lep_code=MP, topology='coordinator_workers', propagation_mode='one_to_many', repetition_index=0,
            termination_reason='injection_target_unreachable', admission_reason='injection_target_unreachable')
    monkeypatch.setattr(runner, '_execute', execute)
    entry = dict(scenario_id='slot', fixture_id='f', task_family='research_synthesis', condition='single_lep',
                 lep_code=MP, lep_codes=[MP], topology='coordinator_workers', propagation_mode='one_to_many')
    runner.run(plan=[entry])
    runner.run(plan=[entry])
    assert len(calls) == 2  # One attempt per invocation, no hidden resampling.
    assert calls[0]['attempt_id'] != calls[1]['attempt_id']
    records = [json.loads(x) for x in (root / 'benchmark_records.jsonl').read_text().splitlines()]
    assert len(records) == 2
    analyzed = PropagationAnalyzer(root).analyze()['overall']
    assert analyzed['planned_perturbed_slots'] == 1
    assert analyzed['started_perturbed_attempts'] == 2
    with pytest.raises(ValueError, match='different frozen plan'):
        runner.run(plan=[{**entry, 'scenario_id': 'new-slot'}])


def test_merge_preserves_rejections_plans_and_attempts(tmp_path):
    from benchmark.split_merge import merge_outputs
    nodes = []
    for i in range(2):
        node = tmp_path / f'node_{i}'
        node.mkdir()
        entry = dict(scenario_id=f'slot-{i}', fixture_id='f', task_family='research_synthesis',
                     condition='single_lep', lep_code=MP, lep_codes=[MP], topology='coordinator_workers',
                     propagation_mode='one_to_many')
        record = dict(entry, attempt_id=f'a-{i}', trace_id=f't-{i}', dataset_eligible=False,
                      termination_reason='backend_connection_error')
        (node / 'benchmark_plan.json').write_text(json.dumps({'schema': 'agentprop-frozen-plan-v1', 'entries': [entry]}))
        (node / 'benchmark_records.jsonl').write_text(json.dumps(record)+'\n')
        (node / 'attempt_starts.jsonl').write_text(json.dumps(record)+'\n')
        (node / 'rejected_traces').mkdir()
        (node / 'rejected_traces' / f't-{i}_trace.json').write_text('{}')
        nodes.append(node)
    merged = tmp_path / 'merged'
    merge_outputs(nodes, merged)
    assert len(list((merged / 'rejected_traces').glob('*.json'))) == 2
    assert len(json.loads((merged / 'benchmark_plan.json').read_text())['entries']) == 2
    assert len((merged / 'attempt_starts.jsonl').read_text().splitlines()) == 2
    accounting = json.loads((merged / 'benchmark_summary.json').read_text())['accounting']['overall']
    assert accounting['started_perturbed_attempts'] == 2
    assert accounting['infrastructure_failures'] == 2


def test_missing_lep_configuration_is_unverifiable_not_clean(tmp_path, valid):
    valid.metadata['lep_codes'] = []
    valid.metadata['admission_contract']['scenario']['lep_configs'] = []
    save(tmp_path, valid)
    result = PropagationAnalyzer(tmp_path).analyze()['overall']
    assert result['started_perturbed_attempts'] == 1
    assert result['unverifiable_traces'] == 1
    assert result['valid_admitted_perturbed_traces'] == 0
    assert result['propagation_probability'] is None


def test_counterfactual_is_not_a_perturbed_attempt(tmp_path, valid):
    trace = run_research(tmp_path / 'run', code=None).trace
    trace.metadata['condition'] = 'counterfactual'
    trace.metadata['admission_contract']['scenario']['condition'] = 'counterfactual'
    trace.metadata['lep_codes'] = [MP]
    trace.metadata['admission_contract']['scenario']['lep_configs'] = deepcopy(
        valid.metadata['admission_contract']['scenario']['lep_configs'])
    save(tmp_path, trace)
    summary = PropagationAnalyzer(tmp_path).analyze()
    assert summary['overall']['started_perturbed_attempts'] == 0
    assert summary['clean_reference_admission']['admitted'] == 1
