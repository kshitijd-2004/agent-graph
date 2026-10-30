from concurrent.futures import ProcessPoolExecutor
from collections import Counter
import audit_checks as a

def check(tid):
    r=a.byid[tid];t,refs,s=a.data(r);b=a.b;fam=r['task_family']
    fs=[b.extract_features_v2(tt,s) for tt in [t]+refs]
    maps=[{cp['key']:cp for cp in b._checkpoints(tt)} for tt in [t]+refs]
    mixed=[];origin_cps=[]
    origin_indices={e['event_index'] for e in t['events'] if (e.get('hidden') or {}).get('perturbed_hash') or (e.get('hidden') or {}).get('mutation_evidence')}
    for cp in r['stage']['checkpoints']:
        if not cp.get('available'):continue
        key=cp['key'];fcp=[b._feat_from_checkpoint(ff,m[key],fam,s) for ff,m in zip(fs,maps)]
        masks=[]
        for i,j in [(0,1),(0,2),(0,3),(1,2),(1,3),(2,3)]:
            d=b.pairwise_distance_v2(fcp[i],fcp[j],fam)
            allowed=['assertions','actions']+(['decisions'] if cp['is_final'] else [])
            masks.append(tuple(k for k in allowed if getattr(d,k) is not None))
        if len(set(masks))>1:mixed.append({'key':key,'masks':masks})
        if cp['event_index'] in origin_indices:origin_cps.append({'key':key,'event_index':cp['event_index']})
    return {'trace_id':tid,'mixed_pair_masks':mixed,'scored_origin_checkpoints':origin_cps}

if __name__=='__main__':
    tids=[r['trace_id'] for r in a.rows if r['comparable']]
    with ProcessPoolExecutor(max_workers=8) as pool:rs=list(pool.map(check,tids,chunksize=16))
    a.dump('stage_census.json',rs)
    out={'n':len(rs),'traces_with_mixed_pair_masks':sum(bool(r['mixed_pair_masks']) for r in rs),
         'mixed_checkpoints':sum(len(r['mixed_pair_masks']) for r in rs),'traces_with_scored_origin_checkpoints':sum(bool(r['scored_origin_checkpoints']) for r in rs),
         'scored_origin_checkpoints':sum(len(r['scored_origin_checkpoints']) for r in rs)}
    a.dump('stage_census_summary.json',out);print(out)
