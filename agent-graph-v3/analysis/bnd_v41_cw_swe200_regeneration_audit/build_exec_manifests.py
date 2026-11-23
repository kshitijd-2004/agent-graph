"""Build the executable plan manifests + sharding plan for the BND V4.1
benign regeneration batch (writes only into this audit directory).

Produces:
  cw_regen_fanout_default_manifest.json   (fixture-root: workspace_fixtures)
  v3_manifest.json                        (fixture-root: workspace_fixtures_v3;
                                          CW v3-root cells + bv/rl gap cells)
  bndv41_regen_plan.json                  (sharding plan + resource spec)

All entries are condition=benign, execution_variant=standard, lep_codes=[]
=> LEP_MEMORY_POISONING and all memory_enabled cohorts are structurally
excluded. Fresh rep indices were verified collision-free against the frozen
staged pools (0 of 937 collide).
"""
from __future__ import annotations
import json, sys, collections, hashlib
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
sys.path.insert(0, str(ROOT))
from benchmark import baseline_deviation as bd
from benchmark.benchmark_runner import BenchmarkManifest
from benchmark.plan_manifest import load_plan_manifest
from benchmark.split_merge import split_plan
from scripts.analyze_baseline_deviation import discover

AUD = ROOT / 'analysis' / 'bnd_v41_cw_swe200_regeneration_audit'
STAGED = ROOT / 'analysis' / 'bnd_v41_cw_swe200_provisional' / 'staged' / 'merged'

SETTINGS = {
    'num_repetitions': 1,
    'num_benign_repetitions': 6,
    'dry_run': False,
    'model_name': 'Qwen/Qwen2.5-72B-Instruct-AWQ',
    'backend_name': 'vllm',
    'vllm_url': 'http://127.0.0.1:8001/v1',
    'seed': 42,
    'max_events': 300,
    'max_agent_turns': 80,
    'temperature': 0.4,
}

def sha(rootname, fx):
    p = ROOT / rootname / fx / 'manifest.json'
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

def meta(fx, rootname):
    m = json.loads((ROOT / rootname / fx / 'manifest.json').read_text())
    return m.get('task_family'), m.get('task_variant', 'default')

def make_entry(rid, topo, fx, tf, tv, rep):
    return {
        'run_id': f'run-{rid:04d}',
        'scenario_id': f'{topo}_{fx}_standard_{rep:02d}_benign',
        'task_family': tf, 'fixture_id': fx, 'task_variant': tv,
        'condition': 'benign', 'execution_variant': 'standard', 'lep_codes': [],
        'topology': topo, 'propagation_mode': 'single_origin', 'lep_code': '',
        'repetition_index': rep,
        'pair_tag': f'b_{topo}_{fx}_standard_{rep:02d}',
        'is_baseline': True,
    }

def make_manifest(entries, rootname, label):
    hm = {e['fixture_id']: sha(rootname, e['fixture_id']) for e in entries}
    bad = [fx for fx, h in hm.items() if h is None]
    assert not bad, f'fixtures missing under {rootname}: {bad}'
    return {
        'schema': 'agentprop-readiness-selection-v1',
        'settings': dict(SETTINGS),
        'selection': label,
        'execution_ready': True,
        'blocker': None,
        'fixture_manifest_sha256': hm,
        'scenarios': entries,
    }

def main():
    refs, lep, _ = discover(STAGED)
    lep = [r for r in lep if r['execution_variant'] == 'standard'
           and r['lep_code'] != 'LEP_MEMORY_POISONING']

    # ---- CW FANOUT plan (fresh rep indices, collision-verified) ----
    plan = json.loads((AUD / 'cw_fanout_regen_plan.json').read_text())
    cw_plan = {tuple(json.loads(k)): v for k, v in plan.items()}

    cwmap = json.loads((ROOT / 'analysis/coordinator_workers_regeneration/filtered_manifest.json').read_text())['fixture_manifest_sha256']
    swemap = json.loads((ROOT / 'analysis/swebench_200_addition/swebench_200_addition_manifest.json').read_text())['fixture_manifest_sha256']
    def root_of(fx):
        hd = sha('workspace_fixtures', fx); hv = sha('workspace_fixtures_v3', fx)
        if hd and not hv: return 'default'
        if hv and not hd: return 'v3'
        known, sw = cwmap.get(fx), swemap.get(fx)
        match_def = hd in {known, sw}; match_v3 = hv in {known, sw}
        return 'v3' if (match_v3 or not match_def) else 'default'

    cw_default, cw_v3 = [], []
    for (fx, topo, ev), fresh in sorted(cw_plan.items()):
        r = root_of(fx)
        rootname = 'workspace_fixtures' if r == 'default' else 'workspace_fixtures_v3'
        tf, tv = meta(fx, rootname)
        for rp in fresh:
            e = make_entry(0, 'coordinator_workers', fx, tf, tv, rp)
            (cw_default if r == 'default' else cw_v3).append(e)

    # ---- bv/rl gap cells (11 fixtures, all v3-root) ----
    bvr = [json.loads(l) for l in (AUD / 'regen_manifest.jsonl').open()
           if json.loads(l)['topology'] in ('branch_and_verify', 'review_loop')]
    need_cells = set((x['fixture'], x['topology']) for x in bvr)
    job = json.loads((ROOT / 'analysis/bv_rl_regeneration_v2/regeneration_manifest.json').read_text())
    job_cells = set((s['fixture_id'], s['topology']) for s in job['scenarios']
                    if s['condition'] == 'benign')
    gap_cells = sorted(need_cells - job_cells)
    print('bv/rl gap cells:', len(gap_cells))

    gap_entries = []
    for fx, topo in gap_cells:
        assert root_of(fx) == 'v3', f'{fx} not v3-root: {root_of(fx)}'
        tf, tv = meta(fx, 'workspace_fixtures_v3')
        key = (fx, topo, 'standard')
        e = refs.get(key) or {'pool_paths': []}
        existing = set(json.loads(Path(p).read_text())['metadata'].get('repetition_index')
                       for p in e.get('pool_paths', e.get('paths', [])))
        xnext = sorted(x for x in bvr if x['fixture'] == fx and x['topology'] == topo)
        need = xnext[0]['new_benign_executions']
        mx = max(existing, default=-1)
        for i in range(need):
            gap_entries.append(make_entry(0, topo, fx, tf, tv, mx + 1 + i))

    # renumber run_ids
    for lst in (cw_default, cw_v3, gap_entries):
        for j, e in enumerate(lst):
            e['run_id'] = f'run-{j:04d}'

    m_def = make_manifest(cw_default, 'workspace_fixtures',
                          'bndv41-cw-fanout-benign-regen-default-root')
    merged_v3 = cw_v3 + gap_entries
    for j, e in enumerate(merged_v3):
        e['run_id'] = f'run-{j:04d}'
    m_v3 = make_manifest(merged_v3, 'workspace_fixtures_v3',
                         'bndv41-cw-fanout-v3-root-plus-bvrl-gap')

    (AUD / 'cw_regen_fanout_default_manifest.json').write_text(json.dumps(m_def, indent=1))
    (AUD / 'v3_manifest.json').write_text(json.dumps(m_v3, indent=1))
    for mf, rootname in [('cw_regen_fanout_default_manifest.json', 'workspace_fixtures'),
                         ('v3_manifest.json', 'workspace_fixtures_v3')]:
        man = json.loads((AUD / mf).read_text())
        base = BenchmarkManifest(
            topologies=sorted({e['topology'] for e in man['scenarios']}),
            task_families=['code_review', 'financial_analysis', 'research_synthesis'],
            lep_configs=None, num_repetitions=1, num_benign_repetitions=6,
            fixture_root=Path(rootname))
        _m, entries = load_plan_manifest(AUD / mf, base)
        print(f'{mf}: VALID, {len(entries)} scenarios, {len(_m.fixture_ids)} fixture groups')

    # ---- sharding plan ----
    plan_doc = {
        'jobs': [
            {'id': 'A', 'label': 'cw-default',
             'manifest': str(AUD / 'cw_regen_fanout_default_manifest.json'),
             'fixture_root': 'workspace_fixtures', 'n_exec': len(m_def['scenarios'])},
            {'id': 'B', 'label': 'v3-plus-gap',
             'manifest': str(AUD / 'v3_manifest.json'),
             'fixture_root': 'workspace_fixtures_v3', 'n_exec': len(m_v3['scenarios'])},
        ],
    }
    for j in plan_doc['jobs']:
        man = json.loads((ROOT / j['manifest']).read_text())
        n = 64 if j['id'] == 'A' else 32
        sizes = [len(split_plan(man['scenarios'], i, n)) for i in range(n)]
        j['n_shards'] = n
        j['shard_sizes'] = sizes
        j['worst_shard'] = max(sizes)
        j['est_wall_min'] = round((540 + max(sizes) * 90 + 300) / 60)
        j['wall_limit'] = '00:45:00'
        j['concurrent'] = 16
    (AUD / 'bndv41_regen_plan.json').write_text(json.dumps(plan_doc, indent=1))
    print(json.dumps({j['id']: {'shards': j['n_shards'], 'worst': j['worst_shard'],
          'est_wall_min': j['est_wall_min'], 'exec': j['n_exec']} for j in plan_doc['jobs']}, indent=1))

if __name__ == '__main__':
    main()
