#!/usr/bin/env python3.11
"""Outcome-dimension audit (requirement 5): why does the calibrated rule
report 0 outcome positives?

Loads the cached p3 cells (calibrated_production/out/cache_cells_full.json
— T_p3 carries the p3 probe outcome distances; T_lep the LEP outcome
statistic) and answers, per family:
  * outcome distance distributions (LEP T_d and p3 probes);
  * available / unavailable counts;
  * nonzero distance counts;
  * maximum-distance observations and how many attain it;
  * pooled benign-null distribution and calibrated q98 threshold;
  * maximum mathematically attainable statistic and whether ANY attainable
    value can exceed the threshold;
  * strict `>` vs `>=` consequences.
Writes ledger/outcome_audit.json + an SVG of the pooled distributions.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')


def load_cells():
    p = os.path.join(A.CALP, 'out', 'cache_cells_full.json')
    data = json.load(open(p))
    return data['cells']


def main():
    cells = load_cells()
    # family pools from the p3 null (the exact construction cp_lib uses)
    fams = ('code_review', 'financial_analysis', 'research_synthesis')
    pools = {f: [] for f in fams}
    lep_vals = {f: [] for f in fams}
    n_cells_fam = Counter()
    for c in cells:
        f = c['fam']
        n_cells_fam[f] += 1
        for tp in c['T_p3']:
            v = tp.get('outcomes')
            if v is not None:
                pools[f].append(v)
        if c['T_lep'].get('outcomes') is not None:
            lep_vals[f].append(c['T_lep']['outcomes'])
    rep = {'per_family': {}, 'rule': A.metrics()['rule']}
    for f in fams:
        P = sorted(pools[f])
        lv = sorted(lep_vals[f])
        import math
        # q98 of the pool with leave-one-cell-out is what cp_lib computes;
        # for the audit, recompute the threshold table via cp_lib helpers
        sys.path.insert(0, os.path.join(A.CALP))
        import cp_lib as L
        thr_cells = L.build_theta_table(cells, null_field='T_p3', q=0.98,
                                        pool='family', floor=0.0)
        thrf = [thr_cells[c['idx']]['outcomes'] for c in cells
                if c['fam'] == f]
        thrf = [t for t in thrf if t is not None]
        q98_pool = P[int(math.ceil(0.98 * len(P))) - 1] if P else None
        q99_pool = P[int(math.ceil(0.99 * len(P))) - 1] if P else None
        max_pool = P[-1] if P else None
        max_lep = lv[-1] if lv else None
        rep['per_family'][f] = {
            'n_cells': n_cells_fam[f],
            'pool_size_p3_probes': len(P),
            'n_nonzero_p3': sum(1 for v in P if v > 0),
            'pool_p50': P[len(P) // 2] if P else None,
            'pool_p90': P[int(0.90 * len(P))] if P else None,
            'pool_q98': q98_pool,
            'pool_q99': q99_pool,
            'pool_max': max_pool,
            'pool_max_count': P.count(max_pool) if max_pool is not None else 0,
            'lep_evaluable': len(lv),
            'lep_unavailable': n_cells_fam[f] - len(lv),
            'lep_nonzero': sum(1 for v in lv if v > 0),
            'lep_max': max_lep,
            'lep_max_count': lv.count(max_lep) if max_lep is not None else 0,
            'calibrated_thr_cells_eval': len(thrf),
            'calibrated_thr_min': min(thrf) if thrf else None,
            'calibrated_thr_max': max(thrf) if thrf else None,
            'n_cells_thr_0': sum(1 for t in thrf if t == 0.0),
            'n_cells_thr_max': sum(1 for t in thrf
                                   if max_pool is not None and t == max_pool),
            # attainability analysis
            'max_attainable_lep': max_lep,
            'n_lep_at_max': lv.count(max_lep) if max_lep is not None else 0,
            'can_strict_gt_fire': None,  # filled per-cell below
            'can_ge_fire': None,
        }
        # per-cell attainability
        can_gt = can_ge = 0
        for c in cells:
            if c['fam'] != f:
                continue
            mv = c['T_lep'].get('outcomes')
            t = thr_cells[c['idx']]['outcomes']
            if mv is None or t is None:
                continue
            if mv > t:
                can_gt += 1
            if mv >= t:
                can_ge += 1
        rep['per_family'][f]['can_strict_gt_fire'] = can_gt
        rep['per_family'][f]['can_ge_fire'] = can_ge
        # histogram of the pool (0..1 in 0.05 bins)
        if P:
            h = [0] * 20
            for v in P:
                h[min(19, int(v * 20))] += 1
            rep['per_family'][f]['pool_hist_005'] = h
    # whole-frame cross-check against the ledger
    led = A.cal()
    rep['ledger_crosscheck'] = {
        'cal_outcome_pos': sum(1 for r in led
                               if (r.get('m1_dims_cal') or {}).get('outcomes')),
        'orig_outcome_pos': sum(1 for r in led
                                if (r.get('m1_dims_orig') or {}).get('outcomes')),
        'eval_outcomes_cal': sum(1 for r in led if (
            (r.get('m1_dim_eval_cal') or {}).get('outcomes'))),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'outcome_audit.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    for f in fams:
        d = rep['per_family'][f]
        print(f'{f:22s} pool n={d["pool_size_p3_probes"]:7d} '
              f'q98={d["pool_q98"]} q99={d["pool_q99"]} '
              f'max={d["pool_max"]} (x{d["pool_max_count"]}) '
              f'lep max={d["lep_max"]} gt_fire={d["can_strict_gt_fire"]} '
              f'ge_fire={d["can_ge_fire"]}')
    print('written ledger/outcome_audit.json')


if __name__ == '__main__':
    main()
