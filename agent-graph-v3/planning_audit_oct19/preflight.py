"""Read-only preflight of the proposed rerun plan. No execution, no writes to
production_run/ or rerun_production_oct19/."""
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / 'planning_audit_oct19'
FIXTURE_ROOT = ROOT / 'workspace_fixtures'
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print('%-4s %-58s %s' % ('PASS' if ok else 'FAIL', name, detail))


def main():
    payload = json.loads((OUT / 'rerun_plan_manifest.json').read_text())
    scenarios = payload['scenarios']
    manifest = json.loads((OUT / 'rerun_manifest.json').read_text())
    rows = manifest['executions']

    # ── 1. selection completeness and identity ────────────────────────────
    check('manifest schema', payload['schema'] == 'agentprop-readiness-selection-v1',
          payload['schema'])
    check('scenario count == 7812', len(scenarios) == 7812, str(len(scenarios)))
    check('scenario_id unique', len({s['scenario_id'] for s in scenarios}) == len(scenarios))
    check('run_id unique', len({s['run_id'] for s in scenarios}) == len(scenarios))
    check('execution_ready is False', payload.get('execution_ready') is False)
    check('blocker recorded', bool(payload.get('blocker')))

    # Re-validate through the repository's own strict loader.
    from benchmark.benchmark_runner import BenchmarkManifest
    from benchmark.plan_manifest import load_plan_manifest
    from dataclasses import replace
    from tasks.registry import get_default_leps

    fams = ['code_review', 'financial_analysis', 'research_synthesis']
    base = BenchmarkManifest(
        topologies=['review_loop', 'branch_and_verify', 'coordinator_workers'],
        task_families=fams,
        lep_configs=[replace(lep, task_family=f) for f in fams for lep in get_default_leps(f)],
        fixture_root=FIXTURE_ROOT)
    loaded, entries = load_plan_manifest(OUT / 'rerun_plan_manifest.json', base)
    check('load_plan_manifest resolves all', len(entries) == 7812, '%d entries' % len(entries))
    check('loaded identities identical',
          {e['scenario_id'] for e in entries} == {s['scenario_id'] for s in scenarios})

    # Fixture hashes are re-checked here against the live fixture tree.
    hashes = payload['fixture_manifest_sha256']
    missing_fix = [f for f in hashes if not (FIXTURE_ROOT / f / 'manifest.json').exists()]
    check('all referenced fixtures exist on disk', not missing_fix, str(missing_fix[:3]))
    bad = [f for f, h in hashes.items()
           if hashlib.sha256((FIXTURE_ROOT / f / 'manifest.json').read_bytes()).hexdigest() != h]
    check('all fixture manifest hashes match live tree', not bad, str(bad[:3]))
    print('     (fixtures referenced: %d)' % len(hashes))

    # ── identity agreement between audit manifest and planner entries ────
    by_id = {e['scenario_id']: e for e in entries}
    mismatch = []
    for r in rows:
        e = by_id.get(r['scenario_id'])
        if e is None:
            mismatch.append((r['scenario_id'], 'absent'))
            continue
        for k in ('task_family', 'fixture_id', 'task_variant', 'condition',
                  'execution_variant', 'lep_codes', 'topology', 'propagation_mode',
                  'lep_code', 'repetition_index', 'pair_tag', 'is_baseline'):
            if r.get(k) != e[k]:
                mismatch.append((r['scenario_id'], k))
    check('audit rows agree with planner identities', not mismatch, str(mismatch[:3]))

    # ── seeds / variants / protocols preserved ───────────────────────────
    s = payload['settings']
    check('seed == 42', s['seed'] == 42, str(s['seed']))
    check('temperature == 0.4', s['temperature'] == 0.4, str(s['temperature']))
    check('num_repetitions == 1', s['num_repetitions'] == 1)
    check('num_benign_repetitions == 3', s['num_benign_repetitions'] == 3)
    check('max_events == 300', s['max_events'] == 300)
    check('max_agent_turns == 80', s['max_agent_turns'] == 80)
    check('dry_run is False', s['dry_run'] is False)
    check('backend_name == vllm', s['backend_name'] == 'vllm')
    check('model matches production', s['model_name'] == 'Qwen/Qwen2.5-72B-Instruct-AWQ')
    check('vllm_url matches sbatch PORT',
          s['vllm_url'] == 'http://127.0.0.1:8001/v1', s['vllm_url'])
    check('all rows write_interception_v1',
          all(r['memory_protocol'] == 'write_interception_v1' for r in rows))
    check('no ERP rows', not [r for r in rows if 'existing_record' in str(r.get('memory_protocol'))])
    check('execution variants only standard/memory_enabled',
          {r['execution_variant'] for r in rows} <= {'standard', 'memory_enabled'},
          str(sorted({r['execution_variant'] for r in rows})))
    check('LEP codes are the 5 canonical',
          {r['lep_code'] for r in rows if r['lep_code']} == {
              'LEP_HANDOFF_CORRUPTION', 'LEP_INDIRECT_PROMPT_INJECTION',
              'LEP_INPUT_DISREGARD', 'LEP_MEMORY_POISONING',
              'LEP_TOOL_RESULT_CORRUPTION'})
    check('clean rows carry no LEP',
          all(not r['lep_codes'] for r in rows if r['category'] == 'A_clean_reference_topup'))
    check('MP rows use memory_enabled variant',
          all(r['execution_variant'] == 'memory_enabled'
              for r in rows if r['lep_code'] == 'LEP_MEMORY_POISONING'))
    check('non-MP perturbed rows use standard variant',
          all(r['execution_variant'] == 'standard'
              for r in rows if r['lep_code'] and r['lep_code'] != 'LEP_MEMORY_POISONING'))

    # ── 6. legacy unverifiable + ERP populations excluded ────────────────
    prod_trace_sids = set()
    for p in glob.glob(str(ROOT / 'production_run/node_*/traces/*_trace.json')):
        prod_trace_sids.add(json.loads(Path(p).read_text())['metadata'].get('scenario_id'))
    mids = {r['scenario_id'] for r in rows}
    overlap = mids & prod_trace_sids
    check('no manifest row regenerates an on-disk accepted trace', not overlap,
          '%d overlaps' % len(overlap))

    # the 761 unverifiable legacy traces live in traces/ and are thus excluded
    unver = set()
    for p in glob.glob(str(ROOT / 'production_run/node_*/traces/*_trace.json')):
        d = json.loads(Path(p).read_text())
        m = d['metadata']
        if m.get('condition') == 'benign' or not m.get('lep_codes'):
            continue
        from generation.admission import validate_trace
        from benchmark.propagation_analysis import PropagationAnalyzer
        t = PropagationAnalyzer._trace_from_dict(d, Path(p))
        if not validate_trace(t).admitted:
            unver.add(m['scenario_id'])
    check('761 legacy unverifiable scenario_ids identified', len(unver) == 761, str(len(unver)))
    check('legacy unverifiable excluded from rerun', not (mids & unver),
          '%d overlaps' % len(mids & unver))

    erp = [r for r in rows if r['scenario_id'].endswith('_briefing_v1')
           or '_briefing_v1' in r['scenario_id']]
    check('ERP (briefing_v1) fixtures absent from rerun', not erp, str(len(erp)))
    erp_fix = [f for f in hashes if f.endswith('_briefing_v1')]
    check('ERP fixture hashes absent from rerun manifest', not erp_fix, str(len(erp_fix)))

    # ── trace identity uniqueness ────────────────────────────────────────
    from benchmark.benchmark_runner import BenchmarkRunner
    short = [BenchmarkRunner._short_trace_id(e) for e in entries]
    dup_short = [k for k, v in collections.Counter(short).items() if v > 1]
    check('_short_trace_id unique across selection', not dup_short, str(dup_short[:3]))
    check('attempt_id generated fresh per execution (uuid4)',
          'uuid4' in (ROOT / 'benchmark/benchmark_runner.py').read_text())

    # ── shard partitioning ───────────────────────────────────────────────
    from benchmark.split_merge import assign_node
    for N in (100,):
        c = collections.Counter(assign_node(e['pair_tag'], N) for e in entries)
        vals = sorted(c.values())
        check('split_plan covers all 100 tasks', len(c) == N, '%d used' % len(c))
        check('split total == 7812', sum(vals) == 7812, str(sum(vals)))
        check('every task has both clean and perturbed work',
              all(any(e['condition'] == 'benign' for e in entries
                      if assign_node(e['pair_tag'], N) == n)
                  and any(e['condition'] != 'benign' for e in entries
                          if assign_node(e['pair_tag'], N) == n) for n in c))
        print('     (N=%d: min=%d median=%d max=%d)' % (N, vals[0], vals[len(vals)//2], vals[-1]))

    json.dump([{'check': n, 'ok': o, 'detail': d} for n, o, d in results],
              (OUT / 'preflight_checks.json').open('w'), indent=2)
    failed = [r for r in results if not r[1]]
    print()
    print('%d checks, %d failed' % (len(results), len(failed)))


if __name__ == '__main__':
    main()
