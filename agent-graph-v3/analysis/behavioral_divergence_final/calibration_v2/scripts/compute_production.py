#!/usr/bin/env python3.11
"""M2 dimension-specific checkpoint gate + M3/M4 recomputation (S1 frozen).

Consumes ledger/checkpoint_pairs.jsonl (the frame-wide per-component
re-score of the frozen checkpoint machinery — no composite) and the
frozen grader/reach artifacts, and produces:
  * M2 dimension-specific union: at every usable CROSS-AGENT,
    post-injection-boundary checkpoint, each evaluable component
    (intermediate mask {assertions,actions}; final mask
    {assertions,decisions,outcomes}) is scored against its OWN
    dimension threshold at CHECKPOINT scope (family-pooled q98 exact
    leave-one-cell-out of the per-component benign-pair pool — the same
    cardinality object the frozen composite used, but per-component and
    unweighted).  A row is M2-positive iff ANY cross-agent checkpoint's
    ANY evaluable component exceeds its threshold.  Multiple-opportunity
    accounting: probe-observation FPR (per component benign-pair) and
    row-OR (execution-level) FPR are reported SEPARATELY (the
    checkpoint-scope B2B control = the benign-pair observations vs the
    same per-component LOO threshold).
  * M3 dependency-supported: M2_positive ∩ reach ∈ {verified,plausible}
    ∩ delivery_verified (reach tiers are checkpoint-agnostic
    content/lineage evidence; the execution-level join is the correct
    construction — the audit's M2_M3_RECONCILIATION §1), with the reach
    evidence aligned to the downstream checkpoint where a checkpoint key
    exists.
  * M4 matched degradation: the FROZEN five-way classes over the M3
    denominator (LEP incorrect AND ≥1 compatible benign correct;
    ungradeable is NEVER a failure).  No causal attribution.
Writes ledger/m2m3_production.json.  Run AFTER compute_checkpoint
completes (reads its full output).
"""
import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

DIMS = cv2.DIMS
Q = cv2.Q


def main():
    os.makedirs(OUT, exist_ok=True)
    cells, _ = cv2.load_cells()
    pairs = [json.loads(l) for l in open(os.path.join(OUT,
                                                       'checkpoint_pairs.jsonl'))]
    # the checkpoint re-score ran over the full P1 row view (16,773, incl.
    # the 6 accidental duplicates); the canonical population is P1*
    # (16,767 unique-trace).  Align per trace_id (the duplicate rows are
    # byte-identical per the reconciliation, so dedup is exact).
    by_tid: dict = {}
    for p in pairs:
        by_tid.setdefault(p['trace_id'], p)
    pairs = [by_tid[c['trace_id']] for c in cells
             if c['trace_id'] in by_tid]
    assert len(pairs) == len(cells), \
        f'checkpoint_pairs aligned rows {len(pairs)} != cells {len(cells)}'

    # ── per-component family pools at checkpoint scope (q98 LOO) ──────
    pool_inter: dict = {d: [] for d in DIMS}
    pool_final: dict = {d: [] for d in DIMS}
    for r in pairs:
        bi = r.get('ben_inter') or {}
        bf = r.get('ben_final') or {}
        for d in DIMS:
            pool_inter[d].extend(bi.get(d) or [])
            pool_final[d].extend(bf.get(d) or [])
    for d in DIMS:
        pool_inter[d] = sorted(pool_inter[d])
        pool_final[d] = sorted(pool_final[d])

    def loo_thr(pool_sorted, R):
        R = sorted(R)
        return cv2.loo_quantile(pool_sorted, R, q=Q)

    # ── per-row M2 dimension-specific gate ────────────────────────────
    rows = []
    probe_eval = 0
    probe_fire = 0
    b2b_row_or = 0
    for r in pairs:
        bi = r.get('ben_inter') or {}
        bf = r.get('ben_final') or {}
        thr_inter = {d: loo_thr(pool_inter[d], bi.get(d) or []) for d in DIMS}
        thr_final = {d: loo_thr(pool_final[d], bf.get(d) or []) for d in DIMS}
        # B2B control: benign-pair observations vs the SAME per-component
        # LOO threshold (probe-observation rate, checkpoint scope) + the
        # execution-level row-OR (a B2B execution is flagged if ANY of the
        # cell's benign-pair observations exceeds its LOO threshold — the
        # multiple-opportunity figure, reported separately).
        b2b_row_fired = False
        for d in DIMS:
            for v in (bi.get(d) or []):
                t = thr_inter[d]
                if t is None:
                    continue
                probe_eval += 1
                if v > t:
                    probe_fire += 1
                    b2b_row_fired = True
            for v in (bf.get(d) or []):
                t = thr_final[d]
                if t is None:
                    continue
                probe_eval += 1
                if v > t:
                    probe_fire += 1
                    b2b_row_fired = True
        if b2b_row_fired:
            b2b_row_or += 1
        usable = [u for u in (r.get('usable') or [])
                  if u.get('is_cross_agent')]
        fired = []
        row_app = False
        scope_fired = {'inter': set(), 'final': set()}
        for u in usable:
            scope = 'final' if u.get('is_final') else 'inter'
            thr = thr_final if scope == 'final' else thr_inter
            comps = u.get('comp_mean') or {}
            for d, v in comps.items():
                t = thr.get(d)
                if v is None or t is None:
                    continue
                row_app = True
                if v > t:
                    fired.append((u.get('key'), d))
                    scope_fired[scope].add(d)
        rows.append({
            'trace_id': r['trace_id'], 'fam': r.get('fam'),
            'lep_code': r.get('lep_code'),
            'm2_dim_union': bool(fired),
            'm2_fired': fired,
            'm2_fired_inter': sorted(scope_fired['inter']),
            'm2_fired_final': sorted(scope_fired['final']),
            'n_cross_agent_ckpt': len(usable),
            'm2_undetermined': (not row_app and len(usable) > 0),
            'm2_no_ckpt': (len(usable) == 0),
            'reach': r.get('m3_reach_class'),
            'delivery_verified': r.get('delivery_verified'),
            'origin_roles': r.get('origin_roles'),
        })

    m2_pos = [x for x in rows if x['m2_dim_union']]
    m2_app = [x for x in rows if not x['m2_no_ckpt']]
    # M2 ⊄ M1 cross-tab (requirement 10): against the S1 whole-trace union
    cand = json.load(open(os.path.join(OUT, 'candidates.json')))
    s1 = cand['policies']['S1_shrinkage']['p1star']
    s1u = {r['trace_id']: r['union'] for r in s1['rows']} if 'rows' in s1 \
        else {}
    # rows were dropped from candidates.json; recompute the S1 P1* union
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    pair_pools = cv2.build_pair_pools(full)
    ztab = cv2.shrinkage_weights(full, v6dim, pair_pools, pools)
    outnull = cv2.outcome_null(full)
    cells_u = {c['trace_id']: c for c in cells}
    s1rows = cv2.score_candidate([cells_u[x['trace_id']] for x in
                                  rows if x['trace_id'] in cells_u],
                                 v6dim, pools, ztab, outnull,
                                 'S1_shrinkage')
    s1_union = {x['trace_id']: x['union'] for x in s1rows}
    crosstab = Counter()
    for x in rows:
        u = x['trace_id'] in cells_u
        m1 = s1_union.get(x['trace_id']) if u else None
        crosstab[(bool(m1), x['m2_dim_union'])] += 1

    # ── M3 / M4 (requirement 10) ──────────────────────────────────────
    # M4 five-way classes come from the FROZEN v6 grader artifact
    # (v6 ledger m4_class; the audit's m2_m3_reconciliation join).
    v6_m4 = {r['trace_id']: r.get('m4_class')
             for r in cv2._jsonl(cv2.V6_LEDGER)}
    m3_supported = [x for x in m2_pos
                    if x['reach'] in ('verified', 'plausible')
                    and x['delivery_verified']]
    reach_classes = Counter(x['reach'] for x in m2_pos)
    m4 = Counter((v6_m4.get(x['trace_id']) or 'none')
                 for x in m3_supported)
    matched = sum(1 for x in m3_supported
                  if v6_m4.get(x['trace_id']) == 'degraded')
    m2b = {
        'probe_obs': probe_eval, 'probe_fire': probe_fire,
        'probe_fpr': (probe_fire / probe_eval) if probe_eval else None,
        'b2b_execution_row_or_fired': b2b_row_or,
        'b2b_execution_row_or_fpr': (b2b_row_or / len(rows))
        if rows else None,
        'note': ('probe_fpr = per benign-pair observation vs its LOO '
                 'per-component threshold (checkpoint-scope observation '
                 'rate); b2b_execution_row_or_fpr = the per-execution '
                 'B2B control: a cell is "flagged" if ANY benign-pair '
                 'observation exceeds its LOO threshold (the multiple-'
                 'opportunity figure).  LEP M2 positivity is reported '
                 'separately under m2_dimension_specific.positive; the '
                 'two are never conflated.'),
    }
    rep = {
        'm2_dimension_specific': {
            'positive': len(m2_pos), 'denominator': len(rows),
            'rate': len(m2_pos) / len(rows) if rows else None,
            'by_dim': dict(Counter(d for x in m2_pos for _, d in
                                   x['m2_fired'])),
            'by_scope': {
                'inter_fired': sum(1 for x in m2_pos
                                    if x['m2_fired_inter']),
                'final_fired': sum(1 for x in m2_pos
                                    if x['m2_fired_final'])},
            'cross_agent_ckpt': m2b,
            'm2_no_ckpt': sum(1 for x in rows if x['m2_no_ckpt']),
            'm2_undetermined': sum(1 for x in rows if x['m2_undetermined']),
            'not_subset_of_m1': dict(('(M1,S1:%s,M2:%s)' % (m1, m2), n)
                                      for (m1, m2), n in
                                      sorted(crosstab.items())),
            'note': 'composite REMOVED: per-component unweighted union at '
                    'checkpoint scope; M2 not assumed ⊆ M1 (cross-tab '
                    'computed both directions); checkpoint-scope null '
                    'separate from whole-trace null',
        },
        'm3_dependency_supported': {
            'supported': len(m3_supported),
            'verified': sum(1 for x in m3_supported
                            if x['reach'] == 'verified'),
            'plausible': sum(1 for x in m3_supported
                             if x['reach'] == 'plausible'),
            'share_of_m2': (len(m3_supported) / len(m2_pos)) if m2_pos
            else None,
            'm2_reach_classes': dict(reach_classes),
            'subset_of_m2': True,
            'note': 'reach tiers are checkpoint-agnostic content/lineage '
                    'evidence; execution-level join (M2∩reach∩delivery) is '
                    'the correct construction; NO causal attribution',
        },
        'm4_matched_degradation': {
            'classes': dict(m4), 'matched_degraded': matched,
            'denominator': len(m3_supported),
            'rate': (matched / len(m3_supported)) if m3_supported else None,
            'note': 'frozen five-way classes over the M3 denominator; '
                    'degraded = LEP incorrect AND ≥1 compatible benign '
                    'correct; ungradeable is NEVER counted a failure; '
                    'persistent_failure / persistent_correct / improved / '
                    'mixed_or_unavailable reported separately; context '
                    'only, not causal',
        },
    }
    json.dump(rep, open(os.path.join(OUT, 'm2m3_production.json'), 'w'),
              indent=1)
    print('M2 dim-union:', len(m2_pos), '/', len(rows))
    print('M3 supported:', len(m3_supported),
          '(v %s / p %s)' % (rep['m3_dependency_supported']['verified'],
                              rep['m3_dependency_supported']['plausible']))
    print('M4 matched:', matched, 'of', len(m3_supported), '=',
          rep['m4_matched_degradation']['rate'])
    print('M2 B2B probe obs:', m2b['probe_fpr'],
          'execution row-OR:', m2b['b2b_execution_row_or_fpr'])


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
