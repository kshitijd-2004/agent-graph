#!/usr/bin/env python3.11
"""Track B3 — dev-set regrade of the 600 development financial tasks.

Runs the corrected two-stage extractor (``benchmark/fin_answer_v5.py``) with
the versioned spec overlay (``fin_spec_revised``) over the DEV tasks only,
and writes ``v5_task_grade`` alongside the frozen ``task_grade`` for
apples-to-apples comparison.  NO sealed held-out task is read by this
script (constraint B3).  Output: ``regrade_dev_report.json``.

The frozen analyzer ``analyze_downstream.py`` is NOT modified; this harness
replicates its financial branch of ``task_grade``:
    grade = correct  if |extracted - expected| <= tolerance
            failure  otherwise (extraction observed)
            not_gradeable if extraction unavailable or no GT
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
VAL = HERE.parent / 'validation'
FROZEN = ROOT / 'analysis/bnd_v41_cw_swe200_provisional'
SPECROOT = FROZEN / 'specroot'
FROZEN_RUN = FROZEN / 'frozen_run'

# ── module loading (frozen bdv41 needs the frozen benchmark package on path)
sys.path.insert(0, str(FROZEN_RUN))
sp = importlib.util.spec_from_file_location(
    'bdv41_frozen', FROZEN_RUN / 'benchmark/baseline_deviation_v41.py')
frz = importlib.util.module_from_spec(sp)
sys.modules['bdv41_frozen'] = frz
sp.loader.exec_module(frz)

sp2 = importlib.util.spec_from_file_location(
    'fin_answer_v5', ROOT / 'benchmark/fin_answer_v5.py')
v5 = importlib.util.module_from_spec(sp2)
sys.modules['fin_answer_v5'] = v5
sp2.loader.exec_module(v5)

sp3 = importlib.util.spec_from_file_location(
    'fin_spec_revised', HERE.parent / 'fin_spec_revised/fin_spec_revised.py')
spec = importlib.util.module_from_spec(sp3)
sys.modules['fin_spec_revised'] = spec
sp3.loader.exec_module(spec)

T2P = json.loads(Path('/tmp/agrade_audit2/t2p.json').read_text())


def main():
    tasks = [json.loads(l) for l in
             (VAL / 'annotation_tasks_FINAL.jsonl').open()]
    dev = [t for t in tasks
           if t['list_role'] == 'dev_tuning' and not t.get('dup_id')]
    assert len(dev) == 600, len(dev)

    rows = []
    for t in dev:
        tid = t['trace_id']
        fid = t['fixture']
        path = T2P.get(tid) or t.get('trace_file')
        trace = json.loads(Path(path).read_text())
        fr_text = frz._final_response_text(trace)
        frozen_grade = t.get('frozen_task_grade')

        # corrected spec (overlay) + frozen GT (value unchanged)
        cspec = spec.load_corrected_spec(fid)
        ans = cspec['required_facts']['answer']
        corr = spec.corrected_unit_for(fid)
        excl = bool(corr and corr.get('exclude'))

        r = v5.run_v5(fr_text, ans, corr if corr else None)
        ex = r['extraction']
        vd = r['verdict']

        # v5_task_grade: three-way, mirroring the frozen financial branch
        if excl:
            v5_grade = 'excluded'          # no-answer or value-bug fixture
            excl_basis = corr['basis']
        else:
            v5_grade = vd['label']         # correct / incorrect / ungradeable
            excl_basis = ''
        # freeze-compatibility marker: would the frozen 3-state mapping put
        # this row in "not_gradeable"? (unavailable|refused|ambiguous -> ng)
        v5_frozen_mapped = ('not_gradeable' if vd['label'] == 'ungradeable'
                            else ('correct' if vd['label'] == 'correct'
                                  else 'failure'))
        rows.append({
            'trace_id': tid, 'fixture': fid,
            'frozen_task_grade': frozen_grade,
            'v5_grade': v5_grade,
            'v5_grade_frozen_mapped': v5_frozen_mapped,
            'excluded': excl, 'excluded_basis': excl_basis,
            'corrected_unit': (corr or {}).get('unit'),
            'unit_basis': (corr or {}).get('basis'),
            'extracted': ex['value'],
            'gt_value': ans.get('value'),
            'gt_tolerance': ans.get('tolerance'),
            'extraction_status': ex['status'],
            'extraction_basis': ex['basis'],
            'verdict_reason': vd['reason'],
            'flag_bucket': t.get('flag_bucket'),
            'sample_flags': t.get('sample_flags'),
            'lep': t.get('lep'), 'mode': t.get('mode'),
            'topology': t.get('topology'), 'stratum': t.get('stratum'),
        })

    # ── aggregates ──
    gradable = [r for r in rows if not r['excluded']]
    def _cnt(rows, key, val):
        return sum(1 for r in rows if r[key] == val)
    report = {
        'n_dev_tasks': len(rows),
        'excluded_from_grading': _cnt(rows, 'excluded', True),
        'excluded_by_basis': {
            b: sum(1 for r in rows if r['excluded'] and
                  r['excluded_basis'] == b)
            for b in sorted(set(r['excluded_basis'] for r in rows
                                if r['excluded']))},
        'v5_grade_among_gradable': {
            'correct': _cnt(gradable, 'v5_grade', 'correct'),
            'incorrect': _cnt(gradable, 'v5_grade', 'incorrect'),
            'ungradeable': _cnt(gradable, 'v5_grade', 'ungradeable'),
        },
        'frozen_mapped_among_gradable': {
            'correct': _cnt(gradable, 'v5_grade_frozen_mapped', 'correct'),
            'failure': _cnt(gradable, 'v5_grade_frozen_mapped', 'failure'),
            'not_gradeable': _cnt(gradable, 'v5_grade_frozen_mapped',
                                   'not_gradeable'),
        },
        'frozen_grade_among_gradable': {
            'correct': _cnt(gradable, 'frozen_task_grade', 'correct'),
            'failure': _cnt(gradable, 'frozen_task_grade', 'failure'),
            'not_gradeable': _cnt(gradable, 'frozen_task_grade',
                                   'not_gradeable'),
        },
        'extraction_status_among_gradable': {
            s: sum(1 for r in gradable if r['extraction_status'] == s)
            for s in sorted(set(r['extraction_status'] for r in gradable))},
        'transition_matrix_frozen_to_v5_mapped': {},
    }
    trans = report['transition_matrix_frozen_to_v5_mapped']
    for frz_g in ('correct', 'failure', 'not_gradeable'):
        for v5_g in ('correct', 'failure', 'not_gradeable'):
            trans[f'{frz_g}->{v5_g}'] = sum(
                1 for r in gradable
                if r['frozen_task_grade'] == frz_g
                and r['v5_grade_frozen_mapped'] == v5_g)
    (HERE / 'regrade_dev_report.json').write_text(json.dumps(report, indent=1))
    (HERE / 'regrade_dev_rows.jsonl').write_text(
        ''.join(json.dumps(r) + '\n' for r in rows))
    print(json.dumps(report, indent=1))
    print('wrote regrade_dev_report.json + regrade_dev_rows.jsonl')
    return 0


if __name__ == '__main__':
    sys.exit(main())
