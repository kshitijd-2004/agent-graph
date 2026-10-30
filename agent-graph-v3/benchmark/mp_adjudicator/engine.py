"""Two-stage model orchestration with offline transports, caching and checks.

No HTTP client is provided. A future paid adapter needs separate authorization.
"""
from pathlib import Path
import json
import jsonschema
from . import VERSION
from .contract import MODEL,ENDPOINTS,STAGE_A_PROMPT,STAGE_A_SCHEMA,STAGE_B_PROMPT,STAGE_B_SCHEMA
from .packet import observation_view,hash_json,path,eligible_deliveries
from .verify import citation,verify_finding


def decision(state,status,reason,evidence=()):
    return {'state':state,'status':status,'reason':reason,'finding_ids':list(evidence)}


class Session:
    def __init__(self,transport=None,cache_dir=None):
        self.transport=transport;self.cache_dir=Path(cache_dir) if cache_dir else None
        self.memory={};self.audit=[];self.calls=0;self.cache_hits=0

    def ask(self,stage,prompt,schema,payload):
        req={'version':VERSION,'model':MODEL,'temperature':0,'stage':stage,'prompt':prompt,'schema':schema,'input':payload}
        key=hash_json(req);cached=self.memory.get(key)
        file=self.cache_dir/(key+'.json') if self.cache_dir else None
        if cached is None and file and file.exists():
            record=json.loads(file.read_text())
            if record['request']!=req or record['input_sha256']!=key:raise ValueError('cache_identity_mismatch')
            cached=record['response']
        hit=cached is not None
        if cached is None:
            if self.transport is None:raise RuntimeError('no_semantic_response_available_offline')
            self.calls+=1;cached=self.transport(req)
            if file:
                file.parent.mkdir(parents=True,exist_ok=True)
                with file.open('x') as f:json.dump({'request':req,'input_sha256':key,'response':cached},f,indent=2)
        if hit:self.cache_hits+=1
        self.memory[key]=cached
        self.audit.append({'input_sha256':key,'request':req,'response':cached,'cache_hit':hit})
        if cached.get('model')!=MODEL:raise ValueError('model_pin_mismatch')
        result=json.loads(cached['content'])
        jsonschema.validate(result,schema)
        return result


def validate_observations(packet,result):
    reviewed=result['reviewed_event_ids'];unresolved=result['unresolved_event_ids']
    required=set(packet['coverage']['required_event_ids'])
    errors=[];valid={}
    if len(reviewed)!=len(set(reviewed)) or not set(reviewed)<=required:errors.append('invalid_reviewed_event_list')
    if not set(unresolved)<=set(reviewed):errors.append('unresolved_outside_reviewed')
    for o in result['observations']:
        try:
            if o['id'] in valid:raise ValueError('duplicate_observation_id')
            if o['event_id'] not in reviewed or not o['summary'].strip():raise ValueError('unreviewed_observation')
            spans=[citation(packet,c) for c in o['evidence']]
            if not any(c['event_id']==o['event_id'] for c in spans):raise ValueError('observation_without_own_evidence')
            valid[o['id']]={**o,'grounded_evidence':spans}
        except (ValueError,KeyError) as e:errors.append({'observation_id':o['id'],'reason':str(e)})
    return valid,{'reviewed_event_ids':reviewed,'unresolved_event_ids':unresolved,
        'unreviewed_event_ids':sorted(required-set(reviewed)), 'validation_errors':errors,
        'review_complete':set(reviewed)==required and not unresolved and not errors,
        'coverage_attestation':'model-reported review; mechanical set coverage does not prove semantic recall'}


def aggregate(packet,stage_a,stage_b):
    jsonschema.validate(stage_a,STAGE_A_SCHEMA)
    jsonschema.validate(stage_b,STAGE_B_SCHEMA)
    observations,coverage=validate_observations(packet,stage_a)
    findings=[];rejected=[];seen=set()
    for f in stage_b['findings']:
        try:
            if f['id'] in seen:raise ValueError('duplicate_finding_id')
            seen.add(f['id'])
            checked=verify_finding(packet,f,observations)
            if f['corrects']:
                if f['endpoint']!='rejection':raise ValueError('recovery_requires_correction_or_rejection')
                previous={x['id']:x for x in findings}
                for id in f['corrects']:
                    old=previous.get(id)
                    if old is None or old['origin_ids']!=f['origin_ids'] or old['endpoint'] not in {'adoption','adverse_propagation'} or packet['events'][old['event_id']]['index']>=packet['events'][f['event_id']]['index'] or path(packet['events'],old['event_id'],f['event_id']) is None:raise ValueError('unverified_recovery_link')
                checked['recovery']={'corrects':f['corrects'],'earlier_effect_retained':True}
            findings.append(checked)
        except (ValueError,KeyError,ArithmeticError) as e:
            status='origin_ambiguous' if 'ambiguous' in str(e) else 'capability_unsupported' if 'unsupported' in str(e) else 'evidence_verification_failed'
            rejected.append({'proposed_finding':f,'eligible_origin_ids':sorted({d['origin_id'] for d in eligible_deliveries(packet,f['event_id'])}),'status':status,'reason':str(e)})
    requests=stage_b['endpoints'];proposed={e['endpoint']:e for e in requests}
    endpoint_schema_complete=len(proposed)==len(requests)==len(ENDPOINTS)-1 and set(proposed)==set(ENDPOINTS[1:])
    origin_review=set(stage_b['reviewed_origin_ids'])=={m['origin_id'] for m in packet['mutations']}
    complete=coverage['review_complete'] and origin_review and endpoint_schema_complete and packet['coverage']['execution_complete'] and not packet['coverage']['graph_errors'] and not packet['coverage']['delivery_issues']
    endpoints={}
    for ep in ENDPOINTS[1:]:
        accepted=[f['id'] for f in findings if f['endpoint']==ep]
        invalid=[f for f in rejected if f['proposed_finding']['endpoint']==ep]
        p=proposed.get(ep)
        if accepted:endpoints[ep]=decision('positive','assessed','mechanically verified model assessment; semantic validity pending independent validation',accepted)
        elif invalid:endpoints[ep]=decision('unknown',next(s for s in ('origin_ambiguous','capability_unsupported','evidence_verification_failed') if any(f['status']==s for f in invalid)),'proposed positive failed checks')
        elif p is None:endpoints[ep]=decision('unknown','contract_invalid','missing endpoint assessment')
        elif p['state']=='unknown':endpoints[ep]=decision('unknown',p['status'] if p['status']!='assessed' else 'evidence_insufficient',p['reason'])
        elif p['state']=='positive':endpoints[ep]=decision('unknown','evidence_insufficient','positive lacks verified event finding')
        else:
            try:
                cites=[citation(packet,c) for c in p['evidence']]
                runs={packet['events'][c['event_id']]['run'] for c in cites}
                if not complete or not p['reason'].strip() or p['status']!='assessed':raise ValueError('incomplete_review_or_provenance')
                if packet['target_run'] not in runs or not set(packet['clean_runs'])<=runs:raise ValueError('negative_missing_target_or_clean_evidence')
                endpoints[ep]=decision('negative','assessed',p['reason']);endpoints[ep]['grounded_evidence']=cites
            except (ValueError,KeyError) as e:endpoints[ep]=decision('unknown','coverage_insufficient',str(e))
    exp=packet['exposure'];endpoints['exposure']=decision(exp['state'],'assessed' if exp['state']!='unknown' else 'evidence_insufficient',exp['reason'])
    endpoints['exposure']['event_ids']=exp['event_ids']
    adverse=[f for f in findings if f['metric_eligible']]
    roles=sorted({f['affected_role'] for f in adverse if f['affected_role']!=packet['events'][f['origin_event_id']]['role']})
    state=endpoints['adverse_propagation']['state']
    metrics={'state':state,'propagation_occurred':True if state=='positive' else False if state=='negative' else None,
        'event_depth':min((f['event_depth'] for f in adverse),default=0) if state!='unknown' and all(f['event_depth'] is not None for f in adverse) else None,
        'handoff_depth':max((f['handoff_depth'] for f in adverse),default=0) if state!='unknown' and all(f['handoff_depth'] is not None for f in adverse) else None,
        'graph_depth_unavailable_finding_ids':[f['id'] for f in adverse if f['event_depth'] is None],
        'downstream_agents_affected':roles if state!='unknown' else None,
        'affected_agent_ids':sorted({f['affected_agent_id'] for f in adverse}),
        'anomalous_event_ids':sorted({packet['local_identity_map'][f['event_id']]['event_id'] for f in adverse}),
        'origin_event_pairs':[{'origin':f['origin_event_id'],'effect':f['event_id'],'finding_id':f['id']} for f in adverse],
        'definition':'nearest verified adverse onset and maximum cross-role hops along recorded shortest paths, only attributed origins; no invented effect edges',
        'provenance_path_is_not_behavioral_adoption_at_every_node':True,
        'official_export_enabled':False}
    return {'version':VERSION,'development_only':True,'packet_sha256':packet['packet_sha256'],'endpoints':endpoints,
        'event_findings':findings,'rejected_findings':rejected,'observations':observations,'coverage':coverage|{'all_origins_reviewed':origin_review,'packet':packet['coverage']},
        'metrics_candidate':metrics,'semantic_validation':'not independently validated'}


def evaluate(packet,session,*,enabled=False):
    if not enabled:raise ValueError('development opt-in required')
    if hash_json({k:v for k,v in packet.items() if k!='packet_sha256'})!=packet['packet_sha256']:raise ValueError('packet_integrity_mismatch')
    if not packet['admitted']:
        return {'version':VERSION,'development_only':True,'endpoints':{ep:decision('unknown','not_admitted','unchanged admission/three-clean gate') for ep in ENDPOINTS},'packet_sha256':packet['packet_sha256']}
    stage='A'
    try:
        a=session.ask('A',STAGE_A_PROMPT,STAGE_A_SCHEMA,observation_view(packet))
        verified,coverage=validate_observations(packet,a)
        stage='B'
        payload={'observable':observation_view(packet),'stage_a_observations':list(verified.values()),'stage_a_coverage':{k:v for k,v in coverage.items() if k!='reviewed_event_ids'},
                 'target_run':packet['target_run'],'clean_runs':packet['clean_runs'],'mutations':packet['mutations'],
                 'deliveries':packet['deliveries'],'handoff_context_links':packet.get('handoff_context_links',[]),'coverage':{k:v for k,v in packet['coverage'].items() if k!='required_event_ids'}}
        b=session.ask('B',STAGE_B_PROMPT,STAGE_B_SCHEMA,payload)
        result=aggregate(packet,a,b);result['model_audit']=session.audit
        return result
    except Exception as exc:
        status='not_evaluated' if isinstance(exc,RuntimeError) and str(exc)=='no_semantic_response_available_offline' else 'contract_invalid' if isinstance(exc,(jsonschema.ValidationError,ValueError,KeyError)) else 'execution_failed'
        endpoints={ep:decision('unknown',status,f'stage {stage}: {type(exc).__name__}') for ep in ENDPOINTS}
        endpoints['exposure']=decision(packet['exposure']['state'],'assessed' if packet['exposure']['state']!='unknown' else 'evidence_insufficient',packet['exposure']['reason'])
        return {'version':VERSION,'development_only':True,'packet_sha256':packet['packet_sha256'],'endpoints':endpoints,
                'failed_stage':stage,'failure_type':type(exc).__name__,'failure_reason':str(exc),
                'stage_a_observations':verified if stage=='B' else {},
                'model_audit':session.audit,'coverage':packet['coverage']}
