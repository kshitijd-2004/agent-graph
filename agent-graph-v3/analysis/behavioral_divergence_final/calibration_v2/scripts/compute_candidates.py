#!/usr/bin/env python3.11
"""Candidate policies: full-frame (P1*) + dev + confirmatory scoring.

Families pools are built on the FULL P1 row view (16,773 — the audited null
object), then every policy is scored:
  * over P1* (canonical 16,767 unique-trace population) -> production
  * over the dev partition -> selection evidence ONLY (pre-registration)
  * over the confirmatory partition -> the single confirmatory evaluation
    (consumed once, after the candidate is frozen)

B2B controls (matched + non-circular, per rule): p3 probe-union FPR
(observation) + execution-level row-OR + O1 outcome probe. Reference =
frozen V4.1 behavioral_anomaly (comparable-gated) + per-dim V4.1 extremal.

Writes ledger/candidates.json (machine-readable; CANDIDATE_COMPARISON.md
is derived from it). Deterministic (seeds in cv2).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

POLICIES = ('C0_original', 'C1_family_q98', 'S1_shrinkage',
            'S2_localmax', 'S3_minfloor', 'S4_shrinkage_stratum')


def score_partition(rows_in, cells_by_tid, v6dim, pools, ztab, outnull,
                    policy, ref):
    cells = [cells_by_tid[t] for t in rows_in]
    sc = cv2.score_candidate(cells, v6dim, pools, ztab, outnull, policy)
    th, _ = cv2.build_policy_theta(cells, v6dim, pools, ztab, policy)
    cm = cv2.confusion(sc, ref['union'])
    ci = cv2.union_rate_ci(sc)
    # per-dimension sensitivity vs the per-dim V4.1 reference
    per_dim_ref = ref['per_dim']
    dim_stat = {}
    for d in cv2.DIMS:
        tp = fp = fn = tn = 0
        undet = 0
        for r in sc:
            rv = (per_dim_ref.get(r['trace_id']) or {}).get(d)
            pv = r['fires'].get(d)
            if rv is None or pv is None:
                undet += 1
                continue
            if pv and rv:
                tp += 1
            elif pv and not rv:
                fp += 1
            elif not pv and rv:
                fn += 1
            else:
                tn += 1
        n = tp + fp + fn + tn
        p = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        dim_stat[d] = {'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn,
                       'n_eval': n, 'n_undetermined': undet,
                       'precision': p, 'recall': rec,
                       'f1': (2 * p * rec / (p + rec))
                       if (p is not None and rec is not None and (p + rec))
                       else None}
    return {'n_rows': len(sc),
            'union_pos': sum(1 for r in sc if r['union']),
            'undetermined_rows': sum(1 for r in sc if r['undetermined']),
            'confusion': cm, 'union_ci': ci,
            'per_dim': dim_stat,
            'rows': sc, 'theta': th}


def main():
    os.makedirs(OUT, exist_ok=True)
    cells, dupes = cv2.load_cells()
    cells_by_tid = {c['trace_id']: c for c in cells}
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    pair_pools = cv2.build_pair_pools(full)
    ztab = cv2.shrinkage_weights(full, v6dim, pair_pools, pools)
    outnull = cv2.outcome_null(full)
    rep = json.load(open(os.path.join(OUT, 'reference_surface.json')))
    ref = {'union': rep['v41_union'], 'per_dim': rep['v41_per_dim']}
    split = json.load(open(os.path.join(OUT, 'partition_split.json')))

    out = {'policies': {}, 'outcomes': {}, 'meta': {}}
    for pol in POLICIES:
        res = {
            'p1star': score_partition(
                [c['trace_id'] for c in cells], cells_by_tid, v6dim, pools,
                ztab, outnull, pol, ref),
            'dev': score_partition(
                [t for t in split['dev_rows'] if t in cells_by_tid],
                cells_by_tid, v6dim, pools, ztab, outnull, pol, ref),
            'confirm': score_partition(
                [t for t in split['confirm_rows'] if t in cells_by_tid],
                cells_by_tid, v6dim, pools, ztab, outnull, pol, ref),
        }
        # B2B control at P1 (probe-union + execution row-OR + O1 outcome)
        th_full, _ = cv2.build_policy_theta(full, v6dim, pools, ztab, pol)
        res['b2b_full'] = cv2.b2b_control(full, th_full, outnull)
        # drop the big row arrays before storing
        for part in ('p1star', 'dev', 'confirm'):
            res[part]['triggered_profiles'] = {}
            from collections import Counter
            prof = Counter(tuple(r['triggered']) for r in res[part]['rows'])
            res[part]['triggered_profiles'] = {
                '|'.join(k) or 'none': v for k, v in prof.items()
                if v}
            # outcome O1 p-distribution
            op = Counter(r.get('outcome_p_emp') for r in res[part]['rows']
                         if r.get('outcome_p_emp') is not None)
            res[part]['outcome_p_hist'] = {str(k): v for k, v in
                                           sorted(op.items(),
                                                  key=lambda x: (x[0] is None,
                                                                 x[0]))}
            del res[part]['rows']
        out['policies'][pol] = res
    # meta
    out['meta'] = {
        'P1_full': len(full), 'P1star': len(cells), 'dupes': len(dupes),
        'shrinkage_z': {
            f'{f}|{d}': {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in zt.items()}
            for (f, d), zt in sorted(ztab.items())},
        'outcome_null': outnull,
        'seeds': {'shrink': cv2.SEED_SHRINK, 'ci': cv2.SEED_CI,
                  'devsplit': cv2.SEED_DEVSPLIT},
        'alpha_out': cv2.ALPHA_OUT, 'q': cv2.Q,
    }
    json.dump(out, open(os.path.join(OUT, 'candidates.json'), 'w'),
              indent=1)
    # console summary
    for pol in POLICIES:
        p1 = out['policies'][pol]['p1star']
        dev = out['policies'][pol]['dev']
        conf = out['policies'][pol]['confirm']
        b2b = out['policies'][pol]['b2b_full']
        cm = p1['confusion']
        print(f"{pol}: P1* union={p1['union_pos']} "
              f"(P1*-ref F1={cm['f1']:.3f} P={cm['precision']:.3f} "
              f"R={cm['recall']:.3f} n={cm['n_eval']}) | "
              f"dev F1={dev['confusion']['f1']:.3f} "
              f"(R={dev['confusion']['recall']:.3f} "
              f"n={dev['confusion']['n_eval']}, "
              f"pos={dev['confusion']['TP']+dev['confusion']['FN']}) | "
              f"confirm F1={conf['confusion']['f1']:.3f} "
              f"(R={conf['confusion']['recall']:.3f} "
              f"n={conf['confusion']['n_eval']}, "
              f"pos={conf['confusion']['TP']+conf['confusion']['FN']}) | "
              f"B2B probe-union={b2b['probe_union_fpr']:.4f} "
              f"rowOR={b2b['row_or_fpr']:.4f} "
              f"o-probe={b2b['outcome_probe_fpr']}")


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)
    main()
