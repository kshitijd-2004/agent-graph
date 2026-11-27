#!/usr/bin/env python3.11
"""Strict > vs >= operator sensitivity (requirement 5, last item).

The calibrated rule fires on strict T_d > theta_d.  Where a T-value
ties exactly with the pooled threshold, strict > and >= differ.  This
quantifies, per family/dimension, on the full frame:
  * cells where T_d == theta_q98_family exactly (the tie set);
  * how many of those tie cells are currently negative but would
    flip positive under >=;
  * the effect on the B2B probe null;
  * the CONSEQUENCE assessment (requirement 5: "Do not change the
    comparison operator simply to produce positive results.  Evaluate
    the consequences for false positives and sensitivity").
Writes ledger/operator_sensitivity.json.
"""
import json
import os
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
    thr = L.build_theta_table(cells, null_field='T_p3', q=0.98,
                              pool='family', floor=0.0)
    tie = {d: Counter() for d in A.DIMS}
    flip = {d: Counter() for d in A.DIMS}
    union_tie_cells = 0
    union_ge_extra = 0
    probe_tie = 0            # probe unions that fire under >= but not >
    probe_strict_fire = 0    # probe unions firing under strict >
    probe_ge_fire = 0        # probe unions firing under >=
    probe_n = 0
    for ci, c in enumerate(cells):
        t = thr[ci]
        strict_fire = set()
        ge_fire = set()
        cell_has_tie = False
        for d in A.DIMS:
            T, th = c['T_lep'][d], t[d]
            if T is None or th is None:
                continue
            if T == th:
                tie[d]['tie'] += 1
                cell_has_tie = True
            if T > th:
                strict_fire.add(d)
            if T >= th:
                ge_fire.add(d)
        for d in ge_fire - strict_fire:
            flip[d]['would_flip'] += 1
        if cell_has_tie:
            union_tie_cells += 1
        if strict_fire:
            pass
        union_strict = bool(strict_fire)
        union_ge = bool(ge_fire)
        if union_ge and not union_strict:
            union_ge_extra += 1
        for tp in c['T_p3']:
            s, g = set(), set()
            evaluable = False
            for d in A.DIMS:
                if tp.get(d) is None or t[d] is None:
                    continue
                evaluable = True
                if tp[d] > t[d]:
                    s.add(d)
                if tp[d] >= t[d]:
                    g.add(d)
            if evaluable:
                probe_n += 1
            probe_strict_fire += int(bool(s))
            probe_ge_fire += int(bool(g))
            if g and not s:
                probe_tie += 1
    rep = {
        'operator': 'strict > (the executed rule) vs >= ',
        'lep_tie_cells': union_tie_cells,
        'lep_union_flip_under_ge': union_ge_extra,
        'per_dimension': {
            d: {'n_tie': tie[d]['tie'], 'n_would_flip': flip[d]['would_flip']}
            for d in A.DIMS},
        'b2b_probe_observations': probe_n,
        'b2b_probe_strict_fire_union': probe_strict_fire,
        'b2b_probe_ge_fire_union': probe_ge_fire,
        'b2b_probe_tie_flip': probe_tie,
        'consequence_assessment': {
            'fpr_effect': ('switching to >= moves every exact-tie T into '
                           'the positive class; since the tie set includes '
                           'the pooled quantile value itself, the B2B '
                           'null gains `probe_tie` fired probes -> the '
                           'empirical FPR rises by probe_tie / 50319 '
                           '(computed below); the LEP side gains the '
                           'flip set.  The operator choice is therefore '
                           'not neutral: it shifts BOTH sides.'),
            'sensitivity_effect': ('the flips are by construction rows '
                                   'at-or-above the pooled q98 boundary: '
                                   'they are the largest within-family '
                                   'deviations that strict > excludes by '
                                   'a measure-zero tie.  They are NOT '
                                   'arbitrary positives.'),
            'recommendation': ('do NOT switch to >= to manufacture '
                               'positives (requirement 5).  Where the '
                               'pool mass concentrates at a value '
                               '(outcomes at 1.0), the tie is the '
                               'MAXIMUM attainable statistic — the '
                               'dimension is effectively disabled either '
                               'way; the operator question for outcomes '
                               'is moot at q98.  For continuous dims '
                               '(assertions/decisions/actions) ties are '
                               'rare and the strict- > is the '
                               'conservative, G5-consistent choice.'),
        },
    }
    rep['consequence_assessment']['fpr_effect'] = (
        f"strict: {rep['b2b_probe_strict_fire_union']}/"
        f"{rep['b2b_probe_observations']} "
        f"({rep['b2b_probe_strict_fire_union']/max(1,rep['b2b_probe_observations']):.6f}); "
        f"under >=: +{rep['b2b_probe_tie_flip']} tie-flips -> "
        f"{(rep['b2b_probe_strict_fire_union']+rep['b2b_probe_tie_flip'])/max(1,rep['b2b_probe_observations']):.6f}")
    with open(os.path.join(OUT, 'operator_sensitivity.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: v for k, v in rep.items()
                      if k != 'consequence_assessment'}, indent=1))


if __name__ == '__main__':
    main()
