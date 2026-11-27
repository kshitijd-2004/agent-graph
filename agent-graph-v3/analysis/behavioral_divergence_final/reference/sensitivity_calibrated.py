#!/usr/bin/env python3.11
"""
Full-frame calibrated-theta PRODUCTION SENSITIVITY (brief §7 'sensitivity
analyses'; MEASUREMENT_SPEC §11).

The PRIMARY production measurement uses each implementation's FROZEN
per-cell max theta (out/production_summary_v6.json, v6: M1 union 5,037 /
16,773 = 30.03%).  This script re-scores the SAME full P1 frame under
the REPAIRED matched-cardinality control (dev-selected rule
p3_q98_f0_family, exact multiset-LOO quantile, family pool, floor 0.0),
PLUS the sensitivity band (p2_q98_f0_family clean 2-ref null;
p3_q99_f0_family stricter).  A full-frame B2B control is reported so
the excess is available on the production population, not just heldout.

It is a SENSITIVITY record: the frozen-theta numbers remain the
primary production measurement; this answers 'what would the union be
under the repaired theta'.  Hash-pinned like calib_experiment (the
candidate TV kernel is float-order sensitive; see FROZEN_PROTOCOL §6).

Writes out/production_sensitivity_calibrated_v6.json (read-only
otherwise).  ~8 min.
"""
from __future__ import annotations

import importlib.util
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
import features_v6 as F6                 # noqa: E402


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def _nri(spec, fam):
    return len(spec.get('required_issues') or []) \
        if fam == 'code_review' else 0


def _grade(feats, spec, fam):
    V6G = common.pipelines()['V6G']
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)[0]
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)[0]
    if fam == 'code_review':
        return V6G.grade_code(getattr(feats, 'decisions_semantic',
                                       feats.decisions), spec)[0]
    return 'ungradeable'


def main() -> None:
    # exact-LOO quantile from the (patched) calib_experiment module
    sp = importlib.util.spec_from_file_location(
        'ce_sens', os.path.join(_HERE, 'calib_experiment.py'))
    ce = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(ce)
    loo = ce._loo_rank

    specs = common.load_specs()
    rows_all = [json.loads(l) for l in open(os.path.join(
        common.CONS, 'downstream_results', 'per_trace.jsonl'))
        if l.strip()]
    rows = [r for r in rows_all if len(r.get('reference_set') or []) >= 3]
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                     'trace_index.jsonl')) if l.strip()}

    RP = importlib.util.spec_from_file_location(
        'rp_v6_sens', os.path.join(common.V6_DIR, 'run_pipeline.py'))
    m = importlib.util.module_from_spec(RP)
    sys.modules['rp_v6_sens'] = m
    RP.loader.exec_module(m)

    def dist(fa, fb, spec, fam):
        ga, da = m.outcome_of(fa, spec, fam)
        gb, db = m.outcome_of(fb, spec, fam)
        return m.dim_distances(fa, fb, fam, ga, gb, da, db)

    t0 = time.time()
    feat_cache: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}

    def feats(tid):
        if tid not in feat_cache:
            spec, fam = meta[tid]
            p = idx.get(tid)
            tr = json.load(open(p)) if p and os.path.exists(p) else None
            feat_cache[tid] = (F6.extract_features_v6(tr, spec, fam)
                               if tr is not None else None)
        return feat_cache[tid]

    cells = []
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
        lep_d = [dist(flep, fb[k], spec, fam) for k in range(3)]
        pairs = {}
        for i in range(3):
            for j in range(i + 1, 3):
                pairs[(i, j)] = dist(fb[i], fb[j], spec, fam)
        T_lep = {d: _mean([x.get(d) for x in lep_d]) for d in common.DIMS}
        T_p2, T_p3 = [], []
        for bi in range(3):
            o2 = [pairs[(min(bi, k), max(bi, k))] for k in range(3)
                 if k != bi]
            T_p2.append({d: _mean([o.get(d) for o in o2])
                         for d in common.DIMS})
            T_p3.append({d: _mean([o.get(d) for o in ([lep_d[bi]] + o2)])
                        for d in common.DIMS})
        cells.append({'fam': fam, 'family': fam,
                      'T_lep': T_lep, 'T_p2': T_p2, 'T_p3': T_p3})
    print(f'[sens] full frame: {len(cells)} cells in '
          f'{time.time() - t0:.0f}s', flush=True)

    DIMS = common.DIMS
    FLOOR = 0.0

    def build_theta(null_field: str, q: float,
                    pool: str = 'family') -> Dict[int, Dict[str, Optional[float]]]:
        pools: Dict[str, Dict[str, List[float]]] = {}
        for ci, c in enumerate(cells):
            ent = pools.setdefault(c[pool], {d: [] for d in DIMS})
            for tp in c[null_field]:
                for d in DIMS:
                    if tp[d] is not None:
                        ent[d].append(tp[d])
        for ent in pools.values():
            for d in DIMS:
                ent[d] = sorted(ent[d])
        thr: Dict[int, Dict[str, Optional[float]]] = {}
        for ci, c in enumerate(cells):
            out = {}
            for d in DIMS:
                P = pools[c[pool]][d]
                R = sorted(tp[d] for tp in c[null_field] if tp[d] is not None)
                i_target = int(math.ceil(q * (len(P) - len(R)))) - 1
                j = loo(P, max(0, i_target), R) if len(P) - len(R) > 0 \
                    else None
                out[d] = (max(P[j], FLOOR) if j is not None else None)
            thr[ci] = out
        return thr

    def fire(mv, th):
        return [d for d in DIMS if mv.get(d) is not None
                and th.get(d) is not None and mv[d] > th[d]]

    def score(null_field, q, pool):
        thr = build_theta(null_field, q, pool)
        st_l = {'n': 0, 'u': 0, 'dims': {d: 0 for d in DIMS},
                'zero_only': 0}
        st_b = {'n': 0, 'u': 0, 'dims': {d: 0 for d in DIMS}}
        for ci, c in enumerate(cells):
            t = thr[ci]
            trig = fire(c['T_lep'], t)
            if any(c['T_lep'].get(d) is not None and t.get(d) is not None
                   for d in DIMS):
                st_l['n'] += 1
                if trig:
                    st_l['u'] += 1
                    for d in trig:
                        st_l['dims'][d] += 1
                    if all(t[d] == FLOOR for d in trig):
                        st_l['zero_only'] += 1
            for tp in c[null_field]:
                tb = fire(tp, t)
                if any(tp.get(d) is not None and t.get(d) is not None
                       for d in DIMS):
                    st_b['n'] += 1
                    if tb:
                        st_b['u'] += 1
                        for d in tb:
                            st_b['dims'][d] += 1
        n_l, n_b = st_l['n'], st_b['n']
        lr = st_l['u'] / n_l if n_l else None
        br = st_b['u'] / n_b if n_b else None
        out = {'null': null_field, 'q': q, 'pool': pool,
               'floor': FLOOR,
               'lep_union': (st_l['u'], n_l, lr,
                              st_l['dims'], st_l['zero_only']),
               'b2b_union': (st_b['u'], n_b, br, st_b['dims'])}
        return out

    rep: Dict[str, Any] = {
        'artifact': 'out/production_sensitivity_calibrated_v6.json',
        'kernel': 'v6 (features_v6 + run_pipeline.dim_distances; '
                  'outcome_of for the grade feeds)',
        'population': 'full P1 frame (all standard LEP rows with >=3 '
                      'compatible benign references)',
        'note': 'SENSITIVITY RECORD only: the primary production '
                'measurement is the frozen per-cell max theta '
                '(out/production_summary_v6.json: M1 union 5,037 / '
                '16,773 = 30.03%).  The repaired control (dev-selected '
                'p3_q98_f0_family, exact multiset-LOO quantile, family '
                'pool, floor 0.0) answers what the union would be under '
                'the repaired theta.  G5 criterion (FPR<=0.05) is '
                'unchanged; the B2B control side is reported on the '
                'full frame as well.',
        'rules': {},
    }
    rep['rules']['p3_q98_f0_family (dev-selected; G5 rule)'] = \
        score('T_p3', 0.98, 'family')
    rep['rules']['p2_q98_f0_family (clean 2-ref null)'] = \
        score('T_p2', 0.98, 'family')
    rep['rules']['p3_q99_f0_family (stricter band)'] = \
        score('T_p3', 0.99, 'family')
    rep['frozen_theta_primary'] = {
        'm1_union_count': 5037, 'frame_rows': 16773,
        'm1_union_rate': 5037 / 16773,
        'source': 'out/production_summary_v6.json'}
    common.write_json(rep, 'production_sensitivity_calibrated_v6.json')
    for rn, v in rep['rules'].items():
        lu, ln, lr_, dims, zo = v['lep_union']
        bu, bn, br_, _ = v['b2b_union']
        print(f"  {rn}: LEP union {lu}/{ln}={lr_:.4f} "
              f"(zero-only {zo}) | B2B {bu}/{bn}={br_:.4f} "
              f"excess {lr_ - br_:+.4f}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        _env = dict(os.environ, PYTHONHASHSEED='0')
        os.execve(sys.executable, [sys.executable,
                                    os.path.abspath(__file__)]
                  + sys.argv[1:], _env)
    t0 = time.time()
    main()
    print(f'(wrapper) done in {time.time() - t0:.0f}s')
