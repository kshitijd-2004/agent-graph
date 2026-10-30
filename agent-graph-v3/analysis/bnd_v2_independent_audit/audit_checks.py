"""Read-only offline checks; all artifacts are confined to this directory."""
import copy
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmark import baseline_deviation_v2 as b

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
def read(p):
    return json.loads(Path(p).read_text())
def lines(p):
    return [json.loads(s) for s in Path(p).open()]
rows = lines(ROOT/'full_production_baseline_deviation_v2/bnd_v2_per_trace.jsonl')
byid = {r['trace_id']:r for r in rows}
old = {r['trace_id']:r for r in lines(ROOT/'full_production_baseline_deviation_v1/bnd_per_trace.jsonl')}
ledger = lines(ROOT/'full_production_id_trc_v2/propagation_metrics.jsonl')
led = {r['trace_id']:r for r in ledger}
idx = read(ROOT/'bnd_v1_validation/idx_traces.json')
@lru_cache(maxsize=256)
def trace(tid):
    t = read(idx[tid])
    assert t['trace_id'] == tid
    return t
@lru_cache(maxsize=1024)
def spec(fid):
    return read(Path('workspace_fixtures')/fid/'manifest.json')
def data(r):
    t = trace(r['trace_id'])
    return t, [trace(tid) for tid in r['references']], spec(t['metadata']['fixture_id'])
def sig(t, s, cp=None):
    f = b.extract_features_v2(t,s)
    if cp is not None:
        f = b._feat_from_checkpoint(f,cp,f.task_family,s)
    return dict(b._assertion_signature(f,f.task_family) or ())
def present(v):
    return v is not None and v != 'UNKNOWN'
def dump(name,x):
    (OUT/name).write_text(json.dumps(x,indent=2,sort_keys=True,default=str)+'\n')

def arithmetic():
    c = [r for r in rows if r['comparable']]
    errors=[]
    for r in c:
        ps=r['pair_benign']+r['pair_lep']
        avail=[k for k in b.COMPONENTS if all(p[k] is not None for p in ps)]
        w=r['weights']; den=sum(w[k] for k in avail)
        totals=[sum(p[k]*w[k] for k in avail)/den for p in ps]
        db=sum(totals[:3])/3; dl=sum(totals[3:])/3; th=max(totals[:3])
        if any(abs(x-y)>1e-14 for x,y in zip(totals,[p['total'] for p in ps])) or abs(dl-r['d_lep'])>1e-14 or abs(db-r['d_benign'])>1e-14 or abs(dl-db-r['bnd'])>1e-14 or abs(th-r['threshold']['t'])>1e-14 or (dl>th)!=r['legacy_anomaly']:
            errors.append(r['trace_id'])
    cases=read(ROOT/'bnd_v1_validation/recovery_case_studies_data.json')
    recovered=[v for v in cases if v['v2_transient']=='detected' and v['v2_persistent']=='not_detected']
    stats={}
    for name,rs in [('all277',cases),('true86',recovered),('miss33',[v for v in recovered if not v['bnd_flag']])]:
        stats[name]={'n':len(rs), **{k:sum(byid[v['trace_id']]['stage'].get(k) is True for v in rs) for k in ['high_intermediate_deviation','final_deviation','behavioral_convergence','verified_recovery']}}
    out={'n':len(rows),'comparable':len(c),'anomaly':sum(r['legacy_anomaly'] for r in c),'arithmetic_mismatches':errors,'recovery_subsets':stats,
         'flips':Counter(str(old[r['trace_id']]['behavioral_anomaly'])+' -> '+str(r['legacy_anomaly']) for r in c),
         'intersection':len({r['trace_id'] for r in c}&set(led)),
         'join_mismatches':[r['trace_id'] for r in rows if (r['v2_status']=='joined') != (r['trace_id'] in led)],
         'no_boundary_flags':{k:sum(r['stage'].get(k) is True for r in c if r['stage'].get('verified_recovery') is None) for k in ['high_intermediate_deviation','final_deviation','behavioral_convergence']},
         'stage_counts':{k:dict(Counter(str(r['stage'].get(k)) for r in c)) for k in ['high_intermediate_deviation','final_deviation','behavioral_convergence','verified_recovery']},
         'convergence_final_high':sum(r['stage']['behavioral_convergence'] is True and r['stage']['final_deviation'] is True for r in c),
         'unique_comparable_cells':len({tuple(r['references']) for r in c}),
         'zero_threshold_rows':sum(r['threshold']['t']==0 for r in c),
         'zero_threshold_reference_triples':len({tuple(r['references']) for r in c if r['threshold']['t']==0}),
         'near_actual_threshold_001':sum(abs(r['d_lep']-r['threshold']['t'])<=.01 for r in c)}
    dump('arithmetic.json',out)
    return recovered

def disagreements(cases):
    out=[]
    for case in cases:
        r=byid[case['trace_id']]
        if r['stage']['verified_recovery'] is True: continue
        t,refs,s=data(r); cps=b._checkpoints(t); boundary=b._injection_boundary(t)
        prop=led[r['trace_id']]
        ids=set(prop.get('anomalous_event_ids',[]))
        for re in prop.get('recovery_events',[]):
            ids.update(str(re.get(k)) for k in ['event_id','seed_event_id','origin_id'])
        for e in t['events']:
            if (e.get('hidden') or {}).get('mutation_evidence'): ids.add(e['event_id'])
        events=[]
        for e in t['events']:
            if e['event_id'] not in ids and e['event_type'] not in ['agent_handoff','final_response']: continue
            events.append({k:e.get(k) for k in ['event_id','event_index','event_type','agent_role','depends_on','provenance_ids','tool_arguments','output_text','hidden']})
        by_event={str(e['event_id']):e for e in t['events']}
        bad_edges=[]
        for re in prop.get('recovery_events',[]):
            path=re.get('path',[])
            for a,z in zip(path,path[1:]):
                if a not in (by_event[z].get('depends_on') or []) and a not in (by_event[z].get('provenance_ids') or []): bad_edges.append([a,z])
        out.append({'trace_id':r['trace_id'],'path':idx[r['trace_id']],'case':case,'stage':r['stage'],'boundary':boundary,'spec':s,
                    'signatures':[{'key':cp['key'],'event_index':cp['event_index'],'signature':sig(t,s,cp)} for cp in cps],
                    'benign_full_signatures':[sig(ref,s) for ref in refs],
                    'benign_final_signatures':[sig(ref,s,b._checkpoints(ref)[-1]) for ref in refs],
                    'events':events,'propagation':prop,'invalid_direct_path_edges':bad_edges})
    dump('disagreement_evidence.json',out)

def recovery_population():
    out=[]
    for r in rows:
        if not r['comparable'] or r['stage']['verified_recovery'] is not True: continue
        t,refs,s=data(r); cps=b._checkpoints(t); boundary=b._injection_boundary(t)
        final=sig(t,s,cps[-1]); witnesses=[]
        refmaps=[{cp['key']:cp for cp in b._checkpoints(ref)} for ref in refs]
        for cp in cps:
            if cp['is_final'] or cp['event_index']<=boundary: continue
            inter=sig(t,s,cp)
            if inter==final: continue
            changes=[k for k in inter.keys() | final.keys() if inter.get(k)!=final.get(k)]
            observed=[k for k in changes if present(inter.get(k)) and present(final.get(k))]
            same_stage=[sig(ref,s,m[cp['key']]) for ref,m in zip(refs,refmaps) if cp['key'] in m]
            witnesses.append({'key':cp['key'],'event_index':cp['event_index'],'changed_keys':changes,'observed_value_changes':observed,
                              'inter_signature':inter,'final_signature':final,'matches_benign_same_stage':inter in same_stage})
        out.append({'trace_id':r['trace_id'],'family':r['task_family'],'lep':r['lep_code'],'prop_class':r['v2_classification'],
                    'high_intermediate':r['stage']['high_intermediate_deviation'],'final_deviation':r['stage']['final_deviation'],
                    'witnesses':witnesses})
    dump('recovery_population.json',out)
    summary={'n':len(out),'by_family':Counter(r['family'] for r in out),'by_lep':Counter(r['lep'] for r in out),
             'by_prop_class':Counter(str(r['prop_class']) for r in out),
             'no_observed_value_change':sum(not any(w['observed_value_changes'] for w in r['witnesses']) for r in out),
             'all_witnesses_match_a_benign_same_stage':sum(all(w['matches_benign_same_stage'] for w in r['witnesses']) for r in out),
             'any_witness_matches_a_benign_same_stage':sum(any(w['matches_benign_same_stage'] for w in r['witnesses']) for r in out),
             'high_intermediate_false':sum(not r['high_intermediate'] for r in out),'final_deviation_true':sum(r['final_deviation'] for r in out)}
    dump('recovery_population_summary.json',summary)

if __name__=='__main__':
    cases=arithmetic(); print('arithmetic done',flush=True)
    disagreements(cases); print('21 disagreements collected',flush=True)
    recovery_population(); print('1602 recovery signature checks done',flush=True)
