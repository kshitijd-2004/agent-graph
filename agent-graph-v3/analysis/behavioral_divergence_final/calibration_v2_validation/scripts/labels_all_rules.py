#!/usr/bin/env python3.11
"""Per-trace labels for ALL four rules under comparison (requirement 6).

  S1     — frozen calibration_v2 shrinkage rule (population pools; the
           frozen pin, byte-stable)
  C0     — original per-cell extremal (T > B + θ1, i.e. the recentered-
           original / V4.1-construct form)
  C1     — previous pooled-tail rule p3_q98_f0_family (family q98 LOO
           of the p3 probe pool)
  V41    — the frozen V4.1 recentered-extremal per-dimension union
           (the independent byte-pinned reference surface; union +
           per-dimension)

All labels are recomputed from the sealed ledgers (never from the
reports), so this study's rule table is implementation-independent of
the calibration_v2 report layer.  Writes ledger/rule_labels.jsonl
(one row per P1* trace; the study's analysis filters to its sample).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
OUT = os.path.join(STUDY, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = cv2.DIMS


def main():
    os.makedirs(OUT, exist_ok=True)
    cells, _ = cv2.load_cells()
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    zt = cv2.shrinkage_weights(full, v6dim, cv2.build_pair_pools(full),
                               pools)
    onull = cv2.outcome_null(full)
    bytid = {c['trace_id']: i for i, c in enumerate(full)}

    rows = []
    for policy in ('S1_shrinkage', 'C0_original', 'C1_family_q98'):
        scored = cv2.score_candidate(full, v6dim, pools, zt, onull,
                                      policy)
        for r in scored:
            rows.append({'trace_id': r['trace_id'], 'rule': policy,
                        'union': r['union'], 'fires': r['fires'],
                        'triggered': r['triggered'],
                        'n_eval': r['n_eval'],
                        'undetermined': r['undetermined']})
    rs = json.load(open(os.path.join(V2, 'ledger',
                                     'reference_surface.json')))
    for tid in rs['v41_union']:
        u = rs['v41_union'][tid]
        pd = rs['v41_per_dim'].get(tid) or {}
        fires = {d: pd.get(d) for d in DIMS}
        rows.append({'trace_id': tid, 'rule': 'V41_union',
                     'union': bool(u) if u is not None else None,
                     'fires': fires,
                     'triggered': [d for d in DIMS if pd.get(d) is True],
                     'n_eval': sum(1 for d in DIMS if pd.get(d)
                                   is not None),
                     'undetermined': u is None})
    json.dump(rows, open(os.path.join(OUT, 'rule_labels.jsonl'), 'w'),
              indent=None)
    print('rule label rows:', len(rows),
          '(3 cv2 policies x 16,773 + V4.1 x', len(rs['v41_union']),
          ')')


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
