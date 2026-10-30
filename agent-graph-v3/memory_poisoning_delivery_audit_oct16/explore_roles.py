import json, glob
from collections import Counter, defaultdict

c_role = defaultdict(Counter)
c_desc = Counter()
c_origins_per = Counter()
n = 0
paths_no_ev = []
for p in glob.glob('production_run/node_*/traces/*MEMORY_POISONING*_trace.json'):
    d = json.load(open(p))
    n += 1
    tf = d['metadata'].get('task_family')
    k = d['metadata'].get('topology')
    norigin = 0
    for e in d['events']:
        hid = e.get('hidden') or {}
        inj = hid.get('lep_injection') or {}
        lab = e.get('event_labels') or {}
        if hid.get('injected') is True or lab.get('is_injection_origin'):
            norigin += 1
            c_role[tf][(inj.get('target_agent_intended'), inj.get('agent_role_actual'),
                        bool(inj.get('fired_in_intended_role')))] += 1
            v = (e.get('tool_arguments') or {}).get('value', '') or ''
            if v.startswith('Review of'):
                c_desc['template_defect_absence'] += 1
            elif 'PRELIMINARY and should not be used' in v:
                c_desc['legacy_stale_marker'] += 1
            elif v.startswith('Verified:'):
                c_desc['verified_wrong_fact'] += 1
            else:
                c_desc['other_false_fact'] += 1
    c_origins_per[norigin] += 1

print('traces:', n)
print('origins per trace:', dict(c_origins_per))
print('\npayload kinds:', c_desc.most_common())
for tf, cc in sorted(c_role.items()):
    print(f'\n[{tf}] origin events: {sum(cc.values())}')
    for kk, v in cc.most_common(14):
        print('  ', kk, v)
