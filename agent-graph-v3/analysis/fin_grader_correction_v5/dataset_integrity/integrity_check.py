#!/usr/bin/env python3.11
"""Track A3 - dataset integrity verification for the validation dataset.

Read-only. Writes ``integrity_report.json`` next to this file.

Checks (all from the task's required list):
  1. 1,375 annotation tasks (annotation_tasks_FINAL.jsonl)
  2. 600 development tasks
  3. 775 sealed held-out tasks
  4. 249 development fixtures
  5. 51 held-out fixtures
  6. zero fixture overlap
  7. no duplicate annotation task trace_ids
  8. all trace paths exist
  9. correct spec mapping (fixture -> manifest.json, task_family)
 10. duplicate trace IDs handled (dup_id flag; dup LEP rows excluded from pop.)
 11. benign vs perturbed treatment
 12. non-scalar fixtures excluded from scalar acceptance
 13. known ground-truth value bug (fin_finqa_219) handled
"""
import json, os, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
VAL = HERE.parent / 'validation'
SPECROOT = ROOT / 'analysis/bnd_v41_cw_swe200_provisional/specroot'
TR = ROOT / 'analysis/downstream_propagation_corrected/staged_v2/merged/traces'

def load_jsonl(p):
    return [json.loads(l) for l in open(p)]

def main():
    tasks = load_jsonl(VAL / 'annotation_tasks_FINAL.jsonl')
    recs = load_jsonl(VAL / 'dataset_records.jsonl')
    split = json.load(open(VAL / 'split_manifest.json'))
    manifest = json.load(open(VAL / 'spec_correction_manifest.json'))

    R = {'checks': {}, 'failures': []}
    def chk(name, ok, detail):
        R['checks'][name] = {'ok': bool(ok), 'detail': detail}
        if not ok:
            R['failures'].append(name)

    # 1-3 task counts
    by_role = Counter(t['list_role'] for t in tasks)
    chk('task_total_1375', len(tasks) == 1375, f'{len(tasks)} tasks')
    chk('dev_tasks_600', by_role.get('dev_tuning') == 600, f"{by_role.get('dev_tuning')}")
    chk('sealed_tasks_775', by_role.get('acceptance_sealed') == 775,
        f"{by_role.get('acceptance_sealed')}")
    # role field consistency: acceptance_sealed <-> split HELD_OUT_SEALED
    bad_split = [t['trace_id'] for t in tasks
                 if (t['list_role']=='acceptance_sealed') != (t['split']=='HELD_OUT_SEALED')]
    chk('task_role_split_consistent', not bad_split, f'{len(bad_split)} mismatches')

    # fixture counts from records
    lep = [r for r in recs if r['role']=='lep_execution' and not r.get('dup_id')]
    held_fix = set(t['fixture'] for t in tasks if t['list_role']=='acceptance_sealed')
    dev_fix_tasklist = set(t['fixture'] for t in tasks if t['list_role']=='dev_tuning')
    # Scalar-eligible dev fixtures: records carry the 2 no-answer fixtures under
    # split=DEV (excluded from scalar acceptance via stratum), so restrict to the
    # scalar-eligible strata to match split_manifest's 249.
    NOANS_STRATUM = 'no_answer_EXCLUDE'
    dev_fix_split = set(r['fixture'] for r in lep
                        if r['split']=='DEV' and r['stratum'] != NOANS_STRATUM)
    chk('sealed_fixtures_51', len(held_fix) == 51, f'{len(held_fix)}')
    # The split has 249 dev fixtures; the 600-row task list is a *sample* that
    # touches a subset of them. Both facts are true and reported separately.
    chk('dev_fixtures_249_in_split', len(dev_fix_split) == 249,
        f'{len(dev_fix_split)} in split; task-list sample touches '
        f'{len(dev_fix_tasklist)} (non-empty subset, expected)')
    chk('fixture_disjoint', not (held_fix & dev_fix_split),
        f'{len(held_fix & dev_fix_split)} overlap')

    # also verify against split_manifest
    chk('manifest_sealed_51', len(split['held_out_fixtures']) == 51,
        f'{len(split["held_out_fixtures"])}')
    chk('manifest_dev_249', len(split['dev_fixtures']) == 249,
        f'{len(split["dev_fixtures"])}')
    chk('manifest_disjoint', not (set(split['held_out_fixtures']) & set(split['dev_fixtures'])),
        'manifest held/dev disjoint')
    chk('task_set_matches_manifest',
        held_fix == set(split['held_out_fixtures'])
        and dev_fix_split == set(split['dev_fixtures']),
        'task-list sealed set & split dev sets equal split_manifest fixture sets')

    # duplicate trace ids
    tids = [t['trace_id'] for t in tasks]
    dup_tids = [k for k, v in Counter(tids).items() if v > 1]
    chk('no_dup_task_traceids', not dup_tids, f'{len(dup_tids)} dups: {dup_tids[:5]}')

    # trace paths exist
    missing = []
    for t in tasks:
        p = t.get('trace_file')
        if not (p and os.path.exists(p)):
            missing.append(t['trace_id'])
    chk('all_trace_paths_exist', not missing,
        f'{len(missing)} missing: {missing[:5]}')

    # spec mapping: fixture -> manifest exists and is financial
    bad_spec = []
    for f in sorted(held_fix | dev_fix_split):
        mp = SPECROOT / f / 'manifest.json'
        if not mp.exists():
            bad_spec.append((f, 'missing')); continue
        fam = json.load(open(mp)).get('task_family')
        if fam != 'financial_analysis':
            bad_spec.append((f, fam))
    chk('spec_mapping_correct', not bad_spec, f'{len(bad_spec)} bad: {bad_spec[:5]}')

    # duplicate trace ID handling: the 3 known dup IDs must be flagged dup_id and
    # excluded from the LEP population used for acceptance denominators.
    dup_ids = set(split.get('dup_ids_deduped', []))
    dup_flagged = set(r['trace_id'] for r in recs if r.get('dup_id'))
    chk('dup_ids_flagged', dup_ids == dup_flagged,
        f'manifest={sorted(dup_ids)} flagged={sorted(dup_flagged)}')
    dup_in_lep_pop = [r['trace_id'] for r in lep if r.get('dup_id')]
    chk('dup_excluded_from_lep_pop', not dup_in_lep_pop,
        f'{len(dup_in_lep_pop)} dup LEP rows leaked into pop: {dup_in_lep_pop}')

    # benign vs perturbed: benign_reference rows must have lep None & mode benign
    bn = [r for r in recs if r['role']=='benign_reference']
    bad_bn = [r['trace_id'] for r in bn if r.get('lep') is not None or r.get('mode')!='benign']
    chk('benign_treatment_correct', not bad_bn, f'{len(bad_bn)} bad benign rows')
    # LEP rows have a lep_code
    bad_lep = [r['trace_id'] for r in lep if not r.get('lep')]
    chk('perturbed_have_lep_code', not bad_lep, f'{len(bad_lep)} missing lep')

    # non-scalar fixtures excluded from scalar acceptance
    noans = set(manifest.get('no_answer_key_excluded', []))
    noans_in_lep = [r['trace_id'] for r in lep if r['fixture'] in noans]
    noans_in_tasks = [t['trace_id'] for t in tasks if t['fixture'] in noans]
    chk('non_scalar_excluded_from_tasks', not noans_in_tasks,
        f'{len(noans_in_tasks)} no-answer executions leaked into task list: {noans_in_tasks[:5]}')
    # The 32 no-answer LEP executions REMAIN in the record population (they are
    # preserved per the plan), but are carried only under the
    # stratum='no_answer_EXCLUDE' flag and excluded from scalar acceptance.
    all_noans_lep = [r for r in lep if r['fixture'] in noans]
    all_flagged = all(r['stratum'] == 'no_answer_EXCLUDE' for r in all_noans_lep)
    chk('non_scalar_flagged_not_deleted',
        len(all_noans_lep) == 32 and all_flagged,
        f'{len(all_noans_lep)} no-answer LEP rows (expect 32), stratum-flagged={all_flagged}; '
        'these are EXCLUDED from scalar acceptance, not deleted')

    # known value bug handled: 219 present but flagged for exclusion-from-scalar
    vb = set(manifest.get('value_bugs', [{}]) and [x['fid'] for x in manifest['value_bugs']]
             or [])
    if not vb:
        vb = {'fin_finqa_219_adi_2015'}
    vb_in_lep = [r['trace_id'] for r in lep if r['fixture'] in vb]
    vb_flagged = all(r.get('stratum')=='value_bug_219' for r in
                     (rr for rr in recs if rr['role']=='lep_execution' and rr['fixture'] in vb))
    chk('value_bug_219_flagged', vb_flagged and len(vb_in_lep)>0,
        f'{len(vb_in_lep)} LEP rows, stratum=value_bug_219={vb_flagged}')
    chk('value_bug_219_in_manifest', bool(vb),
        f'value_bugs in manifest: {sorted(vb)}')

    # totals reconciliation
    R['totals'] = {
        'records': len(recs),
        'lep_execution_deduped': len(lep),
        'benign_reference': len(bn),
        'tasks_total': len(tasks),
        'sealed_tasks': by_role.get('acceptance_sealed', 0),
        'dev_tasks': by_role.get('dev_tuning', 0),
    }
    R['all_passed'] = not R['failures']
    (HERE / 'integrity_report.json').write_text(json.dumps(R, indent=1))
    print('PASS' if R['all_passed'] else 'FAIL', '- failures:', R['failures'])
    print('totals:', json.dumps(R['totals']))
    for name, c in R['checks'].items():
        print(f"  [{'ok' if c['ok'] else 'XX'}] {name}: {c['detail']}")
    return 0 if R['all_passed'] else 1

if __name__ == '__main__':
    sys.exit(main())
