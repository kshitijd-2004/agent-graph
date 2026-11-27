#!/usr/bin/env python3.11
"""Sensitivity-specificity tradeoff over the full P1 frame (req 7).

Reuses the cached p3 cells (calibrated_production/out/cache_cells_full.json)
so the sweep is cheap and byte-consistent with the production ledger.
Grid: null p2/p3 x q in {0.80, 0.90, 0.95, 0.98, 0.99} x pool
{family, stratum}, floor 0.0 (the floors are a separately-named
sensitivity variant; the frozen choice is 0.0 — reported, not re-tuned).

For each valid operating point:
  * LEP union rate + per-dimension counts + zero-only share;
  * B2B (p3 probe) FPR;
  * LEP-minus-B2B excess + 95% CI;
  * whether any dimension is mathematically incapable of firing
    (max attainable T == max theta, strict >);
  * per-LEP-code union rate + benign excess.
Reference P/R/F1 is NOT recomputed per operating point on the heldout
(reference labels exist only for the heldout partition pair surface;
the frame-level sweep is a retrospective diagnostic per the audit's
standing rules — the classifier_validation artifact carries the
reference-linked evaluation, at the dev-selected rule and,
retrospectively, at the sweep points below where the reference labels
apply).

Writes ledger/sensitivity_tradeoff.json + plots/sensitivity_*.svg.
"""
import json
import math
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A
import svg_plot
sys.path.insert(0, os.path.join(A.CALP))
import cp_lib as L

OUT = os.path.join(A.AUDIT, 'ledger')
PLOTS = os.path.join(A.AUDIT, 'plots')
QVALS = (0.80, 0.90, 0.95, 0.98, 0.99)


def _excess_ci(k1, n1, k2, n2):
    if not (n1 and n2):
        return None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return [d - 1.959964 * se, d + 1.959964 * se]


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(PLOTS, exist_ok=True)
    cells = json.load(open(os.path.join(A.CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    for c in cells:
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
    rep = {'n_cells': len(cells), 'rules': {}}
    for null in ('p2', 'p3'):
        fld = 'T_' + null
        for q in QVALS:
            for pool in ('family', 'stratum'):
                thr = L.build_theta_table(cells, null_field=fld, q=q,
                                          pool=pool, floor=0.0)
                st_l = {'n': 0, 'u': 0, 'dims': {d: 0 for d in A.DIMS},
                        'zero_only': 0}
                st_b = {'n': 0, 'u': 0, 'dims': {d: 0 for d in A.DIMS}}
                by_lep = {code: Counter() for code in
                          ('LEP_HANDOFF_CORRUPTION',
                           'LEP_INDIRECT_PROMPT_INJECTION',
                           'LEP_INPUT_DISREGARD',
                           'LEP_TOOL_RESULT_CORRUPTION')}
                dim_unfireable = {d: 0 for d in A.DIMS}
                for ci, c in enumerate(cells):
                    t = thr[ci]
                    trig = L.fire(c['T_lep'], t)
                    evalrow = any(c['T_lep'].get(d) is not None
                                  and t.get(d) is not None
                                  for d in A.DIMS)
                    if evalrow:
                        st_l['n'] += 1
                        bc = by_lep[c['lep_code']]
                        bc['n'] += 1
                        if trig:
                            st_l['u'] += 1
                            bc['u'] += 1
                            for d in trig:
                                st_l['dims'][d] += 1
                            if all(t[d] == 0.0 for d in trig):
                                st_l['zero_only'] += 1
                    for tp in c[fld]:
                        tb = L.fire(tp, t)
                        teval = any(tp.get(d) is not None and t.get(d)
                                    is not None for d in A.DIMS)
                        if teval:
                            st_b['n'] += 1
                            if tb:
                                st_b['u'] += 1
                                for d in tb:
                                    st_b['dims'][d] += 1
                    for d in A.DIMS:
                        if c['T_lep'][d] is not None and t[d] is not None \
                                and c['T_lep'][d] >= t[d] \
                                and not (c['T_lep'][d] > t[d]):
                            dim_unfireable[d] += 1
                n_l, n_b = st_l['n'], st_b['n']
                lr = st_l['u'] / n_l if n_l else None
                br = st_b['u'] / n_b if n_b else None
                rn = f'{null}_q{int(q*100)}_f0_{pool}'
                rep['rules'][rn] = {
                    'config': {'null': null, 'q': q, 'pool': pool,
                               'floor': 0.0},
                    'lep_union': st_l['u'], 'lep_n': n_l,
                    'lep_rate': lr,
                    'lep_dims': st_l['dims'],
                    'lep_zero_only': st_l['zero_only'],
                    'b2b_fpr_num': st_b['u'], 'b2b_fpr_den': n_b,
                    'b2b_fpr': br,
                    'b2b_dims': st_b['dims'],
                    'excess': (lr - br) if (lr is not None and br
                                           is not None) else None,
                    'excess_ci95': _excess_ci(st_l['u'], n_l, st_b['u'],
                                               n_b),
                    'dim_unfireable_ge_not_gt': dim_unfireable,
                    'dim_unfireable_note': (
                        'cells where T_d == theta_d exactly and strict > '
                        'therefore does not fire (>= would fire); a '
                        'dimension with T_d <= theta_d for all cells is '
                        'mathematically incapable of firing under strict >'),
                    'by_lep_code': {k: {'n': v['n'], 'u': v['u'],
                                        'rate': (v['u'] / v['n']) if v['n']
                                        else None}
                                    for k, v in by_lep.items()},
                }
    # the original frozen rule (per-cell max-of-3) for the side-by-side
    rep['original_frozen_rule'] = {
        'source': 'out/production_summary_v6.json + v6 ledger',
        'm1_union': 5037, 'm1_rate': 5037 / 16773,
        'zero_only_union': 3777,
        'dims': {'assertions': 2452, 'decisions': 2380, 'actions': 2448,
                 'outcomes': 1301}}
    with open(os.path.join(OUT, 'sensitivity_tradeoff.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    # plots
    for null in ('p2', 'p3'):
        xs = [(i, f'q{int(q*100)}\n{pool}')
              for i, (q, pool) in enumerate(
                  [(q, pool) for q in QVALS for pool in
                   ('family', 'stratum')])]
        series = []
        for key in ('lep_rate', 'b2b_fpr'):
            series.append((key, [rep['rules'][f'{null}_q{int(q*100)}_f0_'
                                               f'{pool}'][key]
                                 for q in QVALS for pool in
                                 ('family', 'stratum')]))
        series.append(('excess', [rep['rules'][f'{null}_q{int(q*100)}_f0_'
                                             f'{pool}']['excess']
                                   for q in QVALS for pool in
                                   ('family', 'stratum')]))
        p = svg_plot.line_chart(
            os.path.join(PLOTS, f'sensitivity_{null}_curve.svg'),
            f'LEP rate vs B2B FPR vs excess — {null}-null, '
            'family+stratum pools', xs, series, ylabel='rate')
    # per-LEP excess curves at p3 (the selected null)
    xs2 = [(i, f'q{int(q*100)}\n{pool}')
           for i, (q, pool) in enumerate(
               [(q, pool) for q in QVALS for pool in ('family', 'stratum')])]
    lep_series = []
    for code in by_lep_names():
        lep_series.append((code.replace('LEP_', ''), [
            rep['rules'][f'p3_q{int(q*100)}_f0_{pool}']
            ['by_lep_code'][code]['rate']
            for q in QVALS for pool in ('family', 'stratum')]))
    svg_plot.line_chart(os.path.join(PLOTS, 'sensitivity_per_lep.svg'),
                        'Per-LEP-code union rate across the p3 sweep',
                        xs2, lep_series, ylabel='rate')
    print('written ledger/sensitivity_tradeoff.json')
    for rn in (f'p3_q98_f0_family', 'p3_q98_f0_stratum',
               'p2_q98_f0_family', 'p2_q98_f0_stratum',
               'p3_q95_f0_family', 'p3_q99_f0_family'):
        v = rep['rules'][rn]
        print(f"{rn:18s} LEP {v['lep_union']}/{v['lep_n']} "
              f"= {v['lep_rate']:.4f}  B2B {v['b2b_fpr']:.4f}  "
              f"excess {v['excess']:+.4f} zero_only {v['lep_zero_only']}")


def by_lep_names():
    return ('LEP_HANDOFF_CORRUPTION', 'LEP_INDIRECT_PROMPT_INJECTION',
            'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION')


if __name__ == '__main__':
    main()
