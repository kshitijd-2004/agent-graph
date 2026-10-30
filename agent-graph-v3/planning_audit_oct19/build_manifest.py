"""Build the exact rerun manifest from the inventory.

Categories:
  A. clean_reference_topup — benign executions required to reach 3 valid matched
     clean references for cells that already hold >=1 admitted perturbed trace.
  B. recoverable_perturbed — perturbed slots whose only failure was a backend
     (infrastructure) error, or that were never attempted because the shard hit
     the Slurm time limit.
  C. unreachable_injection_target — perturbed slots whose only recorded outcome
     is injection_target_unreachable. NOT scheduled for retry.
  D. non_recoverable_other — perturbed slots rejected for injection count
     mismatch, event-limit, or protocol violation. Not scheduled; requires a
     scientific decision.

Each manifest row carries the full execution identity needed to reproduce the
run with the existing planner, plus the reason it is being rerun.
"""
import collections
import glob
import json

ROOT = '/u/kdhande/agent-graph/agent-graph-v3'
inv = json.load(open(ROOT + '/planning_audit_oct19/inventory.json'))
canon = {e['scenario_id']: e
         for e in json.load(open(ROOT + '/planning_audit_oct19/canonical_plan.json'))}
missing_clean = json.load(open(ROOT + '/planning_audit_oct19/missing_clean_slots.json'))

# Map a scenario_id back to the shard that owns it (for batching only).
slot_shard = {}
for p in glob.glob(ROOT + '/production_run/node_*/benchmark_plan.json'):
    node = p.split('/')[-2].replace('node_', '')
    for e in json.load(open(p))['entries']:
        slot_shard[e['scenario_id']] = node

manifest = []

# ── A. clean reference top-ups ─────────────────────────────────────────────
# A slot is needed only when the matching clean-reference group has fewer than
# three distinct admitted repetitions AND at least one admitted perturbed trace
# depends on it.
for m in missing_clean:
    fid, topo, variant, rep = (m['fixture_id'], m['topology'],
                               m['execution_variant'], m['repetition_index'])
    sid = '%s_%s_%s_%02d_benign' % (topo, fid, variant, rep)
    entry = canon.get(sid)
    if entry is None:
        raise SystemExit('No canonical plan entry for clean slot %s' % sid)
    manifest.append({
        'category': 'A_clean_reference_topup',
        'scenario_id': sid,
        'task_family': entry['task_family'],
        'fixture_id': fid,
        'task_variant': entry['task_variant'],
        'topology': topo,
        'propagation_mode': entry['propagation_mode'],
        'lep_code': '',
        'lep_codes': [],
        'execution_variant': variant,
        'condition': 'benign',
        'repetition_index': rep,
        'pair_tag': entry['pair_tag'],
        'is_baseline': True,
        'memory_protocol': 'write_interception_v1',
        'origin_shard': slot_shard.get(sid),
        'reason': ('clean reference group has <3 admitted repetitions; '
                   '%d admitted perturbed trace(s) currently blocked'
                   % m['lep_traces_blocked']),
        'dependent_lep_codes': m['lep_codes'],
        'retry_count': 1,
    })

# ── B. recoverable perturbed ───────────────────────────────────────────────
for r in inv['perturbed_slots_recoverable']:
    e = r['entry']
    manifest.append({
        'category': 'B_recoverable_perturbed',
        'scenario_id': r['scenario_id'],
        'task_family': e['task_family'],
        'fixture_id': e['fixture_id'],
        'task_variant': e['task_variant'],
        'topology': e['topology'],
        'propagation_mode': e['propagation_mode'],
        'lep_code': e['lep_code'],
        'lep_codes': e['lep_codes'],
        'execution_variant': e['execution_variant'],
        'condition': e['condition'],
        'repetition_index': e['repetition_index'],
        'pair_tag': e['pair_tag'],
        'is_baseline': False,
        'memory_protocol': e.get('memory_protocol', 'write_interception_v1'),
        'origin_shard': slot_shard.get(r['scenario_id']),
        'reason': 'recoverable failure: %s' % ','.join(r['termination_reasons']),
        'retry_count': 1,
    })

# ── B2. never-attempted slots (Slurm time limit cut the shard short) ───────
for r in inv['perturbed_slots_other']:
    if r.get('reason') != 'no_attempt_recorded':
        continue
    e = r['entry']
    manifest.append({
        'category': 'B_never_attempted',
        'scenario_id': r['scenario_id'],
        'task_family': e['task_family'],
        'fixture_id': e['fixture_id'],
        'task_variant': e['task_variant'],
        'topology': e['topology'],
        'propagation_mode': e['propagation_mode'],
        'lep_code': e['lep_code'],
        'lep_codes': e['lep_codes'],
        'execution_variant': e['execution_variant'],
        'condition': e['condition'],
        'repetition_index': e['repetition_index'],
        'pair_tag': e['pair_tag'],
        'is_baseline': False,
        'memory_protocol': e.get('memory_protocol', 'write_interception_v1'),
        'origin_shard': slot_shard.get(r['scenario_id']),
        'reason': 'shard hit Slurm time limit before this slot was dispatched',
        'retry_count': 1,
    })

# ── C. unreachable injection targets (report only, no retry) ───────────────
unreachable = []
for r in inv['perturbed_slots_unreachable']:
    e = r['entry']
    unreachable.append({
        'scenario_id': r['scenario_id'],
        'task_family': e['task_family'],
        'fixture_id': e['fixture_id'],
        'topology': e['topology'],
        'propagation_mode': e['propagation_mode'],
        'lep_code': e['lep_code'],
        'execution_variant': e['execution_variant'],
        'termination_reasons': r['termination_reasons'],
        'origin_shard': slot_shard.get(r['scenario_id']),
    })

# ── D. other non-recoverable (report only) ────────────────────────────────
other = []
for r in inv['perturbed_slots_other']:
    if r.get('reason') == 'no_attempt_recorded':
        continue
    e = r['entry']
    other.append({
        'scenario_id': r['scenario_id'],
        'task_family': e['task_family'],
        'fixture_id': e['fixture_id'],
        'topology': e['topology'],
        'propagation_mode': e['propagation_mode'],
        'lep_code': e['lep_code'],
        'execution_variant': e['execution_variant'],
        'termination_reasons': r.get('termination_reasons', []),
        'admission_reasons': r.get('admission_reasons', []),
        'origin_shard': slot_shard.get(r['scenario_id']),
    })

# ── Deduplicate and validate the manifest ─────────────────────────────────
ids = [m['scenario_id'] for m in manifest]
dupes = [i for i, c in collections.Counter(ids).items() if c > 1]
assert not dupes, 'duplicate manifest identities: %s' % dupes[:5]
assert len(set(ids)) == len(manifest)

# No manifest row may correspond to an already-admitted trace.
admitted = set()
for key in ('admitted_perturbed_traces',):
    pass
import os
for p in glob.glob(ROOT + '/production_run/node_*/traces/*_trace.json'):
    d = json.load(open(p))
    admitted.add(d['metadata'].get('scenario_id'))
overlap = [i for i in ids if i in admitted]
assert not overlap, 'manifest would regenerate admitted traces: %s' % overlap[:5]

counts = collections.Counter(m['category'] for m in manifest)
print('manifest rows:', len(manifest))
for k, v in sorted(counts.items()):
    print('  %-28s %d' % (k, v))
print('unreachable (no retry):', len(unreachable))
print('other non-recoverable (no retry):', len(other))

json.dump({'schema': 'agentprop-rerun-manifest-v1',
           'source_inventory': 'planning_audit_oct19/inventory.json',
           'generated_from': 'production_run/ (read-only)',
           'settings': {'num_repetitions': 1, 'num_benign_repetitions': 3,
                        'max_events': 300, 'max_agent_turns': 80,
                        'model_name': 'Qwen/Qwen2.5-72B-Instruct-AWQ',
                        'temperature': 0.4, 'seed': 42, 'dry_run': False,
                        'backend_name': 'vllm',
                        'vllm_url': 'http://127.0.0.1:8001/v1'},
           'executions': manifest,
           'excluded_unreachable': unreachable,
           'excluded_other': other},
          open(ROOT + '/planning_audit_oct19/rerun_manifest.json', 'w'), indent=1)
print('wrote planning_audit_oct19/rerun_manifest.json')
