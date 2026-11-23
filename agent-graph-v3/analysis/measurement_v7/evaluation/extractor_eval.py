"""
Extractor performance vs a reference label set (requirement 11).

Compares the FROZEN, V6, and V7 extractors (and their per-dimension
features) against a REFERENCE (human-adjudicated or AI-adjudicated; loaded
through :mod:`reference`, which gates on its existence).  Metrics per family
and per dimension:

  precision, recall, F1, false-positive rate, false-negative rate, coverage,
  exact agreement, and error categories.

DESIGN-WARED REPORTING (the sample's own rules):
  * the representative (A) and targeted (B) components are kept SEPARATE —
    B is outcome-enriched and its rates are conditional, never pooled into a
    population estimate;
  * where a partition is used (dev / heldout), the interval is a
    fixture-cluster bootstrap, not an n-based one;
  * performance against an AI reference is labelled AI-adjudicated, not
    human-validated (see reference.provenance_label).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence


def _to_states(labels: Any) -> Dict[str, str]:
    """Normalise a label set to {item_key: state}.  Accepts the annotation
    template shape ({field: {state, value, evidence}}) and a flat map."""
    out: Dict[str, str] = {}
    if not isinstance(labels, dict):
        return out
    for k, v in labels.items():
        if isinstance(v, dict) and 'state' in v:
            out[k] = str(v.get('state') or 'unavailable').lower()
        elif isinstance(v, str):
            out[k] = v.lower()
    return out


def _cmp_cell(ref_state: str, pred_state: str,
              ref_val: Any, pred_val: Any
              ) -> str:
    """One reference-vs-prediction cell in the extraction taxonomy.

    The taxonomy mirrors views.compare_views so that the same vocabulary the
    validation framework already uses is what a report reader sees:

      exact_match | value_differs | state_differs | both_unavailable
      | one_unavailable | ambiguous_on_one_side | fp_observed | fn_absent
    """
    if ref_state == 'unavailable' and pred_state == 'unavailable':
        return 'both_unavailable'
    if 'unavailable' in (ref_state, pred_state):
        return 'one_unavailable'
    if ref_state == 'absent' and pred_state == 'observed':
        return 'fp_observed'       # predicted a value the reference says is
                                    # genuinely absent
    if ref_state == 'observed' and pred_state == 'absent':
        return 'fn_absent'         # reference has a value; extractor said
                                    # not-discussed
    if ref_state != pred_state:
        return 'state_differs'
    if ref_val == pred_val or ref_val is None and pred_val is None:
        return 'exact_match'
    return 'value_differs'


def extractor_metrics(reference_rows: List[Dict[str, Any]],
                      pred_by_item: Dict[str, Dict[str, Any]],
                      fam_key: str = 'family',
                      component: Optional[str] = None
                      ) -> Dict[str, Any]:
    """Per-family metrics for one extractor.

    ``reference_rows``: [{item_token, family, component, labels:{...}}]
    ``pred_by_item``:   {item_token: {'states': {k: state},
                                     'values': {k: value}}}
    """
    fams = sorted({r.get(fam_key, 'unknown') for r in reference_rows})
    out: Dict[str, Any] = {'n_items': len(reference_rows),
                           'by_family': {}, 'overall': {}}
    for fam in fams + ['overall']:
        rows = reference_rows if fam == 'overall' else [
            r for r in reference_rows if r.get(fam_key) == fam]
        tally: Counter = Counter()
        n_judgeable = 0
        n_absent_tp = n_observed_pred = n_observed_ref = 0
        for r in rows:
            tok = r.get('item_token')
            ref = _to_states((r.get('labels') or {}))
            pred = (pred_by_item.get(tok) or {}).get('states') or {}
            ref_vals = (r.get('labels') or {})
            pred_vals = (pred_by_item.get(tok) or {}).get('values') or {}
            keys = set(ref) | set(pred)
            for k in keys:
                rs = ref.get(k, 'unavailable')
                ps = pred.get(k, 'unavailable')
                cell = _cmp_cell(rs, ps,
                                 ref_vals.get(k) if isinstance(
                                     ref_vals, dict) else None,
                                 pred_vals.get(k) if isinstance(
                                     pred_vals, dict) else None)
                tally[cell] += 1
                if rs in ('observed', 'absent') and ps in (
                        'observed', 'absent'):
                    n_judgeable += 1
                if rs == 'observed':
                    n_observed_ref += 1
                    if ps == 'observed':
                        n_absent_tp += 0
                        tally['observed_match_state'] = \
                            tally.get('observed_match_state', 0) + 0
                    if ps == 'observed':
                        tally['tp_state'] += 1
                if ps == 'observed':
                    n_observed_pred += 1
        # precision/recall over the OBSERVED call: did the extractor say
        # "observed" where the reference says observed?
        tp = tally.get('tp_state', 0)
        fp = tally.get('fp_observed', 0)
        fn = tally.get('fn_absent', 0)
        tn = tally.get('exact_match', 0)
        prec = (tp / (tp + fp)) if (tp + fp) else None
        rec_ = (tp / (tp + fn)) if (tp + fn) else None
        f1 = (2 * prec * rec_ / (prec + rec_)
               if prec is not None and rec_ is not None and
               (prec + rec_) else None)
        out['by_family' if fam != 'overall' else 'overall'][fam] = {
            'n': len(rows),
            'cell_tally': dict(tally),
            'n_judgeable': n_judgeable,
            'precision_observed_call': prec,
            'recall_observed_call': rec_,
            'f1_observed_call': f1,
            'false_positive_rate': (fp / (fp + tn)) if (fp + tn) else None,
            'false_negative_rate': (fn / (fn + tp)) if (fn + tp) else None,
            'coverage_observed_ref': n_observed_ref,
            'coverage_observed_pred': n_observed_pred,
            'note': ('rates are conditional on the reference call being '
                     'usable (observed/absent both sides); unavailable '
                     'references are excluded, never scored as negatives'),
        }
    return out


def divergence_metrics(reference_rows: List[Dict[str, Any]],
                       pred: Dict[str, Dict[str, Any]],
                       fam_key: str = 'family'
                       ) -> Dict[str, Any]:
    """Per-dimension + union divergence metrics against reference
    divergence labels.  ``reference_rows`` must carry, per item,
    ``ref_divergence = {dim: bool|None, union: bool|None}``; ``pred`` maps
    item_token -> {dim: bool|None, union: bool|None}.  None means the
    side was not evaluable: excluded from precision/recall, counted in
    coverage."""
    fams = sorted({r.get(fam_key, 'unknown') for r in reference_rows})
    out: Dict[str, Any] = {'n_items': len(reference_rows),
                           'by_family': {}}

    def stats(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, Any]:
        tp = fp = fn = tn = 0
        n_unavail = 0
        for r in rows:
            ref = (r.get('ref_divergence') or {}).get(key)
            pv = (pred.get(r.get('item_token')) or {}).get(key)
            if ref is None or pv is None:
                n_unavail += 1
                continue
            if ref and pv:
                tp += 1
            elif ref and not pv:
                fn += 1
            elif not ref and pv:
                fp += 1
            else:
                tn += 1
        den = max(1, tp + fp + fn + tn)
        return {
            'n_evaluable': tp + fp + fn + tn, 'n_unavailable': n_unavail,
            'precision': tp / max(1, tp + fp) if (tp + fp) else None,
            'recall': tp / max(1, tp + fn) if (tp + fn) else None,
            'f1': ((2 * (tp / max(1, tp + fp)) * (tp / max(1, tp + fn))) /
                    ((tp / max(1, tp + fp)) + (tp / max(1, tp + fn))))
                  if (tp + fp) and (tp + fn) else None,
            'false_positive_rate': fp / den,
            'false_negative_rate': fn / den,
            'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        }

    for fam in fams:
        rows = [r for r in reference_rows if r.get(fam_key) == fam]
        out['by_family'][fam] = {k: stats(rows, k) for k in
                                 ('assertions', 'decisions', 'actions',
                                  'outcomes', 'union')}
    out['overall'] = {k: stats(reference_rows, k) for k in
                      ('assertions', 'decisions', 'actions', 'outcomes',
                       'union')}
    return out
