"""Convert the rerun manifest into a validated --plan-manifest payload.

Emits schema ``agentprop-readiness-selection-v1`` (the exact schema
``benchmark/plan_manifest.load_plan_manifest`` accepts), then verifies the
payload round-trips through that loader. No inference; nothing is executed.

The emitted file is written to the isolated planning directory only. It is not
a production artifact and does not touch production_run/.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / 'planning_audit_oct19'
FIXTURE_ROOT = ROOT / 'workspace_fixtures'

ENTRY_FIELDS = {'run_id', 'scenario_id', 'task_family', 'fixture_id', 'task_variant',
                'condition', 'execution_variant', 'lep_codes', 'topology',
                'propagation_mode', 'lep_code', 'repetition_index', 'pair_tag',
                'is_baseline'}
SETTINGS = {'num_repetitions', 'num_benign_repetitions', 'dry_run', 'model_name',
            'backend_name', 'vllm_url', 'seed', 'max_events', 'max_agent_turns',
            'temperature'}


def main():
    manifest = json.loads((OUT / 'rerun_manifest.json').read_text())
    canon = {e['scenario_id']: e for e in
             json.loads((OUT / 'canonical_plan.json').read_text())}

    settings = dict(manifest['settings'])
    assert set(settings) == SETTINGS, sorted(set(settings) ^ SETTINGS)

    scenarios = []
    used_run_ids = set()
    for i, row in enumerate(manifest['executions']):
        sid = row['scenario_id']
        base = canon.get(sid)
        if base is None:
            raise SystemExit('Scenario %s is not in the canonical plan' % sid)
        # run_id must be unique in the selection; the planner's original run_id
        # is retained in the audit manifest and reproduced here verbatim so the
        # selection stays byte-comparable with production planning.
        run_id = base['run_id']
        if run_id in used_run_ids:
            raise SystemExit('Duplicate run_id %s' % run_id)
        used_run_ids.add(run_id)
        entry = {k: base[k] for k in ENTRY_FIELDS}
        # Guard: the audit manifest's identity must agree with the planner's.
        for k in ('task_family', 'fixture_id', 'task_variant', 'condition',
                  'execution_variant', 'lep_codes', 'topology', 'propagation_mode',
                  'lep_code', 'repetition_index', 'pair_tag', 'is_baseline'):
            if row.get(k) != entry[k]:
                raise SystemExit('Identity mismatch for %s on %s' % (sid, k))
        scenarios.append(entry)

    hashes = {}
    for fixture in {e['fixture_id'] for e in scenarios}:
        raw = (FIXTURE_ROOT / fixture / 'manifest.json').read_bytes()
        hashes[fixture] = hashlib.sha256(raw).hexdigest()

    payload = {
        'schema': 'agentprop-readiness-selection-v1',
        'settings': settings,
        'selection': {
            'seed': 42,
            'method': ('Rerun manifest for production gaps identified by '
                       'planning_audit_oct19; identities are exact production '
                       'plan entries, no resampling or substitution.'),
            'source_inventory': 'planning_audit_oct19/inventory.json',
            'categories': {c: sum(1 for r in manifest['executions']
                                  if r['category'] == c)
                           for c in sorted({r['category']
                                            for r in manifest['executions']})},
        },
        'execution_ready': False,
        'blocker': ('Prepared for review only. No Slurm submission, vLLM start, or '
                    'production write has been performed.'),
        'fixture_manifest_sha256': hashes,
        'scenarios': scenarios,
    }
    path = OUT / 'rerun_plan_manifest.json'
    path.write_text(json.dumps(payload, indent=2))

    # Verify it loads through the repository's own strict loader.
    from benchmark.benchmark_runner import BenchmarkManifest
    from benchmark.plan_manifest import load_plan_manifest
    from dataclasses import replace
    from tasks.registry import get_default_leps

    base = BenchmarkManifest(
        topologies=['review_loop', 'branch_and_verify', 'coordinator_workers'],
        task_families=['code_review', 'financial_analysis', 'research_synthesis'],
        lep_configs=[replace(lep, task_family=f)
                     for f in ['code_review', 'financial_analysis', 'research_synthesis']
                     for lep in get_default_leps(f)],
        fixture_root=FIXTURE_ROOT)
    loaded, entries = load_plan_manifest(path, base)
    assert len(entries) == len(scenarios), (len(entries), len(scenarios))
    assert {e['scenario_id'] for e in entries} == {e['scenario_id'] for e in scenarios}
    print('validated %d scenarios through load_plan_manifest' % len(entries))
    print('wrote %s' % path.relative_to(ROOT))


if __name__ == '__main__':
    main()
