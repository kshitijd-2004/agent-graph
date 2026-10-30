"""Read-only: per-shard recoverable perturbed slot counts for sizing.

Also computes the analysis-eligibility view: how many additional clean runs are
needed only for cells that already have >=1 admitted perturbed trace (which is
all deficit cells), versus how many perturbed reruns are recoverable.
"""
import collections
import glob
import json

inv = json.load(open('planning_audit_oct19/inventory.json'))

slot_shard = {}
for p in glob.glob('production_run/node_*/benchmark_plan.json'):
    node = p.split('/')[-2]
    for e in json.load(open(p))['entries']:
        slot_shard[e['scenario_id']] = node

# ── Clean reference deficit, deduplicated by clean-reference group ──────────
groups = collections.defaultdict(lambda: {'missing': 0, 'lep_codes': set(),
                                          'blocked': 0, 'family': '', 'variant': ''})
for c in inv['clean_deficit_cells']:
    key = (c['fixture_id'], c['topology'], c['execution_variant'])
    g = groups[key]
    g['missing'] = max(g['missing'], c['missing'])
    g['lep_codes'].add(c['lep_code'])
    g['blocked'] += c['lep_traces_blocked']
    g['family'] = c['task_family']
    g['variant'] = c['execution_variant']

print('distinct clean-reference groups in deficit:', len(groups))
print('additional clean executions (max-deficit per group):', sum(g['missing'] for g in groups.values()))
print('LEP traces currently blocked:', sum(g['blocked'] for g in groups.values()))

# Which specific (group, rep) slots are missing? Only need reps that are absent.
present = collections.defaultdict(set)
for p in glob.glob('production_run/node_*/traces/*_trace.json'):
    d = json.load(open(p))
    m = d['metadata']
    if m.get('condition') in ('benign', 'counterfactual'):
        present[(m['fixture_id'], m['topology'], m['execution_variant'])].add(m['repetition_index'])

missing_clean = []
for (fid, topo, variant), g in sorted(groups.items()):
    have = present.get((fid, topo, variant), set())
    need = [r for r in (0, 1, 2) if r not in have]
    for r in need:
        missing_clean.append({'fixture_id': fid, 'topology': topo,
                              'execution_variant': variant, 'repetition_index': r,
                              'task_family': g['family'],
                              'lep_codes': sorted(g['lep_codes']),
                              'lep_traces_blocked': g['blocked']})
print('concrete missing clean slots:', len(missing_clean))
print('by variant:', collections.Counter(m['execution_variant'] for m in missing_clean))
print('by topology:', collections.Counter(m['topology'] for m in missing_clean))
print('by rep:', collections.Counter(m['repetition_index'] for m in missing_clean))
print('by family:', collections.Counter(m['task_family'] for m in missing_clean))

# ── Recoverable perturbed slots by shard ───────────────────────────────────
rec = collections.Counter(slot_shard.get(r['scenario_id'], '?')
                          for r in inv['perturbed_slots_recoverable'])
print()
print('recoverable perturbed slots:', len(inv['perturbed_slots_recoverable']))
print('distinct shards touched:', len(rec))
print('per-shard min/median/max:', min(rec.values()), sorted(rec.values())[len(rec)//2], max(rec.values()))

json.dump(missing_clean, open('planning_audit_oct19/missing_clean_slots.json', 'w'), indent=2)
