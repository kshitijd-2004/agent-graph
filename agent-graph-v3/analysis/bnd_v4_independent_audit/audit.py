"""Read-only audit of frozen outputs; never executes the benchmark runner."""
import collections
import functools
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
LEDGER = ROOT / 'analysis/full_production_baseline_deviation_v4'
from benchmark import baseline_deviation_v4 as v

def dump(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

INDEX = json.loads((ROOT / 'analysis/bnd_v3_dev/trace_index.json').read_text())
@functools.lru_cache(maxsize=32)
def trace(tid):
    t = json.loads(Path(INDEX[tid]).read_text())
    assert t['trace_id'] == tid
    return t

@functools.lru_cache(maxsize=256)
def spec_for(tid):
    t = trace(tid)
    return json.loads((ROOT / 'workspace_fixtures' / t['metadata']['fixture_id'] / 'manifest.json').read_text())

def reach(t, a, b):
    """Independent backward traversal: does b depend on a?"""
    parents = {e['event_id']: e.get('depends_on', []) for e in t['events']}
    seen, todo = set(), list(parents.get(b, []))
    while todo:
        n = todo.pop()
        if n == a:
            return True
        if n not in seen:
            seen.add(n)
            todo.extend(parents.get(n, []))
    return False

def mean(xs):
    a = [x for x in xs if x is not None]
    return sum(a)/len(a) if a else None

def same(a,b):
    return a == b or (a is not None and b is not None and math.isclose(a,b,abs_tol=1e-12))

def main():
    counts, perlep, mismatches = collections.Counter(), collections.defaultdict(collections.Counter), []
    episodes, missed, absent_paths, no_initial_departure = [], [], [], []
    zero_cells, cells, truezero, truecells = set(), set(), set(), set()
    transitions = collections.Counter()
    unavailable_examples, partial_examples = [], []
    v3flags = {}
    for line in (ROOT / 'analysis/full_production_baseline_deviation_v3/bnd_v3_per_trace.jsonl').open():
        r = json.loads(line); v3flags[r['trace_id']] = r.get('behavioral_anomaly')
    for line in (LEDGER / 'bnd_v4_per_trace.jsonl').open():
        r = json.loads(line)
        counts['measured'] += 1
        if not r['comparable']:
            continue
        counts['comparable'] += 1
        tid, flag, stage = r['trace_id'], r['behavioral_anomaly'], r['stage']
        lc = perlep[r['lep_code']]
        lc['comparable'] += 1; lc[str(flag)] += 1; counts[str(flag)] += 1
        transitions[f'{v3flags.get(tid)}->{flag}'] += 1
        pair_values = {}
        for side in ('benign','lep'):
            vals = []
            for p in r['pair_'+side]:
                mask = [c for c in r['schema']['component_mask'] if r['weights'][c] > 0]
                expected = None if not mask or any(p['components'][c] is None for c in mask) else sum(r['weights'][c]*p['components'][c] for c in mask)/sum(r['weights'][c] for c in mask)
                if not same(expected,p['total']): mismatches.append([tid,side,'pair'])
                vals.append(expected); counts['pairs'] += 1
                counts[side+'_unavailable_pairs'] += expected is None
            pair_values[side] = vals
            if not same(mean(vals),r['d_'+side]): mismatches.append([tid,side,'mean'])
        db, dl = mean(pair_values['benign']),mean(pair_values['lep'])
        threshold = max((x for x in pair_values['benign'] if x is not None),default=None)
        expected_flag = None if dl is None or threshold is None else dl > threshold
        if expected_flag != flag: mismatches.append([tid,'flag'])
        if not same(None if db is None or dl is None else dl-db,r['bnd']): mismatches.append([tid,'bnd'])
        cell = (r['task_family'],r['topology'],r['propagation_mode'],tuple(r['references']))
        if threshold is not None: truecells.add(cell)
        if threshold == 0: truezero.add(cell); counts['true_zero_t_rows'] += 1
        stored_t = r['threshold']['t']
        if stored_t is not None: cells.add(cell)
        if stored_t == 0: zero_cells.add(cell); counts['stored_zero_t_rows'] += 1
        if dl is not None and stored_t is not None:
            counts['near_threshold'] += abs(dl-stored_t) <= .01
            counts['abs_bnd_near'] += abs(r['bnd']) <= .01
        else: counts['near_unavailable'] += 1
        unavailable = sum(x is None for xs in pair_values.values() for x in xs)
        counts['rows_any_unavailable'] += unavailable > 0
        if flag is None:
            counts['none_with_all_lep_void'] += all(x is None for x in pair_values['lep'])
            counts['none_with_benign_defined'] += threshold is not None
            if len(unavailable_examples)<4 or threshold is None: unavailable_examples.append({'trace_id':tid,'reasons':r['unavailable_reasons'],'schema':r['schema']})
        elif unavailable: partial_examples.append({'trace_id':tid,'pairs':r['pair_lep'],'schema':r['schema']})
        for key in ('d_peak','d_final_output','high_intermediate_deviation','persistent_final_deviation','observational_convergence','evidence_supported_correction','relapse'):
            counts[key+'_present'] += stage.get(key) is not None
            counts[key+'_true'] += stage.get(key) is True
            lc[key+'_true'] += stage.get(key) is True
        eps = r.get('correction_episodes',[])
        if not eps: continue
        t = trace(tid)
        events = {e['event_id']: e for e in t['events']}
        by_index = {e['event_index']:e['event_id'] for e in t['events']}
        for j,ep in enumerate(eps):
            counts['episodes'] += 1
            p,q = ep['p_event'],ep['q_event']
            item = {'trace_id':tid,'path':INDEX[tid],'episode_index':j,**ep,
                    'p_index':events[p]['event_index'],'q_index':events[q]['event_index'],
                    'actual_dependency_path':reach(t,p,q)}
            counts['unchanged_episodes'] += ep['value_at_peak']==ep['corrected_value']
            if not item['actual_dependency_path']: absent_paths.append(item)
            # Check all episodes for exact-value relapse, independently of first-episode selection.
            candidates = []
            for cp in stage['checkpoints']:
                fv = cp.get('lep_assertion_map',{}).get(ep['key'],{})
                eid = by_index.get(cp['event_index'])
                if fv.get('state') in ('observed','absent') and fv.get('value') == ep['value_at_peak'] and reach(t,q,eid):
                    candidates.append({'event':eid,'index':cp['event_index'],'site':cp['key'],'value':fv.get('value')})
            if candidates and not stage['relapse']:
                missed.append({**item,'readoptions':candidates})
            # Compare peak to the matched benign peak, independent of a later match.
            p_values=[]
            for refid in r['references']:
                rt=trace(refid); rv=v.authored_view(rt)
                cp=next((c for c in v.checkpoints_v4(rt,rv) if c.key==ep['p_site']),None)
                if cp:
                    ff=v.extract_features_v4(rt,spec_for(tid),rv)
                    fv=v._feat_from_checkpoint_v4(ff,cp,r['task_family'],spec_for(tid)).assertion_map(r['task_family']).get(ep['key'])
                    p_values.append(fv.jsonable() if fv else None)
            item['benign_p_values']=p_values
            if len(p_values)==3 and all(x and x.get('state')=='observed' and x.get('value')==ep['value_at_peak'] for x in p_values):
                no_initial_departure.append(item)
            episodes.append(item)
    result={'counts':dict(counts),'per_lep':dict(perlep),'arithmetic_mismatches':mismatches,'transitions':dict(transitions),
            'stored_zero_cells':len(zero_cells),'stored_defined_cells':len(cells),'benign_only_zero_cells':len(truezero),'benign_only_defined_cells':len(truecells),
            'missing_path_episodes':len(absent_paths),'missed_relapse_episodes':len(missed),'missed_relapse_traces':len({x['trace_id'] for x in missed}),
            'all_benign_peak_agreement_episodes':len(no_initial_departure),'all_benign_peak_agreement_traces':len({x['trace_id'] for x in no_initial_departure}),
            'module_sha256':hashlib.sha256((ROOT/'benchmark/baseline_deviation_v4.py').read_bytes()).hexdigest()}
    for name,data in [('ledger_checks.json',result),('episodes_checked.json',episodes),('missing_paths.json',absent_paths),('missed_relapses.json',missed),('no_initial_departure.json',no_initial_departure),('unavailable_examples.json',unavailable_examples),('partial_pairs.json',partial_examples)]: dump(name,data)
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__': main()
