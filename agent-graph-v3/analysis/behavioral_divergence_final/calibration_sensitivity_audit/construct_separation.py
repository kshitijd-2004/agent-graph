#!/usr/bin/env python3.11
"""Requirement 8 — construct separation: the two rules measure different
things.

Four nested/approximately-nested constructs (row level, P1 frame):
  C1 observable-difference   any evaluable dim with T_d > 0
     (LEP differs, even trivially, from the mean benign reference)
  C2 exceeds-own-variability any dim with delta_d = T_d - B_d > 0
     (LEP strays FURTHER than the mean benign pair in its own cell)
  C3 exceeds-all-benign-pairs  (the ORIGINAL rule) any dim with
     T_d > B_d + theta_max3  = M1_orig = 5,037
  C4 pooled-tail-anomaly  (the CALIBRATED rule) any dim with
     T_d > theta_q98_family(LOO)  = M1_cal = 943
Population-level construct:
  C5 perturbation-associated EXCESS: LEP rate - matched B2B null rate
     (+2.78 pp at p3_q98; the G5 statistic) — a POPULATION estimate,
     never a row label.

C2 ⊆ C1, C3 ⊆ C2.  C4 is NOT a subset of C3 (256 new positives) and
C3 is NOT a subset of C4 (4,350 removed).  The gap C3 − C4 is the
rows that "exceed their own cell's benign pairs" yet sit inside the
family's pooled benign tail — i.e. the original rule's per-cell
extremal construct vs the calibrated rule's pooled-tail construct.
This is the construct difference the audit must make explicit.
Writes ledger/construct_funnel.json + plots/construct_funnel.svg.
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A
import svg_plot

OUT = os.path.join(A.AUDIT, 'ledger')
PLOTS = os.path.join(A.AUDIT, 'plots')


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(PLOTS, exist_ok=True)
    rows = [json.loads(l) for l in
            open(os.path.join(OUT, 'transition_ledger.jsonl'))]
    n = len(rows)
    c1 = c2 = c3 = c4 = 0
    by_fam = Counter()
    c1f = Counter(); c2f = Counter(); c3f = Counter(); c4f = Counter()
    for r in rows:
        cd, od = r['cal']['dims'], r['orig']['dims']
        fam = r['task_family']
        t1 = t2 = False
        for d in A.DIMS:
            T = cd[d]['distance']
            delta = od[d]['delta']
            if T is not None and T > 0:
                t1 = True
            if delta is not None and delta > 0:
                t2 = True
        c3_ = bool(r['orig']['triggered'])
        c4_ = bool(r['cal']['triggered'])
        c1 += int(t1); c2 += int(t2)
        c3 += int(c3_); c4 += int(c4_)
        if t1: c1f[fam] += 1
        if t2: c2f[fam] += 1
        if c3_: c3f[fam] += 1
        if c4_: c4f[fam] += 1
    rep = {
        'n_frame': n,
        'C1_observable_difference': c1,
        'C2_exceeds_own_cell_benign_mean': c2,
        'C3_original_rule_exceeds_all_benign_pairs': c3,
        'C4_calibrated_pooled_tail_anomaly': c4,
        'rates': {k: v / n for k, v in
                   (('C1', c1), ('C2', c2), ('C3', c3), ('C4', c4))},
        'per_family': {f: {
            'C1': c1f[f], 'C2': c2f[f], 'C3': c3f[f], 'C4': c4f[f],
            'n': sum(1 for r in rows if r['task_family'] == f)}
            for f in ('code_review', 'financial_analysis',
                      'research_synthesis')},
        'C5_population_excess': {
            'source': 'out/production_sensitivity_calibrated_v6.json + '
                      'calibrated_production B2B control',
            'p3_q98_family': {'LEP_union': 943 / 16773,
                              'B2B_probe_union': 1430 / 50319,
                              'excess_pp': 2.78,
                              'ci95_pp': [2.40, 3.16]},
        },
        'nesting': ('C2 ⊆ C1 and C3 ⊆ C2 hold by construction '
                     '(B_d >= 0, theta_max3 >= 0).  C4 ⊄ C3: 256 new '
                     'positives; C3 ⊄ C4: 4,350 removed.  The '
                     'intersection C3 ∩ C4 = 687 (the "stay positive" '
                     'rows).'),
        'interpretation': (
            'C3 is a PER-CELL EXTREMAL construct: "this LEP differs '
            'from ALL its own benign pairs more than the most '
            'different benign pair differs from itself".  C4 is a '
            'POOLED TAIL construct: "this LEP\'s deviation sits in the '
            'top 2% of pooled benign probe deviations for its family".  '
            'They are DIFFERENT statistical questions: C3 asks '
            '"is the LEP the most different execution in this cell?" '
            'and, whenever the cell\'s benigns agree on a dimension '
            '(zero theta), ANY nonzero LEP deviation passes; C4 asks '
            '"is this deviation rare among benign executions in the '
            'family?" The 30.03% is the per-cell-extremal rate; the '
            '5.62% is the pooled-tail-anomaly rate; NEITHER is "the " '
            'incidence of meaningful deviation.  The meaningful-'
            'deviation construct is best approached by C2 (exceeds own '
            'cell\'s benign mean) + the reference-confirmed slice, '
            'reported as a separate measurement, NOT as a headline '
            'anomaly rate.'),
    }
    with open(os.path.join(OUT, 'construct_funnel.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    svg_plot.bar_chart(
        os.path.join(PLOTS, 'construct_funnel.svg'),
        f'Construct funnel over P1 frame (n={n})',
        [('C1 any T>0', c1), ('C2 delta>0', c2),
         ('C3 orig union', c3), ('C4 cal union', c4)],
        ylabel='rows')
    print(json.dumps({k: rep[k] for k in
                      ('C1_observable_difference',
                       'C2_exceeds_own_cell_benign_mean',
                       'C3_original_rule_exceeds_all_benign_pairs',
                       'C4_calibrated_pooled_tail_anomaly', 'rates')},
                     indent=1))
    print('per family:', json.dumps(rep['per_family'], indent=1))


if __name__ == '__main__':
    main()
