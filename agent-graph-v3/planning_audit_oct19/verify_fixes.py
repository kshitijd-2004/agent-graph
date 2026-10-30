"""Verify the two preflight fixes behave correctly.

1. --run-node/--num-nodes partition the plan-manifest selection to exactly the
   shard's share (7,812 total across 100 tasks, each task's share unique).
2. The backend-failure guard detects a majority-backend-failure shard.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.benchmark_runner import BenchmarkManifest
from benchmark.plan_manifest import load_plan_manifest
from benchmark.split_merge import assign_node, split_plan
from dataclasses import replace
from tasks.registry import get_default_leps

fams = ['code_review', 'financial_analysis', 'research_synthesis']
base = BenchmarkManifest(
    topologies=['review_loop', 'branch_and_verify', 'coordinator_workers'],
    task_families=fams,
    lep_configs=[replace(lep, task_family=f) for f in fams for lep in get_default_leps(f)],
    fixture_root=ROOT / 'workspace_fixtures')

manifest, entries = load_plan_manifest(
    ROOT / 'planning_audit_oct19/rerun_plan_manifest.json', base)

# Simulate the runner's partitioning exactly as benchmark_runner.run() does.
total = 0
seen = set()
for task in range(100):
    m = replace(base, **{k: getattr(manifest, k) for k in (
        'num_repetitions', 'num_benign_repetitions', 'dry_run', 'model_name',
        'backend_name', 'vllm_url', 'seed', 'max_events', 'max_agent_turns',
        'temperature')},
        task_families=list(manifest.task_families),
        fixture_ids=dict(manifest.fixture_ids),
        topologies=list(manifest.topologies),
        propagation_modes=list(manifest.propagation_modes),
        lep_configs=list(manifest.lep_configs), smoke_fixtures=False,
        node_id=task, num_nodes=100)
    plan = split_plan(list(entries), m.node_id, m.num_nodes)
    total += len(plan)
    for e in plan:
        assert e['scenario_id'] not in seen, 'duplicate dispatch: %s' % e['scenario_id']
        seen.add(e['scenario_id'])
    if task == 0:
        print('task 0 executes %d entries' % len(plan))

print('total dispatched across 100 tasks: %d (expected 7812)' % total)
print('distinct scenario_ids dispatched: %d' % len(seen))
assert total == 7812 and len(seen) == 7812

# ── backend-failure guard ────────────────────────────────────────────────
guard = '''
set -e
BACKEND_FAILURES=$(grep -c '"termination_reason": "backend_' "$1" 2>/dev/null || true)
TOTAL_RECORDS=$(wc -l < "$1" 2>/dev/null || echo 0)
echo "Backend-failed executions: ${BACKEND_FAILURES:-0} / ${TOTAL_RECORDS}"
if [ "${TOTAL_RECORDS}" -gt 0 ] && [ "${BACKEND_FAILURES:-0}" -gt $(( TOTAL_RECORDS / 2 )) ]; then
    echo "GUARD_TRIGGERED"
    exit 1
fi
echo "GUARD_PASSED"
'''
script = Path(tempfile.mkstemp(suffix='.sh')[1])
script.write_text(guard)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # Case 1: all backend failures (the node_44 production scenario)
    bad = td / 'bad.jsonl'
    bad.write_text(''.join(
        json.dumps({'scenario_id': 's%d' % i,
                    'termination_reason': 'backend_http_error'}) + '\n'
        for i in range(183)))
    r = subprocess.run(['bash', str(script), str(bad)], capture_output=True, text=True)
    print('all-backend-failure shard -> exit %d, %s' % (r.returncode,
          'GUARD_TRIGGERED' if 'GUARD_TRIGGERED' in r.stdout else 'GUARD_PASSED'))
    assert r.returncode == 1 and 'GUARD_TRIGGERED' in r.stdout

    # Case 2: a healthy shard with normal scientific rejections
    good = td / 'good.jsonl'
    rows = ([{'termination_reason': 'completed'} for _ in range(150)]
            + [{'termination_reason': 'injection_target_unreachable'} for _ in range(5)])
    good.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    r = subprocess.run(['bash', str(script), str(good)], capture_output=True, text=True)
    print('healthy shard            -> exit %d, %s' % (r.returncode,
          'GUARD_TRIGGERED' if 'GUARD_TRIGGERED' in r.stdout else 'GUARD_PASSED'))
    assert r.returncode == 0 and 'GUARD_PASSED' in r.stdout

    # Case 3: exactly half failures must NOT trigger (strict majority)
    half = td / 'half.jsonl'
    half.write_text(''.join(
        json.dumps({'termination_reason': 'backend_http_error' if i % 2 else 'completed'}) + '\n'
        for i in range(100)))
    r = subprocess.run(['bash', str(script), str(half)], capture_output=True, text=True)
    print('exactly-50%% failure      -> exit %d, %s' % (r.returncode,
          'GUARD_TRIGGERED' if 'GUARD_TRIGGERED' in r.stdout else 'GUARD_PASSED'))
    assert r.returncode == 0

    # Case 4: missing records file must not crash the script
    r = subprocess.run(['bash', str(script), str(td / 'nope.jsonl')],
                       capture_output=True, text=True)
    print('missing records file     -> exit %d' % r.returncode)
    assert r.returncode == 0

script.unlink()
print()
print('BOTH FIXES VERIFIED')
