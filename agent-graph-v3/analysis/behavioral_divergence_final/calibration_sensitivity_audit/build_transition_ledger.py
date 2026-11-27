#!/usr/bin/env python3.11
"""Build the complete transition ledger (audit requirement 2 + 4).

For every frame row, record both operating rules' per-dimension
distances/thresholds/flags plus all context needed to classify the
4,350 removed positives and 256 new positives (requirement 4's five
evidence classes).  Writes ledger/transition_ledger.jsonl + a summary.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')
os.makedirs(OUT, exist_ok=True)


def state_of(r):
    return 'pos' if r['m1_union_orig'] else 'neg'


def main():
    rows = A.cal()
    v6 = A.v6_by()
    ds = A.ds_by()
    fr = A.frozen_by()
    out_rows = []
    for r in rows:
        tid = r['trace_id']
        d6 = v6.get(tid, {}).get('per_dimension') or {}
        f = fr.get(tid, {})
        st = f.get('stage') or {}
        dsc = ds.get(tid, {})
        # original rule fields
        orig_dims = {}
        for d in A.DIMS:
            e = d6.get(d) or {}
            orig_dims[d] = {
                'distance': e.get('lep_distance'),
                'threshold': e.get('threshold'),
                'benign_ref': e.get('benign_reference_distance'),
                'delta': e.get('delta'),
                'divergent': bool(e.get('divergent')),
                'evaluable': bool(e.get('evaluable')),
                'zero_threshold': bool(e.get('zero_threshold')),
                'reason': e.get('reason'),
            }
        u6 = v6.get(tid, {}).get('union') or {}
        trig_o = [d for d in (u6.get('triggered_dimensions') or [])]
        zt_o = [d for d in (u6.get('zero_threshold_triggered') or [])]
        # calibrated rule fields
        cal_dims = {d: {
            'distance': (r.get('m1_dist_cal') or {}).get(d),
            'threshold': (r.get('m1_thr_cal') or {}).get(d),
            'divergent': bool((r.get('m1_dims_cal') or {}).get(d)),
            'evaluable': bool((r.get('m1_dim_eval_cal') or {}).get(d)),
        } for d in A.DIMS}
        trig_c = r.get('m1_triggered_cal') or []
        removed = r['m1_union_orig'] and not r['m1_union_cal']
        added = (not r['m1_union_orig']) and r['m1_union_cal']
        # reference labels, where they exist (heldout reference pairs)
        rec = {
            'trace_id': tid,
            'fixture_id': tid.split('.')[0],
            'task_family': r.get('task_family'),
            'lep_code': r.get('lep_code'),
            'topology': r.get('topology'),
            'propagation_mode': r.get('propagation_mode'),
            'transition': ('removed_positive' if removed
                           else ('new_positive' if added
                                 else ('stay_positive'
                                       if r['m1_union_orig']
                                       else 'stay_negative'))),
            'orig': {'union': bool(r['m1_union_orig']),
                     'dims': orig_dims, 'triggered': trig_o,
                     'zero_only': r.get('m1_zero_only_orig')},
            'cal': {'union': bool(r['m1_union_cal']),
                    'state': r.get('m1_cal_state'),
                    'dims': cal_dims, 'triggered': trig_c,
                    'zero_only': r.get('m1_zero_only_cal')},
            # original rule: which dims of a removed positive rested on a
            # ZERO theta (the frozen zero-threshold band)
            'orig_triggered_on_zero': [d for d in trig_o
                                      if orig_dims[d]['zero_threshold']],
            # calibrated: any dim where T_d strictly exceeds the pooled theta
            # by a large margin (candidate "genuine large change" evidence)
            'cal_dim_excess': {d: ((cal_dims[d]['distance']
                                     - cal_dims[d]['threshold'])
                                    if (cal_dims[d]['distance'] is not None
                                        and cal_dims[d]['threshold']
                                        is not None) else None)
                                for d in A.DIMS},
            # original rule delta margins (T_d - B_d - theta_d > 0 fired)
            'orig_delta': {d: orig_dims[d]['delta'] for d in A.DIMS},
            'orig_threshold': {d: orig_dims[d]['threshold']
                               for d in A.DIMS},
            # m4 / reach context (frozen evidence, unchanged)
            'm2_cross_agent': bool(dsc.get('m2_cross_agent_positive')),
            'm2_scope': dsc.get('m2_scope'),
            'm2_nonzero_threshold': bool(dsc.get('m2_nonzero_threshold')),
            'm3_reach_class': dsc.get('m3_reach_class'),
            'm3_detail': dsc.get('m3_detail'),
            'm4_task_failure': dsc.get('m4_task_failure'),
            'm4_class': v6.get(tid, {}).get('m4_class'),
            'm4_matched': bool(v6.get(tid, {}).get('m4_matched')),
            'lep_grade': v6.get(tid, {}).get('lep_grade'),
            'benign_grades': v6.get(tid, {}).get('benign_grades'),
            # checkpoint composite (frozen M2 gate inputs)
            'ckpt_d_peak': st.get('d_peak'),
            'ckpt_d_final': st.get('d_final_output'),
            'ckpt_stage_thr_frozen': st.get('stage_threshold'),
            'ckpt_final_thr_frozen': st.get('final_threshold'),
        }
        out_rows.append(rec)

    p = os.path.join(OUT, 'transition_ledger.jsonl')
    with open(p, 'w') as fh:
        for rec in out_rows:
            fh.write(json.dumps(rec) + '\n')
    # quick summary
    import collections
    c = collections.Counter(rec['transition'] for rec in out_rows)
    zc = collections.Counter()
    for rec in out_rows:
        if rec['transition'] == 'removed_positive':
            if rec['orig']['zero_only']:
                zc['zero_only_union'] += 1
            elif rec['orig_triggered_on_zero']:
                zc['partly_zero'] += 1
            else:
                zc['all_nonzero_theta'] += 1
    summary = {'n': len(out_rows), 'transitions': dict(c),
               'removed_positive_zero_classes': dict(zc),
               'note': 'zero_only_union = every triggered dim had theta=0; '
                       'partly_zero = >=1 zero-theta triggered dim plus '
                       'nonzero-theta dims; all_nonzero_theta = every '
                       'triggered dim fired on a nonzero per-cell theta'}
    with open(os.path.join(OUT, 'transition_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
