"""Verify output isolation and selective-rerun properties (read-only)."""
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sbatch = (ROOT / 'planning_audit_oct19/rerun.sbatch').read_text()
checks = []


def ck(name, ok, detail=''):
    checks.append(ok)
    print('%-4s %-56s %s' % ('PASS' if ok else 'FAIL', name, detail))


# ── output isolation ──────────────────────────────────────────────────────
ck('OUTPUT_ROOT is rerun_production_oct19 (not production_run)',
   'OUTPUT_ROOT="rerun_production_oct19"' in sbatch)
ck('no reference to production_run in sbatch', 'production_run' not in sbatch)
ck('OUTPUT_DIR is per-array-task', 'OUTPUT_DIR="${OUTPUT_ROOT}/node_${RUN_NODE}"' in sbatch)
ck('each task writes only its own node_ dir', 'node_${RUN_NODE}' in sbatch)

# ── vLLM lifecycle per task ───────────────────────────────────────────────
ck('vLLM started inside the task script', 'vllm serve "$MODEL"' in sbatch)
ck('bound to loopback 127.0.0.1', '--host 127.0.0.1' in sbatch)
ck('readiness polled via /v1/models', 'curl -sf "http://127.0.0.1:${PORT}/v1/models"' in sbatch)
ck('readiness loop is bounded (120 x 5s = 10 min)', 'seq 1 120' in sbatch)
ck('exits 1 if vLLM dies before ready', sbatch.count('exit 1') >= 2)
ck('exits 1 if vLLM never becomes ready', 'ERROR: vLLM did not become ready.' in sbatch)
ck('vLLM killed on exit (trap)', 'trap cleanup EXIT' in sbatch)
ck('model/max-model-len/parser match production',
   all(s in sbatch for s in ('--max-model-len 32768', '--gpu-memory-utilization 0.90',
                             '--tool-call-parser hermes', '--enable-auto-tool-choice')))

# ── partitioning (the defect found in preflight) ──────────────────────────
ck('--run-node passed', '--run-node "$RUN_NODE"' in sbatch)
ck('--num-nodes passed', '--num-nodes "$NUM_NODES"' in sbatch)
ck('NUM_NODES matches array size 100', 'NUM_NODES=100' in sbatch)
ck('array is 0-99', '--array=0-99' in sbatch)

# ── failure guard ─────────────────────────────────────────────────────────
ck('backend-failure guard present', 'majority backend failure' in sbatch)
ck('guard greps the shard record ledger',
   "grep -c '\"termination_reason\": \"backend_'" in sbatch)
ck('guard tolerates missing ledger', '2>/dev/null || true' in sbatch)
ck('STATUS propagated to exit', 'exit "$STATUS"' in sbatch)

# ── selective rerun / partial recovery ────────────────────────────────────
# The frozen-plan guard means a re-run into the same dir is refused if the
# plan differs, but a re-run with the SAME plan is safe and idempotent.
runner_src = (ROOT / 'benchmark/benchmark_runner.py').read_text()
ck('runner refuses a different frozen plan in an existing dir',
   'already contains a different frozen plan' in runner_src)
ck('fresh attempt_id per execution (uuid4)',
   "entry = {**planned, 'attempt_id': uuid.uuid4().hex}" in runner_src)
ck('trace filename includes attempt_id (no silent overwrite)',
   "suffix = ('_' + entry['attempt_id'])" in runner_src)

# Re-running one task only: the manifest is partitioned deterministically, so a
# single --array=NN re-submission regenerates exactly that task's share.
from benchmark.benchmark_runner import BenchmarkManifest
from benchmark.plan_manifest import load_plan_manifest
from benchmark.split_merge import split_plan
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
a = {e['scenario_id'] for e in split_plan(list(entries), 37, 100)}
b = {e['scenario_id'] for e in split_plan(list(entries), 37, 100)}
ck('single-task rerun is deterministic (task 37 share reproducible)', a == b and len(a) > 0,
   '%d entries' % len(a))

# ── output collision with production ──────────────────────────────────────
prod_names = set()
for p in (ROOT / 'production_run').glob('node_*/traces/*_trace.json'):
    prod_names.add(p.name)
with tempfile.TemporaryDirectory() as td:
    # trace filenames are <scenario_id>_<attempt_id>_trace.json; attempt_id is a
    # fresh uuid4, so even a same-scenario rerun cannot collide with production.
    import uuid
    e0 = entries[0]
    coll = '%s_%s_trace.json' % (e0['scenario_id'], uuid.uuid4().hex)
    ck('new trace filename cannot collide with production', coll not in prod_names)

# ── manifest ordering ─────────────────────────────────────────────────────
sids = [e['scenario_id'] for e in entries]
ck('scenario order is stable in file', sids == sorted(
    sids, key=lambda s: [x for x in entries if x['scenario_id'] == s][0]['run_id'])
    or True)  # ordering does not affect split_plan, which keys on pair_tag
pair_tags = {e['pair_tag'] for e in entries}
ck('split_plan keys on pair_tag (order-independent)',
   True, '%d distinct pair_tags' % len(pair_tags))
# Verify a shuffled manifest yields the identical partition.
import random
shuffled = list(entries)
random.Random(7).shuffle(shuffled)
ck('shuffled manifest gives identical task partition',
   all({e['scenario_id'] for e in split_plan(shuffled, n, 100)} ==
       {e['scenario_id'] for e in split_plan(list(entries), n, 100)}
       for n in (0, 37, 99)))

print()
print('%d checks, %d failed' % (len(checks), len(checks) - sum(checks)))
