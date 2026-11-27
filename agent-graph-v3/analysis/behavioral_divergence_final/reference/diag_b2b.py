#!/usr/bin/env python3.11
"""
B2B FPR diagnosis (brief: diagnose the 31.9% benign-to-benign FPR before
concluding there is no signal).  For candspec, over the heldout B2B
population (probe = refs[0], others = refs[1:]), compute:

  * per-dimension FPR (each of the 4 dims firing on a benign probe),
  * the per-dimension threshold distribution (n_zero_theta, min/mean),
  * dimension-overlap among union-fires (how many fire on >=1, exactly
    1, >=2, all dims),
  * how many union-fires are zero-threshold-only,
  * pool-variant FPRs:
      A) others-only calibration (C(2,2)=1 pair)  [current b2b_control]
      B) all-pairs calibration (C(3,2)=3 pairs, probe included in the
         calibration pool, probe EXCLUDED from the test distance) —
         the LEP-matched cardinality (same pool size as the LEP eval)
  * and the matching LEP-side FPR-equivalent (real LEP rows, union)
    under the same all-pairs convention, so B2B-vs-LEP are directly
    comparable.

No candidate code is modified; this is read-only diagnostics over the
candidate extractor.  Writes out/diag_b2b.json.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
sys.path.insert(0, common.V6_DIR)
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
import candspec_features as CF            # noqa: E402


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


def _b2b_rows(partition='heldout'):
    """Return [(probe_tid, [other_ref_tids...], spec, fam)] for the
    heldout B2B population (probe = refs[0], others = refs[1:])."""
    part = json.load(open(os.path.join(common.OUT,
                                       'partitions_final.json')))
    held = {k for k, v in part['fixtures'].items()
            if (v.get('partition') if isinstance(v, dict) else v)
            == 'heldout'}
    rows = [json.loads(l) for l in
            open(os.path.join(common.CONS, 'downstream_results',
                              'per_trace.jsonl')) if l.strip()]
    return [(r, r['trace_id']) for r in rows
            if r['trace_id'].split('.')[0] in held
            and len(r.get('reference_set') or []) >= 3]


def run() -> Dict[str, Any]:
    P = common.pipelines()
    V6U = P['V6U']
    specs = common.load_specs()
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                      'trace_index.jsonl')) if l.strip()}
    cache: Dict[Any, Any] = {}

    def feats(tr, spec, fam):
        k = (id(tr),)
        if k not in cache:
            cache[k] = CF.extract_candspec(tr, spec, fam)
        return cache[k]

    pop = _b2b_rows()
    # accumulate
    dim_fires = {d: 0 for d in common.DIMS}
    union_fires = 0
    dim_count_dist: Dict[int, int] = {}
    zero_thr_union = 0
    n_eval = 0
    n_union_zero_only = 0
    # threshold distributions per dim (all-pairs pool)
    thr_dist = {d: [] for d in common.DIMS}
    b2b_actions_tv = []
    lep_dim_fires = {d: 0 for d in common.DIMS}
    lep_zero_cells = {d: 0 for d in common.DIMS}
    dim_fires_B = {d: 0 for d in common.DIMS}
    zero_cells_B = {d: 0 for d in common.DIMS}
    # pool-variant FPRs
    fprA = 0   # others-only calibration (cardinality-mismatched control)
    fprB = 0   # all-pairs calibration (LEP-matched cardinality)
    nA = nB = 0
    dim_fires_A = {d: 0 for d in common.DIMS}
    zero_cells_A = {d: 0 for d in common.DIMS}
    thr_dist_A = {d: [] for d in common.DIMS}
    dim_count_dist_A: Dict[int, int] = {}
    n_A_zero_only = 0
    # LEP-side, all-pairs convention (real LEP rows)
    lep_union = 0
    lep_eval = 0
    for r, lep_tid in pop:
        fam = r['task_family']
        spec = specs.get(r['trace_id'].split('.')[0], {})
        refs = r.get('reference_set') or []
        # load all refs + the LEP trace
        trs = {}
        ok = True
        for t in refs + [lep_tid]:
            p = idx.get(t)
            tr = json.load(open(p)) if p and os.path.exists(p) else None
            trs[t] = tr
            if tr is None:
                ok = False
        if not ok:
            continue
        probe_tid = refs[0]
        others = refs[1:]
        fprobe = feats(trs[probe_tid], spec, fam)
        fothers = [feats(trs[t], spec, fam) for t in others]
        flep = feats(trs[lep_tid], spec, fam) if trs.get(lep_tid) else None

        # all-pairs b2b pool over ALL 3 benigns (probe included for
        # calibration, matching the LEP evaluation's 3-pair pool)
        allb = [fprobe] + fothers
        allpairs = [_dist(allb[i], allb[j], spec, fam)
                    for i in range(len(allb))
                    for j in range(i + 1, len(allb))]
        thr_all = V6U.benign_thresholds(allpairs)
        for d in common.DIMS:
            if thr_all.get(d) is not None:
                thr_dist[d].append(thr_all[d])

        # --- pool variant B: calibrate over ALL pairs, test probe against
        #     the other benigns (probe excluded from the test distance) ---
        probe_dists = [_dist(fprobe, fo, spec, fam) for fo in fothers]
        lep_mean_B = {d: _mean([x.get(d) for x in probe_dists])
                      for d in common.DIMS}
        pd_B = V6U.per_dimension(lep_mean_B, allpairs, thr_all,
                                 aggregation='mean')
        u_B = V6U.union(pd_B)
        if u_B.get('n_evaluable', 0) > 0:
            nB += 1
            for d in common.DIMS:
                if (pd_B.get(d) or {}).get('divergent'):
                    dim_fires_B[d] += 1
                if (pd_B.get(d) or {}).get('zero_threshold'):
                    zero_cells_B[d] += 1
            if u_B.get('divergent'):
                fprB += 1
                dim_count = len(u_B.get('triggered_dimensions') or [])
                dim_count_dist[dim_count] = dim_count_dist.get(dim_count, 0) + 1
                zt = set(u_B.get('zero_threshold_triggered') or [])
                trig = set(u_B.get('triggered_dimensions') or [])
                if zt == trig:
                    n_union_zero_only += 1
                for d in common.DIMS:
                    if (pd_B.get(d) or {}).get('divergent'):
                        dim_fires[d] += 1
        n_eval = nB

        # --- pool variant A: calibrate over OTHERS-only pairs (current) ---
        oth_pairs = [_dist(fothers[i], others_j, spec, fam)
                    for i in range(len(fothers))
                    for others_j in fothers[i + 1:]]
        if oth_pairs:
            thr_oth = V6U.benign_thresholds(oth_pairs)
            pd_A = V6U.per_dimension(lep_mean_B, oth_pairs, thr_oth,
                                     aggregation='mean')
            u_A = V6U.union(pd_A)
            if u_A.get('n_evaluable', 0) > 0:
                nA += 1
                if u_A.get('divergent'):
                    fprA += 1
                    dim_count_dist_A[len(u_A.get('triggered_dimensions')
                                        or [])] = \
                        dim_count_dist_A.get(len(u_A.get(
                            'triggered_dimensions') or []), 0) + 1
                    if set(u_A.get('zero_threshold_triggered') or []) == \
                            set(u_A.get('triggered_dimensions') or []):
                        n_A_zero_only += 1
                for d in common.DIMS:
                    if (pd_A.get(d) or {}).get('divergent'):
                        dim_fires_A[d] += 1
                    if (pd_A.get(d) or {}).get('zero_threshold'):
                        zero_cells_A[d] += 1
                    if (pd_A.get(d) or {}).get('threshold') is not None:
                        thr_dist_A[d].append(pd_A[d]['threshold'])

        # --- LEP-side, all-pairs convention (real LEP trace) ---
        if flep is not None:
            lep_dists = [_dist(flep, fo, spec, fam) for fo in fothers]
            lmean = {d: _mean([x.get(d) for x in lep_dists])
                     for d in common.DIMS}
            pd_L = V6U.per_dimension(lmean, allpairs, thr_all,
                                     aggregation='mean')
            u_L = V6U.union(pd_L)
            if u_L.get('n_evaluable', 0) > 0:
                lep_eval += 1
                if u_L.get('divergent'):
                    lep_union += 1
                for d in common.DIMS:
                    if (pd_L.get(d) or {}).get('divergent'):
                        lep_dim_fires[d] += 1
                    if (pd_L.get(d) or {}).get('zero_threshold'):
                        lep_zero_cells[d] += 1
        # capture actions TV (all-pairs) for the noise picture
        for p in allpairs:
            if p.get('actions') is not None:
                b2b_actions_tv.append(p['actions'])

    out: Dict[str, Any] = {
        'n_cells_evaluated': n_eval,
        'population': 'heldout cells, 3 benigns each; probe=refs[0]',
        'pool_variant_A_fpr_others_only': {
            'n': nA, 'fired': fprA,
            'fpr': (fprA / nA) if nA else None,
            'per_dimension_fires': dim_fires_A,
            'per_dimension_fpr': {d: (dim_fires_A[d] / nA) if nA else None
                                  for d in common.DIMS},
            'zero_threshold_cells': zero_cells_A,
            'threshold_distribution': {
                d: {'n_zero': sum(1 for x in thr_dist_A[d]
                                  if x == 0),
                    'n': len(thr_dist_A[d]),
                    'p50': _pct(thr_dist_A[d], 0.5),
                    'p90': _pct(thr_dist_A[d], 0.90)}
                for d in common.DIMS},
            'union_fire_dimcount_distribution': dim_count_dist_A,
            'union_fires_zero_threshold_only': n_A_zero_only},
        'pool_variant_B_fpr_all_pairs_lep_matched': {
            'n': nB, 'fired': fprB,
            'fpr': (fprB / nB) if nB else None},
        'LEP_union_all_pairs_lep_matched': {
            'n': lep_eval, 'fired': lep_union,
            'union_rate': (lep_union / lep_eval) if lep_eval else None},
        'per_dimension_lep_fires': lep_dim_fires,
        'per_dimension_lep_fpr': {d: (lep_dim_fires[d] / lep_eval) if lep_eval else None for d in common.DIMS},
        'per_dimension_lep_zero_cells': lep_zero_cells,
        'per_dimension_b2b_fires_all_pairs_LOO': dim_fires_B,
        'per_dimension_b2b_zero_cells_LOO': zero_cells_B,
        'per_dimension_b2b_fires_all_pairs': dim_fires,
        'per_dimension_fpr': {d: (dim_fires[d] / n_eval) if n_eval else None
                              for d in common.DIMS},
        'union_fire_dimcount_distribution': dim_count_dist,
        'union_fires_zero_threshold_only': n_union_zero_only,
        'threshold_distribution_all_pairs': {
            d: {'n_zero': sum(1 for x in thr_dist[d] if x == 0),
                'n': len(thr_dist[d]),
                'mean': (sum(thr_dist[d]) / len(thr_dist[d])
                         if thr_dist[d] else None),
                'p50': _pct(thr_dist[d], 0.5), 'p90': _pct(thr_dist[d],
                                                            0.90)}
            for d in common.DIMS},
        'b2b_actions_tv_distribution': {
            'n': len(b2b_actions_tv),
            'mean': _mean(b2b_actions_tv),
            'frac_zero': (sum(1 for x in b2b_actions_tv if x == 0)
                          / len(b2b_actions_tv)) if b2b_actions_tv else None,
            'frac_below_0.05': (sum(1 for x in b2b_actions_tv
                                    if x < 0.05)
                                / len(b2b_actions_tv))
            if b2b_actions_tv else None,
            'p50': _pct(b2b_actions_tv, 0.5), 'p90': _pct(b2b_actions_tv,
                                                          0.90)},
    }
    common.write_json(out, 'diag_b2b.json')
    print(json.dumps({k: out[k] for k in
                      ('n_cells_evaluated',
                       'pool_variant_A_fpr_others_only',
                       'pool_variant_B_fpr_all_pairs_lep_matched',
                       'LEP_union_all_pairs_lep_matched',
                       'per_dimension_fpr',
                       'union_fire_dimcount_distribution',
                       'union_fires_zero_threshold_only')}, indent=1))
    return out


def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    i = int(math.ceil(q * len(s)) - 1)
    i = max(0, min(len(s) - 1, i))
    return s[i]


if __name__ == '__main__':
    import time
    t0 = time.time()
    run()
    print(f'done in {time.time() - t0:.0f}s')
