"""Verify the plan-manifest partitioning interaction empirically."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.benchmark_runner import BenchmarkManifest
from benchmark.plan_manifest import load_plan_manifest
from benchmark.split_merge import assign_node, split_plan
from dataclasses import replace
from tasks.registry import get_default_leps

OUT = ROOT / 'planning_audit_oct19'
fams = ['code_review', 'financial_analysis', 'research_synthesis']
base = BenchmarkManifest(
    topologies=['review_loop', 'branch_and_verify', 'coordinator_workers'],
    task_families=fams,
    lep_configs=[replace(lep, task_family=f) for f in fams for lep in get_default_leps(f)],
    fixture_root=ROOT / 'workspace_fixtures')

manifest, entries = load_plan_manifest(OUT / 'rerun_plan_manifest.json', base)
print('selected scenarios: %d' % len(entries))
print('manifest.num_nodes after load_plan_manifest: %d' % manifest.num_nodes)
print('manifest.node_id  after load_plan_manifest: %d' % manifest.node_id)
print()

# With NO --run-node/--num-nodes on the CLI, num_nodes stays 1, so run()
# skips split_plan entirely and executes ALL 7812 on every task.
plan = list(entries)
if manifest.num_nodes > 1:
    plan = split_plan(plan, manifest.node_id, manifest.num_nodes)
print('entries executed per task WITHOUT partitioning flags: %d' % len(plan))
print('  -> %d tasks x %d = %d total executions (expected 7812)'
      % (100, len(plan), 100 * len(plan)))
print()

# Now show what the production.sbatch path actually does.
plan2 = split_plan(list(entries), 0, 100)
plan3 = split_plan(list(entries), 99, 100)
print('with --run-node N --num-nodes 100: task 0 -> %d, task 99 -> %d'
      % (len(plan2), len(plan3)))
print('sum over all 100 tasks: %d'
      % sum(len(split_plan(list(entries), n, 100)) for n in range(100)))
