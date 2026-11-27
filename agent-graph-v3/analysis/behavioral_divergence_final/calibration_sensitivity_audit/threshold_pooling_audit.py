#!/usr/bin/env python3.11
"""Family-level threshold pooling audit (requirement 6).

Q: does pooling the benign p3-null across a whole task family suppress
genuine changes in otherwise STABLE fixtures?

Reads the cached p3 cells + the v6 ledger (original per-cell theta/B_d);
writes ledger/pooling_audit.json + plots/pooling_*.svg.  All quantities
are per family x dimension x topology x mode x LEP x fixture.

Suppression detector: a cell where the LEP differs substantially from
ALL its own benign references (within-cell evidence: T_d exceeds the
cell's own benign-pair max, i.e. T_d > theta_max3) yet does not fire
under the pooled threshold (T_d <= theta_pooled).  This is the
heterogeneous-variability failure mode: stable cells (near-zero in-cell
benign spread) under a family threshold set by the most variable
families' fixtures.
"""
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A
import svg_plot
sys.path.insert(0, os.path.join(A.CALP))
import cp_lib as L

OUT = os.path.join(A.AUDIT, 'ledger')
PLOTS = os.path.join(A.AUDIT, 'plots')
FAMS = ('code_review', 'financial_analysis', 'research_synthesis')
QVALS = (0.80, 0.90, 0.95, 0.98, 0.99)


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(PLOTS, exist_ok=True)
    cells = json.load(open(os.path.join(A.CALP, 'out',
                                        'cache_cells_full.json')))['cells']
    for c in cells:  # JSON round-trip turned tuples into lists
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
    v6 = A.v6_by()

    tables = {}
    for q in QVALS:
        for pool in ('family', 'stratum'):
            tables[(q, pool)] = L.build_theta_table(
                cells, null_field='T_p3', q=q, pool=pool, floor=0.0)
    thr98_f = tables[(0.98, 'family')]
    thr98_s = tables[(0.98, 'stratum')]

    rep = {'n_cells': len(cells),
           'distributions': {}, 'stable_cell_census': {},
           'suppression_census': {}, 'by_stratum': {},
           'note': ('within-cell theta_max3 and B_d come from the ORIGINAL '
                    'rule\'s ledger (v6 per_dimension.threshold / '
                    'benign_reference_distance); pooled theta from the p3 '
                    'family-pool LOO q98 table (same policy as the '
                    'calibrated rule).  Suppressed = within-cell fired '
                    '(T > B + theta_max3) AND not pooled-fired (T <= '
                    'theta_pooled).')}

    # distributions: T_d evaluable quantiles + pooled theta stats
    t_vals = defaultdict(list)
    th_vals = defaultdict(list)
    for ci, c in enumerate(cells):
        topo = c.get('stratum', (None, None, None))[1]
        for d in A.DIMS:
            if c['T_lep'][d] is not None:
                t_vals[(c['fam'], topo, d)].append(c['T_lep'][d])
            if thr98_f[ci][d] is not None:
                th_vals[(c['fam'], d)].append(thr98_f[ci][d])
    for key, vs in t_vals.items():
        vv = sorted(vs)
        rep['distributions'][f'{key[0]}/{key[1]}/{key[2]}:T_lep'] = {
            'n': len(vv), 'p50': vv[len(vv) // 2],
            'p90': vv[int(0.9 * len(vv))],
            'p98': vv[int(0.98 * len(vv))], 'max': vv[-1]}
    for (f, d), vs in th_vals.items():
        vv = sorted(vs)
        rep['distributions'][f'{f}/{d}:theta_q98_family'] = {
            'n': len(vv), 'p50': vv[len(vv) // 2], 'max': vv[-1],
            'share_zero': sum(1 for v in vv if v == 0.0) / len(vv)}

    # stable-cell census: in-cell theta_max3 == 0 (benigns identical on
    # that dim) — a stable fixture.  Does the pooled theta still let the
    # LEP fire?  And how much headroom does the pool leave?
    sc = Counter()
    for ci, c in enumerate(cells):
        od = v6.get(c['trace_id'], {}).get('per_dimension') or {}
        for d in A.DIMS:
            T = c['T_lep'][d]
            th_cell = (od.get(d) or {}).get('threshold')
            th_p = thr98_f[ci][d]
            if T is None or th_cell is None or th_p is None:
                continue
            stable = th_cell == 0.0
            fired = T > th_p
            sc[f'stable={stable}_fires_q98_family={fired}'] += 1
            # stratum-pool comparison at q98
            th_ps = thr98_s[ci][d]
            if th_ps is not None and th_p != th_ps:
                sc['stratum_theta_differs'] += 1
    rep['stable_cell_census'] = dict(sc)

    # suppression census (the key question), q98 family + stratum
    cens = Counter()
    examples = []
    by_stratum = defaultdict(lambda: Counter())
    for ci, c in enumerate(cells):
        od = v6.get(c['trace_id'], {}).get('per_dimension') or {}
        for d in A.DIMS:
            T = c['T_lep'][d]
            B = (od.get(d) or {}).get('benign_reference_distance')
            th_cell = (od.get(d) or {}).get('threshold')
            th_p = thr98_f[ci][d]
            th_ps = thr98_s[ci][d]
            if T is None or th_p is None:
                continue
            within = (th_cell is not None and B is not None
                      and T > B + th_cell)     # original within-cell fired
            fired_f = T > th_p
            fired_s = (th_ps is not None and T > th_ps)
            key = (c['fam'],) + (c.get('stratum', (None, None, None))[1:])
            if within and not fired_f:
                cens['suppressed_by_family_q98'] += 1
                cens[f'suppressed_dim_{d}'] += 1
                by_stratum[str(key)]['suppressed'] += 1
                if len(examples) < 200:
                    examples.append({
                        'trace_id': c['trace_id'], 'dim': d, 'T': T,
                        'B': B, 'theta_cell_max3': th_cell,
                        'theta_q98_family': th_p,
                        'theta_q98_stratum': th_ps,
                        'fires_under_stratum': fired_s,
                        'margin_T_minus_theta_pooled': T - th_p})
            if within and not fired_s:
                cens['suppressed_by_stratum_q98'] += 1
    rep['suppression_census'] = dict(cens)
    rep['suppression_examples'] = examples
    rep['suppression_by_stratum'] = {k: dict(v) for k, v
                                     in sorted(by_stratum.items())}
    with open(os.path.join(OUT, 'pooling_audit.json'), 'w') as fh:
        json.dump(rep, fh, indent=1)

    # plots: theta_q98_family distribution share-zero per family/dim
    labels = [f'{f}/{d}' for f in FAMS for d in A.DIMS]
    xs = [(i, lb) for i, lb in enumerate(labels)]
    th = lambda f, d: rep['distributions'][f'{f}/{d}:theta_q98_family']
    p1 = svg_plot.line_chart(
        os.path.join(PLOTS, 'pooling_theta_q98_family.svg'),
        'Pooled q98 family threshold by family/dim (share zero + p50 + max)',
        xs,
        [('share_zero', [th(f, d)['share_zero'] for f in FAMS
                          for d in A.DIMS]),
         ('p50', [th(f, d)['p50'] for f in FAMS for d in A.DIMS]),
         ('max', [th(f, d)['max'] for f in FAMS for d in A.DIMS])],
        ylabel='threshold')
    # T_lep quantiles by family/topology/dim (the distribution keys carry
    # the topology)
    labels2 = [f'{f}/{t}/{d}' for f in FAMS
               for t in ('branch_and_verify', 'coordinator_workers',
                          'review_loop') for d in A.DIMS]
    dist = rep['distributions']
    p2 = svg_plot.line_chart(
        os.path.join(PLOTS, 'pooling_T_lep_quantiles.svg'),
        'LEP T_d quantiles by family/topology/dim',
        [(i, lb.split('/', 1)[1]) for i, lb in enumerate(labels2)],
        [(q, [dist.get(lbl + ':T_lep', {}).get(q) for lbl in labels2])
         for q in ('p50', 'p90', 'p98', 'max')],
        ylabel='T_d')
    print('written ledger/pooling_audit.json', p1, p2)
    print('census:', rep['suppression_census'])
    print('stable:', rep['stable_cell_census'])


if __name__ == '__main__':
    main()
