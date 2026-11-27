#!/usr/bin/env python3.11
"""Generate the audit SVG plots from the ledger artifacts (deterministic)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import svg_plot

OUT = os.path.join(HERE, 'ledger')
PLOTS = os.path.join(HERE, 'plots')
os.makedirs(PLOTS, exist_ok=True)


def _load(name):
    return json.load(open(os.path.join(OUT, name)))


def main():
    # 1) outcome-dimension pool histograms (the "mass at the maximum")
    oa = _load('outcome_audit.json')
    for f, d in oa['per_family'].items():
        h = d['pool_hist_005']
        labels = [f'{i * 5}%' if i < 19 else '0.95+' for i in range(20)]
        svg_plot.bar_chart(
            os.path.join(PLOTS, f'outcome_pool_{f}.svg'),
            f'{f}: p3 benign-null outcome-distance pool '
            f'(n={d["pool_size_p3_probes"]}; 1.0-mass '
            f'{d["pool_max_count"]})',
            list(zip(labels, h)),
            ylabel='probe count')
    # 2) sensitivity-specificity / precision-recall operating curve
    op = _load('operating_point_reference.json')
    pts = list(op['operating_points'].items())
    xs = [(i, k.replace('_', '\n')) for i, (k, _) in enumerate(pts)]
    svg_plot.line_chart(
        os.path.join(PLOTS, 'operating_points_pr.svg'),
        'Operating points vs the observable-divergence reference '
        '(RETROSPECTIVE, heldout, partition-local pools)',
        xs,
        [('precision', [v['precision'] for _, v in pts]),
         ('recall', [v['recall'] for _, v in pts])],
        ylabel='rate')
    # 3) sensitivity tradeoff curve from the sweep
    st = _load('sensitivity_tradeoff.json')
    p3 = [k for k in st['rules'] if k.startswith('p3_')]
    xs = [(i, k.replace('_f0_', '\n').replace('_', '/'))
          for i, k in enumerate(sorted(p3))]
    svg_plot.line_chart(
        os.path.join(PLOTS, 'sensitivity_tradeoff_curve.svg'),
        'LEP rate vs B2B probe FPR vs LEP-B2B excess across the p3 '
        'sweep (full P1 frame, q in 0.80..0.99, family+stratum pools)',
        xs,
        [('LEP union rate', [st['rules'][k]['lep_rate'] for k in sorted(p3)]),
         ('B2B probe FPR', [st['rules'][k]['b2b_fpr'] for k in
                            sorted(p3)]),
         ('LEP - B2B excess', [st['rules'][k]['excess']
                               for k in sorted(p3)])],
        ylabel='rate')
    # 4) threshold-pooling: stable vs variable cell suppression
    pa = _load('pooling_audit.json')
    sc = pa['stable_cell_census']
    svg_plot.bar_chart(
        os.path.join(PLOTS, 'pooling_stable_cells.svg'),
        'Stable cells (in-cell theta_max3 = 0) vs pooled q98 family '
        'threshold',
        [(k, v) for k, v in sorted(sc.items())],
        ylabel='cells x dims')
    print('wrote outcome pool plots + operating-point PR + sensitivity '
          'curve + pooling stable-cell plots')
    print('plots:', sorted(os.listdir(PLOTS)))


if __name__ == '__main__':
    main()
