"""In-memory adversarial probes. Does not alter any production input."""
import copy
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import audit_checks as a
b=a.b

def ev(i,kind,role='analyst',text='',**kw):
    return {'event_id':str(i),'event_index':i,'event_type':kind,'agent_role':role,'output_text':text,**kw}
def tr(tid,events,fam='financial_analysis'):
    return {'trace_id':tid,'metadata':{'task_family':fam,'fixture_id':'probe','topology':'branch_and_verify','execution_variant':'standard'},'events':events}
S={'required_facts':{'revenue':{'label':'total revenue','value':100,'unit':'USD millions'},'income':{'label':'net income','value':20,'unit':'USD millions'}}}
def synthetic():
    out={}
    # Ordinary completion is identical to benign at both stages.
    es=[ev(0,'tool_result',hidden={'perturbed_hash':'x'}),ev(1,'agent_handoff',text='total revenue: 100'),ev(2,'final_response',text='total revenue: 100\nnet income: 20')]
    t=tr('probe',es);refs=[tr('b'+str(i),copy.deepcopy(es)) for i in range(3)]
    for ref in refs: ref['events'][0].pop('hidden')
    out['benign_identical_progression']=b.stage_deviation(t,refs,S).summary()
    # Checkpoint payload contamination after a later handoff of the same role.
    es=[ev(0,'agent_handoff',text='total revenue: 999\nnet income: 20',hidden={'perturbed_hash':'x'}),ev(1,'agent_handoff',text='done'),ev(2,'final_response',text='total revenue: 100\nnet income: 20')]
    t=tr('origin_only',es);refs=[copy.deepcopy(t) for _ in range(3)]
    for i,ref in enumerate(refs):
        ref['trace_id']='b'+str(i);ref['events'][0]['output_text']='total revenue: 100\nnet income: 20';ref['events'][0].pop('hidden')
    out['origin_only_changed']=b.measure_configuration_v2(t,refs,S)
    # No causal edges between A and B. Reindexing their recording order alters convergence.
    def mk(order,lep):
        vals={'A': 'total revenue: 999\nnet income: 20' if lep else 'total revenue: 100\nnet income: 20','B':'total revenue: 100\nnet income: 20'}
        es=[ev(0,'tool_result',hidden={'perturbed_hash':'x'} if lep else {})]
        es += [ev(i+1,'agent_handoff',role=role,text=vals[role],depends_on=['0']) for i,role in enumerate(order)]
        es += [ev(3,'final_response',role='final',text=vals['A'])]
        return tr('order'+''.join(order),es)
    refs=[mk(['A','B'],False) for _ in range(3)]
    out['parallel_AB']=b.stage_deviation(mk(['A','B'],True),refs,S).summary()
    out['parallel_BA']=b.stage_deviation(mk(['B','A'],True),refs,S).summary()
    out['final_answer_bad_candidates']={s:b.extract_final_answer_v2(tr('f',[ev(0,'final_response',text=s)])) for s in ['The answer for 2004 is 8.91%.','Net margin was 20%. The requested cash balance is unavailable.','The ratio is 12.34.']}
    fa=b.FeaturesV2('a','','financial_analysis');fb=b.FeaturesV2('b','','financial_analysis')
    fa.financial_facts,_=b.extract_financial_facts_v2('total revenue: 1 million',S)
    fb.financial_facts,_=b.extract_financial_facts_v2('total revenue: 1000 thousand',S)
    out['equivalent_units_distance']={'a':fa.financial_facts,'b':fb.financial_facts,'distance':b.pair_assertions_v2(fa,fb,'financial_analysis')}
    cs={'required_issues':[{'id':'bug1','location':'src/utils.py'},{'id':'bug2','location':'src/utils.py'}]}
    out['file_mention_counts_as_both_repairs']=b.extract_repair_targets_v2('I read src/utils.py; no defects were found.',cs)
    def cr(path):return tr(path,[ev(0,'tool_call',tool_name='write_file',tool_arguments={'path':path,'content':'no defects'}),ev(1,'final_response',text='no defects')],'code_review')
    ca=b.extract_features_v2(cr('reports/alpha.md'),cs);cb=b.extract_features_v2(cr('reports/beta.md'),cs)
    out['report_rename_pair']=asdict(b.pairwise_distance_v2(ca,cb,'code_review'))
    out['report_path_heuristic']={p:b._is_report_path(p) for p in ['src/report.py','src/analysis.py','src/review.py','notes.md','reports/result.md']}
    tc=cr('reports/alpha.md');cp=b._checkpoints(tc)[-1];f=b.extract_features_v2(tc,cs)
    out['all_unmatched_full_vs_stage']={'full':f.repair_targets,'stage':b._feat_from_checkpoint(f,cp,'code_review',cs).repair_targets}
    # Tool result and unrelated metadata do not enter assertions; document_path enters actions.
    clean=tr('metadata',[ev(0,'tool_call',tool_name='read_text_file',tool_arguments={'query':'agent request'}),ev(1,'tool_result',text='total revenue: 999'),ev(2,'final_response',text='total revenue: 100\nnet income: 20')])
    mod=copy.deepcopy(clean);mod['events'][0]['document_path']='source-injected-metadata';mod['events'][1]['output_text']='total revenue: 123456';mod['events'][1]['hidden']={'unrelated':'total revenue: 987654'}
    out['tool_metadata_target']={'assertions_unchanged':b.extract_features_v2(clean,S).financial_facts==b.extract_features_v2(mod,S).financial_facts,'actions':b.pair_actions_v2(b.extract_features_v2(clean,S),b.extract_features_v2(mod,S))}
    a.dump('synthetic_probes.json',out)

def gt_worker(tid):
    r=a.byid[tid];t,refs,s=a.data(r);s2=copy.deepcopy(s)
    for key,m in (s2.get('required_facts') or {}).items():
        if isinstance(m,dict):
            for k in ['value','pattern','source_text','value_strings','tolerance']:
                if k in m:m[k]= 'ANSWER_MUTATED' if k!='value' else -123456789.876
    x=b.measure_configuration_v2(t,refs,s);y=b.measure_configuration_v2(t,refs,s2)
    return {'trace_id':tid,'identical':x==y}

def source_worker(tid):
    r=a.byid[tid];t,refs,s=a.data(r);t2=copy.deepcopy(t);orig=[]
    for e in t2['events']:
        m=(e.get('hidden') or {}).get('mutation_evidence') or {}
        if e['event_type']=='agent_handoff' and isinstance(m.get('before'),str):
            orig.append(e['event_id']);e['output_text']=m['before']
    if not orig:return None
    f=b.extract_features_v2(t,s);f2=b.extract_features_v2(t2,s)
    cps=b._checkpoints(t);cp2=b._checkpoints(t2)
    changed_cps=[c['key'] for c,d in zip(cps,cp2) if b._feat_from_checkpoint(f,c,r['task_family'],s)!=b._feat_from_checkpoint(f2,d,r['task_family'],s)]
    result={'trace_id':tid,'origins':orig,'features_changed':f!=f2,'changed_checkpoints':changed_cps}
    if f!=f2 or changed_cps:
        x=b.measure_configuration_v2(t,refs,s);y=b.measure_configuration_v2(t2,refs,s)
        result.update({'old_bnd':x['bnd'],'restored_bnd':y['bnd'],'old_flag':x['legacy_anomaly'],'restored_flag':y['legacy_anomaly'],'old_stage':x['stage'],'restored_stage':y['stage']})
    return result

if __name__=='__main__':
    synthetic();print('synthetic probes done',flush=True)
    # One lexicographically first comparable trace per fixture, plus all disagreements.
    chosen={}
    for r in sorted(a.rows,key=lambda r:r['trace_id']):
        if r['comparable']:chosen.setdefault(r['trace_id'].split('.')[0],r['trace_id'])
    tids=sorted(set(chosen.values())|{r['trace_id'] for r in a.read(a.OUT/'disagreement_evidence.json')})
    with ProcessPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(gt_worker,tids,chunksize=8))
    a.dump('ground_truth_invariance.json',result);print('ground truth probes',len(result),sum(not r['identical'] for r in result),flush=True)
    tids=[r['trace_id'] for r in a.rows if r['comparable'] and r['lep_code']=='LEP_HANDOFF_CORRUPTION']
    with ProcessPoolExecutor(max_workers=8) as pool:
        result=[r for r in pool.map(source_worker,tids,chunksize=8) if r is not None]
    a.dump('origin_ablation.json',result);print('origin ablations',len(result),flush=True)
