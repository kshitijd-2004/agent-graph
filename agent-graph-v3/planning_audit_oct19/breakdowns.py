"""Read-only breakdowns for the unreachable / recoverable classification."""
import collections
import glob
import json

c = collections.Counter()
sub = collections.Counter()
for p in glob.glob('production_run/node_*/rejected_traces/*_trace.json'):
    d = json.load(open(p))
    m = d['metadata']
    term = m.get('termination_reason')
    ui = m.get('unfulfilled_injections') or {}
    statuses = tuple(sorted({str(v.get('status')) for v in ui.values() if isinstance(v, dict)}))
    key = (str(m.get('lep_codes')), m.get('topology'), m.get('propagation_mode'),
           m.get('execution_variant'))
    if term == 'injection_target_unreachable':
        c[(key, statuses)] += 1
    if term == 'injection_count_mismatch':
        sub[(key, statuses, m.get('admission', {}).get('reason') if isinstance(m.get('admission'), dict) else None)] += 1

print('=== injection_target_unreachable by (lep, topo, mode, variant, unfulfilled status) ===')
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    print(v, k)
print()
print('=== injection_count_mismatch ===')
for k, v in sorted(sub.items(), key=lambda x: -x[1]):
    print(v, k)
