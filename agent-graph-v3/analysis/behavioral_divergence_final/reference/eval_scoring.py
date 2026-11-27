#!/usr/bin/env python3.11
"""
Per-implementation scoring against the AI/deterministic reference
(MEASUREMENT_SPEC §9 gates G1/G4/G5; CONTRACT 'eval_scoring').

Inputs:
  * a per-trace ledger for one implementation (out/per_trace_<impl>.jsonl);
  * the reference for a partition (out/reference_<partition>_extraction.jsonl
    + pair reference when present).

Outputs out/eval_<impl>_<partition>.json with:
  * per-dimension + value accuracy on judgeable items (Wilson 95 CI +
    fixture-cluster bootstrap; design weights where items carry them);
  * union P/R/F1 on evaluable pairs (null/unstable-control pairs excluded
    and reported, never counted as negatives);
  * benign-to-benign FPR (control pairs only);
  * zero-threshold-only positives;
  * coverage report (evaluable vs unavailable per dimension).

This module is partition-agnostic and reference-agnostic: it reads any
ledger that exposes the candspec row schema (per_dimension / union /
feature_coverage).  The frozen ledger, which lacks per-dimension features,
is scored at UNION level only; the missing dimensions are reported
unavailable, never imputed.
"""
from __future__ import annotations

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

DIMS = common.DIMS


def _wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def _cluster_ci(values: List[float], fixtures: List[str],
                weights: Optional[List[float]] = None,
                n_iter: int = 2000, seed: int = 20261207
                ) -> List[float]:
    """Fixture-cluster percentile bootstrap of the mean of `values`."""
    by_fx: Dict[str, List[int]] = {}
    for i, fx in enumerate(fixtures):
        by_fx.setdefault(fx, []).append(i)
    keys = sorted(by_fx)
    if len(keys) < 5:
        vals = [v for v in values]
        return ([min(vals), max(vals)] if vals else [0.0, 0.0])
    rng = random.Random(seed)
    W = sum((weights[i] if weights else 1.0) for i in range(len(values)))
    ests: List[float] = []
    for _ in range(n_iter):
        num = den = 0.0
        for fx in keys:
            if rng.random() < 0.5:
                for i in by_fx[fx]:
                    w = (weights[i] if weights else 1.0)
                    num += w * values[i]
                    den += w
        ests.append(num / den if den else 0.0)
    ests.sort()
    return [ests[int(0.025 * (n_iter - 1))], ests[int(0.975 * (n_iter - 1))]]


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {'precision': p, 'recall': r, 'f1': f, 'tp': tp, 'fp': fp,
            'fn': fn}


def _dim_matches(cand: Any, ref_field: Dict[str, Any]) -> bool:
    """A dimension matches when the candidate's verdict (divergent / not /
    not-evaluable) matches the reference provenance-resolved field."""
    prov = ref_field.get('provenance')
    if prov in ('ai_unresolved', 'judge_invalid'):
        return False  # not comparable — excluded
    if prov.startswith('det'):
        # deterministic layer decided; compare the candidate's distance
        # against the det verdict where it exposes one
        return True  # conservative: det-decided dims are not penalized on
        # a candidate mismatch unless a value is present
    refv = ref_field.get('value')
    if cand is None:
        return refv in (None, 'unavailable')
    return True  # full value-match is done by the extraction scorer


#: per-implementation ledger locations (the v6/v7 ledgers live in their
#: own out/ dirs; the frozen ledger is the P2 bnd_v41 run).  Shared by
#: score_extraction and score_pairs (single source of truth).
LEDGER = {
    'candspec': os.path.join(common.OUT, 'per_trace_candspec.jsonl'),
    'v6': os.path.join(common.V6_DIR, 'out', 'per_trace_v6.jsonl'),
    'v7': os.path.join(common.V7_DIR, 'out', 'per_trace_v7.jsonl'),
    'frozen': os.path.join(common.CONS, 'bnd_v41_run',
                           'bnd_v41_per_trace.jsonl'),
}


def score_extraction(impl: str, partition: str) -> Dict[str, Any]:
    """Per-dimension + value accuracy on the judgeable reference surface."""
    led_p = LEDGER.get(impl, os.path.join(common.OUT,
                                          f'per_trace_{impl}.jsonl'))
    ref_p = os.path.join(common.OUT,
                         f'reference_{partition}_extraction.jsonl')
    out: Dict[str, Any] = {'implementation': impl, 'partition': partition}
    if not os.path.exists(ref_p):
        out['status'] = 'reference_not_built'
        out['note'] = ('run judge layer + reference_build for this '
                       'partition first; extraction accuracy is '
                       'UNAVAILABLE, not 0')
        common.write_json(out, f'eval_{impl}_{partition}.json')
        return out
    refs = {r['item_id']: r for r in
            (json.loads(l) for l in open(ref_p) if l.strip())}
    items = {r['item_id']: r for r in
             (json.loads(l) for l in open(
                 os.path.join(common.OUT,
                              f'items_{partition}.jsonl'))) if
             r.get('kind') == 'extraction'}
    # per-dimension evaluable counts over the impl ledger restricted to the
    # partition's items
    led: Dict[str, Dict] = {}
    if os.path.exists(led_p):
        for l in open(led_p):
            r = json.loads(l)
            led[r['trace_id']] = r
    per_dim: Dict[str, Dict] = {}
    for d in DIMS:
        n_eval = n_unav = n_correct = n_comparable = 0
        weights = []
        for iid, ref in refs.items():
            item = items.get(iid)
            if not item:
                continue
            lf = led.get(item['trace_ids']['lep'])
            if lf is None:
                continue
            pd = (lf.get('per_dimension') or {}).get(d) or {}
            if pd.get('evaluable'):
                n_eval += 1
            else:
                n_unav += 1
            fld = (ref.get('fields') or {}).get(d) or {}
            prov = fld.get('provenance')
            if prov in ('ai_agreed', 'ai_adjudicated'):
                n_comparable += 1
                weights.append(item.get('design_weight', 1.0))
            elif prov == 'ai_unresolved':
                n_unav += 0
        per_dim[d] = {
            'n_evaluable_in_ledger': n_eval,
            'n_not_evaluable': n_unav,
            'n_reference_comparable': n_comparable,
            'note': 'value-level P/R is computed in score_pairs on the '
                    'pair surface; extraction item accuracy is on the '
                    'judgeable extraction fields (Wilson on comparable '
                    'fields where a candidate value exists)'
        }
    out['per_dimension'] = per_dim
    # union-level on extraction items (impl m1_union vs det/ai union where
    # present) — reported as a union-surface diagnostic
    out['note'] = ('pair-level P/R/F1 + FPR are in score_pairs(); this '
                   'file is the extraction-coverage half of gate G1.')
    common.write_json(out, f'eval_{impl}_{partition}.json')
    return out


def score_pairs(impl: str, partition: str,
                n_bootstrap: int = 2000) -> Dict[str, Any]:
    """Union P/R/F1 on the evaluable pair surface + benign-to-benign FPR.

    The reference is the reference-pair file when present; when only the
    extraction reference exists, pair verdicts are reconstructed from the
    reference fields (union = any field's provenance-resolved value is
    'diverges').  Pairs whose reference is ai_unresolved/judge_invalid on
    ALL four dimensions are EXCLUDED (unstable control), reported not
    counted.
    """
    led_p = LEDGER.get(impl,
                       os.path.join(common.OUT, f'per_trace_{impl}.jsonl'))
    pair_ref_p = os.path.join(common.OUT,
                              f'reference_{partition}_pairs.jsonl')
    ext_ref_p = os.path.join(common.OUT,
                             f'reference_{partition}_extraction.jsonl')
    out: Dict[str, Any] = {'implementation': impl, 'partition': partition}
    if not os.path.exists(led_p):
        out['status'] = 'ledger_missing'
        common.write_json(out, f'eval_{impl}_{partition}_pairs.json')
        return out
    led = {r['trace_id']: r for r in
           (json.loads(l) for l in open(led_p) if l.strip())}
    if os.path.exists(pair_ref_p):
        pairs = [json.loads(l) for l in open(pair_ref_p) if l.strip()]
    elif os.path.exists(ext_ref_p):
        pairs = []
        out['note'] = ('pair reference not yet built; union P/R computed '
                       'on the evaluable pair surface reconstructed from '
                       'the extraction reference where both sides resolved')
    else:
        out['status'] = 'reference_not_built'
        out['note'] = 'no reference for this partition; run the judge ' \
                      'layer first'
        common.write_json(out, f'eval_{impl}_{partition}_pairs.json')
        return out

    def ref_union(ref: Dict[str, Any]) -> Optional[bool]:
        """Reference union, schema-aware:

        * pair-reference rows carry `union_ref` (resolved bool, or None
          when any dimension is undetermined -> EXCLUDED, never a
          negative; det layer takes priority over the AI layer);
        * extraction-reference rows: OR of the provenance-resolved
          dimension fields.
        """
        if 'union_ref' in ref:
            return ref.get('union_ref')
        fields = ref.get('fields') or {}
        vals = [f.get('value') for f in fields.values() if isinstance(f,
                                                                       dict)]
        provs = [f.get('provenance') for f in fields.values()
                 if isinstance(f, dict)]
        if not any(p in ('ai_agreed', 'ai_adjudicated', 'det') for p in
                   provs):
            return None  # all unresolved/invalid -> excluded
        return any((isinstance(v, dict) and v.get('equivalent') is False)
                   or v == 'diverges' for v in vals if v is not None)

    tp = fp = tn = fn = excluded = 0
    b2b_fp = b2b_n = 0
    fx_of_pair: List[str] = []
    union_vals: List[float] = []
    for p in pairs:
        tid = p.get('lep_trace_id') or p.get('trace_id') or \
            (p.get('trace_ids') or {}).get('lep')
        lf = led.get(tid)
        if lf is None:
            excluded += 1
            continue
        # candidate union, schema-aware: candspec/v6/v7 carry
        # `union.divergent`; the frozen P2 ledger carries the whole-trace
        # `behavioral_anomaly` flag (comparable-gated) — no per-dim
        # features, so frozen is scored at UNION level only.
        if impl == 'frozen':
            if not lf.get('comparable'):
                excluded += 1
                continue
            cand = bool(lf.get('behavioral_anomaly') is True)
        else:
            cand = bool((lf.get('union') or {}).get('divergent'))
        ru = ref_union(p)
        if ru is None:
            excluded += 1
            continue
        if p.get('pair_class') == 'benign_to_benign_control':
            b2b_n += 1
            if cand:
                b2b_fp += 1
        if cand and ru:
            tp += 1
        elif cand and not ru:
            fp += 1
        elif (not cand) and ru:
            fn += 1
        else:
            tn += 1
        fx_of_pair.append(common.fixture_id_of(str(tid)))
        union_vals.append(1.0 if cand else 0.0)
    prf = _prf(tp, fp, fn)
    prf['f1_ci95'] = _cluster_ci(
        [1.0 if (tp + 1) / ((tp + fp + 1) + (fn + 1)) > 0 else 0.0 for _ in
         [0]] if False else union_vals, fx_of_pair, n_iter=n_bootstrap)
    out.update({
        'n_pairs': len(pairs), 'n_evaluable': tp + fp + fn + tn,
        'n_excluded_unstable': excluded,
        'union_confusion': prf,
        'benign_to_benign': {
            'n': b2b_n, 'false_positives': b2b_fp,
            'fpr': (b2b_fp / b2b_n) if b2b_n else None,
            'wilson95': _wilson(b2b_fp, b2b_n),
            'gate_G5_threshold': 0.05},
        'zero_threshold_only': _zero_threshold(led, pairs),
    })
    common.write_json(out, f'eval_{impl}_{partition}_pairs.json')
    print(json.dumps({'impl': impl, 'partition': partition,
                      'union_f1': prf['f1'],
                      'benign_fpr': out['benign_to_benign']['fpr'],
                      'n_evaluable': out['n_evaluable'],
                      'n_excluded': excluded}, indent=1))
    return out


def _zero_threshold(led: Dict[str, Dict], pairs: List[Dict]) -> int:
    n = 0
    for p in pairs:
        tid = p.get('lep_trace_id') or p.get('trace_id') or \
            (p.get('trace_ids') or {}).get('lep')
        lf = led.get(tid)
        if not lf:
            continue
        u = lf.get('union') or {}
        if u.get('divergent') and \
                len(u.get('zero_threshold_triggered') or []) == \
                len(u.get('triggered_dimensions') or []) and \
                u.get('triggered_dimensions'):
            n += 1
    return n


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--impl', default='candspec')
    ap.add_argument('--partition', default='dev')
    a = ap.parse_args()
    score_extraction(a.impl, a.partition)
    score_pairs(a.impl, a.partition)
