#!/usr/bin/env python3.11
"""Full-population financial regrade under the corrected v5 extractor.

Answers the central audit question: of the 3,130 frozen not-gradeable
financial executions, how many are genuinely ungradeable (agent omitted /
ambiguous) versus a valid answer the frozen extractor threw away?

Runs the two-stage corrected extractor + versioned spec overlay over ALL
4,409 financial per_trace rows (3 duplicate trace_ids retained with the
ledger's own multiplicity, matching how the frozen numbers were computed),
reads NO sealed task annotations, and writes:

  regrade_full_report.json   — aggregates, transition matrices, breakdowns
  regrade_full_rows.jsonl    — one JSON row per execution (full audit log)

Frozen artifacts (per_trace.jsonl, traces, frozen v4.1, analyze_downstream)
are read-only inputs; nothing in the frozen pipeline is modified.
"""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path('/u/kdhande/agent-graph/agent-graph-v3')
FROZEN = ROOT / 'analysis/bnd_v41_cw_swe200_provisional'
FROZEN_RUN = FROZEN / 'frozen_run'
PER_TRACE = (ROOT / 'analysis/downstream_propagation_corrected'
             / 'results_v2/per_trace.jsonl')
T2P = Path('/tmp/agrade_audit2/t2p.json')

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


def main():
    t2p = json.loads(T2P.read_text())
    per = [json.loads(l) for l in PER_TRACE.open()
           if json.loads(l).get('task_family') == 'financial_analysis']
    assert len(per) == 4409, len(per)

    rows, cached = [], {}
    for i, pt in enumerate(per):
        tid = pt['trace_id']
        fid = tid.split('.')[0]
        # spec + corrected-unit cached per fixture (identical across its
        # executions); the trace itself must be loaded PER ROW: each
        # execution of a fixture has its own final response.
        if fid not in cached:
            cached[fid] = (spec.load_corrected_spec(fid)['required_facts'],
                           spec.corrected_unit_for(fid))
        rf, corr = cached[fid]
        tr = json.loads(Path(t2p[tid]).read_text())
        fr_text = frz._final_response_text(tr)
        ans = rf.get('answer') or {}
        corr_unit = corr.get('unit') if corr else None
        excl = bool(corr and corr.get('exclude'))
        r = v5.run_v5(fr_text, ans,
                      {'unit': corr_unit} if corr_unit else None)
        ex, vd = r['extraction'], r['verdict']
        v5map = ('not_gradeable' if vd['label'] == 'ungradeable'
                 else ('correct' if vd['label'] == 'correct' else 'failure'))
        rows.append({
            'trace_id': tid, 'fixture': fid,
            'lep': pt.get('lep_code'), 'topology': pt.get('topology'),
            'mode': pt.get('propagation_mode'),
            'benign_or_perturbed': 'benign' if pt.get('reference_set')
                                    and tid in pt['reference_set']
                                   else 'perturbed',
            'frozen_task_grade': pt.get('task_grade'),
            'v5_grade': vd['label'],
            'v5_grade_frozen_mapped': v5map,
            'excluded': excl,
            'excluded_basis': (corr or {}).get('basis', ''),
            'extracted': ex['value'], 'gt_value': ans.get('value'),
            'status': ex['status'], 'basis': ex['basis'],
            'ambiguous_reason': ex['ambiguity'],
            'm1': pt.get('m1_whole_trace_positive'),
            'm2': pt.get('m2_cross_agent_positive'),
            'm3': pt.get('m3_reach_class'),
            'm4': pt.get('m4_task_failure'),
        })
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(per)} ...', file=sys.stderr)

    (HERE / 'regrade_full_rows.jsonl').write_text(
        ''.join(json.dumps(x) + '\n' for x in rows))

    g = [x for x in rows if not x['excluded']]
    e = [x for x in rows if x['excluded']]

    def c(rows_, key, val=None):
        return (sum(1 for r in rows_ if r[key] == val) if val is not None
                else Counter(r[key] for r in rows_))

    ng_frozen = [x for x in g if x['frozen_task_grade'] == 'not_gradeable']
    trans = {}
    for fg in ('correct', 'failure', 'not_gradeable'):
        for vg in ('correct', 'failure', 'not_gradeable'):
            trans[f'{fg}->{vg}'] = c(g, 'frozen_task_grade', fg) and sum(
                1 for x in g
                if x['frozen_task_grade'] == fg
                and x['v5_grade_frozen_mapped'] == vg)

    # the CENTRAL number: of the 3,130 frozen not-gradeable (gradable-set),
    # how many does v5 make gradeable?
    newly_gradeable = [x for x in ng_frozen
                       if x['v5_grade_frozen_mapped'] in ('correct', 'failure')]
    still_ng = [x for x in ng_frozen
                if x['v5_grade_frozen_mapped'] == 'not_gradeable']
    still_ng_reason = Counter(x['ambiguous_reason'] or x['status']
                              for x in still_ng)

    # sensitivity (Section 8): overall failure rate over the full 4,409
    fin_fail = c(rows, 'frozen_task_grade', 'failure')
    fin_corr = c(rows, 'frozen_task_grade', 'correct')
    fin_ng = c(rows, 'frozen_task_grade', 'not_gradeable')
    # worst case: all NG are failures
    worst = (fin_fail + fin_ng) / len(rows)
    # best case: all NG are correct
    best = fin_fail / len(rows)
    # v5 point estimate on the gradable set:
    v5_fail_g = c(g, 'v5_grade_frozen_mapped', 'failure')
    v5_corr_g = c(g, 'v5_grade_frozen_mapped', 'correct')
    v5_ng_g = c(g, 'v5_grade_frozen_mapped', 'not_gradeable')
    # v5 band: assume still-NG worst/best
    v5_worst = (v5_fail_g + v5_ng_g) / len(rows)
    v5_best = v5_fail_g / len(rows)

    by_lep = {}
    for lep in sorted(set(x['lep'] for x in g)):
        sub = [x for x in g if x['lep'] == lep]
        by_lep[lep] = {
            'total': len(sub),
            'frozen_ng': c(sub, 'frozen_task_grade', 'not_gradeable'),
            'v5_ng': c(sub, 'v5_grade_frozen_mapped', 'not_gradeable'),
            'v5_correct': c(sub, 'v5_grade_frozen_mapped', 'correct'),
            'v5_failure': c(sub, 'v5_grade_frozen_mapped', 'failure'),
            'v5_rescued': sum(1 for x in sub
                              if x['frozen_task_grade'] == 'not_gradeable'
                              and x['v5_grade_frozen_mapped']
                              in ('correct', 'failure')),
        }

    report = {
        'n_total': len(rows), 'n_excluded': len(e),
        'excluded_by_basis': Counter(x['excluded_basis'] for x in e),
        'frozen_grade': dict(Counter(x['frozen_task_grade'] for x in rows)),
        'v5_frozen_mapped_among_gradable': dict(Counter(
            x['v5_grade_frozen_mapped'] for x in g)),
        'v5_extraction_status_among_gradable': dict(Counter(
            x['status'] for x in g)),
        'frozen_ng_total_gradable_set': len(ng_frozen),
        'central_question': {
            'frozen_ng': len(ng_frozen),
            'v5_newly_gradeable': len(newly_gradeable),
            'v5_newly_correct': c(newly_gradeable, 'v5_grade_frozen_mapped',
                                   'correct'),
            'v5_newly_failure': c(newly_gradeable, 'v5_grade_frozen_mapped',
                                  'failure'),
            'v5_still_ungradeable': len(still_ng),
            'still_ng_reasons': dict(still_ng_reason),
            'pct_of_frozen_ng_rescued':
                round(len(newly_gradeable) / max(1, len(ng_frozen)), 4),
        },
        'transition_matrix_frozen_to_v5': trans,
        'sensitivity_full_4409': {
            'frozen': {'failure_rate_best_all_ng_correct': round(best, 4),
                       'failure_rate_worst_all_ng_failure': round(worst, 4),
                       'observed_gradable_only': round(fin_fail / len(rows),
                                                        4)},
            'v5': {'failure_rate_best_all_ng_correct': round(v5_best, 4),
                    'failure_rate_worst_all_ng_failure': round(v5_worst, 4),
                    'observed_gradable_only':
                        round(v5_fail_g / len(rows), 4)},
        },
        'by_lep_gradable_set': by_lep,
        'by_mode': {m: {'total': len([x for x in g if x['mode'] == m]),
                        'frozen_ng': c([x for x in g if x['mode'] == m],
                                       'frozen_task_grade',
                                       'not_gradeable'),
                        'v5_ng': c([x for x in g if x['mode'] == m],
                                   'v5_grade_frozen_mapped',
                                   'not_gradeable')}
                    for m in sorted(set(x['mode'] for x in g))},
        'by_topology': {m: {'total': len([x for x in g if x['topology'] == m]),
                            'frozen_ng': c([x for x in g if x['topology'] == m],
                                           'frozen_task_grade',
                                           'not_gradeable'),
                            'v5_ng': c([x for x in g if x['topology'] == m],
                                       'v5_grade_frozen_mapped',
                                       'not_gradeable')}
                        for m in sorted(set(x['topology'] for x in g))},
    }
    (HERE / 'regrade_full_report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in
                      ('n_total', 'frozen_grade',
                       'v5_frozen_mapped_among_gradable',
                       'central_question', 'transition_matrix_frozen_to_v5',
                       'sensitivity_full_4409')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
