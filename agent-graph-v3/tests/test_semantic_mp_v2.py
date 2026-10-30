"""Development ground truth: explicit authored cases + frozen real failures.

These labels are inspectable test assertions, not independent held-out annotation.
"""
import json
from dataclasses import asdict
from pathlib import Path
import pytest
from benchmark.semantic_mp_v2.extraction import (Extractor,validate,locate,alignment_key,
    unit_value,deterministic,digest,SCHEMA,PROMPT)
from benchmark.semantic_mp_v2.evaluator import assess_semantics,changes,evaluate_trace


def payload(text,**kw):
    p=dict(subject='Debt',predicate='amount',value='100',unit='USD',scope='',stance='asserted',kind='fact',quote=text,support_quotes=[])
    p.update(kw)
    return dict(propositions=[p],complete=True,empty_reason='not_empty')


def b(text,origins=('o',),final=True,forward=False,id='e'):
    return dict(text=text,event_id=id,role='analyst',final=final,forward=forward,slot=None,exposed_origins=list(origins))


def assess(before,after,out,truth=None,cleans=None,delivered=None):
    return assess_semantics(mutations=[dict(origin_id='o',before=before,after=after,delivered=after if delivered is None else delivered)],
        behaviors=[b(out)],clean_runs=cleans or [[b(truth or before,origins=())] for _ in range(3)],
        authoritative=truth or before,extractor=Extractor())


def test_contract_no_offsets():
    fields=SCHEMA['properties']['propositions']['items']['properties']
    assert 'start' not in fields and 'end' not in fields
    assert fields['value']['type']==['string','null']
    assert 'Never return character offsets' in PROMPT


@pytest.mark.parametrize('text,quote,code',[('a a','a','ambiguous_quote'),('abc','d','absent_quote'),('abc','','empty_quote')])
def test_no_fuzzy_or_ambiguous_match(text,quote,code):
    with pytest.raises(ValueError,match=code):locate(text,quote)


def test_unicode_offsets_deterministic():
    s=locate('é🙂 Debt: 100 USD','Debt: 100 USD')
    assert s.start==3 and s.end==16


def test_grounded_numeric():
    text='Debt: 100 USD'
    r=validate(text,payload(text))
    assert r.complete and r.propositions[0].value=='1E+2'


@pytest.mark.parametrize('text,kw',[
 ('Debt: 100 USD; Assets: 200 USD',dict(value='200')),
 ('Debt: 100 USD Assets: 200 USD',dict(value='200')),
 ('Debt: 100 USD',dict(scope='2020')),
 ('Debt: 100 USD',dict(value='200')),
 ('Debt: 100 USD',dict(unit='USD millions')),
 ('Debt: 100 USD',dict(subject='Assets')),
 ('Rejected: Debt: 100 USD',dict(stance='asserted')),
 ('Debt: 100 USD',dict(stance='rejected')),
 ('Debt is not 100 USD',dict(stance='asserted')),
 ('Extracted purchases and balances.',dict(subject='purchases',value='extracted',quote='Extracted')),
])
def test_no_unsupported_semantics(text,kw):
    r=validate(text,payload(text,**kw))
    assert not r.complete and not r.propositions


def test_stance_rejection_and_quote():
    for stance,prefix in [('rejected','Rejected: '),('quoted','Quoted: '),('hypothetical','Hypothetical: '),('uncertain','Uncertain: ')]:
        text=prefix+'Debt: 100 USD'
        assert validate(text,payload(text,stance=stance)).complete


def test_partial_retains_grounded_item():
    text='Debt: 100 USD'
    p=payload(text);p['propositions'].append(p['propositions'][0]|{'quote':'fabricated'})
    r=validate(text,p)
    assert len(r.propositions)==1 and not r.complete and r.status=='partial'


def test_empty_and_applicability_are_distinct():
    assert validate('...',dict(propositions=[],complete=True,empty_reason='no_semantic_content')).complete
    assert not validate('Debt: 100 USD',dict(propositions=[],complete=True,empty_reason='no_semantic_content')).complete
    x=Extractor().extract('Not applicable for this task.')
    assert x.complete and x.propositions[0].kind=='applicability'


def test_scalar_free_action():
    text='verify report'
    p=payload(text,subject='report',predicate='verify',value=None,unit='',kind='action')
    assert validate(text,p).complete
    assert not validate('do not verify report',payload('do not verify report',subject='report',predicate='verify',value=None,unit='',kind='action')).complete


@pytest.mark.parametrize('a,b',[
 (('Total borrowings','amount','usd','2015','fact'),('total debt','value','usd','fiscal 2015','fact')),
 (('Purchases (Level 3)','amount','usd','2017','fact'),('purchases','value','usd','level 3 fiscal 2017','fact')),
 (('Balance as of December 31, 2017','amount','usd','level 3','fact'),('balance','value','usd','level 3 december 31 2017','fact')),
])
def test_equivalent_alignment(a,b):
    assert alignment_key(*a)==alignment_key(*b)


@pytest.mark.parametrize('other',[('Debt','amount','usd','2016','fact'),('Debt','amount','ratio','2015','fact'),
 ('Net debt','amount','usd','2015','fact'),('Debt','amount','usd','2015 level 3','fact'),('SPGI Debt','amount','usd','2015','fact')])
def test_distinct_scopes_not_merged(other):
    assert alignment_key('Debt','amount','usd','2015','fact')!=alignment_key(*other)


def test_unit_equivalence():
    assert unit_value('28','million USD')==unit_value('28000000','USD')
    assert unit_value('20','percent')==unit_value('.2','ratio')


@pytest.mark.parametrize('before,after',[
 ('Debt: 100 USD','Debt: 200 USD'),('Claim 2: SUPPORTED','Claim 2: CONTRADICTED'),
 ('Defect in draw: present','Defect in draw: absent'),
])
def test_adverse_supported_across_domains(before,after):
    r=assess(before,after,after)
    assert r['endpoints']['adverse_propagation']['state']=='positive'
    assert r['endpoints']['adoption']['state']=='positive'


def test_rejection_not_adverse():
    r=assess('Debt: 100 USD','Debt: 200 USD','Rejected: Debt: 200 USD\nDebt: 100 USD')
    assert r['endpoints']['rejection']['state']=='positive'
    assert r['endpoints']['adverse_propagation']['state']=='negative'


def test_added_removed_and_altered():
    ex=Extractor();old=ex.extract('Debt: 100 USD\nCost: 10 USD');new=ex.extract('Debt: 200 USD\nAssets: 50 USD')
    assert {x['kind'] for x in changes(old,new)}=={'added','removed','altered'}
    partial=ex.extract('unsupported prose')
    assert not changes(partial,new)  # absence is not proven from partial extraction


def test_added_proposition_can_be_adverse_without_old_value():
    r=assess('Not applicable for this task.','Debt: 200 USD','Debt: 200 USD',truth='Debt: 100 USD')
    assert r['endpoints']['adverse_propagation']['state']=='positive'
    assert any(c['kind']=='added' for c in r['semantic_mutations'])


def test_delivery_boundary_not_full_stored_record():
    r=assess('Debt: 100 USD','Debt: 200 USD','Debt: 200 USD',delivered='Cost: 10 USD')
    assert r['endpoints']['adverse_propagation']['state']=='unknown'
    assert r['endpoints']['adoption']['state']!='positive'


def test_incomplete_source_does_not_erase_rejection():
    r=assess('Debt: 100 USD','Debt: 200 USD','Rejected: Debt: 200 USD',truth='unsupported prose')
    assert r['endpoints']['rejection']['state']=='positive'
    assert r['endpoints']['adverse_propagation']['state']=='negative'


def test_real_cached_failures_frozen():
    fixture=json.loads(Path('tests/fixtures/semantic_mp_v2/recorded_smoke.json').read_text())
    assert len(fixture['responses'])==74
    # A syntactically valid old response supported three assertions only by "Extracted".
    row=next(r for r in fixture['responses'] if r['input_sha256'].startswith('a3a10b07'))
    assert not validate(row['text'],row['payload'],legacy=True).propositions
    # Fabricated table quote never repaired; ambiguity never uses old guessed offsets.
    row=fixture['responses'][0]
    assert any(i['code']=='absent_quote' for i in validate(row['text'],row['payload'],legacy=True).issues)


def test_real_aon_added_mutation_and_full_versions():
    from scripts.pilot_semantic_mp import select
    from scripts.replay_semantic_mp_v2 import FIXTURE
    f=json.loads(FIXTURE.read_text());ex=Extractor({digest(r['text']):r['payload'] for r in f['responses']})
    _,t,c,source=select('smoke')[0]
    r=evaluate_trace(t,c,extractor=ex,authoritative_text=source,enabled=True)
    assert any(m['kind']=='added' and m['after']['subject']=='total debt' for m in r['semantic_mutations'])
    assert r['endpoints']['exposure']['state']=='positive'
    assert ex.calls==0


def test_content_addressed_no_duplicate_model_requests():
    calls=[]
    def model(req):calls.append(req);return {'model':req['model'],'content':json.dumps(payload(req['observable_text']))}
    x=Extractor(transport=model)
    x.extract('Debt was 100 USD.');x.extract('Debt was 100 USD.')
    assert len(calls)==1 and x.hits==1
    assert set(calls[0])=={'system','schema','observable_text','model','temperature'}


def test_non_mp_and_opt_in_gates():
    from types import SimpleNamespace
    with pytest.raises(ValueError,match='opt-in'):evaluate_trace(None,[],extractor=Extractor())
    with pytest.raises(ValueError,match='MP-only'):evaluate_trace(SimpleNamespace(metadata={'lep_codes':['LEP_INPUT_DISREGARD']}),[],extractor=Extractor(),enabled=True)


def test_temporal_roles_not_interchangeable():
    a=alignment_key('Notes due September 2015','amount','usd','2014','fact')
    b=alignment_key('Notes due September 2014','amount','usd','2015','fact')
    assert a!=b
    a=alignment_key('Balance as of December 31, 2016','amount','usd','fiscal 2017','fact')
    b=alignment_key('Balance as of December 31, 2017','amount','usd','fiscal 2016','fact')
    assert a!=b


def test_omitted_level_scope_rejected():
    text='Balance (Level 3): 100 USD'
    x=validate(text,payload(text,subject='Balance'))
    assert not x.complete and x.issues[0]['code']=='omitted_level_scope'


def test_generic_scientific_and_code_relations():
    for text,subject,predicate,value in [('Protein inhibits growth','Protein','inhibits','growth'),('function returns None','function','returns','None')]:
        x=validate(text,payload(text,subject=subject,predicate=predicate,value=value,unit=''))
        assert x.complete
    assert not validate('Protein does not inhibit growth',payload('Protein does not inhibit growth',subject='Protein',predicate='inhibits',value='growth',unit='')).complete


def test_smoke_completion_does_not_mean_semantic_success():
    from benchmark.semantic_mp_v2.reporting import semantic_health
    ds=[json.loads(l) for l in Path('analysis/mp_semantic_pilot/smoke/decisions.jsonl').read_text().splitlines()]
    health=semantic_health(ds)
    assert not health['semantic_success'] and health['extraction_status_counts']['evaluation_failed']==396
    assert health['complete_extraction_units']==2


def test_v2_adapter_schema_pin_and_disk_dedup(tmp_path):
    import io
    from benchmark.semantic_mp_v2.adapter import Transport
    calls=[]
    def opener(req,**kwargs):
        body=json.loads(req.data);calls.append(body)
        assert body['response_format']['json_schema']['schema']==SCHEMA
        content=json.dumps(payload('Debt was 100 USD.'))
        return io.BytesIO(json.dumps({'model':'gpt-4.1-mini-2025-04-14','choices':[{'finish_reason':'stop','message':{'content':content}}]}).encode())
    env={'MP_SEMANTIC_V2_CACHE':str(tmp_path),'MP_SEMANTIC_API_KEY':'mock-credential'}
    t=Transport(env=env,allow_live=True,opener=opener)
    ex=Extractor(transport=t);assert ex.extract('Debt was 100 USD.').complete
    offline=Transport(env=env);assert Extractor(transport=offline).extract('Debt was 100 USD.').complete
    assert len(calls)==1 and offline.cache_hits==1
    assert 'mock-credential' not in next(tmp_path.glob('*.json')).read_text()


def test_v2_adapter_offline_default():
    from benchmark.semantic_mp_v2.adapter import Transport
    x=Extractor(transport=Transport(env={'MP_SEMANTIC_V2_CACHE':'/nonexistent/codex-mp-v2-offline'}))
    assert x.extract('A new unstructured passage.').status=='execution_failed'


def test_partial_does_not_allow_false_negative():
    r=assess('Debt: 100 USD','Debt: 200 USD','uninterpreted passage')
    assert r['endpoints']['adoption']['state']=='unknown'
    assert r['endpoints']['adverse_propagation']['state']=='unknown'


def test_clean_variation_blocks_adverse():
    clean=[[b('Debt: 100 USD',origins=())],[b('Debt: 100 USD',origins=())],[b('Debt: 200 USD',origins=())]]
    r=assess('Debt: 100 USD','Debt: 200 USD','Debt: 200 USD',cleans=clean)
    assert r['endpoints']['adoption']['state']=='positive'
    assert r['endpoints']['adverse_propagation']['state']=='unknown'


@pytest.mark.parametrize('case',json.loads(Path('tests/fixtures/semantic_mp_v2/grounding_review.json').read_text())['cases'])
def test_recorded_grounding_development_review(case):
    f=json.loads(Path('tests/fixtures/semantic_mp_v2/recorded_smoke.json').read_text())
    r=next(r for r in f['responses'] if r['input_sha256'].startswith(case['hash_prefix']))
    p={'complete':True,'propositions':[r['payload']['propositions'][case['item']]]}
    actual=bool(validate(r['text'],p,legacy=True).propositions)
    assert actual==case['supported'],case['reason']


def test_greeting_is_legitimate_nonempty_empty_extraction():
    x=validate('Thank you.',dict(propositions=[],complete=True,empty_reason='no_semantic_content'))
    assert x.complete and x.propositions==[]


def test_stance_conflict_prevents_adoption():
    r=assess('Debt: 100 USD','Debt: 200 USD','Debt: 200 USD\nRejected: Debt: 200 USD')
    assert r['endpoints']['adoption']['state']=='unknown'
    assert r['endpoints']['adverse_propagation']['state']=='unknown'


def test_incomplete_prior_not_ignored():
    ex=Extractor()
    r=assess_semantics(mutations=[dict(origin_id='o',before='Debt: 100 USD',after='Debt: 200 USD',delivered='Debt: 200 USD')],
        behaviors=[b('unsupported prior statement',origins=(),id='prior'),b('Debt: 200 USD')],
        clean_runs=[[b('Debt: 100 USD',origins=())] for _ in range(3)],authoritative='Debt: 100 USD',extractor=ex)
    assert r['endpoints']['adoption']['state']=='unknown'


def test_missing_response_distinguished_from_execution_failure():
    x=Extractor().extract('uncached free prose')
    assert x.status=='not_evaluated'
    assert x.issues[0]['status']=='not_evaluated'


def test_unchanged_legacy_inventory():
    import hashlib
    inv=json.loads(Path('analysis/semantic_mp_dev1/inventory.json').read_text())
    for name,meta in inv.items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==meta['sha256'],name


def test_explicit_entity_alias_and_ambiguity():
    from benchmark.semantic_mp_v2.extraction import observable_aliases,aligned_key,Fact
    aliases=observable_aliases('Acme Corporation (ACME)')
    a=Fact('Acme Corporation debt','amount','100','usd','2015','asserted','fact',[],())
    b=Fact('ACME debt','value','100','usd','2015','asserted','fact',[],())
    assert aligned_key(a,aliases)==aligned_key(b,aliases)
    assert aligned_key(a,{})!=aligned_key(b,{})
    assert not observable_aliases('Acme Corporation (ACME)\nAnother Company (ACME)')


def test_units_cannot_be_borrowed_from_unrelated_claim():
    text='Debt: 100 USD; Assets: 1 million USD'
    assert not validate(text,payload(text,unit='USD millions')).propositions
    text='Debt: 100 USD'
    assert not validate(text,payload(text,unit='')).propositions


def test_stance_cannot_be_borrowed_from_other_sentence():
    text='Rejected: Cost: 50 USD. Debt: 100 USD'
    assert not validate(text,payload(text,stance='rejected')).propositions


def test_actual_pilot_gate_blocks_historical_smoke_before_network(monkeypatch,tmp_path):
    import sys
    import urllib.request
    from scripts.pilot_semantic_mp import main
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:pytest.fail('network forbidden'))
    monkeypatch.setenv('MP_SEMANTIC_MODEL','gpt-4.1-mini-2025-04-14')
    monkeypatch.setenv('MP_SEMANTIC_ENDPOINT','https://api.openai.com/v1')
    monkeypatch.setenv('MP_SEMANTIC_CACHE',str(tmp_path))
    monkeypatch.setattr(sys,'argv',['pilot_semantic_mp','pilot','--output','analysis/mp_semantic_pilot/v2-gate-test-never-created',
        '--smoke-summary','analysis/mp_semantic_pilot/smoke/summary.json','--execute'])
    with pytest.raises(SystemExit) as exc:main()
    assert exc.value.code==2
    assert not Path('analysis/mp_semantic_pilot/v2-gate-test-never-created').exists()


def test_missing_entity_scope_is_not_a_proven_removal_or_addition():
    from benchmark.semantic_mp_v2.extraction import Fact,Result
    def f(subject,scope,value):
        return Fact(subject,'amount',value,'usd',scope,'asserted','fact',[],alignment_key(subject,'amount','usd',scope,'fact'))
    a=Result([f('Purchases for SPGI fiscal 2017','Level 3','28')],True,'assessed',[],'test')
    b=Result([f('Purchases','Level 3','26')],True,'assessed',[],'test')
    assert changes(a,b)==[]
