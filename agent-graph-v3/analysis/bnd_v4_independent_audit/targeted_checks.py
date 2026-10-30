import copy
from audit import *

@functools.lru_cache(maxsize=15000)
def finalkeys(tid,fam):
    t=trace(tid); s=spec_for(tid)
    fn=v.extract_code_stances_v4 if fam=='code_review' else v.extract_research_verdicts_v4
    return {k for k,x in fn(v._final_response_text(t),s)[0].items() if x.observed()}

out={}; n=0; example=None
for line in (LEDGER/'bnd_v4_per_trace.jsonl').open():
    r=json.loads(line)
    if not r['comparable'] or r['task_family'] not in ('code_review','research_synthesis') or 'decisions' not in r['schema']['component_mask']:continue
    fam,tid=r['task_family'],r['trace_id']; field='final_stances' if fam=='code_review' else 'final_claims'
    ks=[{k for k,x in r['features'][tid][field].items() if x['state'] in ('observed','absent')}]+[finalkeys(x,fam) for x in r['references']]
    basis=[ks[i]&ks[j] for i,j in [(1,2),(1,3),(2,3),(0,1),(0,2),(0,3)]]
    pairs=r['pair_benign']+r['pair_lep']
    valid=[tuple(sorted(b)) for p,b in zip(pairs,basis) if p['total'] is not None]
    if len(set(valid))>1:
        n+=1
        if example is None and len(valid)==6:example={'trace_id':tid,'path':INDEX[tid],'valid_decision_bases':valid,'pairs':pairs,'schema':r['schema']}
out['different_decision_bases_among_available_pairs']=n;out['decision_example']=example

selected=[('fin_finqa_069_pkg_2004.bv.LEP_TOOL_RESULT_CORRUPTION.many_to_one.00.std__b',[27,37]),
          ('fin_finqa_009_awk_2017.rl.LEP_MEMORY_POISONING.single_origin.00.mem__b',[53,83]),
          ('code_review_swe_007_django_django_15569.rl.LEP_INDIRECT_PROMPT_INJECTION.single_origin.00.std__b',[8,25])]
out['extraction_cases']=[]
for tid,inds in selected:
    t=trace(tid);s=spec_for(tid);fam=t['metadata']['task_family']
    fn=v.extract_financial_facts_v4 if fam=='financial_analysis' else v.extract_code_stances_v4
    for e in t['events']:
        if e['event_index'] in inds:
            vals,diags=fn(e.get('output_text',''),s)
            out['extraction_cases'].append({'trace_id':tid,'path':INDEX[tid],'event_index':e['event_index'],'text':e.get('output_text'), 'values':{k:x.jsonable() for k,x in vals.items()},'diagnostics':diags})

# Example of unfixed stage masks using real aligned checkpoints and their three matched references.
tid='res_scifact_119_1351_1352.bv.LEP_INDIRECT_PROMPT_INJECTION.many_to_one.00.std__b'
for line in (LEDGER/'bnd_v4_per_trace.jsonl').open():
    r=json.loads(line)
    if r['trace_id']==tid:break
t=trace(tid);s=spec_for(tid);refs=[trace(x) for x in r['references']];sch=v.build_comparison_schema(refs,s)
feat=[];cps=[]
for rt in [t]+refs:
    view=v.authored_view(rt); f=v.extract_features_v4(rt,s,view)
    cps.append({c.key:v._feat_from_checkpoint_v4(f,c,'research_synthesis',s) for c in v.checkpoints_v4(rt,view)})
sc=[]
for key in cps[0]:
    if not all(key in x for x in cps):continue
    pairs=[]
    for i,j in [(1,2),(1,3),(2,3),(0,1),(0,2),(0,3)]:
        total,comps,unavail=v._pair_computation(cps[i][key],cps[j][key],'research_synthesis',v.ComponentWeights(),sch.final_components if key=='final' else sch.intermediate_components,sch.stage_assertion_keys,key=='final',key=='final')
        pairs.append({'pair':[i,j],'total':total,'assertion_basis':comps['assertions'][1],'unavailable':unavail})
    sc.append({'key':key,'pairs':pairs})
out['stage_case']={'trace_id':tid,'path':INDEX[tid],'checkpoints':sc}

# Extra historical read leaves final output metric unchanged.
base=v.stage_deviation_v4(t,refs,s);mut=copy.deepcopy(t)
mut['events'].insert(0,{'event_id':'audit_read','event_index':-1,'event_type':'tool_call','agent_role':'audit','tool_name':'read_text_file','tool_arguments':{'path':'audit/read.md'},'depends_on':[]})
after=v.stage_deviation_v4(mut,refs,s)
out['final_action_separation']={'before_final':base.d_final_output,'after_final':after.d_final_output,'before_actions':base.d_action_history,'after_actions':after.d_action_history}
dump('targeted_checks.json',out)
print(n,example['trace_id'])
