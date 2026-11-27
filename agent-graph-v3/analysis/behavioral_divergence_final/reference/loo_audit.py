#!/usr/bin/env python3.11
"""
LOO-quantile audit for the G5 calibration repair (out/calib_experiment_*).

The control's per-cell theta is the q-quantile of the compatible-pool
T-distribution with the cell's own <=3 probe T-values trimmed (exact
LOO; a probe never calibrates its own threshold).  The shipped O(1)
implementation `_loo_rank` in calib_experiment.py approximates the
multiset trim by an index shift.  This audit (a) fuzz-tests that
approximation against the PHYSICAL multiset-trim gold standard on
tie-heavy pools, and (b) re-derives the actual heldout-v6
p3_q98_f0_family theta tables and B2B/LEP fires under BOTH
conventions, so the G5 FPR is reported under the physically-correct
theta even if the shipped index shift has a tie bias.

Writes out/loo_audit.json.  Read-only otherwise.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common


def physical_trim_quantile(P_sorted: List[float],
                           R: List[float],
                           i_target: int) -> Optional[float]:
    """Gold standard: physically remove R (multiset) from P_sorted,
    return T[i_target]; None when the trimmed pool is too small."""
    P = list(P_sorted)
    Rc = Counter(R)
    T: List[float] = []
    for x in P:
        if Rc.get(x, 0) > 0:
            Rc[x] -= 1
        else:
            T.append(x)
    if i_target >= len(T):
        return None
    return T[i_target]


def main() -> None:
    OUT = common.OUT
    # load the shipped _loo_rank
    sp = importlib.util.spec_from_file_location(
        'ce_looaudit',
        os.path.join(_HERE, 'calib_experiment.py'))
    ce = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(ce)
    loo = ce._loo_rank

    rep: Dict[str, Any] = {'artifact': 'out/loo_audit.json',
                           'purpose': 'tie-bias audit of the shipped '
                                      'LOO-quantile index shift vs the '
                                      'physical multiset-trim gold '
                                      'standard'}

    # ── (a) fuzz on tie-heavy synthetic pools ────────────────────────
    random.seed(20261207)
    n_test = n_mismatch = 0
    low = high = same = 0
    for _ in range(60000):
        N = random.randint(4, 18)
        P = sorted(round(random.choice([0.0, 0.0, 0.0, 0.0, 0.1, 0.2,
                                        0.5, random.random()]), 6)
                   for _ in range(N))
        r = random.randint(1, 3)
        R = sorted(random.choice(P) for _ in range(r))
        q = random.choice([0.80, 0.90, 0.95, 0.98, 0.99])
        i_target = max(0, int(math.ceil(q * (N - len(R)))) - 1)
        if N - len(R) <= i_target:
            continue
        got_i = loo(P, i_target, R)
        got = P[got_i] if got_i is not None else None
        want = physical_trim_quantile(P, R, i_target)
        n_test += 1
        if got != want:
            n_mismatch += 1
            if got is None or want is None:
                low += 1 if got is None else high
            elif got < want:
                low += 1
            elif got > want:
                high += 1
            else:
                same += 1
    rep['fuzz'] = {'n_test': n_test, 'n_mismatch': n_mismatch,
                   'mismatch_rate': n_mismatch / n_test,
                   'direction': {'theta_lower_than_gold': low,
                                 'theta_higher_than_gold': high,
                                 'null_mismatch': n_mismatch - low - high -
                                 same},
                   'note': 'when the shipped theta is LOWER than the '
                           'physical-LOO theta the control fires MORE '
                           '(B2B FPR over-stated = G5-conservative); '
                           'when HIGHER, FPR under-stated '
                           '(G5-anti-conservative)'}

    # ── (b) real heldout-v6 p3_q98_f0_family pool: both conventions ──
    def rerun_cells(impl='v6'):
        specs = common.load_specs()
        part = json.load(open(os.path.join(OUT, 'partitions_final.json')))
        held = {k for k, v in part['fixtures'].items()
                if (v.get('partition') if isinstance(v, dict) else v)
                == 'heldout'}
        rows = [json.loads(l) for l in
                open(os.path.join(common.CONS, 'downstream_results',
                                  'per_trace.jsonl')) if l.strip()]
        rows = [r for r in rows
                if r['trace_id'].split('.')[0] in held
                and len(r.get('reference_set') or []) >= 3]
        idx = {json.loads(l)['trace_id']: json.loads(l)['path']
               for l in open(os.path.join(common.VALIDATION_CACHE,
                                          'trace_index.jsonl'))
               if l.strip()}

        import importlib.util as _ilu
        import features_v6 as F6

        def _load(name, path):
            sp2 = _ilu.spec_from_file_location(name, path)
            m = _ilu.module_from_spec(sp2)
            sys.modules[name] = m
            sp2.loader.exec_module(m)
            return m

        RP = _load('rp_v6_audit', os.path.join(common.V6_DIR,
                                               'run_pipeline.py'))

        def dist(fa, fb, spec, fam):
            ga, da = RP.outcome_of(fa, spec, fam)
            gb, db = RP.outcome_of(fb, spec, fam)
            return RP.dim_distances(fa, fb, fam, ga, gb, da, db)

        DIMS = common.DIMS

        def _mean(xs):
            xs = [x for x in xs if x is not None]
            return sum(xs) / len(xs) if xs else None

        cells = []
        for r in rows:
            fam = r['task_family']
            spec = specs.get(r['trace_id'].split('.')[0], {})
            refs = r.get('reference_set') or []
            L = idx.get(r['trace_id'])
            if not L or not os.path.exists(L):
                continue
            L = json.load(open(L))
            B = [json.load(open(idx[x]))
                 for x in refs[:3] if x in idx and os.path.exists(idx[x])]
            if len(B) < 3:
                continue
            fl = F6.extract_features_v6(L, spec, fam)
            fb = [F6.extract_features_v6(b, spec, fam) for b in B[:3]]
            ld = [dist(fl, fb[k], spec, fam) for k in range(3)]
            pairs = {(i, j): dist(fb[i], fb[j], spec, fam)
                     for i in range(3) for j in range(i + 1, 3)}
            Tlep = {d: _mean([x.get(d) for x in ld]) for d in DIMS}
            T3 = []
            for bi in range(3):
                o2 = [pairs[(min(bi, k), max(bi, k))]
                      for k in range(3) if k != bi]
                T3.append({d: _mean([o.get(d) for o in
                                     ([ld[bi]] + o2)])
                           for d in DIMS})
            cells.append({'fam': fam, 'Tlep': Tlep, 'T3': T3})
        return cells

    cells = rerun_cells('v6')
    rep['n_cells_heldout_v6'] = len(cells)

    DIMS = common.DIMS
    Q, FLOOR = 0.98, 0.0

    # family pools (physical trim needs per-cell R anyway; pool = all
    # T3 values, sorted, per family per dim)
    pool: Dict[str, Dict[str, List[float]]] = {}
    for c in cells:
        for tp in c['T3']:
            ent = pool.setdefault(c['fam'], {d: [] for d in DIMS})
            for d in DIMS:
                if tp[d] is not None:
                    ent[d].append(tp[d])
    for fam in pool:
        for d in DIMS:
            pool[fam][d] = sorted(pool[fam][d])

    def theta_for(c, d, mode):
        """Per-cell theta under `mode` in {shipped, physical}."""
        P = pool[c['fam']][d]
        R = sorted(tp[d] for tp in c['T3'] if tp[d] is not None)
        if mode == 'shipped':
            i_target = int(math.ceil(Q * (len(P) - len(R)))) - 1
            j = loo(P, max(0, i_target), R)
            tv = P[j] if j is not None else None
        else:
            M = len(P) - len(R)
            i_target = max(0, int(math.ceil(Q * M)) - 1)
            tv = physical_trim_quantile(P, R, i_target)
        return (max(tv, FLOOR) if tv is not None else None)

    def fires(c, mode, side):
        m = c['Tlep'] if side == 'lep' else None
        out = []
        if side == 'lep':
            for d in DIMS:
                t = theta_for(c, d, mode)
                if m.get(d) is not None and t is not None \
                        and m[d] > t:
                    out.append(d)
            return any(out)
        # b2b probes
        anyfire = False
        for tp in c['T3']:
            for d in DIMS:
                t = theta_for(c, d, mode)
                if tp.get(d) is not None and t is not None \
                        and tp[d] > t:
                    anyfire = True
                    break
            if anyfire:
                break
        return anyfire

    for mode in ('shipped', 'physical'):
        lep_u = sum(1 for c in cells if fires(c, mode, 'lep'))
        b2b_cells = [c for c in cells]
        b2b_u = sum(1 for c in b2b_cells if fires(c, mode, 'b2b'))
        rep[mode] = {
            'n_cells': len(cells),
            'lep_fired': lep_u, 'lep_rate': lep_u / len(cells),
            'b2b_fired_cells': b2b_u,
            'note': (f'p3-null q={Q} family-pool floor={FLOOR}; theta '
                     f'convention = {mode} multiset-trim')
        }
    # per-cell theta agreement across conventions
    n_cells_checked = 0
    n_cells_anydim_diff = 0
    lower = higher = 0
    for c in cells:
        dmin = 0
        for d in DIMS:
            ts = theta_for(c, d, 'shipped')
            tp = theta_for(c, d, 'physical')
            if (ts is None) != (tp is None):
                dmin = 1
            elif ts is not None and tp is not None and ts != tp:
                dmin = 1
                if ts < tp:
                    lower += 1
                elif ts > tp:
                    higher += 1
        n_cells_checked += 1
        n_cells_anydim_diff += int(dmin)
    rep['theta_agreement'] = {
        'n_cells': n_cells_checked,
        'cells_with_any_dim_theta_diff': n_cells_anydim_diff,
        'dim_cell_diffs_shipped_lower': lower,
        'dim_cell_diffs_shipped_higher': higher,
        'note': 'direction of the shipped theta relative to the '
                'physical-LOO theta (lower = over-fire = G5-'
                'conservative; higher = under-fire = G5-anti-'
                'conservative)'}

    common.write_json(rep, 'loo_audit.json')
    print(json.dumps({k: v for k, v in rep.items()
                      if k not in ('n_cells_heldout_v6',)}, indent=1))


if __name__ == '__main__':
    main()
