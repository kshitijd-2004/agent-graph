import json, os, glob
TR='/u/kdhande/agent-graph/agent-graph-v3/analysis/downstream_propagation_corrected/staged_v2/merged/traces'
t2p={}
for fn in glob.glob(TR+'/*_trace.json'):
    with open(fn) as fh:
        t=json.load(fh)
    tid=t.get('trace_id')
    if tid: t2p[tid]=fn
json.dump(t2p, open('/tmp/agrade_audit2/t2p.json','w'))
print('index:', len(t2p), file=__import__('sys').stderr)
