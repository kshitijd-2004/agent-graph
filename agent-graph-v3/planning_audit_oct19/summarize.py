"""Emit the machine-readable completion summary for the audit."""
import collections
import json
from pathlib import Path

ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
OUT = ROOT / 'planning_audit_oct19'
inv = json.loads((OUT / 'inventory.json').read_text())
manifest = json.loads((OUT / 'rerun_manifest.json').read_text())
ex = manifest['executions']


def dim(rows, key):
    return dict(sorted(collections.Counter(
        '/'.join(str(x) for x in (r[k] for k in key)) if isinstance(key, tuple) else str(r[key])
        for r in rows).items()))


summary = {
    'audit_date': '2026-10-19',
    'source': 'production_run/ (read-only; 0 files modified)',
    'authority': 'generation/admission.py:validate_trace re-run on disk',
    'production_completion': {
        'shards_scheduled': 160,
        'shards_with_accepted_traces': 120,
        'shards_zero_accepted_all_backend_http_error': 40,
        'shards_cancelled_at_time_limit': 9,
        'trace_files_on_disk': inv['trace_files_scanned'],
        'distinct_attempts': inv['distinct_attempts'],
        'duplicate_or_conflicting_attempts': len([d for d in inv['duplicate_attempts'] if d['conflict']]),
        'ingestion_errors': len(inv['ingestion_errors']),
        'admitted_benign_traces': inv['admitted_benign_traces'],
        'admitted_perturbed_traces': inv['admitted_perturbed_traces'],
        'perturbed_eligible_but_unverifiable_on_readmission': 761,
        'protocols_observed': inv['protocols_observed'],
    },
    'analysis_eligibility': {
        'perturbed_traces_analysis_ready': 4574,
        'perturbed_traces_blocked_by_clean_deficit': 5771,
        'clean_reference_groups_short': 1534,
        'additional_clean_executions_required': 1871,
    },
    'missing_work': {
        'A_clean_reference_topup': dim([e for e in ex if e['category'] == 'A_clean_reference_topup'],
                                       ('task_family', 'topology', 'execution_variant', 'repetition_index')),
        'B_recoverable_perturbed': dim([e for e in ex if e['category'] == 'B_recoverable_perturbed'],
                                       ('task_family', 'topology', 'lep_code', 'propagation_mode', 'execution_variant')),
        'B_never_attempted': dim([e for e in ex if e['category'] == 'B_never_attempted'],
                                 ('task_family', 'topology', 'lep_code', 'propagation_mode')),
        'C_unreachable_not_scheduled': dim(manifest['excluded_unreachable'],
                                           ('task_family', 'topology', 'lep_code', 'propagation_mode')),
        'D_other_not_scheduled': dim(manifest['excluded_other'],
                                     ('task_family', 'topology', 'lep_code', 'propagation_mode')),
    },
    'rerun_totals': {
        'total_executions': len(ex),
        'by_category': dict(collections.Counter(e['category'] for e in ex)),
        'by_task_family': dim(ex, 'task_family'),
        'by_topology': dim(ex, 'topology'),
        'by_propagation_mode': dim(ex, 'propagation_mode'),
        'by_execution_variant': dim(ex, 'execution_variant'),
        'by_lep_code': dim(ex, 'lep_code'),
    },
    'proposed_slurm_plan': {
        'array': '0-99',
        'tasks': 100,
        'gpus_per_task': 1,
        'cpus_per_task': 32,
        'partition': 'ghx4',
        'time_limit': '02:00:00',
        'max_node_walltime_conservative_hours': 1.43,
        'median_node_walltime_conservative_hours': 0.84,
        'estimated_gpu_hours': 80,
        'output_root': 'rerun_production_oct19',
        'plan_manifest': 'planning_audit_oct19/rerun_plan_manifest.json',
        'submitted': False,
    },
    'indeterminate': [
        'Root cause of the 40-shard backend_http_error outage (0-byte vLLM logs, no error text).',
        'Whether the 761 unverifiable_legacy perturbed traces reflect an evidence-capture regression or genuinely absent mutations.',
        'Per-fixture acceptance probability for the coordinator_workers/MP/one_to_many cell.',
        'Original shard split for shards 0-18, which never wrote benchmark_plan.json.',
    ],
}
(OUT / 'completion_summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary['rerun_totals'], indent=2))
print()
print('wrote planning_audit_oct19/completion_summary.json')
