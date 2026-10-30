"""Lossless codec and attribution regressions; no live model requests."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_mp_adjudicator_v2 import fixtures
from benchmark.mp_adjudicator.engine import Session
from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator_v2.engine import observations
from benchmark.mp_adjudicator_v2.packet import options,bind,resolve
from benchmark.mp_adjudicator_compact.codec import encode,block,map_evidence
from benchmark.mp_adjudicator_compact.engine import evaluate


def responses(p,a,b):
    c=encode(p);valid,_=observations(p,a);opts=options(p,valid)
    pa={key:f'p{i:x}' for i,key in enumerate(sorted(opts))}
    wa={'events':[dict(event_id=eid,**map_evidence(o,lambda bid:c['reverse'][bid])) for eid,o in a['events'].items()]}
    wb=map_evidence(b,lambda bid:c['reverse'][bid])
    for f in wb['findings']:f['option_id']=pa[f['option_id']]
    return wa,wb


def run(p,wa,wb):
    s=Session(lambda req:{'model':MODEL,'content':json.dumps(wa if req['stage']=='A' else wb)})
    return evaluate(p,s,enabled=True),s


def test_real_packets_roundtrip_every_occurrence():
    for i in (0,1):
        p=json.loads(Path(f'analysis/mp_adjudicator_v2_dev1/final_offline/packet_{i}.json').read_text())
        before=deepcopy(p);c=encode(p);v=c['view'];rows=v['behavior']+v['sources']+v['other_observations']
        assert len(rows)==len(p['cards']) and len(c['lookup'])==len(p['blocks'])
        for row in rows:
            for alias,tid in row['evidence']:
                bid=block(c,p,alias)
                assert p['blocks'][bid]['event_id']==row['id']
                assert resolve(p,bid)['quote']==v['texts'][tid]
        assert p==before
        assert {r['id'] for r in rows if r['type'] in ('agent_handoff','final_response')}=={k for k,v in p['cards'].items() if v['event_type'] in ('agent_handoff','final_response')}


def test_shared_text_separate_run_and_event_occurrences():
    p,a,b=fixtures();c=encode(p)
    left=c['reverse'][p['cards']['C1']['evidence_block_ids'][0]]
    right=c['reverse'][p['cards']['C2']['evidence_block_ids'][0]]
    assert left!=right
    wa,wb=responses(p,a,b)
    next(x for x in wa['events'] if x['event_id']=='C1')['evidence']=[right]
    result,s=run(p,wa,wb)
    assert result['failed_stage']=='A' and s.calls==1
    assert result['endpoints']['adverse_propagation']['state']=='unknown'


def test_intermediate_adverse_and_recovery_preserved():
    p,a,b=fixtures();valid,_=observations(p,a);opts=options(p,valid)
    f=deepcopy(b['findings'][0]);f.update(id='fixed',endpoint='rejection',option_id=next(k for k,v in opts.items() if v['event_id']=='F'),
        evidence=[p['cards'][k]['evidence_block_ids'][0] for k in ('F','H')],corrects=['bad'])
    b['findings'].append(f)
    r,s=run(p,*responses(p,a,b))
    assert s.calls==2 and r['endpoints']['adverse_propagation']['state']=='positive'
    assert r['event_findings'][0]['event_depth']==4
    assert r['event_findings'][1]['recovery']['earlier_effect_retained']


def test_missing_review_not_inferred_from_shared_text():
    p,a,b=fixtures();b['findings']=[]
    for ep in b['endpoints']:b['endpoints'][ep].update(state='negative',status='assessed',scope_review='complete',reason='Review complete.',evidence=[p['cards'][k]['evidence_block_ids'][0] for k in ('F','C1','C2','C3')])
    wa,wb=responses(p,a,b);wa['events']=[x for x in wa['events'] if x['event_id']!='C2']
    r,s=run(p,wa,wb)
    assert r['endpoints']['adverse_propagation']['state']=='unknown'
    assert r['endpoints']['final_answer_impact']['state']=='unknown'


def test_unknown_occurrence_and_duplicate_events_fail_closed():
    p,a,b=fixtures();wa,wb=responses(p,a,b)
    wa['events'][0]['evidence']=['bBAD']
    r,s=run(p,wa,wb);assert r['failed_stage']=='A'
    wa,wb=responses(p,a,b);wa['events'].append(wa['events'][0])
    r,s=run(p,wa,wb);assert r['failed_stage']=='A'


def test_empty_stage_a_skips_b_and_no_label_leakage():
    p,a,b=fixtures();r,s=run(p,{'events':[]},{})
    assert s.calls==1 and r['stage_b_skipped']
    view=s.audit[0]['request']['input']
    assert not any(k in view for k in ('run_roles','mutations','deliveries'))
    assert 'mp-hybrid-compact-dev1' in s.audit[0]['request']['prompt']


def test_option_invention_cannot_target_clean_run():
    p,a,b=fixtures();wa,wb=responses(p,a,b);wb['findings'][0]['option_id']='R3:E75'
    r,s=run(p,wa,wb)
    assert r['endpoints']['adverse_propagation']['state']=='unknown'


def test_multiple_origins_uncertainty_unchanged():
    p,a,b=fixtures();base=deepcopy(p['base'])
    base['mutations'].append(base['mutations'][0]|{'origin_id':'O2','version_id':'O2','record_id':['other','debt']})
    base['deliveries'].append(base['deliveries'][0]|{'delivery_id':'D2','origin_id':'O2','version_id':'O2','record_id':['other','debt']})
    p=bind(base);r,s=run(p,*responses(p,a,b))
    assert r['endpoints']['adverse_propagation']['status']=='origin_ambiguous'
