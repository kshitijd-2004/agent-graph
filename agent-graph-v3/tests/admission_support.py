"""Material execution evidence for synthetic grouping/admission tests."""
from generation.admission import contract_for, mutation_evidence
from generation.injection_origins import remap_task_roles
from generation.topology import get_topology
from collections import defaultdict
from schemas import TraceEvent, TraceEventType


def seal_trace(trace, scenario):
    trace.metadata.update(scenario_id=scenario.scenario_id, fixture_id=scenario.fixture_id,
        task_family=scenario.task_family, condition=scenario.condition,
        topology=scenario.workflow_config.topology, propagation_mode=scenario.workflow_config.propagation_mode,
        lep_codes=[c.code for c in scenario.lep_configs], termination_reason='completed',
        dataset_eligible=True, admission_contract=contract_for(scenario))
    topology = remap_task_roles(get_topology(scenario.workflow_config.topology, defaultdict(str)), scenario.task_family)
    final_role = topology.stage_by_id[topology.exit_stage].agent_role
    augmented = []
    for e in trace.events:
        if e.event_labels.is_injection_origin:
            code = scenario.lep_configs[0].code
            value = e.output_text or 'altered fact'
            name = 'write_memory' if code == 'LEP_MEMORY_POISONING' else 'read_text_file'
            args = {'key': 'findings', 'value': 'clean fact'} if name == 'write_memory' else {'path': 'documents/source.md'}
            call = TraceEvent(trace_id=trace.trace_id, event_id=e.event_id+'-call', event_index=0,
                timestamp='', event_type=TraceEventType.TOOL_CALL, agent_role=e.agent_role,
                tool_name=name, tool_call_id=e.event_id+'-tc', tool_arguments=args)
            augmented.append(call)
            e.event_type = TraceEventType.MEMORY_WRITE if name == 'write_memory' else TraceEventType.TOOL_RESULT
            e.tool_name, e.tool_call_id, e.depends_on = name, call.tool_call_id, [call.event_id]
            e.tool_arguments = {**args, 'value': value} if name == 'write_memory' else args
            e.tool_result = e.output_text = value
            e.hidden['lep_type'] = code
            mutation_evidence(e, code, 'clean fact', value)
            augmented.append(e)
            if name == 'write_memory':
                augmented.append(TraceEvent(trace_id=trace.trace_id, event_id=e.event_id+'-ack', event_index=0,
                    timestamp='', event_type=TraceEventType.TOOL_RESULT, agent_role=e.agent_role,
                    tool_name=name, tool_call_id=call.tool_call_id, depends_on=[call.event_id],
                    output_text='Memory record stored: findings'))
        else:
            augmented.append(e)
    if not augmented or augmented[-1].event_type != TraceEventType.FINAL_RESPONSE:
        augmented.append(TraceEvent(trace_id=trace.trace_id, event_id='terminal', event_index=0,
            timestamp='', event_type=TraceEventType.FINAL_RESPONSE, agent_role=final_role))
    augmented[-1].agent_role = final_role
    for i, e in enumerate(augmented):
        e.event_index = i
    trace.events = augmented
    return trace
