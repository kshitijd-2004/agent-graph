import copy
import dataclasses
import re
from audit import *

@functools.lru_cache(maxsize=15000)
def final_map(tid,fam):
    t=trace(tid); s=spec_for(tid); text=v._final_response_text(t)
    fn=v.extract_code_stances_v4 if fam=='code_review' else v.extract_research_verdicts_v4
    return {k:x.jsonable() for k,x in fn(text,s)[0].items()}

def obskeys(m): return {k for k,x in m.items() if x.get('state') in ('observed','absent')}

def observation(text,fam,s):
    fn={'code_review':v.extract_code_stances_v4,'research_synthesis':v.extract_research_verdicts_v4,'financial_analysis':v.extract_financial_facts_v4}.get(fam)
    return {k:x.jsonable() for k,x in fn(text,s)[0].items()} if fn else {}

counts=collections.Counter(); examples={}; checks=[]; samples={}; raw_relapses=[]
eps=json.loads((OUT/'episodes_checked.json').read_text())
ep_by=collections.defaultdict(list)
for ep in eps: ep_by[ep['trace_id']].append(ep)

for line in (LEDGER/'bnd_v4_per_trace.jsonl').open():
    r=json.loads(line)
    if not r['comparable']: continue
    tid,fam,stage=r['trace_id'],r['task_family'],r['stage']
    for cp in stage['checkpoints']:
        if r['lep_code']=='LEP_INPUT_DISREGARD' and cp.get('is_origin'):
            counts['id_origin_total']+=1
            counts['id_origin_'+str(cp.get('reason','available'))]+=1
    # Independently enumerate effective decision keys for all six whole-trace pairs.
    if fam in ('code_review','research_synthesis') and 'decisions' in r['schema']['component_mask']:
        field='final_stances' if fam=='code_review' else 'final_claims'
        maps=[r['features'][tid][field]]+[final_map(x,fam) for x in r['references']]
        bases=[obskeys(maps[i]) & obskeys(maps[j]) for i,j in [(1,2),(1,3),(2,3),(0,1),(0,2),(0,3)]]
        if len({tuple(sorted(x)) for x in bases})>1:
            counts['decision_basis_drift_rows']+=1
            counts['decision_basis_drift_with_defined_flag']+=r['behavioral_anomaly'] is not None
            examples.setdefault('decision_basis_drift',{'trace_id':tid,'path':INDEX[tid],'six_bases':[sorted(x) for x in bases],'pair_benign':r['pair_benign'],'pair_lep':r['pair_lep'],'maps':maps})
    # Re-extract both ends of every reported episode from production events and its supporting reference.
    if tid in ep_by:
        t=trace(tid); s=spec_for(tid); view=v.authored_view(t)
        ec={e['event_id']:e for e in t['events']}
        for ep in ep_by[tid]:
            p,q=ec[ep['p_event']],ec[ep['q_event']]
            pv=observation(view.authored_by_index.get(p['event_index'],''),fam,s).get(ep['key'])
            qv=observation(view.authored_by_index.get(q['event_index'],''),fam,s).get(ep['key'])
            rt=trace(r['references'][ep['benign_support_ref']]); rv=v.authored_view(rt)
            rcp=next(c for c in v.checkpoints_v4(rt,rv) if c.key==ep['q_site'])
            bv=observation(rcp.state_text,fam,s).get(ep['key'])
            valid=all(x and x.get('state') in ('observed','absent') for x in (pv,qv,bv)) and pv.get('value')==ep['value_at_peak'] and qv.get('value')==ep['corrected_value']==bv.get('value')
            counts['episode_extractions_checked']+=1; counts['episode_extraction_mismatch']+=not valid
    # Source-only and ground-truth invariance on 5 traces per LEP, spanning modes.
    sk=(r['lep_code'],r['propagation_mode'])
    if sk not in samples or (len(samples[sk])<2 and len(r['features'][tid]['provenance'].get('origins',[]))>1):
        samples.setdefault(sk,[]).append(r)
    # Raw research checkpoint scan includes stages excluded from the ledger for alignment.
    if fam=='research_synthesis':
        t=trace(tid); s=spec_for(tid); view=v.authored_view(t); boundary=v.injection_boundary_index(t)
        cps=v.checkpoints_v4(t,view); sites=collections.defaultdict(list)
        for cp in cps:
            if cp.is_origin and not cp.origin_authored_response: continue
            if not cp.is_final and boundary is not None and cp.event_index<boundary: continue
            for key,x in observation(cp.state_text,fam,s).items():
                if x.get('state')=='observed': sites[key].append((cp,x['value'],next(e['event_id'] for e in t['events'] if e['event_index']==cp.event_index)))
        for key,ss in sites.items():
            for p,pv,pe in ss:
                for q,qv,qe in ss:
                    if pv==qv or not reach(t,pe,qe): continue
                    for z,zv,ze in ss:
                        if zv!=pv or not reach(t,qe,ze): continue
                        if stage.get('relapse'): continue
                        support=[]
                        for refid in r['references']:
                            rt=trace(refid)
                            rc=next((c for c in v.checkpoints_v4(rt,v.authored_view(rt)) if c.key==q.key),None)
                            if rc and observation(rc.state_text,fam,s).get(key,{}).get('value')==qv: support.append(refid)
                        if support:
                            raw_relapses.append({'trace_id':tid,'path':INDEX[tid],'key':key,'events':[pe,qe,ze],'values':[pv,qv,zv],'support':support})

for group in samples.values():
    for r in group:
        tid=r['trace_id']; t=trace(tid); s=spec_for(tid); mut=copy.deepcopy(t)
        orig=v.injection_origins(t); by={e['event_index']:e for e in mut['events']}
        for o in orig:
            e=by[o.event_index]
            if o.delivered_is_output: e['output_text']='Claim 1: CONTRADICTED. total revenue: 987654321. applied a patch.'
            me=e.get('hidden',{}).get('mutation_evidence')
            if isinstance(me,dict): me['after']='ALTERED PAYLOAD Claim 2: SUPPORTED.'
            if o.event_type=='memory_write':
                for key in ('value','content'):
                    if key in (e.get('tool_arguments') or {}): e['tool_arguments'][key]='ALTERED POISONED MEMORY'
        f0=v.extract_features_v4(t,s); f1=v.extract_features_v4(mut,s)
        cv0=v.checkpoints_v4(t,v.authored_view(t)); cv1=v.checkpoints_v4(mut,v.authored_view(mut))
        same_features=dataclasses.asdict(f0)==dataclasses.asdict(f1)
        same_cp=cv0==cv1
        refs=[trace(x) for x in r['references']]
        score0=v.score_bnd_v4(t,refs,s); score1=v.score_bnd_v4(mut,refs,s)
        st0=v.stage_deviation_v4(t,refs,s); st1=v.stage_deviation_v4(mut,refs,s)
        sp=copy.deepcopy(s)
        for meta in sp.get('required_facts',{}).values():
            if isinstance(meta,dict): meta.update(value=987654321,tolerance=987654321,pattern='MUTATED GROUND TRUTH')
        gt=v.score_bnd_v4(t,refs,sp); gst=v.stage_deviation_v4(t,refs,sp)
        entry={'trace_id':tid,'n_origins':len(orig),'features_equal':same_features,'checkpoints_equal':same_cp,'scores_equal':score0==score1,'stages_equal':st0==st1,'ground_truth_score_equal':score0==gt,'ground_truth_stages_equal':st0==gst}
        checks.append(entry)
        print('isolation',tid,all(entry[k] for k in entry if k.endswith('equal')),flush=True)

dump('production_checks.json',{'counts':dict(counts),'examples':examples,'source_invariance':checks,'raw_research_missed_relapses':raw_relapses})
print(json.dumps(dict(counts)),flush=True)
