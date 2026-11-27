#!/usr/bin/env python3.11
"""Fixture-cluster bootstrap CIs + per-LEP benign excess for the
headline operating points (writes ledger/operating_point_cis.json).

Replaces the ad-hoc inline block: deterministic (seed 20270125 for
the bootstrap; the v6 TV kernel is hash-order sensitive -> run under
PYTHONHASHSEED=0).  Points: the G5-selected p3_q98_f0_family plus
the q95/q99 bands and the p2_q98 clean-null + stratum-pool
comparisons — all FULL-FRAME (the CIs are on the LEP union rate;
the reference-linked recalls are in operating_point_reference.json,
RETROSPECTIVE).
"""
import json
import math
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A
sys.path.insert(0, os.path.join(A.CALP))
import cp_lib as L

OUT = os.path.join(A.AUDIT, 'ledger')


def main():
    os.makedirs(OUT, exist_ok=True)
    cells = json.load(open(os.path.join(A.CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    for c in cells:
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
    led = A.cal()
    fix = {}
    for r in led:
        fix.setdefault(r['fixture_id'], []).append(r['trace_id'])

    pts = {'p3_q95_family': ('T_p3', 0.95, 'family'),
           'p3_q98_family': ('T_p3', 0.98, 'family'),
           'p3_q99_family': ('T_p3', 0.99, 'family'),
           'p2_q98_family': ('T_p2', 0.98, 'family'),
           'p3_q95_stratum': ('T_p3', 0.95, 'stratum')}
    out = {}
    for name, (null, q, pool) in pts.items():
        thr = L.build_theta_table(cells, null_field=null, q=q, pool=pool,
                                  floor=0.0)
        st_l = {'n': 0, 'u': 0}
        st_b = {'n': 0, 'u': 0}
        by_lep_l = {}
        by_lep_b = {}
        fxvals = {}
        for ci, c in enumerate(cells):
            t = thr[ci]
            trig = L.fire(c['T_lep'], t)
            ev = any(c['T_lep'].get(d) is not None and t.get(d) is not None
                     for d in A.DIMS)
            if ev:
                st_l['n'] += 1
                by_lep_l.setdefault(c['lep_code'], {'n': 0, 'u': 0})
                bl = by_lep_l[c['lep_code']]
                bl['n'] += 1
                if trig:
                    st_l['u'] += 1
                    bl['u'] += 1
                fxvals.setdefault(c['trace_id'].split('.')[0],
                                  []).append(1.0 if trig else 0.0)
            for tp in c[null]:
                tb = L.fire(tp, t)
                evb = any(tp.get(d) is not None and t.get(d) is not None
                          for d in A.DIMS)
                if evb:
                    st_b['n'] += 1
                    by_lep_b.setdefault(c['lep_code'], {'n': 0, 'u': 0})
                    bb = by_lep_b[c['lep_code']]
                    bb['n'] += 1
                    if tb:
                        st_b['u'] += 1
                        bb['u'] += 1
        # fixture-cluster percentile bootstrap of the LEP union rate
        keys = sorted(fxvals)
        rng = random.Random(20270125)
        ests = []
        for _ in range(2000):
            pool_ = []
            for _ in range(len(keys)):
                pool_.extend(fxvals[rng.choice(keys)])
            ests.append(sum(pool_) / len(pool_) if pool_ else 0.0)
        ests.sort()
        lr = st_l['u'] / st_l['n']
        br = st_b['u'] / st_b['n']
        out[name] = {
            'lep_rate': lr, 'lep_n': st_l['n'],
            'lep_ci95_cluster': [ests[50], ests[1949]],
            'lep_wilson95': A.wilson(st_l['u'], st_l['n']),
            'b2b_fpr': br, 'b2b_n': st_b['n'],
            'b2b_wilson95': A.wilson(st_b['u'], st_b['n']),
            'excess': lr - br,
            'per_lep': {k: {'lep': by_lep_l.get(k, {'n': 0, 'u': 0}),
                            'b2b': by_lep_b.get(k, {'n': 0, 'u': 0})}
                        for k in sorted(set(by_lep_l) | set(by_lep_b))},
        }
    with open(os.path.join(OUT, 'operating_point_cis.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    for k in out:
        v = out[k]
        print(f"{k:14s} LEP {v['lep_rate']:.4f} "
              f"[{v['lep_ci95_cluster'][0]:.4f},"
              f"{v['lep_ci95_cluster'][1]:.4f}]  B2B {v['b2b_fpr']:.4f}  "
              f"excess {v['excess']:+.4f}")
    print('written ledger/operating_point_cis.json')


if __name__ == '__main__':
    main()
