"""
Four-dimensional benign-threshold union (v6) — THE METHODOLOGY, UNCHANGED.

Deliberately there is NO weighted composite here.  Weighted composite scoring
was abandoned by design; the unused composite in the frozen module is obsolete
code and is NOT reintroduced.

DEFINITION
----------
For each execution e and each dimension d in {assertions, decisions, actions,
outcomes}:

    delta_d(e) = D_d(e) - D_d(benign reference)

    theta_d    = the benign-calibrated threshold for dimension d, estimated
                 ONLY from benign-to-benign variation:

                     theta_d = max over benign pairs of D_d(benign_i, benign_j)

    divergent_d(e) = 1  iff  delta_d(e) > theta_d
                          AND both D_d(e) and D_d(benign ref) are evaluable

    DIVERGENT(e) = OR over d of divergent_d(e)          <- the union

A dimension whose measurement is not evaluable (None) contributes NOT to the
union and is NOT counted as agreement.  This is requirement 6's
"do not silently treat missing dimensions as zero divergence": missing means
UNKNOWN, which is reported separately as coverage, never folded into 0.

MULTIPLE BENIGN REFERENCES
--------------------------
Three aggregations are supported; ``mean`` is the DEFAULT because it is the
one the FROZEN pipeline uses (``baseline_deviation._mean`` over
``pair_benign`` -> ``component_benign``).  Using anything else would make the
corrected-vs-historical comparison test two changes at once.

  * ``mean``  -- DEFAULT.  The LEP component distance is compared against the
                 MEAN of the LEP-vs-each-benign distances.  Matches the frozen
                 semantics exactly, so a difference in the final numbers is
                 attributable to the extraction corrections alone.
  * ``min``   -- conservative attribution: compare against the best-matching
                 benign reference.  A deviation then counts only if it exceeds
                 even the closest benign run.
  * ``median``-- robust to one outlier reference.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


def benign_thresholds(benign_pair_distances: Sequence[Dict[str, Optional[float]]]
                      ) -> Dict[str, Optional[float]]:
    """theta_d = max over benign pairs of the benign-to-benign distance.

    A dimension with NO evaluable benign pair gets theta_d = None, meaning the
    dimension is UNCALIBRATED for this cell and cannot yield a positive.
    """
    thr: Dict[str, Optional[float]] = {}
    for d in DIMS:
        vals = [p.get(d) for p in benign_pair_distances]
        vals = [v for v in vals if v is not None]
        thr[d] = max(vals) if vals else None
    return thr


def per_dimension(
        lep_dists: Dict[str, Optional[float]],
        benign_dists: Sequence[Dict[str, Optional[float]]],
        thresholds: Dict[str, Optional[float]],
        aggregation: str = 'mean') -> Dict[str, Dict[str, Any]]:
    """Per-dimension divergence with explicit attribution and coverage.

    ``benign_dists`` is one dict per benign reference (LEP-vs-that-reference).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for d in DIMS:
        thr = thresholds.get(d)
        dv = lep_dists.get(d)
        bvals = [b.get(d) for b in benign_dists]
        bvals = [v for v in bvals if v is not None]
        if dv is None or not bvals or thr is None:
            out[d] = {
                'evaluable': False,
                'threshold': thr,
                'lep_distance': dv,
                'benign_distances': bvals,
                'delta': None,
                'divergent': False,
                'reason': ('lep_not_evaluable' if dv is None
                           else 'no_benign_reference' if not bvals
                           else 'uncalibrated_dimension'),
            }
            continue
        if aggregation == 'min':
            ref = min(bvals)
            ref_kind = 'best_matching_min'
        elif aggregation == 'median':
            bvals_sorted = sorted(bvals)
            n = len(bvals_sorted)
            ref = (bvals_sorted[n // 2] if n % 2
                   else 0.5 * (bvals_sorted[n // 2 - 1] + bvals_sorted[n // 2]))
            ref_kind = 'median'
        else:
            ref = sum(bvals) / len(bvals)
            ref_kind = 'mean_frozen_equivalent'
        delta = dv - ref
        out[d] = {
            'evaluable': True,
            'threshold': thr,
            'lep_distance': dv,
            'benign_distances': bvals,
            'benign_reference_distance': ref,
            'benign_reference_kind': ref_kind,
            'delta': delta,
            'divergent': bool(delta > thr),
            'zero_threshold': (thr == 0.0),
        }
    return out


def union(per_dim: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Overall divergence = union of the four dimension indicators."""
    triggered = [d for d in DIMS if per_dim.get(d, {}).get('divergent')]
    evaluable = [d for d in DIMS if per_dim.get(d, {}).get('evaluable')]
    unknown = [d for d in DIMS if not per_dim.get(d, {}).get('evaluable')]
    zero_thr_trig = [d for d in triggered
                     if per_dim.get(d, {}).get('zero_threshold')]
    return {
        'divergent': bool(triggered),
        'triggered_dimensions': triggered,
        'evaluable_dimensions': evaluable,
        'unknown_dimensions': unknown,
        # transparency: which positives rest on a zero benign threshold
        'zero_threshold_triggered': zero_thr_trig,
        'n_triggered': len(triggered),
        'n_evaluable': len(evaluable),
    }


def summarise(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Corpus-level report: per-dimension rates, union rate, overlap, coverage.

    EXACTLY the required report shape (requirement 1):
      * divergence rate for each dimension
      * union divergence rate
      * overlap between dimensions
      * valid / unavailable measurements per dimension
      * which dimensions triggered each trace
    """
    from collections import Counter
    n = 0
    trigger = Counter()
    evaluable = Counter()
    unknown = Counter()
    combos = Counter()
    union_pos = 0
    zero_only = 0
    for r in rows:
        n += 1
        pd = r['per_dimension']
        u = r['union']
        for d in DIMS:
            if pd.get(d, {}).get('evaluable'):
                evaluable[d] += 1
            else:
                unknown[d] += 1
            if pd.get(d, {}).get('divergent'):
                trigger[d] += 1
        if u['divergent']:
            union_pos += 1
            combos[','.join(u['triggered_dimensions'])] += 1
            if len(u['zero_threshold_triggered']) == len(
                    u['triggered_dimensions']):
                zero_only += 1
    den = max(1, n)
    overlap = {}
    for i, a in enumerate(DIMS):
        for b in DIMS[i + 1:]:
            both = sum(1 for r in rows
                       if r['per_dimension'].get(a, {}).get('divergent')
                       and r['per_dimension'].get(b, {}).get('divergent'))
            overlap[f'{a}&{b}'] = both
    return {
        'n': n,
        'dimension_divergence_rate': {d: trigger[d] / den for d in DIMS},
        'dimension_divergence_count': {d: trigger[d] for d in DIMS},
        'dimension_evaluable_count': {d: evaluable[d] for d in DIMS},
        'dimension_unavailable_count': {d: unknown[d] for d in DIMS},
        'union_divergence_count': union_pos,
        'union_divergence_rate': union_pos / den,
        'union_zero_threshold_only_count': zero_only,
        'dimension_overlap_count': overlap,
        'trigger_combinations': dict(combos.most_common(20)),
    }
