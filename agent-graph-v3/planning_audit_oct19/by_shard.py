"""Read-only: locate missing work by shard, including timed-out shards."""
import collections
import glob
import json

inv = json.load(open('planning_audit_oct19/inventory.json'))
slot_shard = {}
for p in glob.glob('production_run/node_*/benchmark_plan.json'):
    node = p.split('/')[-2].replace('node_', '')
    for e in json.load(open(p))['entries']:
        slot_shard[e['scenario_id']] = node

TIMEOUTS = {'0', '3', '6', '47', '61', '62', '65', '145', '155'}
ZERO_ACCEPT = {'44', '45', '49', '50', '52', '73', '74', '75', '76', '77', '78', '79',
               '80', '81', '82', '83', '84', '85', '93', '94', '100', '101', '104',
               '105', '106', '107', '108', '109', '110', '111', '113', '114', '115',
               '116', '117', '118', '119', '120', '121', '122'}

bm = [b for b in inv['benign_slots_missing'] if slot_shard.get(b['scenario_id']) in TIMEOUTS]
print('benign slots missing in timed-out shards:', len(bm))

mc = json.load(open('planning_audit_oct19/missing_clean_slots.json'))
mct = [m for m in mc
       if slot_shard.get('%s_%s_%s_%02d_benign' % (m['topology'], m['fixture_id'],
                                                   m['execution_variant'],
                                                   m['repetition_index'])) in TIMEOUTS]
print('analysis-relevant clean slots in timed-out shards:', len(mct))

mcz = [m for m in mc
       if slot_shard.get('%s_%s_%s_%02d_benign' % (m['topology'], m['fixture_id'],
                                                   m['execution_variant'],
                                                   m['repetition_index'])) in ZERO_ACCEPT]
print('analysis-relevant clean slots in zero-accept shards:', len(mcz))

rec_t = [r for r in inv['perturbed_slots_recoverable'] if slot_shard.get(r['scenario_id']) in TIMEOUTS]
print('recoverable perturbed slots in timed-out shards:', len(rec_t))
other_t = [r for r in inv['perturbed_slots_other'] if slot_shard.get(r['scenario_id']) in TIMEOUTS]
print('other perturbed slots in timed-out shards:', len(other_t))

# Shard distribution of all proposed work
work = collections.Counter()
for m in mc:
    sid = '%s_%s_%s_%02d_benign' % (m['topology'], m['fixture_id'],
                                   m['execution_variant'], m['repetition_index'])
    work[(slot_shard.get(sid), 'clean')] += 1
for r in inv['perturbed_slots_recoverable']:
    work[slot_shard.get(r['scenario_id']), 'perturbed'] += 1
for r in inv['perturbed_slots_other']:
    work[slot_shard.get(r['scenario_id']), 'other'] += 1

shards = collections.defaultdict(lambda: collections.Counter())
for (shard, kind), n in work.items():
    shards[shard][kind] = n
print()
print('shards touched by proposed reruns: %d' % len(shards))
totals = [(s, c['clean'] + c['perturbed'] + c['other']) for s, c in shards.items()]
totals.sort(key=lambda x: -x[1])
print('per-shard work: min=%d median=%d max=%d' % (
    totals[-1][1], totals[len(totals) // 2][1], totals[0][1]))
print('total proposed executions: %d' % sum(t[1] for t in totals))
json.dump({s: dict(c) for s, c in shards.items()},
          open('planning_audit_oct19/shard_work.json', 'w'), indent=2)
