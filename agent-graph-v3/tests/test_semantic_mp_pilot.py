import io
import json
import urllib.request
from decimal import Decimal
import pytest
from scripts.pilot_semantic_mp import Meter, StopPilot, cost, reservation, identity, select
from benchmark.semantic_mp.adapter import OpenAICompatibleTransport
from benchmark.semantic_mp.extraction import PROMPT


def req():
    return urllib.request.Request('https://api.openai.com/v1/chat/completions',data=json.dumps({'max_tokens':10}).encode())


def test_cost():
    assert cost(1000000,1000000,500000)==Decimal('1.85')


def test_budget_blocks_before_network(tmp_path):
    def fail(*a,**k):pytest.fail('network called')
    m=Meter(tmp_path,'.000001',2,fail)
    with pytest.raises(StopPilot):m(req())
    assert m.calls==0


def test_call_limit_and_usage(tmp_path):
    raw={'usage':{'prompt_tokens':20,'completion_tokens':3,'prompt_tokens_details':{'cached_tokens':10}}}
    m=Meter(tmp_path,'1',1,lambda *a,**k:io.BytesIO(json.dumps(raw).encode()))
    m(req())
    assert m.spent==cost(20,3,10)
    with pytest.raises(StopPilot):m(req())
    assert m.calls==1


def test_missing_usage_retains_reservation(tmp_path):
    m=Meter(tmp_path,'1',1,lambda *a,**k:io.BytesIO(b'{}'))
    with pytest.raises(ValueError):m(req())
    assert m.spent==reservation({'max_tokens':10})
    assert m.rows[-1]['status']=='failure'


def test_cache_identity_matches_existing_adapter(tmp_path):
    t=OpenAICompatibleTransport(env={'MP_SEMANTIC_CACHE':str(tmp_path)})
    r={'pin':t.pin.__dict__,'system':PROMPT,'text':'Observable evidence'}
    obj,h=identity(t,r)
    raw={'model':t.model,'choices':[{'finish_reason':'stop','message':{'content':'{"propositions":[],"complete":false}'}}]}
    (tmp_path/(h+'.json')).write_text(json.dumps({'input_sha256':h,'request':obj,'response':raw}))
    assert t(r)['input_sha256']==h


def test_frozen_selection():
    smoke=select('smoke');pilot=select('pilot')
    assert len(smoke)==2 and len(pilot)==5
    assert [j[0]['path'] for j in smoke]==[j[0]['path'] for j in pilot[:2]]
    assert all(t.metadata['lep_codes']==['LEP_MEMORY_POISONING'] and len(c)>=3 for _,t,c,_ in pilot)
