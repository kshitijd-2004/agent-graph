#!/usr/bin/env python3.11
"""V4.3 candidate-gate evaluation (deterministic, no LLM).

Runs over a given ledger (default: the frozen V4.2 ledger for the gate-only
CEILING; later the V4.3 ledger with extraction fixes) and tests a small
defensible candidate set of M1 gate forms — all per-dimension + logical OR,
NO weighted composite:

  G0  V4.2 OP0  recentered all dims: (T-B) > th.
  G1  V4.2 OP1  recentered continuous + family exact disagree-mass test on
                discrete dims (decisions, outcomes), alpha=0.05.
  G2  G1 + per-pair MAX statistic on continuous dims:
                (max_p T_p - B) >= th   (anti-dilution; tie-inclusive).
  G3  G2 + effect-size floor on continuous dims: require delta >= eps,
                eps = per-(fam,dim) q95 of the LEP-free p3 delta null
                (T_p3 - B, LOO-excluding the cell; eps=0 if pool<30).
  G2s G2 strict (> instead of >=) — tie ablation.

For each candidate, on the round-2 dev surface (221 rows): confusion, P/R/F1,
per-dim/per-LEP/per-family; plus the THREE FPR definitions separately:
  FPR-1 semantic-reference-negative = FP/137  (NOT a pure-benign FPR)
  FPR-2 benign p3 pool-observation   (fam q98 LOO-excl-cell on the p3 pool)
  FPR-3 benign EXECUTION-level union  (p3 exchangeable row-OR + independent-
        multiplicity model split)
And the gate-only CEILING: of the 84 ref-positives, how many have ANY
dim with T_d - B_d > 0 under the current ledger (upper bound of what ANY
recentered-style gate can recover without extraction changes); reported
per archetype.

Usage:  python3 m1_candidates_v43.py [ledger_path]
Writes out_v43/m1_candidates_v43.json (or stdout table when ledger is V4.2).
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, V2)
import cv2  # noqa: E402

import m1_gate_v43 as G  # helpers: mean, cell_stats, refs, binom, p_hat, REPO
from m1_gate_v43 import mean, cell_stats, family_disagree_mass, \
    load_round2_refs, _binom_tail, DIMS, CONT_DIMS, DISC_DIMS, ALPHA
REPO = G.REPO
sys.path.insert(0, os.path.join(REPO, 'benchmark'))

EPS_POOL_MIN = 30
EPS_Q = 0.95


def load_ledger(path):
    return {json.loads(l)['trace_id']: json.loads(l) for l in open(path)}


def build_eps(led, P1, cells):
    """Per-(fam,dim) q95 of the LEP-free p3 delta null (T_p3 - B_d),
    LOO-excluding the cell; eps=0 when the pool is too small."""
    Bcell = {}
    for l in led:
        r = led[l]
        if not r.get('comparable'):
            continue
        Bcell[r['trace_id']] = {d: cell_stats(r, d)[1] for d in DIMS}
    by_cell = {c['trace_id']: c for c in cells}
    pools = {f: {d: [] for d in CONT_DIMS}
             for f in ('code_review', 'financial_analysis',
                       'research_synthesis')}
    cell_pool = {f: {d: {} for d in CONT_DIMS}
                 for f in pools}
    for t in P1:
        c = by_cell.get(t)
        row = led.get(t)
        if c is None or row is None or not row.get('comparable'):
            continue
        fam = row.get('task_family') or c.get('fam')
        if fam not in pools:
            continue
        for d in CONT_DIMS:
            B = Bcell[t][d]
            for tp in (c.get('T_p3') or []):
                v = (tp or {}).get(d)
                if v is None or B is None:
                    continue
                cell_pool[fam][d].setdefault(t, []).append(v - B)
    eps = {}
    for f in pools:
        for d in CONT_DIMS:
            per_cell = cell_pool[f][d]
            vals = [x for lst in per_cell.values() for x in lst]
            eps[(f, d)] = vals
    return eps


def eps_val(eps_cells, fam, dim, tid):
    vals = eps_cells.get((fam, dim), [])
    if len(vals) < EPS_POOL_MIN:
        return 0.0
    # LOO: exclude this cell's probes
    srt = sorted(vals)
    import bisect
    k = EPS_Q * (len(srt) - 1)
    return srt[min(len(srt) - 1, int(round(k)))]


def lepv_max(row, d):
    vals = [((p.get('components') or {}).get(d)) for p in row.get('pair_lep', [])]
    vals = [x for x in vals if x is not None]
    return (max(vals)) if vals else None


def fire(op, row, d, ctx):
    T, B, th = cell_stats(row, d)
    fam = row.get('task_family') or 'unknown'
    tid = row.get('trace_id', '')
    if d in DISC_DIMS:
        # family exact disagree-mass test (V4.2 OP1 construct)
        p = ctx['p_hat'].get((fam, d))
        if p is None:
            return None
        lepv = [((p2.get('components') or {}).get(d)) for p2 in
                row.get('pair_lep', [])]
        lepv = [x for x in lepv if x is not None]
        if not lepv:
            return None
        k = sum(1 for x in lepv if x > 0)
        return (k > 0 and _binom_tail(len(lepv), k, p) <= ctx['alpha'])
    # continuous dims
    if op == 'G0':
        if T is None or B is None or th is None:
            return None
        return (T - B) > th
    if op == 'G1':
        if T is None or B is None or th is None:
            return None
        return (T - B) > th
    if op in ('G2', 'G2s', 'G3'):
        m = lepv_max(row, d)
        if m is None or B is None or th is None:
            return None
        delta = m - B
        fired = (delta > th) if op == 'G2s' else (delta >= th)
        if op == 'G3':
            e = ctx['eps_val'](fam, d, tid)
            fired = fired and (delta >= e)
        return fired
    raise ValueError(op)


def eval_ops(led, refs, P1, P1star, cells, ctx):
    out = {}
    for op in ('G0', 'G1', 'G2s', 'G2', 'G3'):
        TP = FP = FN = TN = 0
        per_dim = {d: Counter() for d in DIMS}
        per_lep = Counter(); per_fam = {f: Counter() for f in
                                        ('code_review',
                                         'financial_analysis',
                                         'research_synthesis')}
        missed, fired_neg = [], []
        for t in P1star:
            if t not in refs or refs[t]['label'] is None:
                continue
            row = led.get(t)
            if row is None or not row.get('comparable'):
                continue
            fam = row.get('task_family') or 'unknown'
            lep = row.get('lep_code') or 'unknown'
            fires = {d: fire(op, row, d, ctx) for d in DIMS}
            pred = any(v is True for v in fires.values())
            pos = refs[t]['label'] == 'diverges'
            refpos = set(d for d in DIMS
                         if (refs[t]['per_dim'] or {}).get(d) is True)
            if pos and pred:
                TP += 1; per_fam[fam]['TP'] += 1; per_lep[('tp', lep)] += 1
            elif pos:
                FN += 1; per_fam[fam]['FN'] += 1; per_lep[('fn', lep)] += 1
                missed.append(t)
            elif pred:
                FP += 1; per_fam[fam]['FP'] += 1; per_lep[('fp', lep)] += 1
                fired_neg.append(t)
            else:
                TN += 1
            for d in DIMS:
                if d in refpos and fires.get(d) is True:
                    per_dim[d]['tp'] += 1
                elif d in refpos and fires.get(d) is not True:
                    per_dim[d]['fn'] += 1
                elif d not in refpos and fires.get(d) is True:
                    per_dim[d]['fp'] += 1
        P = TP / (TP + FP) if TP + FP else None
        R = TP / (TP + FN) if TP + FN else None
        F = (2 * P * R / (P + R)) if (P and R) else None
        out[op] = {'conf': {'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN,
                            'P': P, 'R': R, 'F1': F},
                   'per_dim': {d: dict(per_dim[d]) for d in DIMS},
                   'per_lep': {k[1]: {'tp': per_lep.get(('tp', k[1]), 0),
                                       'fp': per_lep.get(('fp', k[1]), 0),
                                       'fn': per_lep.get(('fn', k[1]), 0)}
                               for k in per_lep},
                   'per_fam': {k: dict(v) for k, v in per_fam.items()},
                   'missed': missed, 'fired_neg': fired_neg}
    return out


def gate_ceiling(led, refs, P1star):
    """The gate-only ceiling: ref-positives with ANY dim having a nonzero
    LEP deviation that COULD exceed some defensible threshold, per state
    (NULL / ZERO / below-th positive-delta)."""
    states = Counter()
    for t in P1star:
        if t not in refs or refs[t]['label'] != 'diverges':
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            states['not_comparable'] += 1
            continue
        refpos = set(d for d in DIMS
                    if (refs[t]['per_dim'] or {}).get(d) is True) \
            or set(DIMS)
        has_zero = has_pos = has_null = False
        for d in refpos:
            T, B, th = cell_stats(row, d)
            if T is None or B is None:
                has_null = True
            elif T == 0 and B == 0:
                has_zero = True
            else:
                has_pos = True
        if has_pos:
            states['some_ref_dim_has_positive_delta'] += 1
        elif has_zero and not has_null:
            states['all_ref_dims_zero'] += 1
        elif has_null and not has_zero:
            states['all_ref_dims_null'] += 1
        else:
            states['mixed_null_zero_or_unattr'] += 1
    return dict(states)


def fpr_triple(led, P1, P1star, cells):
    """FPR-2 (p3 pool obs rate) + FPR-3 (execution row-OR + multiplicity
    model), on the RECENTRED GATE form with fam-pool q98 LOO thresholds."""
    fam_pool = {f: {d: [] for d in DIMS}
                for f in ('code_review', 'financial_analysis',
                          'research_synthesis')}
    by_cell = {c['trace_id']: c for c in cells}
    for c in cells:
        fam = c.get('fam')
        if fam not in fam_pool:
            continue
        for tp in (c.get('T_p3') or []):
            for d in DIMS:
                v = (tp or {}).get(d)
                if v is not None:
                    fam_pool[fam][d].append(v)
    for f in fam_pool:
        for d in fam_pool[f]:
            fam_pool[f][d].sort()

    def loo_excl(pool, R, q=0.98):
        return cv2.loo_quantile(pool, sorted(R), q=q)

    pe = pf = ror = rows = 0
    per_cell_m = []
    for t in P1star:
        c = by_cell.get(t)
        fam = (c.get('fam') if c else None)
        if fam not in fam_pool:
            continue
        tps = c.get('T_p3') or []
        if len(tps) < 3:
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        Bcell = {d: cell_stats(row, d)[1] for d in DIMS}
        rows += 1
        anyf = False
        m = 0
        for d in DIMS:
            R = [tp.get(d) for tp in tps if tp.get(d) is not None]
            B = Bcell[d]
            if len(R) < 2 or B is None:
                continue
            th = loo_excl(fam_pool[fam][d], R)
            for v in R:
                pe += 1
                m += 1
                if th is not None:
                    delta = v - B
                    if delta > th:
                        pf += 1
                        anyf = True
        if anyf:
            ror += 1
        per_cell_m.append(m)
    import math
    p_obs = pf / pe if pe else 0.0
    # independent multiplicity model: expected row-OR = 1 - (1-p)^m per cell
    model_sum = 0.0
    for m in per_cell_m:
        model_sum += 1 - (1 - p_obs) ** m if m else 0.0
    model_rate = model_sum / rows if rows else None
    return {'fpr2_p3_pool_obs_rate': pf / pe if pe else None,
            'fpr2_probe_obs': pf, 'fpr2_probe_eval': pe,
            'fpr3_exec_row_or': ror / rows if rows else None,
            'fpr3_exec_row_or_n': ror, 'fpr3_n_cells': rows,
            'fpr3_multiplicity_model_row_or': model_rate,
            'fpr3_excess_over_model': ((ror / rows) - model_rate
                                       if (rows and model_rate is not None)
                                       else None),
            'note': 'recentered gate form; FPR-2 = p3 pool-observation '
                    '(fam q98 LOO-excl-cell); FPR-3 = exchangeable p3 '
                    'execution row-OR + independent-multiplicity split'}


def main():
    argv = sys.argv[1:]
    ledpath = argv[0] if argv else os.path.join(
        REPO, 'analysis', 'full_production_baseline_deviation_v42',
        'bnd_v42_per_trace.jsonl')
    led = load_ledger(ledpath)
    refs = load_round2_refs()
    cells, _ = cv2.load_cells()
    P1 = [c['trace_id'] for c in cells]
    seen = set(); P1star = []
    for t in P1:
        if t not in seen:
            seen.add(t)
            P1star.append(t)
    p_hat = family_disagree_mass(led, P1star)
    eps_cells = build_eps(led, P1star, cells)
    ctx = {'p_hat': p_hat, 'alpha': ALPHA,
           'eps_val': lambda fam, dim, tid: eps_val(eps_cells, fam, dim, tid)}
    res = eval_ops(led, refs, P1, P1star, cells, ctx)
    ceiling = gate_ceiling(led, refs, P1star)
    triple = fpr_triple(led, P1, P1star, cells)
    rep = {'ledger': ledpath,
           'candidates': res, 'gate_only_ceiling': ceiling,
           'fpr_triple_recentered': triple,
           'eps_cells_sizes': {f'{f}|{d}': len(v)
                               for (f, d), v in eps_cells.items()},
           'note': 'round-2 dev surface; NO confirmatory claim; FPR-1 = '
                   'semantic-ref-negative (FP/n_neg), distinct from '
                   'FPR-2/FPR-3 benign objects'}
    tag = 'v43' if 'v43' in os.path.basename(ledpath) else 'v42ceiling'
    out = os.path.join(V2, 'out_v43', f'm1_candidates_{tag}.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, 'w'), indent=1)
    print(f'ledger: {os.path.basename(ledpath)} -> {out}')
    for op in ('G0', 'G1', 'G2s', 'G2', 'G3'):
        c = res[op]['conf']
        P = c['P'] or 0
        R = c['R'] or 0
        F = c['F1'] or 0
        print(f'  {op}: P={P:.3f} R={R:.3f} F1={F:.3f} '
              f"(TP {c['TP']} FP {c['FP']} FN {c['FN']})")
    print('  gate-only ceiling:', ceiling)
    print('  FPR triple (recentered form):',
          {k: (round(v, 5) if isinstance(v, float) else v)
           for k, v in triple.items()
           if isinstance(v, (float, int)) and 'note' not in k and 'n' not in k and 'excess' not in k and 'model' not in k},
          'excess_over_model:', round(triple['fpr3_excess_over_model'] or 0, 5))
    return rep


if __name__ == '__main__':
    main()
