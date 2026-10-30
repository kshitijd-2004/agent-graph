from copy import deepcopy
import json
from pathlib import Path
import pytest
import jsonschema
from test_mp_adjudicator_v2 import fixtures
from test_mp_adjudicator_compact import responses
from benchmark.mp_adjudicator_compact.codec import encode
from benchmark.mp_compact_refs_v2.decode import decode
from benchmark.mp_compact_refs_v2.contract import schema
from benchmark.mp_compact_refs_v2.engine import evaluate
from benchmark.mp_adjudicator.engine import Session
from benchmark.mp_adjudicator.contract import MODEL
from scripts.replay_mp_compact_refs_v2 import CACHE

@pytest.mark.parametrize('i,counts',[(0,{'duplicate_event':4,'wrong_namespace':5,'cross_event_evidence':1}),
    (1,{'unknown_event':5,'invalid_evidence_relationship':35})])
def test_frozen_failures(i,counts):
    p=json.loads(Path(f'analysis/mp_compact_smoke/smoke_001/packet_{i}.json').read_text())
    r=json.loads(Path(f'analysis/mp_compact_smoke/cache/{CACHE[i]}.json').read_text())
    raw=json.loads(r['raw_response']['choices'][0]['message']['content']); before=deepcopy(raw)
    result=decode(p,encode(p),raw)
    assert result['error_counts']==counts and not result['accepted_event_ids']
    assert result['quarantined_rows']==list(range(5)) and raw==before
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(raw,schema(p,encode(p)))


def test_duplicates_quarantine_all_and_preserve_independent_rows():
    p,a,b=fixtures();wa,_=responses(p,a,b);wa['events'].append(deepcopy(wa['events'][0]))
    d=decode(p,encode(p),wa)
    assert d['error_counts']['duplicate_event']==2
    assert wa['events'][0]['event_id'] not in d['accepted_event_ids']
    assert len(d['accepted_event_ids'])==len(wa['events'])-2

@pytest.mark.parametrize('ref,code',[('t0','wrong_namespace'),('B123','wrong_namespace'),('bdeadbeef','unknown_occurrence')])
def test_bad_references(ref,code):
    p,a,b=fixtures();wa,_=responses(p,a,b);wa['events'][0]['evidence']=[ref]
    assert decode(p,encode(p),wa)['rows'][0]['errors'][0]['code']==code


def test_cross_event_is_not_relocated():
    p,a,b=fixtures();wa,_=responses(p,a,b)
    wa['events'][0]['evidence']=wa['events'][1]['evidence']
    d=decode(p,encode(p),wa)
    assert 'cross_event_evidence' in d['error_counts']
    assert wa['events'][0]['event_id'] not in d['accepted_event_ids']


def test_pipeline_and_cache_replay():
    p,a,b=fixtures();wa,wb=responses(p,a,b)
    session=Session(lambda req:dict(model=MODEL,content=json.dumps(wa if req['stage']=='A' else wb)))
    r=evaluate(p,session,enabled=True)
    assert r['observation_verification_status']=='completed' and session.calls==2
    assert r['endpoints']['adverse_propagation']['state']=='positive'
    evaluate(p,session,enabled=True)
    assert session.calls==2 and session.cache_hits==2


def test_schema_failure_diagnostics_and_not_run():
    p,a,b=fixtures();wa,_=responses(p,a,b);wa['events'][0]['event_id']='FAKE'
    s=Session(lambda req:dict(model=MODEL,content=json.dumps(wa)))
    r=evaluate(p,s,enabled=True)
    assert s.calls==1 and r['observation_errors'] is None
    assert r['observation_verification_status']=='not_run'
    assert r['reference_diagnostics']['error_counts']['unknown_event']==1


def test_no_valid_rows_skips_stage_b_without_claiming_observation_success():
    p,a,b=fixtures();wa,_=responses(p,a,b)
    wa['events']=[wa['events'][0],deepcopy(wa['events'][0])]
    s=Session(lambda req:dict(model=MODEL,content=json.dumps(wa)))
    r=evaluate(p,s,enabled=True)
    assert s.calls==1 and r['stage_b_skipped']
    assert r['observation_verification_status']=='not_run_no_valid_rows'
    assert r['observation_errors'] is None


def test_empty_review_cannot_support_negative():
    p,a,b=fixtures();wa,_=responses(p,a,b)
    for row in wa['events']:row['evidence']=[]
    d=decode(p,encode(p),wa)
    assert not d['accepted_event_ids']
    assert all(row['review']=='unresolved' for row in d['decoded']['events'].values())
