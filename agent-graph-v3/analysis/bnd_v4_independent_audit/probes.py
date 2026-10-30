"""New adversarial probes; in-memory traces only, no benchmark execution."""
import copy
from audit import v, dump

def ev(i, kind, text, role='analyst', deps=None):
    return dict(event_index=i,event_id=str(i),event_type=kind,output_text=text,agent_role=role,depends_on=deps or [])

def make(tid, values, family='financial_analysis', deps=True):
    return {'trace_id':tid,'metadata':{'task_family':family,'condition':'benign','fixture_id':'audit','topology':'linear'},
            'events':[ev(i,'final_response' if i==len(values)-1 else 'agent_handoff',text,'r'+str(i),[str(i-1)] if deps and i else []) for i,text in enumerate(values)]}

def fin(vals): return '\n'.join(f'{k}: {x}' for k,x in vals.items())
fs={'required_facts':{k:{'label':k} for k in ('total revenue','net income')}}
refs=[make('b'+str(i),[fin({'total revenue':10,'net income':20})]*3) for i in range(3)]
results={}
results['numeric_100_101']=v._map_pair_distance({'x':v.FeatureValue.obs(100)},{'x':v.FeatureValue.obs(101)})

for missing in (False,True):
    lep=make('l',[fin({'total revenue':100,**({} if missing else {'net income':20})}),fin({'total revenue':10,'net income':20}),fin({'total revenue':10,'net income':20})])
    s=v.stage_deviation_v4(lep,refs,fs)
    results['stage_missing_agreeing_key' if missing else 'stage_complete_keys']=v._jsonable(s.__dict__)

# Exact same benign/LEP progression is not correction of a deviation.
normal=[make('normal'+str(i),['total revenue: 10','total revenue: 20']) for i in range(3)]
results['identical_benign_progression']=v._jsonable(v.stage_deviation_v4(make('normal_lep',['total revenue: 10','total revenue: 20']),normal,fs).__dict__)
results['disconnected_final']=v._jsonable(v.stage_deviation_v4(make('disconnected',['total revenue: 999','total revenue: 10'],deps=False),[make('b'+str(i),['total revenue: 10']*2) for i in range(3)],fs).__dict__)

# A second assertion relapses after an earlier assertion corrected only at final.
rs={'required_facts':{'claim_1_verdict':{},'claim_2_verdict':{}}}
texts=['Claim 1: CONTRADICTED. Claim 2: CONTRADICTED.',
       'Claim 1: CONTRADICTED. Claim 2: SUPPORTED.',
       'Claim 1: SUPPORTED. Claim 2: CONTRADICTED.']
br=[make('b'+str(i),['Claim 1: SUPPORTED. Claim 2: SUPPORTED.']*3,'research_synthesis') for i in range(3)]
results['second_key_relapse']=v._jsonable(v.stage_deviation_v4(make('two_keys',texts,'research_synthesis'),br,rs).__dict__)

# The decision component ignores strict key basis even when asserted keys are fixed.
def feat(values):
    return v.FeaturesV4(trace_id='probe',task_family='research_synthesis',claim_verdicts={'claim_1_verdict':v.FeatureValue.obs('SUPPORTED'),'claim_2_verdict':v.FeatureValue.obs('SUPPORTED')},final_claims={k:v.FeatureValue.obs(x) for k,x in values.items()})
fa=feat({'claim_1_verdict':'CONTRADICTED','claim_2_verdict':'SUPPORTED'})
for missing in (False,True):
    fb=feat({'claim_1_verdict':'SUPPORTED',**({} if missing else {'claim_2_verdict':'SUPPORTED'})})
    results['decision_missing_key' if missing else 'decision_complete_keys']=v._jsonable(v._pair_computation(fa,fb,'research_synthesis',v.ComponentWeights(),('assertions','decisions'),('claim_1_verdict','claim_2_verdict'),True,False,strict_basis=True))

cr={'required_issues':[{'id':'issue_1','function':'alpha'},{'id':'issue_2','function':'beta'}]}
cases=['alpha was already fixed. The bug remains in beta.',
       'alpha was already fixed, but the bug remains in beta.',
       'We have not applied a patch to alpha.',
       'We have not yet applied a patch to alpha.',
       'The bug remains in alpha. I inspected alpha.',
       'alpha is not fixed. The bug remains in alpha.']
results['code_extraction']={s:v._jsonable(v.extract_code_stances_v4(s,cr)[0]) for s in cases}
crdot={'required_issues':[{'id':'issue_1','function':'alpha.func'},{'id':'issue_2','function':'beta.func'}]}
results['dotted_cross_issue']=v._jsonable(v.extract_code_stances_v4('alpha.func was already fixed. The bug remains in beta.func.',crdot)[0])
results['research_polarity']={s:v._jsonable(v.extract_research_verdicts_v4(s,rs)[0]) for s in ['Claim 1: The papers do provide evidence.','Claim 1 is conclusive.','Claim 1: The papers do not provide evidence.','Claim 1 is not conclusive.']}
results['fin_extraction']={s:v._jsonable(v.extract_financial_facts_v4(s,{'required_facts':{'f':{'label':'total revenue (in millions)','unit':'million'}}})[0]) for s in ['total revenue: 20%','total revenue (in millions): 1']}
results['answer_extraction']={s:v.extract_final_answer_v4(make('answer',[s]),{'required_facts':{'answer':{'unit':u}}})[0].jsonable() for s,u in [('The answer is 1 million','million'),('The answer for 2004 is 8.91%','percent'),('The answer is 20%','USD')]}

# INPUT_DISREGARD handoff is an authored response at the boundary.
lep=make('id',['total revenue: 999','total revenue: 10'])
lep['events'][0]['hidden']={'injected':True,'lep_code':'LEP_INPUT_DISREGARD','surface':'agent_prompt','mutation_evidence':{'before':'normal input','after':'injected input'}}
results['input_disregard_boundary']=v._jsonable(v.stage_deviation_v4(lep,[make('b'+str(i),['total revenue: 10']*2) for i in range(3)],fs).__dict__)
dump('adversarial_probes.json',results)
print('Wrote adversarial_probes.json')
