"""Mocked compact-v2 runner integration. No paid calls."""
import io,json
from dataclasses import replace
from decimal import Decimal
import pytest
from test_mp_adjudicator_v2 import fixtures
from test_mp_adjudicator_compact import responses
from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator.smoke_transport import Config as OldConfig,StopSmoke
from benchmark.mp_compact_smoke.transport import Config,Transport
from scripts.smoke_mp_compact import execute,preview,health,jobs


def setup():
    p,a,b=fixtures();wa,wb=responses(p,a,b);return p,wa,wb


def opener(wa,wb,calls):
    def call(req,**kwargs):
        body=json.loads(req.data);calls.append(body)
        assert body['model']==MODEL and body['max_completion_tokens']==16384
        assert 'mp-hybrid-compact-dev1' in body['messages'][0]['content']
        payload=json.loads(body['messages'][1]['content'])
        a=body['response_format']['json_schema']['name'].endswith('_a')
        if a:
            assert 'texts' in payload and 'behavior' in payload and 'run_roles' not in payload
        else:
            assert payload['run_roles']['target']=='R0' and 'attribution_options' in payload
        raw={'model':MODEL,'usage':{'prompt_tokens':100,'completion_tokens':50},
             'choices':[{'finish_reason':'stop','message':{'content':json.dumps(wa if a else wb)}}]}
        return io.BytesIO(json.dumps(raw).encode())
    return call


def test_exact_path_cache_and_artifacts(tmp_path):
    p,wa,wb=setup();calls=[];items=[('mock-aon',p),('mock-spgi',p)]
    first=execute(items,Config(),tmp_path/'cache',tmp_path/'run',authorize=lambda:True,opener=opener(wa,wb,calls),key='test')
    assert first['status']=='success' and len(calls)==2 and first['cache_hits']==2
    again=execute(items,Config(max_calls=0),tmp_path/'cache',tmp_path/'replay',
                  opener=lambda *a,**k:pytest.fail('network'),key='')
    assert again['paid_calls']==0 and again['cache_hits']==4 and again['status']=='success'
    row=json.loads((tmp_path/'run/decisions.jsonl').read_text().splitlines()[0])
    assert row['result']['version']=='mp-hybrid-compact-dev1'
    assert row['result']['event_findings'][0]['origin_event_id']=='O'
    assert row['stage_a_observation_validity']['valid']
    assert row['stage_b_eligibility']['attempted']
    assert row['semantic_endpoint_validity']['adverse_propagation']['usable']
    for name in ('packet_0.json','compact_0.json','requests.jsonl','validation_0.json','event_findings.jsonl','implementation_hashes.json'):
        assert (tmp_path/'run'/name).is_file()


def test_stage_b_skipped_transport_success_distinct(tmp_path):
    p,wa,wb=setup();calls=[]
    r=execute([('mock-aon',p)],Config(),tmp_path/'cache',tmp_path/'run',authorize=lambda:True,opener=opener({'events':[]},{},calls),key='test')
    assert len(calls)==1 and r['transport_status']=='completed' and not r['semantic_success']
    assert r['traces'][0]['stage_b_eligibility']['status']=='skipped_no_verified_target_behavior'


def test_actual_large_stage_b_blocked_after_stage_a(tmp_path):
    p,wa,wb=setup()
    for o in wa['events']:o['summary']='Long observable summary. '*3000
    calls=[]
    r=execute([('mock-aon',p)],Config(),tmp_path/'cache',tmp_path/'run',authorize=lambda:True,opener=opener(wa,wb,calls),key='test')
    assert len(calls)==1 and r['paid_calls']==1 and not r['semantic_success']
    logs=[json.loads(x) for x in (tmp_path/'run/requests.jsonl').read_text().splitlines()]
    planned=[x for x in logs if x['stage']=='B' and x['status']=='planned'][0]
    assert planned['input_token_bound']>160000
    assert any('exceeds limit' in x.get('reason','') for x in logs)


def test_new_identity_and_exact_serialization():
    from test_mp_adjudicator_smoke import request
    req=request();cfg=Config();identity,key=cfg.identity(req)
    old=OldConfig(max_output_tokens=16384).identity(req)[1]
    assert key!=old and 'implementation_sha256' in identity
    assert cfg.bounds(identity)['serialized_request_bytes']==len(json.dumps(identity['body'],ensure_ascii=False).encode())
    assert cfg.cost(160000,16384)*4==Decimal('0.3608576')
    for change in ({'max_input_tokens':160001},{'budget_usd':'.41'},{'max_calls':5}):
        with pytest.raises(ValueError):replace(cfg,**change)


def test_budget_and_confirmation_fail_closed(tmp_path):
    from test_mp_adjudicator_smoke import request
    for cfg,auth in [(Config(budget_usd='.00001'),lambda:True),(Config(),lambda:False)]:
        t=Transport(cfg,tmp_path,authorize=auth,key='test',progress=False,opener=lambda *a,**k:pytest.fail('network'))
        with pytest.raises(StopSmoke):t(request())
        assert t.calls==0


def test_unresolved_reservation_and_no_retry(tmp_path):
    from test_mp_adjudicator_smoke import request
    calls=[]
    def timeout(*a,**k):calls.append(1);raise TimeoutError()
    t=Transport(Config(),tmp_path,authorize=lambda:True,key='test',progress=False,opener=timeout)
    req=request();reserve=Decimal(t.config.bounds(t.config.identity(req)[0])['reserved_usd'])
    with pytest.raises(TimeoutError):t(req)
    assert t.spent==reserve
    again=Transport(Config(),tmp_path,authorize=lambda:True,key='test',progress=False,opener=timeout)
    with pytest.raises(StopSmoke):again(req)
    assert len(calls)==1


def test_selection_only_aon_spgi_and_three_controls():
    selected=jobs()
    assert len(selected)==2
    for (trace,p),term in zip(selected,('aon','spgi')):
        assert term in trace.lower()
        assert len(p['base']['clean_runs'])==3 and p['base']['admitted']


def test_preview_no_network_or_artifacts(tmp_path):
    p,wa,wb=setup();r=preview([('mock',p)],Config(),tmp_path/'cache')
    assert r['uncached_requests_known_now'][0]['max_output_tokens']==16384
    assert r['configuration']['max_input_tokens']==160000
    assert not (tmp_path/'cache').exists()
