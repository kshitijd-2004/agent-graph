#!/usr/bin/env python3.11
"""
Calibration-defect REPAIR experiment (brief §2) — matched, non-circular,
empirical-quantile threshold estimation for the four-dimension union.

DIAGNOSIS (out/diag_b2b.json + out/b2b_control.json)
-----------------------------------------------------
The frozen LEP test calibrates θ_d = max over the C(3,2)=3 benign pairs
of the cell's own 3 benigns, then fires dim d iff
    δ_d = mean(target-vs-3) − mean(3 b2b pairs)  >  θ_d.
A faithful leave-one-probe-out benign control, with 3 benigns, can only
estimate θ from C(2,2)=1 pair (probe removed) → systematically LOWER
θ (max-of-1 ≤ max-of-3) → benigns over-fire (32.1% "FPR"), at a
DIFFERENT cardinality than the LEP test (1-pair vs 3-pair; probe-vs-2 vs
target-vs-3).  So "LEP 30.5% vs B2B 32.1%" is not apples-to-apples:
the two sides used DIFFERENT θ estimators.  Drivers:
  (a) zero-θ cells: 47–58% of cells have θ_d=0 for ≥1 dim → ANY
      nonzero deviation fires that dim;
  (b) heavy-tailed actions-TV (68% of b2b pairs nonzero; p90=0.63):
      max-of-3 (LEP) / max-of-1 (control) are noisy estimators.

REPAIR — score LEPs and B2B probes with the SAME statistic and the SAME
threshold, so the comparison is non-circular and matched-cardinality:

  T_d(target, cell) = mean distance of the target to its cell peers.
    Under H0 (no perturbation delivered) all 4 executions of a cell are
    EXCHANGEABLE, so the benign-probe statistic(s) are:
      p2-null : probe k vs the OTHER 2 benigns      (2-peer mean)
      p3-null : probe k vs the OTHER 3 executions,
                which under H0 includes the LEP row  (3-peer mean;
                d(x,y)=d(y,x), NO ground truth involved — a
                benign↔benign distance under the null)
  θ_d = q-quantile of {T_d over benign probes in a compatible pool}
      (pool ∈ {stratum=(fam,topo,mode), family}), computed EXACT
      leave-one-cell-out (multiset trim; a probe's own T-values never
      enter its own θ).  Floor `f` = minimum tolerance on the T scale
      (reported, never hidden).
  FIRE dim d iff T_d > θ_d.  A dim with no pooled sample or no
      evaluable T stays UNDETERMINED (never a forced 0/negative).
      UNION = logical OR of the four dim indicators (unchanged).

Empirical-quantile calibration on the correct null (brief §2).  It
controls per-dim FPR by construction (q=0.95 → each dim fires on ~5%
of benign probes); the JOINT union FPR is reported from the measured
dimension-overlap (dimcount), not assumed independent.  The candidate
measurement (distance kernels, union, dimensions) is UNCHANGED — only
θ estimation is repaired.

BASELINES in the same table:
  frozen_shared  : the frozen per-cell max-of-3 θ applied to BOTH sides
                   (the circular variant B → B2B FPR ≈ 0 by algebra).
  frozen_loo     : LEP θ=max-of-3, B2B θ=max-of-others (LOO max-of-1)
                   → the 30.5% / 32.1% mismatched-cardinality pair.

SELECTION: this run is DEV.  The winning (null, q, floor, pool) is
applied to the HELDOUT partition exactly once at final evaluation
(partition-local pool: heldout θ from heldout benigns only — no
cross-partition data at evaluation time).

--impl {candspec,v6,v7}: run the identical experiment under that
implementation's OWN extraction + distance kernel (the control must
use the implementation's machinery, not a proxy).  candspec =
CF.extract_candspec + CF.classify_dimensions (default); v6 =
features_v6 + run_pipeline.dim_distances; v7 = measure.extract_
features_v7 (policy research_polarity=rubric, as the v7 ledger was
run) + v7 run_pipeline.dim_distances.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
import candspec_features as CF            # noqa: E402
import features_v6 as F6                  # noqa: E402


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def _nri(spec, fam):
    return len(spec.get('required_issues') or []) \
        if fam == 'code_review' else 0


def _grade(feats, spec, fam):
    P = common.pipelines()
    V6G = P['V6G']
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)[0]
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)[0]
    if fam == 'code_review':
        return V6G.grade_code(getattr(feats, 'decisions_semantic',
                                      feats.decisions), spec)[0]
    return 'ungradeable'


def _dist(fa, fb, spec, fam):
    return CF.classify_dimensions(fa, fb, fam, _grade(fa, spec, fam),
                                  _grade(fb, spec, fam),
                                  n_required_issues=_nri(spec, fam))


def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def excess_ci(k1: int, n1: int, k2: int, n2: int,
              z: float = 1.959964) -> Optional[List[float]]:
    """Normal-approx 95% CI on the difference of two proportions
    (LEP rate − B2B rate).  n1/n2 are cell-level counts; the difference
    is estimated with its independent two-sample SE."""
    if not (n1 and n2):
        return None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return [d - z * se, d + z * se]


def _loo_rank(P: List[float], i_target: int,
              R: List[float]) -> Optional[int]:
    """Exact multiset-LOO quantile, O(r · log n).

    P: sorted pool (the cell's ≤3 probe T-values are inside it, as a
    multiset R).  T = P \\ R (multiset difference; the cell's own probe
    values are EXCLUDED from its own threshold).  Returns a pool index
    j with P[j] == T[i_target], i.e. the q-quantile of the trimmed
    distribution.  TIES ARE EXACT: within a run of equal pooled
    values the value that carries T[i_target] is found by the crossing
    test on per-value survivor counts (c(v) - r(v)), and the returned
    index is a SURVIVING copy of that value (i_target + r_less(v)) —
    not merely a value-level match.  (The predecessor shipped
    index-shift approximation was tie-biased: audit in
    reference/loo_audit.py — on the real heldout pools it moved 27/5204
    cells' θ, all LOWER-θ = G5-conservative direction; this is the
    exact replacement.)  None when the trimmed pool is too small.
    P must be sorted; R unsorted ok (≤3 values, a sub-multiset of P).
    """
    import bisect
    n = len(P)
    r = len(R)
    if n - r <= i_target:
        return None
    R = sorted(R)
    for m in range(0, r + 1):
        j = i_target + m
        if j >= n:
            continue
        v = P[j]
        pos = bisect.bisect_left(P, v)
        c = bisect.bisect_right(P, v) - pos
        r_less = bisect.bisect_left(R, v)
        r_eq = bisect.bisect_right(R, v) - r_less
        # value v carries T positions [pos - r_less, pos - r_less +
        # (c - r_eq)); the crossing value is the one containing i_target
        if pos - r_less <= i_target < pos - r_less + (c - r_eq):
            return i_target + r_less   # a surviving copy of v
    return None


def run(partition: str, impl: str = 'candspec') -> Dict[str, Any]:
    specs = common.load_specs()
    part = json.load(open(os.path.join(common.OUT,
                                       'partitions_final.json')))
    held = {k for k, v in part['fixtures'].items()
            if (v.get('partition') if isinstance(v, dict) else v)
            == 'heldout'}
    target = (set(part['fixtures']) - held) if partition == 'dev' \
        else held
    rows_all = [json.loads(l) for l in
                open(os.path.join(common.CONS, 'downstream_results',
                                  'per_trace.jsonl')) if l.strip()]
    rows = [r for r in rows_all
            if r['trace_id'].split('.')[0] in target
            and len(r.get('reference_set') or []) >= 3]
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                      'trace_index.jsonl')) if l.strip()}
    feat_cache: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}

    # per-implementation extraction + distance machinery (the control
    # must run the implementation's OWN kernel).  The v6 and v7
    # run_pipeline modules are loaded BY PATH under distinct module
    # names (both directories are on sys.path; a bare `import
    # run_pipeline` would silently resolve to v6 for both).
    import importlib.util

    def _load(name, path):
        if name in __import__('sys').modules:
            return __import__('sys').modules[name]
        sp = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(sp)
        __import__('sys').modules[name] = m
        sp.loader.exec_module(m)
        return m

    if impl == 'candspec':
        import candspec_pipeline as CP
        ex_fn = lambda tr, spec, fam: CF.extract_candspec(tr, spec, fam)

        def grade_fn(f, spec, fam):
            return CP._outcome_grade(f, spec, fam)

        def dist_fn(fa, fb, spec, fam):
            ga, _ = grade_fn(fa, spec, fam)
            gb, _ = grade_fn(fb, spec, fam)
            return CF.classify_dimensions(fa, fb, fam, ga, gb,
                                          n_required_issues=_nri(spec, fam))
    elif impl == 'v6':
        RP = _load('rp_v6', os.path.join(common.V6_DIR,
                                          'run_pipeline.py'))
        ex_fn = lambda tr, spec, fam: F6.extract_features_v6(tr, spec, fam)
        dist_fn = lambda fa, fb, spec, fam: _rpdist(RP, fa, fb, spec, fam)
    else:  # v7 (rubric policy — the v7 ledger was run with it)
        RP = _load('rp_v7', os.path.join(common.V7_DIR,
                                          'run_pipeline.py'))
        import measure as M7
        _POL = {'research_polarity': 'rubric'}
        ex_fn = lambda tr, spec, fam: M7.extract_features_v7(
            tr, spec, fam, policy=_POL)
        dist_fn = lambda fa, fb, spec, fam: _rpdist(RP, fa, fb, spec, fam)

    def _rpdist(rp, fa, fb, spec, fam):
        ga, da = rp.outcome_of(fa, spec, fam)
        gb, db = rp.outcome_of(fb, spec, fam)
        return rp.dim_distances(fa, fb, fam, ga, gb, da, db)

    def feats(tid):
        if tid not in feat_cache:
            spec, fam = meta[tid]
            p = idx.get(tid)
            tr = json.load(open(p)) if p and os.path.exists(p) else None
            feat_cache[tid] = ex_fn(tr, spec, fam) \
                if tr is not None else None
        return feat_cache[tid]

    def _vdist(RP, fa, fb, spec, fam):
        ga, da = grade_fn(fa, spec, fam)
        gb, db = grade_fn(fb, spec, fam)
        return RP.dim_distances(fa, fb, fam, ga, gb, da, db)

    t0 = time.time()
    cells: List[Dict[str, Any]] = []
    for r in rows:
        fid = r['trace_id'].split('.')[0]
        spec = specs.get(fid, {})
        fam = r['task_family']
        refs = r.get('reference_set') or []
        meta.setdefault(r['trace_id'], (spec, fam))
        for t in refs:
            meta.setdefault(t, (spec, fam))
        frefs = [feats(t) for t in refs]
        flep = feats(r['trace_id'])
        if flep is None or any(f is None for f in frefs) \
                or len(frefs) < 3:
            continue
        fb = frefs
        lep_d = [dist_fn(flep, fb[k], spec, fam) for k in range(3)]
        pairs = {}
        for i in range(3):
            for j in range(i + 1, 3):
                pairs[(i, j)] = dist_fn(fb[i], fb[j], spec, fam)
        T_lep = {d: _mean([x.get(d) for x in lep_d]) for d in common.DIMS}
        T_p2, T_p3 = [], []
        for bi in range(3):
            o2 = [pairs[(min(bi, k), max(bi, k))]
                  for k in range(3) if k != bi]
            T_p2.append({d: _mean([o.get(d) for o in o2])
                         for d in common.DIMS})
            T_p3.append({d: _mean([o.get(d) for o in
                                   ([lep_d[bi]] + o2)])
                         for d in common.DIMS})
        cm: Dict[str, Optional[float]] = {}
        for d in common.DIMS:
            vs = [pairs[(i, j)].get(d) for (i, j) in pairs
                  if pairs[(i, j)].get(d) is not None]
            cm[d] = max(vs) if vs else None
        cells.append({'fam': fam, 'lep_code': r.get('lep_code'),
                      'stratum': (fam, r.get('topology'),
                                  r.get('propagation_mode')),
                      'T_lep': T_lep, 'T_p2': T_p2, 'T_p3': T_p3,
                      'cellmax': cm, 'pairs': pairs, 'idx': len(cells)})
    print(f'[calib] {partition}: {len(cells)} cells precomputed in '
          f'{time.time() - t0:.0f}s', flush=True)

    # ── θ tables: (null, pool, q) -> per-cell per-dim threshold ─────
    NULLS = {'p2': 'T_p2', 'p3': 'T_p3'}
    QVALS = [0.80, 0.90, 0.95, 0.98, 0.99]
    FLOORS = [0.0, 0.02, 0.05]
    POOLS = ['stratum', 'family']

    pools_raw: Dict[str, Dict[Any, Dict[str, List[float]]]] = {}
    for null, fld in NULLS.items():
        pools_raw[null] = {}
        for pk in POOLS:
            of: Dict[Any, Dict[str, List[float]]] = {}
            for c in cells:
                key = c['stratum'] if pk == 'stratum' else c['fam']
                ent = of.setdefault(key, {d: [] for d in common.DIMS})
                for tp in c[fld]:
                    for d in common.DIMS:
                        if tp[d] is not None:
                            ent[d].append(tp[d])
            pools_raw[null][pk] = {k: {d: sorted(v[d])
                                       for d in common.DIMS}
                                    for k, v in of.items()}

    theta_tables: Dict[tuple, Dict[int, Dict[str, Optional[float]]]] = {}
    t1 = time.time()
    for null in NULLS:
        for pk in POOLS:
            for q in QVALS:
                key = (null, pk, q)
                per_cell: Dict[int, Dict[str, Optional[float]]] = {}
                for pkey, dims in pools_raw[null][pk].items():
                    pcells = [c for c in cells
                               if (c['stratum'] if pk == 'stratum'
                                   else c['fam']) == pkey]
                    for c in pcells:
                        out = {}
                        for d in common.DIMS:
                            P = dims[d]
                            Rd = sorted(tp[d] for tp in c[NULLS[null]]
                                        if tp[d] is not None)
                            n = len(P)
                            i_target = int(math.ceil(
                                q * (n - len(Rd)))) - 1
                            j = (_loo_rank(P, max(0, i_target), Rd)
                                 if n - len(Rd) > 0 else None)
                            out[d] = P[j] if j is not None else None
                        per_cell[c['idx']] = out
                theta_tables[key] = per_cell
    print(f'[calib] θ tables ({len(theta_tables)}) built in '
          f'{time.time() - t1:.0f}s', flush=True)

    # ── scoring ──────────────────────────────────────────────────────
    def _fire(m: Dict[str, Optional[float]],
              thr: Dict[str, Optional[float]]) -> List[str]:
        return [d for d in common.DIMS
                if m.get(d) is not None and thr.get(d) is not None
                and m[d] > thr[d]]

    def _accumulate(st: Dict[str, Any], m, thr, trig) -> None:
        if not any(m.get(d) is not None and thr.get(d) is not None
                   for d in common.DIMS):
            return
        st['n'] += 1
        if trig:
            st['u'] += 1
            st['dimcount'][len(trig)] = st['dimcount'].get(len(trig), 0) + 1
            for d in trig:
                st['dims'][d] += 1
            if all(thr[d] == 0.0 for d in trig):
                st['zero_only'] += 1

    def _blank() -> Dict[str, Any]:
        return {'n': 0, 'u': 0, 'dims': {d: 0 for d in common.DIMS},
                'dimcount': {}, 'zero_only': 0}

    def score(thr_fn, probe_field: str, by_lep_note: str = '',
              matched_field: Optional[str] = None):
        """Score LEP targets + B2B probes.  `matched_field` (optional)
        scores the probe population under the SAME statistic shape as
        the LEP target (the avg-of-3 T_p3 probes) against the CLEAN
        p2-null θ, so the excess is shape-matched as well as θ-matched."""
        st_l, st_b = _blank(), _blank()
        st_m = _blank()
        by_lep: Dict[str, Dict[str, int]] = {}
        for c in cells:
            thr_l = thr_fn(c, None)
            trig_l = _fire(c['T_lep'], thr_l)
            _accumulate(st_l, c['T_lep'], thr_l, trig_l)
            bc = by_lep.setdefault(c['lep_code'], {'n': 0, 'u': 0})
            if any(c['T_lep'].get(d) is not None and
                   thr_l.get(d) is not None for d in common.DIMS):
                bc['n'] += 1
                bc['u'] += int(bool(trig_l))
            for pi, tp in enumerate(c[probe_field]):
                thr_b = thr_fn(c, pi)
                trig_b = _fire(tp, thr_b)
                _accumulate(st_b, tp, thr_b, trig_b)
            if matched_field is not None:
                for pi, tp in enumerate(c[matched_field]):
                    thr_m = thr_fn(c, pi)
                    trig_m = _fire(tp, thr_m)
                    _accumulate(st_m, tp, thr_m, trig_m)
        n_l, n_b, n_m = st_l['n'], st_b['n'], st_m['n']
        lr = st_l['u'] / n_l if n_l else None
        br = st_b['u'] / n_b if n_b else None
        out = {
            'lep': {**st_l, 'rate': lr, 'wilson': wilson(st_l['u'], n_l),
                    'per_dim': {d: st_l['dims'][d] / n_l if n_l else None
                                for d in common.DIMS},
                    'zero_only_share': (st_l['zero_only'] / st_l['u'])
                    if st_l['u'] else None,
                    'by_lep_code': {
                        k: {'n': v['n'],
                            'rate': (v['u'] / v['n']) if v['n']
                            else None,
                            'wilson': wilson(v['u'], v['n'])}
                        for k, v in sorted(by_lep.items())}},
            'b2b': {**st_b, 'fpr': br, 'wilson': wilson(st_b['u'], n_b),
                    'per_dim': {d: st_b['dims'][d] / n_b if n_b else None
                                for d in common.DIMS},
                    'zero_only_share': (st_b['zero_only'] / st_b['u'])
                    if st_b['u'] else None},
            'excess_union': ((lr - br) if (lr is not None
                                           and br is not None) else None),
            'excess_ci95': excess_ci(st_l['u'], n_l, st_b['u'], n_b),
            'note': by_lep_note}
        if matched_field is not None and n_m:
            mr = st_m['u'] / n_m
            out['b2b_matched_shape'] = {
                **st_m, 'fpr': mr, 'wilson': wilson(st_m['u'], n_m),
                'per_dim': {d: st_m['dims'][d] / n_m
                             for d in common.DIMS},
                'note': 'avg-of-3 probes (T_p3 shape) under the CLEAN '
                        'p2-null θ; LEP − this = the shape-matched '
                        'excess (the honest G5/sensitivity statistic)'}
            out['excess_matched_shape'] = (
                (lr - mr) if (lr is not None) else None)
            out['excess_matched_shape_ci95'] = excess_ci(
                st_l['u'], n_l, st_m['u'], n_m)
        return out

    results: Dict[str, Any] = {'partition': partition,
                               'n_cells': len(cells),
                               'population': 'cells with 3 benign refs; '
                                              'LEP targets + 3 B2B '
                                              'probes/cell; threshold '
                                              'is the SAME function on '
                                              'both sides; each probe '
                                              'is EXCLUDED from its own '
                                              'θ pool (exact LOO)',
                               'rules': {}}

    # baseline 1: frozen per-cell max-of-3 θ applied to both sides
    #             (the circular variant B → B2B FPR ≈ 0 by algebra)
    def frozen_shared(c, pi):
        return {d: c['cellmax'][d] for d in common.DIMS}
    results['rules']['frozen_shared'] = \
        score(frozen_shared, 'T_p2',
              'frozen per-cell max-of-3 θ on both sides (circular B)')

    # baseline 2: faithful frozen control — LEP θ=max-of-3; B2B
    #             θ=max-of-others (LOO max-of-1).  The 30.5%/32.1% pair.
    def frozen_loo(c, pi):
        if pi is None:
            # LEP side: the frozen cell-local max of all 3 b2b pairs
            return {d: c['cellmax'][d] for d in common.DIMS}
        # B2B probe pi: the faithful leave-one-out control calibrates θ
        # on the single pair among the TWO OTHERS (probe excluded) —
        # max-of-1, the 32.1% control.  NOT the probe-containing pairs
        # (mean ≤ max would make it never fire → circular null).
        others = [k for k in range(3) if k != pi]
        out = {}
        for d in common.DIMS:
            vs = [c['pairs'][(min(others[0], others[1]),
                             max(others[0], others[1]))].get(d)]
            out[d] = max(v for v in vs if v is not None) if any(
                v is not None for v in vs) else None
        return out
    results['rules']['frozen_loo'] = \
        score(frozen_loo, 'T_p2',
              'frozen LEP max-of-3 vs B2B LOO max-of-1 (mismatched '
              'cardinality)')

    # pooled quantile rules
    for null in NULLS:
        for q in QVALS:
            for f in FLOORS:
                for pk in POOLS:
                    rn = f'{null}_q{int(q * 100)}_f{int(f * 100)}_{pk}'
                    tt = theta_tables[(null, pk, q)]
                    pf = 'T_p3' if null == 'p3' else 'T_p2'

                    def thr_fn(c, pi, _tt=tt, _f=f, _pf=pf):
                        base = _tt.get(c['idx'], {})
                        # LOO: when scoring probe `pi`, also drop its
                        # individual T value from the quantile (the table
                        # already trims the CELL; probe-level trim is
                        # applied by recomputing the threshold for B2B
                        # only if the probe's own T exceeds the trimmed
                        # quantile — negligible for ≤3 probes, so we
                        # use the cell-trimmed table for both sides).
                        return {d: (max(v, _f) if v is not None
                                    else None)
                                for d, v in base.items()}
                    results['rules'][rn] = {
                        'config': {'null': null, 'q': q, 'floor': f,
                                   'pool': pk},
                        # for the CLEAN p2-null rules, also score the
                        # avg-of-3 probes (T_p3 shape = LEP shape) under
                        # the p2-null θ → the shape-matched excess
                        # (the honest sensitivity/G5 statistic).  p3-null
                        # rules are contaminated (pool includes LEP) so
                        # no matched control is reported for them.
                        **score(thr_fn, pf,
                                f'{null} null, LOO-cell pool={pk}, '
                                f'q={q}, floor={f}',
                                matched_field='T_p3' if null == 'p2'
                                else None)}
    outp = (f'calib_experiment_{partition}.json' if impl == 'candspec'
          else f'calib_experiment_{partition}_{impl}.json')
    common.write_json(results, outp)
    print('\nrule:22s   LEP_rate  B2B_fpr   excess   B2B_wil_hi')
    for rn, v in results['rules'].items():
        lr_ = (v.get('lep') or {}).get('rate')
        br_ = (v.get('b2b') or {}).get('fpr')
        ex_ = v.get('excess_union')
        whi = ((v.get('b2b') or {}).get('wilson') or [None, None])[1]
        if lr_ is None:
            print(f'  {rn:20s} (no LEP rate)')
            continue
        print(f'  {rn:20s} {lr_:8.4f} {br_:8.4f} '
              f'{ex_:8.4f} {whi if whi is not None else -1:9.4f}')
    return results


if __name__ == '__main__':
    # REPRODUCIBILITY: the candidate distance kernels sum over
    # set-iteration order (total_variation over the action-key set),
    # whose float summation order is process-dependent via
    # PYTHONHASHSEED -> near-threshold cells could flip between
    # runs of the SAME code (observed: heldout-v6 B2B 478 vs 471
    # across two unpinned runs).  Pin the hash seed; if a pinned
    # seed is already set, respect it; otherwise re-exec once under
    # PYTHONHASHSEED=0.  The distances are mathematically
    # order-invariant — the pin only makes the FLOAT ARTIFACT
    # bit-reproducible (documented in out/loo_audit.json).
    if 'PYTHONHASHSEED' not in os.environ:
        _env = dict(os.environ, PYTHONHASHSEED='0')
        os.execve(sys.executable, [sys.executable,
                                    os.path.abspath(__file__)]
                  + sys.argv[1:], _env)
    t0 = time.time()
    part = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    imp = sys.argv[2] if len(sys.argv) > 2 else 'candspec'
    run(part, imp)
    print(f'done in {time.time() - t0:.0f}s')
