#!/usr/bin/env python3
"""BND V4.1 benign-control calibration manifest — preflight validation.

Performs NO model inference and starts NO backend.  It only:
  * re-reads the FROZEN V4.1 ledger and the FROZEN production source traces;
  * re-runs the frozen V4.1 analyzer's PURE, CPU-only gates
    (`is_comparable`, `execution_shape`, `build_comparison_schema`) on the
    three existing references + one perturbed trace for each selected
    configuration, to independently confirm comparability;
  * checks the manifest / plan-manifest for duplicate fixtures, mismatched
    execution structure, missing reference files, and unsupported runner args.

Exit code 0 = all checks pass; non-zero = at least one blocking check failed.
Run from the repo root:

    python3.11 analysis/bnd_v41_benign_calibration_manifest/preflight_validate.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

OUT = Path(__file__).resolve().parent
MANIFEST = OUT / 'calibration_manifest.json'
PLAN = OUT / 'plan_manifest.json'

from benchmark import baseline_deviation as bd   # frozen, CPU-only gates  # noqa: E402
from benchmark import baseline_deviation_v41 as bdv41  # noqa: E402


def load_manifests():
    man = json.loads(MANIFEST.read_text())
    plan = json.loads(PLAN.read_text())
    return man, plan


def preflight_one(cfg, index, spec_root):
    """Independently re-derive comparability for one selected configuration.

    Returns a dict of checks (all True/False/str) — never mutates any frozen
    file.  Only parses JSON traces; no model/backend is touched.
    """
    fixture = cfg['task_fixture_id']
    topo = cfg['topology']
    var = cfg['execution_variant']
    refs = [cfg['benign_references'][k]['trace_id']
            for k in ('B1', 'B2', 'B3')]
    rep_tid = (cfg['representative_perturbed_trace'] or {}).get('trace_id')

    checks = {'config_id': cfg['config_id']}

    # 1. reference files exist on disk
    ref_paths = [cfg['benign_references'][k]['path']
                 for k in ('B1', 'B2', 'B3')]
    missing = [p for p in ref_paths if not Path(p).is_file()]
    checks['reference_files_present'] = not missing
    if missing:
        checks['missing_reference_files'] = missing

    # 2. the three references are benign and in one cell (via index)
    cells = set()
    ok_cell = True
    for t in refs:
        ix = index.get(t)
        if ix is None:
            ok_cell = False
            break
        if ix.get('condition') not in ('benign', 'counterfactual'):
            ok_cell = False
            break
        cells.add((ix.get('fixture_id'), ix.get('topology'),
                   ix.get('execution_variant')))
    checks['references_single_cell'] = (ok_cell and len(cells) == 1
                                        and refs[0] is not None)
    if ok_cell and len(cells) == 1:
        c = cells.pop()
        checks['cell_fixture_matches'] = c[0] == fixture
        checks['cell_topology_matches'] = c[1] == topo
        checks['cell_variant_matches'] = c[2] == var

    # 3. execution structure: the frozen gate's structural shape must match
    ref_docs = [json.loads(Path(p).read_text()) for p in ref_paths
                if Path(p).is_file()]
    shapes = [bd.execution_shape(d) for d in ref_docs]
    checks['reference_shape_agree'] = all(s == shapes[0] for s in shapes)

    # 4. comparability against the perturbed trace, frozen gate, CPU-only
    if rep_tid and rep_tid in index and Path(index[rep_tid]['_path']).is_file():
        lep_doc = json.loads(Path(index[rep_tid]['_path']).read_text())
        gate = bd.is_comparable(lep_doc, ref_docs)
        checks['perturbed_comparable'] = bool(gate['comparable'])
        checks['perturbed_comparable_reasons'] = gate.get('reasons')
        checks['perturbed_structural_ok'] = gate.get('structural_ok')

        # 5. comparison features/masks valid: schema has a non-empty mask
        spec = None
        sp = Path(spec_root) / fixture / 'manifest.json'
        if sp.is_file():
            spec = json.loads(sp.read_text())
        schema = bdv41.build_comparison_schema(ref_docs, spec, bdv41.ComponentWeights())
        checks['component_mask'] = schema.component_mask
        checks['component_mask_valid'] = bool(schema.component_mask)
        checks['required_keys'] = schema.required_keys
        checks['decision_required_keys'] = schema.decision_required_keys
    else:
        checks['perturbed_comparable'] = False
        checks['perturbed_note'] = 'representative perturbed trace not found'

    # 6. expected new-run trace-id format matches the frozen naming
    for r in cfg['planned_clean_runs']:
        variant_short = 'mem' if var == 'memory_enabled' else 'std'
        topshort = {'review_loop': 'rl', 'branch_and_verify': 'bv',
                    'coordinator_workers': 'coord'}[topo]
        expected = (f"{fixture}.{topshort}.{r['repetition_index']:02d}."
                    f"{variant_short}.benign__a")
        if r['expected_trace_id'] != expected:
            checks.setdefault('trace_id_format_errors', []).append(
                {'slot': r['calibration_slot'], 'got': r['expected_trace_id'],
                 'expected': expected})
    checks['trace_id_format_ok'] = 'trace_id_format_errors' not in checks

    return checks


def check_plan(man, plan):
    """Structural checks on the plan manifest without running any inference."""
    out = {}
    scenarios = plan['scenarios']
    out['n_planned_executions'] = len(scenarios)
    out['all_benign'] = all(s['condition'] == 'benign'
                            and s['is_baseline'] for s in scenarios)
    out['reps_used'] = sorted({s['repetition_index'] for s in scenarios})
    out['per_config_counts_ok'] = True
    seen = {}
    for s in scenarios:
        key = (s['fixture_id'], s['topology'], s['execution_variant'])
        seen[key] = seen.get(key, 0) + 1
    out['planned_runs_per_config'] = sorted(set(seen.values()))
    out['dup_configs_without_var'] = [k for k, v in seen.items()
                                      if v != 6]
    out['all_configs_have_6'] = all(v == 6 for v in seen.values())
    # every planned scenario id is unique
    sids = [s['scenario_id'] for s in scenarios]
    out['scenario_ids_unique'] = len(set(sids)) == len(sids)
    # every planned fixture has a recorded manifest hash
    fx = {s['fixture_id'] for s in scenarios}
    out['fixture_hashes_present'] = fx <= set(plan['fixture_manifest_sha256'])
    # settings sanity
    st = plan['settings']
    out['model_is_72b_awq'] = st['model_name'] == 'Qwen/Qwen2.5-72B-Instruct-AWQ'
    out['not_qwen3_8b'] = 'Qwen3' not in st['model_name']
    out['temp_is_0p4'] = st['temperature'] == 0.4
    out['seed_is_42'] = st['seed'] == 42
    out['backend_vllm'] = st['backend_name'] == 'vllm'
    return out


def check_duplicate_fixtures(man):
    fixtures = [c['task_fixture_id'] for c in man['configs']]
    out = {}
    out['n_configs'] = len(fixtures)
    from collections import Counter
    dup = {f: n for f, n in Counter(fixtures).items() if n > 1}
    # a fixture may appear more than once ACROSS topologies/variants; that is
    # expected.  Flag a fixture only if it repeats with identical (topo,variant).
    keys = [(c['task_fixture_id'], c['topology'], c['execution_variant'])
            for c in man['configs']]
    out['dup_config_cells'] = {k: n for k, n in Counter(keys).items() if n > 1}
    out['n_distinct_fixtures'] = len(set(fixtures))
    out['fixture_reused_across_topologies'] = len(dup)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--repo', default=str(REPO))
    ap.add_argument('--spec-root', default='workspace_fixtures')
    ap.add_argument('--report', default=str(OUT / 'preflight_report.json'))
    args = ap.parse_args(argv)

    spec_root = Path(args.repo) / args.spec_root

    # rebuild the trace index (trace_id -> metadata subset)
    import importlib.util
    work = OUT / '_work'
    idx = {}
    for line in (work / 'trace_index.jsonl').open():
        r = json.loads(line)
        idx[r['trace_id']] = r

    man, plan = load_manifests()

    per_config = {}
    blockers = []
    for cfg in man['configs']:
        c = preflight_one(cfg, idx, spec_root)
        per_config[cfg['config_id']] = c
        if not c.get('reference_files_present'):
            blockers.append(f"{cfg['config_id']}: missing reference files")
        if c.get('references_single_cell') is False:
            blockers.append(f"{cfg['config_id']}: references not a single cell")
        if c.get('perturbed_comparable') is False:
            blockers.append(f"{cfg['config_id']}: perturbed trace not "
                           f"comparable: {c.get('perturbed_comparable_reasons')}")
        if c.get('component_mask_valid') is False:
            blockers.append(f"{cfg['config_id']}: empty comparison mask")
        if not c.get('trace_id_format_ok'):
            blockers.append(f"{cfg['config_id']}: {c.get('trace_id_format_errors')}")

    plan_chk = check_plan(man, plan)
    dup_chk = check_duplicate_fixtures(man)

    # run the real plan-manifest loader (no inference; validates membership of
    # every scenario against the frozen planner) — wrapped so a failure is a
    # reported blocker, not a crash.
    loader_ok, loader_err = True, None
    try:
        from benchmark.benchmark_runner import BenchmarkManifest
        from benchmark.plan_manifest import load_plan_manifest
        base = BenchmarkManifest(
            topologies=['review_loop', 'branch_and_verify', 'coordinator_workers'],
            task_families=['code_review', 'financial_analysis',
                          'research_synthesis'],
            lep_configs=[],
            num_repetitions=1, num_benign_repetitions=3, max_events=300,
            model_name=plan['settings']['model_name'], dry_run=False,
            output_dir=Path('calibration_run_output'),
            fixture_root=Path(args.spec_root), seed=plan['settings']['seed'],
            backend_name=plan['settings']['backend_name'],
            vllm_url=plan['settings']['vllm_url'],
        )
        m2, entries2 = load_plan_manifest(Path(str(PLAN)), base)
        loader_ok = len(entries2) == plan_chk['n_planned_executions']
        if not loader_ok:
            loader_err = f'loader kept {len(entries2)} != planned ' \
                         f'{plan_chk["n_planned_executions"]}'
    except Exception as e:  # noqa: BLE001
        loader_ok = False
        loader_err = f'{type(e).__name__}: {e}'
    plan_chk['loader_membership_validated'] = loader_ok
    if not loader_ok:
        blockers.append(f'plan-manifest loader validation failed: {loader_err}')

    all_good = (not blockers
                and plan_chk['all_configs_have_6']
                and plan_chk['scenario_ids_unique']
                and plan_chk['fixture_hashes_present']
                and plan_chk['model_is_72b_awq']
                and plan_chk['not_qwen3_8b']
                and plan_chk['temp_is_0p4']
                and plan_chk['backend_vllm']
                and not dup_chk['dup_config_cells'])

    report = {
        'version': 'bnd_v41_benign_calibration_preflight_v1',
        'model_inference_performed': False,
        'all_good': bool(all_good),
        'n_configs': dup_chk['n_configs'],
        'n_planned_executions': plan_chk['n_planned_executions'],
        'planned_runs_per_config': plan_chk['planned_runs_per_config'],
        'reps_used': plan_chk['reps_used'],
        'plan_settings_checks': plan_chk,
        'duplicate_fixture_checks': dup_chk,
        'blockers': blockers,
        'per_config': per_config,
    }
    Path(str(args.report)).write_text(json.dumps(report, indent=2, default=str))

    print(f"ALL GOOD: {report['all_good']}")
    print(f"configs: {report['n_configs']}  planned executions: "
          f"{report['n_planned_executions']}")
    print(f"blockers: {len(blockers)}")
    for b in blockers[:50]:
        print('  -', b)
    print(f"report -> {args.report}")
    return 0 if all_good else 1


if __name__ == '__main__':
    raise SystemExit(main())
