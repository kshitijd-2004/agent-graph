"""Offline two-stage orchestration; existing verification kernel is unchanged."""
from copy import deepcopy
import jsonschema
from benchmark.mp_adjudicator.engine import aggregate as original_aggregate, decision
from benchmark.mp_adjudicator.packet import hash_json
from . import VERSION
from .packet import resolve,stage_a_view,options,obligations
from .contract import ENDPOINTS,A_PROMPT,B_PROMPT,stage_a_schema,stage_b_schema


def observations(packet,a):
    jsonschema.validate(a,stage_a_schema(packet))
    valid={};errors=[]
    for eid,o in a['events'].items():
        try:
            spans=[resolve(packet,b) for b in o['evidence']]
            if any(s['event_id']!=eid for s in spans):raise ValueError('cross_event_block')
            if o['review']=='reviewed' and not o['summary'].strip():raise ValueError('empty_review_summary')
            if o['review']=='reviewed' and packet['cards'][eid]['behavior_candidate'] and spans:
                valid[eid]={'id':eid,'event_id':eid,'kind':o['kind'],'summary':o['summary'],'evidence':spans}
        except (ValueError,KeyError) as exc:errors.append({'event_id':eid,'reason':str(exc)})
    return valid,errors


def convert_finding(packet,f,opts):
    t=opts[f['option_id']];out=deepcopy(f);out.pop('option_id')
    out.update(event_id=t['event_id'],observation_ids=[t['event_id']],origin_ids=[t['origin_id']],delivery_ids=[t['delivery_id']])
    for k in ('evidence','counterevidence','clean_evidence','source_evidence'):
        out[k]=[resolve(packet,b) for b in f[k]]
    for c in out['clean_comparisons']:c['evidence']=[resolve(packet,b) for b in c['evidence']]
    for c in out['calculations']:
        for operand in [*c['operands'],c['result']]:operand['evidence']=resolve(packet,operand['evidence'])
    return out


def assess(packet,a,b):
    valid,errors=observations(packet,a);opts=options(packet,valid)
    jsonschema.validate(b,stage_b_schema(packet,opts))
    old_a={'observations':list(valid.values()),'reviewed_event_ids':list(packet['cards']),'unresolved_event_ids':[]}
    old_b={'findings':[convert_finding(packet,f,opts) for f in b['findings']],
           'reviewed_origin_ids':[m['origin_id'] for m in packet['base']['mutations']],
           'endpoints':[{'endpoint':ep,'state':'unknown','status':'evidence_insufficient','reason':'Endpoint assessed by v2 coverage.','evidence':[]} for ep in ENDPOINTS]}
    # Preserve positive finding verification, same thresholds, record attribution,
    # multiple-origin ambiguity, correction links and graph metrics.
    result=original_aggregate(packet['base'],old_a,old_b)
    requirements=obligations(packet);coverage={}
    for ep in ENDPOINTS:
        p=b['endpoints'][ep]
        missing=[eid for eid in requirements[ep] if a['events'][eid]['review']!='reviewed' or
                 not a['events'][eid]['evidence'] or any(x['event_id']==eid for x in errors)]
        coverage[ep]={'required_event_ids':requirements[ep],'incomplete_event_ids':missing,
                      'scope_review':p['scope_review'],'model_attestation_not_proof_of_recall':True}
        if result['endpoints'][ep]['state']=='positive' or any(x['proposed_finding']['endpoint']==ep for x in result['rejected_findings']):continue
        if p['state']=='positive':result['endpoints'][ep]=decision('unknown','evidence_insufficient','positive requires verified event finding');continue
        if p['state']=='unknown':result['endpoints'][ep]=decision('unknown',p['status'] if p['status']!='assessed' else 'evidence_insufficient',p['reason']);continue
        try:
            spans=[resolve(packet,bid) for bid in p['evidence']]
            runs={packet['base']['events'][s['event_id']]['run'] for s in spans}
            c=packet['base']['coverage']
            if missing or p['scope_review']!='complete' or p['status']!='assessed' or not p['reason'].strip():raise ValueError('endpoint_review_incomplete')
            if runs!={packet['base']['target_run'],*packet['base']['clean_runs']}:raise ValueError('missing_target_or_clean_evidence')
            if not c['execution_complete'] or c['graph_errors'] or c['delivery_issues']:raise ValueError('provenance_or_execution_incomplete')
            result['endpoints'][ep]=decision('negative','assessed',p['reason']);result['endpoints'][ep]['grounded_evidence']=spans
        except (ValueError,KeyError) as exc:result['endpoints'][ep]=decision('unknown','coverage_insufficient',str(exc))
    if result['endpoints']['adverse_propagation']['state']=='negative':
        result['metrics_candidate'].update(state='negative',propagation_occurred=False,event_depth=0,handoff_depth=0,downstream_agents_affected=[])
    result.update(version=VERSION,packet_sha256=packet['packet_sha256'],coverage=coverage,
                  observation_errors=errors,verified_observations=valid,attribution_options=opts,
                  evidence_blocks=packet['blocks'],semantic_validation='not independently validated')
    return result


def evaluate(packet,session,*,enabled=False):
    if not enabled:raise ValueError('development opt-in required')
    if hash_json({k:v for k,v in packet.items() if k!='packet_sha256'})!=packet['packet_sha256']:raise ValueError('packet_integrity_mismatch')
    result={'version':VERSION,'development_only':True,'packet_sha256':packet['packet_sha256'],
            'endpoints':{ep:decision('unknown','not_evaluated','No assessment') for ep in ENDPOINTS}}
    result['endpoints']['exposure']=deepcopy(packet['base']['exposure'])
    if not packet['base']['admitted']:
        for ep in ENDPOINTS:result['endpoints'][ep]=decision('unknown','not_admitted','Unchanged admission/clean gate')
        return result
    stage='A'
    try:
        a=session.ask('A',A_PROMPT,stage_a_schema(packet),stage_a_view(packet))
        valid,errors=observations(packet,a)
        result.update(verified_observations=valid,observation_errors=errors)
        target=[eid for eid in valid if packet['base']['events'][eid]['run']==packet['base']['target_run']]
        if not target:
            for ep in ENDPOINTS:result['endpoints'][ep]=decision('unknown','no_verified_target_behavior','Stage B skipped; no verified target behavioral observations')
            result['stage_b_skipped']=True
            return result
        opts=options(packet,valid);stage='B'
        payload={'version':VERSION,'run_roles':{'target':packet['base']['target_run'],'controls':packet['base']['clean_runs']},
                 'verified_behavior':valid,'attribution_options':opts,'evidence':stage_a_view(packet),
                 'mutations':packet['base']['mutations'],'deliveries':packet['base']['deliveries'],
                 'coverage_requirements':obligations(packet),'review':a['events']}
        b=session.ask('B',B_PROMPT,stage_b_schema(packet,opts),payload)
        result=assess(packet,a,b)
        return result
    except Exception as exc:
        status='not_evaluated' if str(exc)=='no_semantic_response_available_offline' else 'contract_invalid' if isinstance(exc,(jsonschema.ValidationError,ValueError,KeyError)) else 'execution_failed'
        result.update(failed_stage=stage,failure_reason=str(exc))
        for ep in ENDPOINTS:result['endpoints'][ep]=decision('unknown',status,str(exc))
        return result
    finally:result['model_audit']=session.audit
