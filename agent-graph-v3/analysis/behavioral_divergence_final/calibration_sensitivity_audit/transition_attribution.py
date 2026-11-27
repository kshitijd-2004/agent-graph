#!/usr/bin/env python3.11
"""Mechanism attribution for the original<->calibrated transition (req 2).

Two rules, two axes:
  STATISTIC   orig: S1 = T_d - B_d   (raw LEP-vs-benign mean distance re-
               centered by the cell's benign-pair MEAN B_d)
               cal:  S2 = T_d        (raw mean LEP-vs-benign distance)
  THRESHOLD   orig: th1 = max over the 3 in-cell benign pairs
               cal:  th2 = q98 FAMILY-pool leave-one-cell-out quantile of
               the p3 probe statistics

  orig fires on d: S1 > th1  (i.e. T_d > B_d + th1)
  cal  fires on d: T_d > th2 (strict)

Because B_d >= 0, orig firing implies T_d > th1.  Hence:
  * a REMOVAL (orig union+ -> cal union-) can only be caused by the
    threshold/pooling change (th2 >= T_d on every orig-fired dim);
  * a NEW positive decomposes per-dimension, mutually exclusively:
      - evaluable gain: B_d None under orig (dim not evaluable there)
      - re-centering  : B_d present, T_d > th1 but T_d <= B_d + th1
                        (the -B_d shift suppressed orig; raw stat + pooled
                         threshold or any threshold fires it)
      - pooling       : T_d <= th1 < B_d + th1 impossible... precisely:
                        T_d > th2 while T_d <= B_d + th1 and T_d > th1
                        excludes re-centering; remaining case is th2 <
                        T_d <= th1 (pooled threshold lower than per-cell
                        max -> pooling enabled)
    This script verifies each class counts against the transition ledger
    and reports the row-level OR-decomposition.  Writes
    ledger/transition_attribution.json.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')


def main():
    rows = [json.loads(l) for l in
            open(os.path.join(OUT, 'transition_ledger.jsonl'))]
    dim = {d: Counter() for d in A.DIMS}
    row_mech = Counter()
    checks = Counter()

    for r in rows:
        od, cd = r['orig']['dims'], r['cal']['dims']
        if r['transition'] == 'removed_positive':
            row_fired_v21 = False
            for d in A.DIMS:
                T = cd[d]['distance']
                th1 = od[d]['threshold']
                if od[d]['divergent']:
                    dim[d]['removed_dim_fired_orig'] += 1
                    # invariant: removed row => no cal dim fires
                    if (T is not None and cd[d]['threshold'] is not None
                            and T > cd[d]['threshold']):
                        checks['INvariant_removed_row_cal_dim_fires'] += 1
                    # T > th1 must hold on every orig-fired dim
                    if th1 is None or T is None or not (T > th1):
                        checks['INvariant_removed_origfired_T_not_gt_th1'] += 1
                        print('  invariant fail:', r['trace_id'], d, T, th1)
                    else:
                        row_fired_v21 = True
            # row-level: does the RAW statistic fire any dim under the
            # ORIGINAL per-cell thresholds? (expected: always yes, since a
            # removed row had >=1 dim fire under orig and B>=0 implies
            # T>th1 on that dim)
            if row_fired_v21:
                checks['removed_rows_V_thr_only_union_fire'] += 1
            else:
                checks['removed_rows_NO_dim_T_gt_th1'] += 1
        elif r['transition'] == 'new_positive':
            mech = set()
            for d in A.DIMS:
                T, B, th1 = cd[d]['distance'], od[d]['benign_ref'], \
                    od[d]['threshold']
                th2 = cd[d]['threshold']
                fired_cal = (T is not None and th2 is not None
                             and T > th2)
                if not fired_cal:
                    continue
                if B is None:
                    mech.add('evaluable_gain')
                    dim[d]['added_mech_evaluable_gain'] += 1
                elif th1 is None:
                    # orig dim uncalibrated (no benign pair) -> orig
                    # unfireable; pooled theta available
                    mech.add('evaluable_gain')
                    dim[d]['added_mech_uncalibrated_orig'] += 1
                elif T > th1:
                    mech.add('recentering')
                    dim[d]['added_mech_recentering'] += 1
                else:
                    mech.add('pooling')
                    dim[d]['added_mech_pooling'] += 1
            key = '+'.join(sorted(mech)) if mech else 'undecomposed'
            row_mech[key] += 1
    rep = {
        'per_dimension': {d: dict(dim[d]) for d in A.DIMS},
        'row_mechanisms_new_positive': dict(row_mech),
        'invariants': dict(checks),
        'definitions': {
            'evaluable_gain': 'dimension not evaluable under the original '
                              'rule (B_d None or th1 None) but evaluable '
                              'and positive under the calibrated rule',
            'recentering': 'raw statistic T_d exceeds the original '
                           'per-cell threshold th1, but the original '
                           'centered statistic T_d - B_d does not: the '
                           '-B_d re-centering suppressed it under orig',
            'pooling': 'T_d > th2 while T_d <= th1: the family-pooled '
                      'q98 threshold is LOWER than the per-cell max-of-3 '
                      'threshold on this dimension -> pooling enabled it',
        },
        'note': ('Removals: on every orig-fired dim of a removed row, '
                  'T_d > th1 holds (B_d >= 0), so each removed union '
                  'positive still fires the V_thr_only variant (raw '
                  'statistic, original per-cell thresholds); removals are '
                  'attributable to the threshold/pooling axis alone. '
                  'New positives: OR over the per-dimension mechanisms '
                  'above; a row can carry multiple (reported as combined).'),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'transition_attribution.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep['invariants'], indent=1))
    print('row mechanisms (new positives):',
          rep['row_mechanisms_new_positive'])
    for d in A.DIMS:
        print(d, dict(dim[d]))


if __name__ == '__main__':
    main()
