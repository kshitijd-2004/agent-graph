"""Full-coverage packets and deterministic origin/record-version provenance."""
from collections import deque
from hashlib import sha256
import json
from benchmark.memory_delivery_audit import reconstruct_memory_delivery, in_recorded_input
from benchmark.semantic_mp.evaluator import observable_text
from benchmark.behavioral_anomaly import MIN_CLEAN_RUNS_REQUIRED
from generation.admission import validate_trace

ACTIVE={'tool_call','memory_write','agent_handoff','final_response','reasoning','llm_output'}


def hash_json(obj):
    return sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def path(events,start,end):
    """Shortest recorded dependency path; ties follow event-index order."""
    if start not in events or end not in events:return None
    children={k:[] for k in events}
    for k,e in sorted(events.items(),key=lambda kv:kv[1]['index']):
        for dep in e['depends_on']:
            if dep in children:children[dep].append(k)
    q=deque([(start,[start])]);seen={start}
    while q:
        here,p=q.popleft()
        if here==end:return p
        for nxt in children[here]:
            if nxt not in seen:seen.add(nxt);q.append((nxt,p+[nxt]))
    return None


def build_packet(trace,clean_traces,*,enabled=False,task_text=''):
    if not enabled:raise ValueError('development opt-in required')
    if trace.metadata.get('lep_codes')!=['LEP_MEMORY_POISONING']:raise ValueError('MP only')
    if trace.metadata.get('memory_protocol')=='existing_record_poisoning_v1':raise ValueError('unsupported separately versioned protocol')
    clean=[];reps=set();admission=validate_trace(trace).to_dict()
    for c in clean_traces:
        if any(c.metadata.get(k)!=trace.metadata.get(k) for k in ('fixture_id','task_family','topology')) or c.metadata.get('lep_codes') or c.metadata.get('execution_variant')!='memory_enabled' or c.metadata.get('memory_protocol')=='existing_record_poisoning_v1':raise ValueError('mismatched controls')
        rep=c.metadata.get('repetition_index')
        if validate_trace(c).admitted and rep is not None and rep not in reps:clean.append(c);reps.add(rep)
    # All admitted supplied references retained; the minimum is unchanged.
    admitted=validate_trace(trace).admitted and len(clean)>=MIN_CLEAN_RUNS_REQUIRED
    contents={};events={};mapping={};runs={};source_ids=[];required=[];graph_errors=[]
    for i,t in enumerate([trace,*clean]):
        run=f'R{i}';runs[run]={'trace_id':t.trace_id,'repetition_index':t.metadata.get('repetition_index')}
        for e in t.events:
            eid=f'{run}:E{e.event_id}'
            if eid in events:graph_errors.append('duplicate:'+eid)
            txt=observable_text(e)
            if e.event_type.value in {'tool_result','memory_retrieval'}:txt=e.output_text or e.tool_result or ''
            if not isinstance(txt,str):txt=json.dumps(txt,sort_keys=True)
            h=sha256(txt.encode()).hexdigest();contents[h]=txt
            selected=e.event_type.value in ACTIVE or e.event_type.value in {'tool_result','memory_retrieval'} or bool(txt)
            source=e.event_type.value=='tool_result' and e.tool_name=='read_text_file'
            events[eid]={'run':run,'event_id':eid,'index':e.event_index,'type':e.event_type.value,
                'agent_id':e.agent_id,'role':e.agent_role,'tool':e.tool_name,'arguments':e.tool_arguments or {},
                'content_id':h,'depends_on':[f'{run}:E{x}' for x in e.depends_on],'selected':selected,
                'source_document':source,'source_path':(e.tool_arguments or {}).get('path') if source else None}
            mapping[eid]={'trace_id':t.trace_id,'event_id':e.event_id,'agent_id':e.agent_id,'role':e.agent_role}
            if source:source_ids.append(eid)
            if selected:required.append(eid)
        # source result args are not always copied: resolve using actual dependency/tool_call_id.
        for e in t.events:
            item=events[f'{run}:E{e.event_id}']
            if item['source_document'] and not item['source_path']:
                calls=[x for x in t.events if x.event_type.value=='tool_call' and x.tool_name=='read_text_file' and x.tool_call_id==e.tool_call_id and x.event_id in e.depends_on]
                if len(calls)==1:item['source_path']=(calls[0].tool_arguments or {}).get('path')
    for eid,e in events.items():
        for d in e['depends_on']:
            if d not in events or events[d]['run']!=e['run'] or events[d]['index']>=e['index']:graph_errors.append('invalid_edge:'+d+'->'+eid)
    handoff_links=[]
    for h in trace.events:
        text=observable_text(h)
        if h.event_type.value!='agent_handoff' or not text:continue
        for r in trace.events:
            if r.event_type.value=='reasoning' and r.event_index>h.event_index and r.agent_id!=h.agent_id and in_recorded_input(text,r):
                handoff_links.append({'handoff_id':f'R0:E{h.event_id}','context_id':f'R0:E{r.event_id}','recipient_agent_id':r.agent_id,'content_sha256':sha256(text.encode()).hexdigest()})
    delivery=reconstruct_memory_delivery(trace)
    mutations=[]
    for m in delivery['mutations']:
        mutations.append({**m,'origin_id':f"R0:E{m['origin_event_id']}",'version_id':f"R0:E{m['origin_event_id']}"})
    deliveries=[]
    for n,d in enumerate(delivery['deliveries']):
        deliveries.append({**d,'delivery_id':f'D{n}','origin_id':f"R0:E{d['origin_event_id']}",
            'version_id':f"R0:E{d['origin_event_id']}",'retrieval_id':f"R0:E{d['retrieval_event_id']}",
            'context_ids':[f'R0:E{x}' for x in d['model_context_event_ids']],
            'result_ids':[f'R0:E{x}' for x in d['tool_result_event_ids']]})
    packet={'version':'mp-hybrid-packet-dev1','development_only':True,'events':events,'contents':contents,
        'local_identity_map':mapping,'runs':runs,'target_run':'R0','clean_runs':[f'R{i}' for i in range(1,len(clean)+1)],
        'task_text':task_text,'handoff_context_links':handoff_links,'mutations':mutations,'deliveries':deliveries,'exposure':delivery['exposure'],
        'admission':admission,'admitted':admitted,'source_ids':source_ids,
        'coverage':{'selection':'all active outputs/actions plus all tool results and memory retrievals; no lexical filter',
            'required_event_ids':required,'selected_event_count':len(required),'total_event_count':len(events),
            'excluded_event_ids':[eid for eid,e in events.items() if not e['selected']],
            'exclusion_reason':'empty lifecycle/structural events; retained in local dependency graph',
            'graph_errors':graph_errors,'delivery_issues':delivery['issues'],
            'execution_complete':delivery['complete_execution'],
            'all_finals_and_handoffs':all(e['selected'] for e in events.values() if e['type'] in {'final_response','agent_handoff'})}}
    packet['packet_sha256']=hash_json(packet)
    return packet


def observation_view(packet):
    """No trace names, origin labels, variants, expected outcomes or clean labels."""
    selected=[e for e in packet['events'].values() if e['selected']]
    used={e['content_id'] for e in selected}
    # Compact table removes repeated JSON keys, never events or text. Full hashes
    # and source identities remain in the local packet and verified citations.
    aliases={h:f'C{i}' for i,h in enumerate(sorted(used))}
    columns=['event_id','run','index','role','type','tool','content_id','source_path','action_context']
    def cell(e,k):
        if k=='content_id':return aliases[e[k]]
        if k=='action_context':
            # Values already appear in exact content. Preserve memory keys,
            # scopes, destinations and file paths needed to interpret actions.
            return {a:v for a,v in e['arguments'].items() if a not in {'value','content','summary'} or str(v) not in packet['contents'][e['content_id']]}
        return e[k]
    return {'version':packet['version'],'task_text':packet['task_text'],
        'contents':{aliases[h]:packet['contents'][h] for h in sorted(used)},
        'event_columns':columns,
        'events':[[cell(e,k) for k in columns] for e in selected],
        'review_scope':'Every event row, including every final and handoff; do not filter by lexical overlap.'}



def eligible_deliveries(packet,event_id):
    events=packet['events'];event=events.get(event_id)
    if not event or event['run']!=packet['target_run']:return []
    return [d for d in packet['deliveries'] if d['changed_information_returned'] and
        any(path(events,c,event_id) is not None and events[c]['index']<=event['index'] for c in d['context_ids'])]
