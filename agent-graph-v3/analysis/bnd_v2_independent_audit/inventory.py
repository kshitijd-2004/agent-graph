"""Independent source inventory, with no production writes."""
import json,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
OUT=Path(__file__).resolve().parent
def inspect(p):
    raw=p.read_bytes();t=json.loads(raw);m=t['metadata']
    return {'path':str(p),'trace_id':t['trace_id'],'sha256':hashlib.sha256(raw).hexdigest(),
            **{k:m.get(k) for k in ['attempt_id','condition','fixture_id','topology','execution_variant','repetition_index','lep_codes','dataset_eligible']}}
if __name__=='__main__':
    ps=sorted(Path('analysis/full_production_consolidated/source/traces').glob('*_trace.json'))
    with ProcessPoolExecutor(max_workers=8) as pool:rs=list(pool.map(inspect,ps,chunksize=32))
    benign=[];lep=[];other=[];groups=defaultdict(list)
    for r in rs:
        if r['condition'] in ['benign','counterfactual'] or not r['lep_codes']:
            benign.append(r)
            if r['fixture_id'] and r['repetition_index'] is not None:groups[(r['fixture_id'],r['topology'],r['execution_variant'] or 'standard',r['repetition_index'])].append(r)
        elif r['condition']=='single_lep' and r['dataset_eligible'] is not False:lep.append(r)
        else:other.append(r)
    cells=Counter(k[:3] for k in groups)
    summary={'source_files':len(rs),'benign_like':len(benign),'eligible_lep':len(lep),'other':len(other),'benign_cells':len(cells),'complete_cells':sum(v>=3 for v in cells.values()),
             'duplicate_benign_cell_repetition_groups':sum(len(v)>1 for v in groups.values()),'benign_repetition_count_histogram':dict(Counter(cells.values())),
             'keys':{name:{k:{'missing':sum(not r[k] for r in vals),'duplicate_excess':sum(n-1 for n in Counter(r[k] for r in vals if r[k]).values())} for k in ['trace_id','attempt_id','sha256']} for name,vals in [('all',rs),('eligible_lep',lep)]}}
    (OUT/'source_inventory.json').write_text(json.dumps(rs,indent=1))
    (OUT/'source_inventory_summary.json').write_text(json.dumps(summary,indent=2));print(summary)
