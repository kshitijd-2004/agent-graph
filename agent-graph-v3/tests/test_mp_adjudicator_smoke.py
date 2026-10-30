"""Offline only: every HTTP response is mocked; no production smoke execution."""
from dataclasses import replace
import io
import json
from decimal import Decimal
import pytest

from benchmark.mp_adjudicator.contract import MODEL
from benchmark.mp_adjudicator.engine import Session, evaluate
from benchmark.mp_adjudicator.smoke_transport import Config, Transport, StopSmoke, NoRedirect
from scripts.smoke_mp_adjudicator import execute, health, preview
from test_mp_adjudicator import synthetic, judgments


def request(stage='A'):
    return {'version':'test','model':MODEL,'temperature':0,'stage':stage,'prompt':'Return JSON.',
            'schema':{'type':'object','properties':{},'additionalProperties':False,'required':[]},'input':{'text':'observed'}}


def raw(content='{}', **kwargs):
    return {'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':content}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50},**kwargs}


def opener_for(packet, calls):
    a,b=judgments(packet)
    def opener(req,**kwargs):
        body=json.loads(req.data);calls.append(body)
        stage=body['response_format']['json_schema']['name']
        assert body['max_completion_tokens']==6000 and body['n']==1 and body['stream'] is False
        return io.BytesIO(json.dumps(raw(json.dumps(a if stage.endswith('_a') else b))).encode())
    return opener


def test_two_stage_cache_replay_without_paid_calls(tmp_path):
    p=synthetic();calls=[];cache=tmp_path/'cache'
    t=Transport(Config(),cache,authorize=lambda:True,opener=opener_for(p,calls),key='test',progress=False)
    t.trace_id='mock-aon'
    result=evaluate(p,Session(t),enabled=True)
    assert health(result)['semantic_success'] and len(calls)==2
    assert t.calls==2 and t.completed==2
    replay=Transport(Config(),cache,opener=lambda *a,**k:pytest.fail('HTTP forbidden'),progress=False)
    replay.trace_id='mock-aon'
    replay_result=evaluate(p,Session(replay),enabled=True)
    assert health(replay_result)['semantic_success']
    assert replay.cache_hits==2 and replay.calls==0 and replay.spent==0
    assert replay_result['event_findings']==result['event_findings']


@pytest.mark.parametrize('config',[
    Config(max_calls=0), Config(max_input_tokens=1), Config(budget_usd='.00000001')])
def test_hard_limits_before_network(tmp_path,config):
    t=Transport(config,tmp_path,authorize=lambda:pytest.fail('confirmation before budget check'),
                opener=lambda *a,**k:pytest.fail('HTTP forbidden'),key='test',progress=False)
    with pytest.raises(StopSmoke):t(request())
    assert t.calls==0


def test_confirmation_required_and_no_preview_write(tmp_path):
    t=Transport(Config(),tmp_path/'cache',authorize=lambda:False,key='test',progress=False)
    with pytest.raises(StopSmoke,match='not confirmed'):t(request())
    assert not (tmp_path/'cache').exists()
    p=preview([('mock',synthetic())],Config(),tmp_path/'cache')
    assert p['uncached_requests_known_now'][0]['estimated_input_tokens']>0
    assert not (tmp_path/'cache').exists()


def test_failed_attempt_cached_no_retry_or_fallback(tmp_path):
    calls=[]
    def fail(*a,**k):calls.append(1);raise TimeoutError('mock timeout')
    t=Transport(Config(),tmp_path,authorize=lambda:True,opener=fail,key='test',progress=False)
    with pytest.raises(TimeoutError):t(request())
    assert len(calls)==1 and t.spent>0
    again=Transport(Config(),tmp_path,authorize=lambda:True,opener=fail,key='test',progress=False)
    with pytest.raises(StopSmoke,match='cached failed'):again(request())
    assert len(calls)==1 and again.calls==0
    with pytest.raises(StopSmoke,match='one attempt'):t(request())


def test_schema_failure_is_not_transport_success(tmp_path):
    t=Transport(Config(),tmp_path,authorize=lambda:True,key='test',progress=False,
                opener=lambda *a,**k:io.BytesIO(json.dumps(raw()).encode()))
    r=evaluate(synthetic(),Session(t),enabled=True)
    assert t.completed==1 and not health(r)['semantic_success']
    assert health(r)['semantic_status']=='failure'
    assert r['endpoints']['adverse_propagation']['state']=='unknown'


def test_completed_two_responses_all_unknown_is_semantic_failure(tmp_path):
    p=synthetic();a,b=judgments(p);b['findings']=[]
    def op(req,**kwargs):
        stage=json.loads(req.data)['response_format']['json_schema']['name']
        return io.BytesIO(json.dumps(raw(json.dumps(a if stage.endswith('_a') else b))).encode())
    result=execute([('mock-aon',p),('mock-spgi',p)],Config(),tmp_path/'cache',tmp_path/'run',
                   authorize=lambda:True,opener=op,key='test')
    assert result['transport_status']=='completed'
    assert result['status']=='semantic_failure' and result['paid_calls']==2
    assert result['cache_hits']==2


def test_smoke_artifacts_progress_and_event_findings(tmp_path,capsys):
    p=synthetic();calls=[]
    r=execute([('mock-aon',p),('mock-spgi',p)],Config(),tmp_path/'cache',tmp_path/'run',
              authorize=lambda:True,opener=opener_for(p,calls),key='test')
    assert r['status']=='success'
    progress=capsys.readouterr().out
    for key in ('trace_id','stage','completed_calls','cache_hits','elapsed_seconds','cumulative_estimated_usd'):
        assert key in progress
    rows=[json.loads(x) for x in (tmp_path/'run/event_findings.jsonl').read_text().splitlines()]
    for key in ('origin_event_id','affected_agent_id','grounded_evidence','relation','cross_agent_transfer','graph_metric_status'):
        assert key in rows[0]
    for f in ('packet_0.json','packet_1.json','requests.jsonl','decisions.jsonl','validation_0.json','summary.json'):
        assert (tmp_path/'run'/f).exists()
    assert 'Bearer test' not in (tmp_path/'run/requests.jsonl').read_text()
    with pytest.raises(FileExistsError):execute([],Config(),tmp_path/'cache',tmp_path/'run')


def test_exact_config_and_prompt_cache_identity():
    c=Config();req=request();_,key=c.identity(req)
    assert c.identity(req)[1]==key
    assert replace(c,max_output_tokens=100).identity(req)[1]!=key
    assert replace(c,endpoint='https://example.org/v1').identity(req)[1]!=key
    assert c.identity(dict(req,prompt='Different prompt'))[1]!=key
    assert c.identity(dict(req,input={'text':'different'}))[1]!=key


@pytest.mark.parametrize('change',[
    {'usage':{}}, {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},
    {'choices':[{'finish_reason':'stop','message':{'content':None,'refusal':'no'}}]},
    {'usage':{'prompt_tokens':100,'completion_tokens':6001}},
])
def test_unusable_response_preserved_without_retry(tmp_path,change):
    t=Transport(Config(),tmp_path,authorize=lambda:True,key='test',progress=False,
                opener=lambda *a,**k:io.BytesIO(json.dumps(raw(**change)).encode()))
    with pytest.raises(StopSmoke):t(request())
    records=[json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
    assert len(records)==1 and 'raw_response' in records[0] and t.calls==1


def test_no_redirect_followup():
    with pytest.raises(StopSmoke):NoRedirect().redirect_request(None,None,307,'redirect',{},'https://elsewhere')


def test_model_pin_mismatch_and_missing_key_fail_closed(tmp_path):
    t=Transport(Config(),tmp_path,authorize=lambda:True,key='',progress=False)
    with pytest.raises(StopSmoke,match='key'):t(request())
    t=Transport(Config(),tmp_path,progress=False)
    with pytest.raises(ValueError,match='model/settings'):t(dict(request(),model='different'))


def test_global_calls_and_estimated_spending_limits(tmp_path):
    count=[]
    def op(*a,**k):count.append(1);return io.BytesIO(json.dumps(raw()).encode())
    t=Transport(Config(max_calls=1),tmp_path/'calls',authorize=lambda:True,key='test',opener=op,progress=False)
    t(request('A'))
    with pytest.raises(StopSmoke,match='budget'):t(request('B'))
    assert len(count)==1
    c=Config();identity,_=c.identity(request())
    reserve=Decimal(c.bounds(identity)['reserved_usd'])
    # First call affordable; its usage charge plus a second reservation is not.
    t=Transport(replace(c,budget_usd=str(reserve)),tmp_path/'money',authorize=lambda:True,key='test',opener=op,progress=False)
    t(request('A'))
    with pytest.raises(StopSmoke,match='budget'):t(request('B'))
    assert t.calls==1


def test_refusal_replay_never_authorizes_new_request(tmp_path):
    payload=raw(choices=[{'finish_reason':'stop','message':{'content':None,'refusal':'no'}}])
    t=Transport(Config(),tmp_path,authorize=lambda:True,key='test',progress=False,
                opener=lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(StopSmoke):t(request())
    again=Transport(Config(),tmp_path,authorize=lambda:pytest.fail('re-authorized refusal'),key='test',progress=False)
    with pytest.raises(StopSmoke):again(request())
    assert again.calls==0
