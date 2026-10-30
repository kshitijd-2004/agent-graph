import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

BASE='analysis/full_production_consolidated/source/traces/'

def load(fn):
    return json.loads(Path(BASE+fn).read_text()) if fn.startswith(('0',)) and '/' not in fn else json.loads(Path(fn).read_text())

def shape(trace_id):
    """Find the trace file by id (cached via index file if present), else glob."""
    idx = Path('analysis/full_production_consolidated/source/trace_index.json')
    fn = None
    if idx.exists():
        d = json.loads(idx.read_text())
        # index may map trace_id->filename
        m = d.get('trace_id_to_file', d)
        if isinstance(m, dict) and trace_id in m:
            fn = m[trace_id]
    if fn is None:
        import glob
        hits = glob.glob(BASE+f'*{trace_id}*.json')
        # fallback: search by content is expensive; use grep
        fn = None
    return fn

def structure(fn):
    t = load(fn)
    evs = t['events']
    roles_seq = [e['agent_role'] for e in evs]
    # find first coordinator block
    first_coord = next((i for i,e in enumerate(evs) if e['agent_role']=='coordinator'), None)
    # fanout detection: any transition depending on an early coordinator handoff
    coord_handoffs = [e['event_index'] for e in evs if e['agent_role']=='coordinator' and e['event_type']=='agent_handoff']
    # gather workers and their transition deps
    workers = {}
    for e in evs:
        if e['agent_role'] in ('specialist_a','specialist_b','synthesizer') and e['event_type']=='topology_transition':
            workers[e['agent_role']] = e['depends_on']
    parallel = any(str(h) in (workers.get(r) or []) for h in coord_handoffs for r in workers)
    return {
        'trace_id': t['trace_id'],
        'n_events': len(evs),
        'first_coordinator_idx': first_coord,
        'coordinator_handoffs': coord_handoffs,
        'worker_transition_deps': workers,
        'parallel_fanout_from_coordinator': parallel,
    }

if __name__=='__main__':
    for fn in sys.argv[1:]:
        print(json.dumps(structure(fn), indent=1))
