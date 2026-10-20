"""Offline source-briefing protocol tests; never instantiate a model backend."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from experiments.scifact_memory_v1.pilot import build_plan, FIXTURES, PROTOCOL
from generation.existing_record_protocol import validate_fixture, evidence
from generation.admission import validate_trace
from benchmark.benchmark_runner import BenchmarkManifest, BenchmarkRunner
from generation.runner import ScenarioRunner
from schemas import ScenarioSpec
from test_injection_reachability import ResearchBackend


def run(tmp_path, benign=False, backend=None):
    entry = next(e for e in build_plan() if (e['condition']=='benign') == benign)
    runner = BenchmarkRunner(BenchmarkManifest())
    config = runner._build_workflow_config(entry)
    spec = ScenarioSpec(scenario_id=entry['scenario_id'], fixture_id=entry['fixture_id'],
        task_family=entry['task_family'], task_variant=entry['task_variant'],
        condition=entry['condition'], workflow_config=config,
        lep_configs=[] if benign else [runner._resolve_lep('LEP_MEMORY_POISONING', 'research_synthesis')])
    backend = backend or ResearchBackend('coordinator_workers', skip_write=True)
    result = ScenarioRunner(llm_backend=backend, dry_run=False, output_dir=tmp_path).run(spec, FIXTURES)
    assert result.runner_success, result.error
    return result.trace, backend


def test_frozen_plan_provenance_and_exact_edits():
    plan = build_plan()
    assert len(plan)==40
    assert sum(e['condition']=='benign' for e in plan)==30
    assert {e['propagation_mode'] for e in plan} == {'one_to_many'}
    for p in FIXTURES.glob('*/manifest.json'):
        config=json.loads(p.read_text())['memory_protocol']
        r=validate_fixture(config,p.parent)
        bad=deepcopy(config);bad['record']['changed']=r['clean']
        with pytest.raises(ValueError):validate_fixture(bad,p.parent)
        bad=deepcopy(config);bad['record']['start']+=1
        with pytest.raises(ValueError):validate_fixture(bad,p.parent)


def test_access_isolation():
    from memory.memory_store import MemoryStore, MemoryRecord
    m=MemoryStore();m.initialize_briefing('coordinator_briefing/paper_a','clean')
    m.commit_briefing('coordinator_briefing/paper_a','clean','altered')
    assert m.get_briefing('coordinator_briefing/paper_a',role='coordinator')=='altered'
    for role in ('specialist_a','specialist_b','synthesizer'):
        with pytest.raises(PermissionError):m.get_briefing('coordinator_briefing/paper_a',role=role)
    assert not m.retrieve('altered') and not m.list_all()
    m.add(MemoryRecord(id='ordinary',key='ordinary',value='normal shared finding'))
    assert m.retrieve('finding')


def test_real_storage_exposure_and_matched_controls(tmp_path):
    trace, backend=run(tmp_path/'perturbed')
    clean, clean_backend=run(tmp_path/'clean',benign=True)
    assert validate_trace(trace).admitted, validate_trace(trace)
    assert validate_trace(clean).admitted, validate_trace(clean)
    facts=evidence(trace)
    assert trace.injection_origin_count==1 and clean.injection_origin_count==0
    assert facts['record']['changed'] in backend.prompts[0]
    assert facts['record']['clean'] in clean_backend.prompts[0]
    assert facts['record']['changed'] not in clean_backend.prompts[0]
    assert [e.event_type for e in trace.events] == [e.event_type for e in clean.events]
    assert not any(e.event_type.value=='memory_write' and e.agent_role=='coordinator' for e in trace.events)
    for e in trace.events:
        assert 'mutation_evidence' not in json.dumps(e.observable)
    # Altered evidence cannot pass by retaining count/trigger labels.
    bad=deepcopy(trace)
    commit=next(e for e in bad.events if e.event_labels.is_injection_origin)
    commit.hidden['briefing']['value']=facts['record']['clean']
    assert not validate_trace(bad).admitted


def save(root, trace, name):
    p=root/'traces'/f'{name}_trace.json';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(trace.to_dict()))


def test_analysis_nonpropagation_and_baseline_matching(tmp_path):
    from benchmark.propagation_analysis import PropagationAnalyzer
    trace,_=run(tmp_path/'p')
    clean,_=run(tmp_path/'c',True)
    for i in range(3):
        c=deepcopy(clean);c.trace_id=f'clean-{i}';c.metadata['repetition_index']=i
        save(tmp_path,c,f'clean-{i}')
    save(tmp_path,trace,'perturbed')
    analyzer=PropagationAnalyzer(tmp_path)
    result=analyzer.analyze()
    assert result['overall']['valid_admitted_perturbed_traces']==1
    assert result['overall']['analysis_failures']==0, analyzer.attempts
    assert result['overall']['measurable_propagation_outcomes']==1
    assert result['overall']['propagation_probability']==0
    metric=json.loads((tmp_path/'propagation_metrics.jsonl').read_text())
    assert metric['protocol_lifecycle']['exposure_event_id']
    # The scripted coordinator ignores the briefing. Receiving ordinary fanout
    # must not count as receiving the altered proposition or propagating it.
    assert metric['protocol_lifecycle']['downstream_behavioral_event_ids']==[]


def test_ordering_and_real_input_evidence(tmp_path):
    trace,_=run(tmp_path)
    facts=evidence(trace)
    bad=deepcopy(trace)
    next(e for e in bad.events if e.event_id==facts['coordinator_anchor_event_id']).input_text='Task only'
    assert not validate_trace(bad).admitted
    bad=deepcopy(trace)
    next(e for e in bad.events if e.event_id==facts['exposure_event_id']).depends_on=[]
    assert not validate_trace(bad).admitted


def test_no_private_metadata_in_workspace_or_inputs(tmp_path):
    trace,backend=run(tmp_path)
    visible=json.loads(next(tmp_path.glob('ws_*/manifest.json')).read_text())
    assert 'memory_protocol' not in visible and 'attack' not in visible and 'seeds' not in visible
    for prompt in backend.prompts:
        for token in ('mutation_evidence','clean_sha256','introduction_actor','existing_record_poisoning_v1'):
            assert token not in prompt


class ForwardingBackend(ResearchBackend):
    """Observable scripted behavior: forward the briefing, never read its namespace."""
    def reset(self, **kwargs):
        super().reset(**kwargs)
        system=kwargs['system_prompt']
        marker='Source: documents/paper_a.md\n'
        if marker in system:
            self.excerpt=system.split(marker,1)[1]
        self.is_coordinator=kwargs['agent_name']=='coordinator'
        # Workers copy only text they will receive in their prompt, not hidden config.
        self.excerpt=getattr(self,'excerpt','')

    def generate(self, prompt='', tool_choice=None):
        if not self.is_coordinator:
            assert self.excerpt in prompt, 'Worker did not receive coordinator handoff'
        self.calls=[(name, {**args, **({'summary':self.excerpt} if 'summary' in args else {}),
                          **({'content':self.excerpt} if 'content' in args else {}),
                          **({'value':self.excerpt} if 'value' in args else {})}) for name,args in self.calls]
        return super().generate(prompt,tool_choice)


def test_downstream_exposure_and_behavior_are_real(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules[__name__], 'ResearchBackend', ForwardingBackend)
    from benchmark.propagation_analysis import PropagationAnalyzer
    trace,_=run(tmp_path/'p')
    clean,_=run(tmp_path/'c',True)
    assert validate_trace(trace).admitted
    for i in range(3):
        c=deepcopy(clean);c.trace_id=f'clean-{i}';c.metadata['repetition_index']=i
        save(tmp_path,c,f'clean-{i}')
    save(tmp_path,trace,'perturbed')
    result=PropagationAnalyzer(tmp_path).analyze()
    assert result['overall']['analysis_failures']==0
    assert result['overall']['propagation_probability']==1
    metric=json.loads((tmp_path/'propagation_metrics.jsonl').read_text())
    assert metric['num_downstream_agents']>=2
    assert metric['protocol_lifecycle']['coordinator_behavioral_event_ids']
    assert metric['protocol_lifecycle']['worker_handoff_exposure_event_ids']


def test_detector_graph_features_do_not_encode_treatment(tmp_path):
    from generation.event_graph_builder import DependsOnGraphBuilder
    from schemas.event_labels import EventLabels
    trace,_=run(tmp_path)
    b=DependsOnGraphBuilder()
    original=b.build(trace,'coordinator_workers','research_synthesis')
    scrubbed=deepcopy(trace)
    for e in scrubbed.events:
        e.hidden={};e.event_labels=EventLabels()
    clean=b.build(scrubbed,'coordinator_workers','research_synthesis')
    assert original.node_features.equal(clean.node_features)
    assert original.edge_features.equal(clean.edge_features)


@pytest.mark.parametrize('entry', [e for e in build_plan() if e['condition']=='single_lep'],
                         ids=lambda e:e['fixture_id'])
def test_all_ten_scripted_injections(tmp_path, entry):
    runner=BenchmarkRunner(BenchmarkManifest(fixture_root=FIXTURES,output_dir=tmp_path,dry_run=False),
                           llm_backend=ResearchBackend('coordinator_workers',skip_write=True))
    result=runner._execute(entry)
    assert result.dataset_eligible, result.error
    assert len(result.injection_event_ids)==1


def test_protocol_pooling_is_rejected(tmp_path):
    from benchmark.propagation_analysis import PropagationAnalyzer
    from test_injection_reachability import run_research
    new,_=run(tmp_path/'new')
    old=run_research(tmp_path/'old').trace
    save(tmp_path,new,'new');save(tmp_path,old,'old')
    with pytest.raises(ValueError,match='pooling'):
        PropagationAnalyzer(tmp_path).analyze()


def test_wrong_control_mode_cannot_supply_baseline(tmp_path):
    from benchmark.propagation_analysis import PropagationAnalyzer
    trace,_=run(tmp_path/'p')
    clean,_=run(tmp_path/'c',True)
    clean.metadata['propagation_mode']='single_origin'
    clean.metadata['admission_contract']['scenario']['workflow_config']['propagation_mode']='single_origin'
    save(tmp_path,trace,'p')
    for i in range(3):
        c=deepcopy(clean);c.trace_id=f'clean-{i}';c.metadata['repetition_index']=i
        save(tmp_path,c,f'c{i}')
    summary=PropagationAnalyzer(tmp_path).analyze()['overall']
    assert summary['valid_admitted_perturbed_traces']==1
    assert summary['measurable_propagation_outcomes']==0
    assert summary['propagation_probability'] is None


def test_public_events_exclude_private_protocol_evidence(tmp_path):
    trace,_=run(tmp_path)
    public=[e.to_observable_dict() for e in trace.events]
    text=json.dumps(public)
    for token in ('mutation_evidence','clean_sha256','before_sha256','briefing_input_sha256',
                  'introduction_actor','is_injection_origin','controlled_injection'):
        assert token not in text


def test_pilot_manifest_drift_fails_before_execution(tmp_path):
    entry=deepcopy(build_plan()[-1]);entry['fixture_manifest_sha256']='forged'
    backend=ResearchBackend('coordinator_workers',skip_write=True)
    runner=BenchmarkRunner(BenchmarkManifest(fixture_root=FIXTURES,output_dir=tmp_path,dry_run=False),
                           llm_backend=backend)
    result=runner._execute(entry)
    assert not result.dataset_eligible
    assert 'manifest changed' in result.error
    assert not backend.visits


def test_new_subtype_round_trip_and_noop_admission(tmp_path):
    from schemas import ScenarioSpec
    entry=build_plan()[-1]
    runner=BenchmarkRunner(BenchmarkManifest())
    s=ScenarioSpec(scenario_id='x',fixture_id=entry['fixture_id'],task_family='research_synthesis',
                   task_variant='scifact_cached_briefing_v1',workflow_config=runner._build_workflow_config(entry))
    assert ScenarioSpec.from_dict(s.to_dict()).workflow_config.memory_protocol==PROTOCOL
    trace,_=run(tmp_path)
    init=next(e for e in trace.events if e.hidden.get('briefing',{}).get('phase')=='initialization')
    init.hidden['briefing']['record']['changed']=init.hidden['briefing']['record']['clean']
    assert validate_trace(trace).reason=='ineffective_mutation'


def test_native_backend_receives_same_briefing_before_first_model_turn(tmp_path):
    class NativeBackend(ResearchBackend):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            self._messages=[{'role':'system','content':kwargs['system_prompt']}]
        def _append_tool_result(self,*args,**kwargs):
            pass
    trace,_=run(tmp_path,backend=NativeBackend('coordinator_workers',skip_write=True))
    assert validate_trace(trace).admitted
    assert evidence(trace)['coordinator_anchor_event_id']


def test_anonymous_detector_export(tmp_path):
    from generation.existing_record_protocol import detector_events
    trace,_=run(tmp_path)
    exported=detector_events(trace)
    assert len(exported)==len(trace.events)
    for event in exported:
        assert not {'trace_id','hidden','event_labels','provenance_ids'} & event.keys()
    assert trace.trace_id not in json.dumps(exported)


def test_complete_forty_attempt_plan_offline(tmp_path, monkeypatch):
    from benchmark.propagation_analysis import PropagationAnalyzer
    manifest=BenchmarkManifest(fixture_root=FIXTURES,output_dir=tmp_path,dry_run=False,
        num_benign_repetitions=3,num_repetitions=1)
    runner=BenchmarkRunner(manifest)
    monkeypatch.setattr(runner,'_default_backend',lambda:ResearchBackend('coordinator_workers',skip_write=True))
    runner.run(plan=build_plan())
    summary=PropagationAnalyzer(tmp_path).analyze()
    overall=summary['overall']
    assert summary['clean_reference_admission']['admitted']==30
    assert overall['started_perturbed_attempts']==10
    assert overall['valid_admitted_perturbed_traces']==10
    assert overall['planned_perturbed_slots']==10
    assert overall['any_valid_cell_coverage']==1
    assert overall['analysis_coverage']==1
    assert overall['analysis_failures']==0
    assert overall['propagation_probability']==0
