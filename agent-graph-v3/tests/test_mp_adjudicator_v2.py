"""Offline contract mechanics; recorded failures are not new model judgments."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
import jsonschema
from test_mp_adjudicator import synthetic,judgments
from benchmark.mp_adjudicator.engine import Session
from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator.packet import hash_json
from benchmark.mp_adjudicator_v2.packet import bind,resolve,stage_a_view,options
from benchmark.mp_adjudicator_v2.contract import ENDPOINTS,stage_a_schema,stage_b_schema
from benchmark.mp_adjudicator_v2.engine import observations,assess,evaluate

BASE=Path('analysis/mp_hybrid_smoke/smoke_001')

def fixtures():
    p=bind(synthetic());old_a,old_b=judgments(p['base'])
    a={'events':{eid:{'review':'reviewed','kind':'other','summary':'Observable content reviewed.',
                     'evidence':c['evidence_block_ids'][:1]} for eid,c in p['cards'].items()}}
    for o in old_a['observations']:a['events'][o['event_id']].update(kind=o['kind'],summary=o['summary'])
    valid,_=observations(p,a);opts=options(p,valid)
    f=deepcopy(old_b['findings'][0])
    for key in ('event_id','observation_ids','origin_ids','delivery_ids'):f.pop(key)
    f['option_id']=next(k for k,v in opts.items() if v['event_id']=='E')
    def block(c):return next(k for k,v in p['blocks'].items() if v['event_id']==c['event_id'] and v['text']==c['quote'])
    for key in ('evidence','counterevidence','clean_evidence','source_evidence'):f[key]=[block(c) for c in f[key]]
    for c in f['clean_comparisons']:c['evidence']=[block(x) for x in c['evidence']]
    b={'findings':[f],'endpoints':{ep:{'state':'unknown','status':'evidence_insufficient','reason':'Not evaluated.',
        'evidence':[],'scope_review':'incomplete'} for ep in ENDPOINTS}}
    return p,a,b


def failures():
    out=[]
    fixture=json.loads(Path('tests/fixtures/mp_adjudicator_v2/failed_smoke.json').read_text())
    for i,row in enumerate(fixture['cases']):
        a=row['stage_a']
        rejected={e['observation_id'] for e in row['errors'] if isinstance(e,dict) and e['reason']=='absent_quote'}
        for o in a['observations']:
            if o['id'] in rejected:out.append((i,o))
    return out


@pytest.mark.parametrize('index,old',failures(),ids=[f'{i}-{o["id"]}' for i,o in failures()])
def test_all_29_recorded_misbindings_unrepresentable(index,old):
    p=bind(json.loads((BASE/f'packet_{index}.json').read_text()))
    for c in old['evidence']:
        text=p['base']['contents'][p['base']['events'][c['event_id']]['content_id']]
        assert c['quote'] not in text
        assert not any(v['event_id']==c['event_id'] and v['text']==c['quote'] for v in p['blocks'].values())
    # No quote is moved to another event even if it occurs elsewhere.
    assert 'quote' not in stage_a_schema(p)['properties']['events']['properties'][old['event_id']]['properties']


def test_binding_integrity_and_no_intervention_labels():
    p,a,b=fixtures()
    for bid,block in p['blocks'].items():
        assert resolve(p,bid)['quote']==block['text']
        assert bid.startswith('B') and block['event_id'] in p['cards']
    bad=deepcopy(p);bid=next(iter(bad['blocks']));bad['blocks'][bid]['run']='wrong'
    with pytest.raises(ValueError):resolve(bad,bid)
    view=stage_a_view(p)
    assert all(k not in view for k in ('target_run','mutations','deliveries','clean_runs'))


def test_valid_positive_and_unrelated_gap():
    p,a,b=fixtures();a['events']['C2']['review']='unresolved'
    r=assess(p,a,b)
    assert r['endpoints']['adverse_propagation']['state']=='positive'
    assert r['event_findings'][0]['origin_event_id']=='O'
    assert r['event_findings'][0]['event_depth']==4


def test_recovery_retains_intermediate_harm():
    p,a,b=fixtures();valid,_=observations(p,a);opts=options(p,valid)
    f=deepcopy(b['findings'][0]);f.update(id='recovery',endpoint='rejection',
        option_id=next(k for k,v in opts.items() if v['event_id']=='F'),
        evidence=[p['cards'][k]['evidence_block_ids'][0] for k in ('F','H')],corrects=['bad'])
    b['findings'].append(f)
    r=assess(p,a,b)
    assert r['endpoints']['rejection']['state']=='positive'
    assert r['event_findings'][1]['recovery']['earlier_effect_retained']


def test_no_stage_b_without_behavior_and_new_cache_identity():
    p,a,b=fixtures()
    for v in a['events'].values():v['review']='unsupported'
    calls=[]
    def mocked(req):calls.append(req);return {'model':MODEL,'content':json.dumps(a)}
    r=evaluate(p,Session(mocked),enabled=True)
    assert len(calls)==1 and r['stage_b_skipped']
    assert all(r['endpoints'][ep]['state']=='unknown' for ep in ENDPOINTS)
    assert 'mp-hybrid-blocks-dev2' in calls[0]['prompt']


def test_clean_targets_and_invalid_options_excluded():
    p,a,b=fixtures();valid,_=observations(p,a);opts=options(p,valid)
    assert all(v['event_id'] not in {'C1','C2','C3'} for v in opts.values())
    b['findings'][0]['option_id']='R3:E75'
    with pytest.raises(jsonschema.ValidationError):assess(p,a,b)


def test_missing_endpoints_contract_failure_and_no_old_response_acceptance():
    p,a,b=fixtures();del b['endpoints']['rejection']
    with pytest.raises(jsonschema.ValidationError):assess(p,a,b)
    for row in map(json.loads,(BASE/'decisions.jsonl').read_text().splitlines()):
        old=json.loads(row['result']['model_audit'][0]['response']['content'])
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(old,stage_a_schema(p))


def test_negative_endpoint_specific_coverage():
    p,a,b=fixtures();b['findings']=[]
    for ep in ENDPOINTS:b['endpoints'][ep].update(state='negative',status='assessed',reason='Reviewed scope without attributable effect.',scope_review='complete',evidence=[p['cards'][k]['evidence_block_ids'][0] for k in ('F','C1','C2','C3')])
    a['events']['H']['review']='unresolved'
    r=assess(p,a,b)
    assert r['endpoints']['adverse_propagation']['state']=='unknown'
    assert r['endpoints']['final_answer_impact']['state']=='negative'


def test_correct_answer_equivalence_not_positive_impact():
    p,a,b=fixtures();f=b['findings'][0];f['endpoint']='final_answer_impact'
    for c in f['clean_comparisons']:c['relation']='equivalent'
    r=assess(p,a,b)
    assert r['endpoints']['final_answer_impact']['state']=='unknown'
    assert 'threshold' in r['rejected_findings'][0]['reason']


def test_multiple_identical_delivered_origins_stay_ambiguous():
    base=synthetic();base['mutations'].append(base['mutations'][0]|{'origin_id':'O2','version_id':'O2','record_id':['other','debt']})
    base['deliveries'].append(base['deliveries'][0]|{'delivery_id':'D2','origin_id':'O2','version_id':'O2','record_id':['other','debt']})
    p,a,b=fixtures();p=bind(base)
    r=assess(p,a,b)
    assert r['endpoints']['adverse_propagation']['status']=='origin_ambiguous'


def test_two_stage_offline_assessment():
    p,a,b=fixtures();s=Session(lambda r:{'model':MODEL,'content':json.dumps(a if r['stage']=='A' else b)})
    r=evaluate(p,s,enabled=True)
    assert s.calls==2 and r['endpoints']['adverse_propagation']['state']=='positive'
    assert len(r['model_audit'])==2


def test_cross_event_selection_and_unknown_block_fail_closed():
    p,a,b=fixtures()
    a['events']['E']['evidence']=[p['cards']['RES']['evidence_block_ids'][0]]
    with pytest.raises(jsonschema.ValidationError):observations(p,a)
    p,a,b=fixtures();b['findings'][0]['evidence']=['B'+'0'*20]
    with pytest.raises(KeyError):assess(p,a,b)


def test_packet_tampering_and_original_model_response_not_replayed():
    p,a,b=fixtures();p['blocks'][next(iter(p['blocks']))]['text']='changed'
    with pytest.raises(ValueError,match='integrity'):evaluate(p,Session(),enabled=True)
    p,a,b=fixtures();original=json.loads((BASE/'decisions.jsonl').read_text().splitlines()[0])['result']['model_audit'][0]['response']
    s=Session(lambda req:original)
    r=evaluate(p,s,enabled=True)
    assert s.calls==1 and r['endpoints']['adverse_propagation']['status']=='contract_invalid'
