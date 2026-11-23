"""Deterministic, re-runnable builder for the SWE-200 addition selection.

Regenerates ``swebench_200_addition_manifest.json`` from the fixtures in
``workspace_fixtures_v3`` under the relaxed ``fixture-contract-v1`` gate.

Design goals (kept deliberately narrow):
  * The already-selected entries are copied verbatim, byte-stable, in their
    original order and with their original run_ids. Nothing about the existing
    selection is re-derived or re-sorted.
  * Size-eligible fixtures newly admitted by the validator relaxation are
    appended as fresh 29-cell blocks in ascending fixture-id order, with
    globally-sequential run_ids continuing after the existing block.
  * ``fixture_manifest_sha256`` is extended with the fresh v3 bytes of the
    newly-admitted fixtures; existing hash entries are carried through
    unchanged.
  * Idempotent: re-running after a successful build is a byte-identical no-op
    (no fixture is ever appended twice, and the admitted set is recomputed,
    never hard-coded).
  * Memory Poisoning and every ``memory_enabled`` variant remain excluded;
    the frozen 1-100 production dataset, its historical hashes, and the
    5,818-execution regeneration manifest are never touched here.

Run from the repo root:
    python analysis/swebench_200_addition/build_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'swebench_200_addition_manifest.json'
FIXTURE_ROOT = REPO / 'workspace_fixtures_v3'

sys.path.insert(0, str(REPO))
from scripts.validate_fixture import check_fixture  # noqa: E402

# Checks that constitute the REAL (non-obsolete) gate. The two memory-only
# gates (``all_five_leps_fire_with_expected_counts`` and
# ``poison_contradicts_truth``) are permanently excluded and are NOT required.
REQUIRED_CHECKS = {
    'manifest',
    'answer_key_grounded',
    'swap_pairs_grounded',
    'prompt_injection_payload_valid',
    'size_under_8k_tokens',
    'distinct_source_instance_id',
}

# The uniform 29-cell block shared by every code_review fixture. Each cell is
# (condition, topology, execution_variant, propagation_mode, lep_code,
#  repetition_index, is_baseline).
_LEPS = ('LEP_TOOL_RESULT_CORRUPTION', 'LEP_HANDOFF_CORRUPTION',
         'LEP_INPUT_DISREGARD', 'LEP_INDIRECT_PROMPT_INJECTION')

_CELLS = [('benign', 'review_loop', 'standard', 'single_origin', '', rep, True)
          for rep in (0, 1, 2)]
_CELLS += [('single_lep', 'review_loop', 'standard', 'single_origin', lep, 0, False)
           for lep in _LEPS]
_CELLS += [('benign', 'branch_and_verify', 'standard', 'single_origin', '', rep, True)
           for rep in (0, 1, 2)]
_CELLS += [('single_lep', 'branch_and_verify', 'standard', mode, lep, 0, False)
           for lep in _LEPS for mode in ('single_origin', 'many_to_one')]
_CELLS += [('benign', 'coordinator_workers', 'standard', 'single_origin', '', rep, True)
           for rep in (0, 1, 2)]
_CELLS += [('single_lep', 'coordinator_workers', 'standard', mode, lep, 0, False)
           for lep in _LEPS for mode in ('single_origin', 'one_to_many')]

assert len(_CELLS) == 29, f'expected 29 cells, built {len(_CELLS)}'


def eligible_fixture_ids() -> list[str]:
    """Fixture ids in the v3 cohort that pass every REAL gate check."""
    admitted = []
    for directory in sorted(FIXTURE_ROOT.glob('code_review_swe_*')):
        if not directory.is_dir():
            continue
        try:
            number = int(directory.name.split('_')[3])
        except (IndexError, ValueError):
            continue
        if not 101 <= number <= 300:
            continue
        result = check_fixture(directory, run_mock=False)
        failing = {name for name, ok in result['checks'].items()
                   if not ok and name in REQUIRED_CHECKS}
        if not failing:
            admitted.append(directory.name)
    return sorted(admitted)


def scenario_id(topology: str, fid: str, execution_variant: str, rep: int,
                condition: str, lep_code: str, propagation_mode: str) -> str:
    if condition == 'benign':
        return f'{topology}_{fid}_{execution_variant}_{rep:02d}_benign'
    return f'{topology}_{fid}_{lep_code}_{propagation_mode}_{rep:02d}_lep'


def build_block(fid: str, start_run: int) -> list[dict]:
    """Build the 29 scenario dicts for one newly-admitted fixture."""
    block = []
    for index, (condition, topology, execv, prop, lep, rep, is_base) in enumerate(_CELLS, start=1):
        run_id = start_run + index - 1
        block.append({
            'run_id': f'run-{run_id:06d}',
            'scenario_id': scenario_id(topology, fid, execv, rep, condition, lep, prop),
            'task_family': 'code_review',
            'fixture_id': fid,
            'task_variant': 'swe_bench_verified',
            'condition': condition,
            'execution_variant': execv,
            'lep_codes': [lep] if lep else [],
            'topology': topology,
            'propagation_mode': prop,
            'lep_code': lep,
            'repetition_index': rep,
            'pair_tag': f'b_{topology}_{fid}_{execv}_{rep:02d}',
            'is_baseline': is_base,
        })
    return block


def fixture_hash(fid: str) -> str:
    return hashlib.sha256((FIXTURE_ROOT / fid / 'manifest.json').read_bytes()).hexdigest()


def self_check() -> None:
    """Ensure the cell builder reproduces an existing manifest block exactly
    (every field, in order). Catches any template drift before appending."""
    current = json.loads(MANIFEST.read_text())
    scenarios = current['scenarios']
    ref_fid = scenarios[0]['fixture_id']
    ref_block = [e for e in scenarios if e['fixture_id'] == ref_fid]
    start = min(int(e['run_id'].split('-')[1]) for e in ref_block)
    rebuilt = build_block(ref_fid, start)
    if len(ref_block) != len(rebuilt):
        raise AssertionError(f'{ref_fid}: existing {len(ref_block)} cells != built {len(rebuilt)}')
    for expected, produced in zip(ref_block, rebuilt):
        if expected != produced:
            raise AssertionError(f'template drift for {ref_fid}:\n  expected={expected}\n  produced={produced}')


def main() -> int:
    current = json.loads(MANIFEST.read_text())
    current_hashes = current['fixture_manifest_sha256']

    self_check()  # guarantees appended blocks match the existing format

    admitted = set(eligible_fixture_ids())
    already = set(current_hashes)
    to_add = sorted(admitted - already)
    excluded = sorted(set(already) - admitted)  # previously admitted now failing the gate

    scenarios = list(current['scenarios'])
    next_run = int(scenarios[-1]['run_id'].split('-')[1]) + 1 if scenarios else 1

    added_hashes = {}
    for fid in to_add:
        scenarios.extend(build_block(fid, next_run))
        next_run += len(_CELLS)
        added_hashes[fid] = fixture_hash(fid)

    new_hashes = dict(current_hashes)
    new_hashes.update(added_hashes)

    output = {
        'schema': current['schema'],
        'settings': current['settings'],
        'selection': current.get('selection'),
        'execution_ready': current.get('execution_ready', True),
        'blocker': current.get('blocker'),
        'fixture_manifest_sha256': new_hashes,
        'scenarios': scenarios,
    }

    fixture_count = len({e['fixture_id'] for e in scenarios})
    execution_count = len(scenarios)
    print(json.dumps({
        'admitted': len(admitted),
        'newly_added': to_add,
        'newly_added_count': len(to_add),
        'excluded_from_gate': excluded,
        'total_fixtures': fixture_count,
        'total_executions': execution_count,
        'run_id_range': [f'run-000001', f'run-{execution_count:06d}'],
    }, indent=2))

    if to_add:
        backup = MANIFEST.with_suffix('.json.bak')
        if not backup.exists():
            shutil.copy2(MANIFEST, backup)
        MANIFEST.write_text(json.dumps(output, indent=2) + '\n')
        print(f'wrote {MANIFEST.name} (backup: {backup.name})')
    else:
        print('no new fixtures to add; manifest already current (idempotent no-op)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
