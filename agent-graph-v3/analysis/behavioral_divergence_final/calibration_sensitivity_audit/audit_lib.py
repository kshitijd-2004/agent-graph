#!/usr/bin/env python3.11
"""Shared loaders + helpers for the calibration sensitivity audit.

Read-only over all inputs; writes nothing (the audit scripts write).
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.abspath(HERE)
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')
V6 = os.path.join(REPO, 'analysis', 'measurement_v6')
CONS = os.path.join(REPO, 'analysis', 'consolidated_20261121_bnd_v41')
V6_LEDGER = os.path.join(V6, 'out', 'per_trace_v6.jsonl')
DOWNSTREAM = os.path.join(CONS, 'downstream_results', 'per_trace.jsonl')
FROZEN = os.path.join(CONS, 'bnd_v41_run', 'bnd_v41_per_trace.jsonl')
CALLEDGER = os.path.join(CALP, 'out', 'ledger_v6_calibrated.jsonl')
REFPAIRS = os.path.join(FINAL, 'out', 'reference_heldout_pairs.jsonl')
ITEMS = os.path.join(FINAL, 'out', 'items_heldout.jsonl')
METRICS = os.path.join(CALP, 'out', 'metrics_core.json')
DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


def _jsonl(p: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(p) if l.strip()]


def _by(p: str) -> Dict[str, Dict[str, Any]]:
    return {json.loads(l)['trace_id']: json.loads(l)
            for l in open(p) if l.strip()}


_cache: Dict[str, Any] = {}


def cal() -> List[Dict[str, Any]]:
    if 'cal' not in _cache:
        _cache['cal'] = _jsonl(CALLEDGER)
    return _cache['cal']


def cal_by() -> Dict[str, Dict[str, Any]]:
    if 'cal_by' not in _cache:
        _cache['cal_by'] = {r['trace_id']: r for r in cal()}
    return _cache['cal_by']


def v6_by() -> Dict[str, Dict[str, Any]]:
    if 'v6' not in _cache:
        _cache['v6'] = _by(V6_LEDGER)
    return _cache['v6']


def ds_by() -> Dict[str, Dict[str, Any]]:
    if 'ds' not in _cache:
        _cache['ds'] = _by(DOWNSTREAM)
    return _cache['ds']


def frozen_by() -> Dict[str, Dict[str, Any]]:
    if 'fr' not in _cache:
        _cache['fr'] = _by(FROZEN)
    return _cache['fr']


def ref_pairs() -> List[Dict[str, Any]]:
    if 'rp' not in _cache:
        _cache['rp'] = _jsonl(REFPAIRS)
    return _cache['rp']


def load_specs() -> Dict[str, Dict[str, Any]]:
    import sys as _sys
    if FINAL not in _sys.path:
        _sys.path.insert(0, FINAL)
    import common  # analysis/behavioral_divergence_final/common.py
    return common.load_specs()


def load_trace_index() -> Dict[str, str]:
    """tid -> path (the fields the extraction path needs)."""
    if 'ti' not in _cache:
        p = os.path.join(REPO, 'analysis', 'measurement_validation',
                         '.cache', 'trace_index.jsonl')
        _cache['ti'] = {json.loads(l)['trace_id']: json.loads(l)['path']
                        for l in open(p) if l.strip()}
    return _cache['ti']


def load_trace_index_full() -> Dict[str, Dict[str, Any]]:
    """tid -> full index record (carries task_family, condition, ...)."""
    if 'tif' not in _cache:
        p = os.path.join(REPO, 'analysis', 'measurement_validation',
                         '.cache', 'trace_index.jsonl')
        _cache['tif'] = {json.loads(l)['trace_id']: json.loads(l)
                         for l in open(p) if l.strip()}
    return _cache['tif']


def items() -> Dict[str, Dict[str, Any]]:
    if 'it' not in _cache:
        _cache['it'] = {json.loads(l)['item_id']: json.loads(l)
                        for l in open(ITEMS) if l.strip()}
    return _cache['it']


def metrics() -> Dict[str, Any]:
    if 'm' not in _cache:
        _cache['m'] = json.load(open(METRICS))
    return _cache['m']


def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def confusion(pos_pred: List[bool], ref: List[Optional[bool]]
              ) -> Dict[str, Any]:
    """Confusion matrix + rates against an optional reference label.

    ref[i] None = unresolved/excluded -> reported in `excluded`, never
    counted as a negative.  Denominators always named.
    """
    tp = fp = tn = fn = excl = 0
    for p, r in zip(pos_pred, ref):
        if r is None:
            excl += 1
            continue
        if p and r:
            tp += 1
        elif p:
            fp += 1
        elif r:
            fn += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    p = tp / (tp + fp) if (tp + fp) else None
    r = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * p * r / (p + r) if (p is not None and r is not None and p + r) \
        else None
    spec = tn / (tn + fp) if (tn + fp) else None
    bal = (0.5 * (r or 0) + 0.5 * (spec or 0)) if \
        not (r is None and spec is None) else None
    return {'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn,
            'excluded_unresolved': excl, 'n_evaluable': n,
            'reference_coverage': (n / (n + excl)) if (n + excl) else None,
            'precision': p, 'recall': r, 'f1': f1, 'specificity': spec,
            'balanced_accuracy': bal,
            'recall_wilson95': wilson(tp, tp + fn),
            'precision_wilson95': wilson(tp, tp + fp),
            'denominators': {'precision': tp + fp, 'recall': tp + fn,
                             'specificity': tn + fp,
                             'balanced': n + excl}}
