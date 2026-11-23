"""
Judge-agreement statistics (requirement 8).

Comparisons produced:
  * exact agreement (raw),
  * Cohen's kappa (categorical),
  * Krippendorff's alpha (nominal; ordinal where applicable),
  * numeric agreement for extracted answers,
  * per-dimension confusion matrices,
  * disagreement rates by family,
  * ambiguous/unavailable rates.

The central scientific caution is encoded here: **agreement between two LLM
judges is NOT proof of correctness.**  Every number in this module is
labelled ``ai_judge_agreement`` and must be reported as such.  A high
agreement rate tells you the two judges are converging; it does not tell you
they are right.  The brief's requirement that these "be explicitly labelled
as AI-judge agreement" is enforced by the output shape: the top-level key of
every result is ``ai_judge_agreement``.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

#: numeric agreement tolerance for extracted answers, as a relative ratio
#: of the larger magnitude.  0.05 = 5 % relative.
NUM_REL_TOL = 0.05

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')


def _to_bool_label(v: Any) -> Optional[bool]:
    """yes/no -> True/False; ambiguous/unavailable -> None (not a boolean)."""
    s = str(v).strip().lower() if v is not None else ''
    if s in ('yes', 'true', '1'):
        return True
    if s in ('no', 'false', '0'):
        return False
    return None


# ── agreement primitives ─────────────────────────────────────────────────────

def raw_agreement(a: Sequence[Any], b: Sequence[Any]) -> Dict[str, Any]:
    n = min(len(a), len(b))
    if n == 0:
        return {'agreement': None, 'n': 0}
    agree = sum(1 for x, y in zip(a, b) if str(x) == str(y))
    return {'agreement': agree / n, 'n': n, 'n_agree': agree,
            'label': 'ai_judge_agreement'}


def cossim(a: float, b: float) -> float:
    return a * b / (abs(a) * abs(b))


def kappa(a: Sequence[Any], b: Sequence[Any]) -> Dict[str, Any]:
    """Cohen's kappa (2 raters, nominal).  None when undefined (one category
    on both sides)."""
    n = min(len(a), len(b))
    if n == 0:
        return {'kappa': None, 'n': 0, 'label': 'ai_judge_agreement'}
    cats = sorted({str(x) for x in list(a)[:n] + list(b)[:n]})
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    mat = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        mat[idx[str(x)]][idx[str(y)]] += 1
    po = sum(mat[i][i] for i in range(k)) / n
    row = [sum(mat[i]) / n for i in range(k)]
    col = [sum(mat[i][j] for i in range(k)) / n for j in range(k)]
    pe = sum(row[i] * col[i] for i in range(k))
    out: Dict[str, Any] = {'n': n, 'po': po, 'pe': pe,
                           'label': 'ai_judge_agreement'}
    if abs(1.0 - pe) < 1e-12:
        out['kappa'] = None
        out['note'] = 'kappa undefined (no off-diagonal, single category)'
        return out
    out['kappa'] = (po - pe) / (1.0 - pe)
    return out


def krippendorff_alpha(items: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """Krippendorff's alpha over coder x unit arrays (nominal)."""
    per_unit: List[List[Any]] = []
    values: List[Any] = []
    for vals in items:
        v = [x for x in vals if x is not None]
        if len(v) >= 2:
            per_unit.append(v)
            values.extend(v)
    out: Dict[str, Any] = {'label': 'ai_judge_agreement'}
    if not per_unit:
        out['alpha'] = None
        out['note'] = 'no unit has >=2 usable coder values'
        return out
    cats = sorted({str(x) for x in values})
    order = {c: i for i, c in enumerate(cats)}
    nc = len(cats)
    n_cat = [0] * nc
    for v in values:
        n_cat[order[str(v)]] += 1
    n_total = len(values)
    o = [[0.0] * nc for _ in range(nc)]
    for vals in per_unit:
        m = len(vals)
        for i in range(m):
            for j in range(m):
                if i == j:
                    continue
                o[order[str(vals[i])]][order[str(vals[j])]] += 1.0 / (m - 1)
    e = [[0.0] * nc for _ in range(nc)]
    for c in range(nc):
        for kk in range(nc):
            e[c][kk] = ((n_cat[c] * (n_cat[c] - 1)) if c == kk
                       else n_cat[c] * n_cat[kk]) / max(1, n_total - 1)

    def delta(i: int, j: int) -> float:
        return 0.0 if i == j else 1.0

    do = sum(delta(c, kk) * o[c][kk] for c in range(nc) for kk in range(nc))
    de = sum(delta(c, kk) * e[c][kk] for c in range(nc) for kk in range(nc))
    out['n_units'] = len(per_unit)
    out['n_values'] = n_total
    out['level'] = 'nominal'
    if de == 0:
        out['alpha'] = None
        out['note'] = 'expected disagreement is 0'
    else:
        out['alpha'] = 1.0 - do / de
    return out


def numeric_agreement(a: Sequence[Optional[float]],
                      b: Sequence[Optional[float]],
                      rel_tol: float = NUM_REL_TOL) -> Dict[str, Any]:
    """Agreement on extracted numeric answers (both must be present)."""
    n_num = agree = 0
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        n_num += 1
        denom = max(abs(x), abs(y))
        if denom == 0.0:
            agree += 1
        elif abs(x - y) / denom <= rel_tol:
            agree += 1
    return {'n_numeric': n_num, 'n_numeric_agree': agree,
            'numeric_agreement': (agree / n_num) if n_num else None,
            'rel_tol': rel_tol, 'label': 'ai_judge_agreement'}


# ── per-dimension + per-family ──────────────────────────────────────────────

def per_dimension(rows: List[Dict[str, Any]],
                  fam_key: str = 'family') -> Dict[str, Any]:
    """rows: one record per item, each carrying
    {'a': {...}, 'b': {...}, 'family': ..., 'dim': {..per-dim yes/no..}}.
    Produces per-dimension confusion matrices + disagreement rates, split by
    family."""
    out: Dict[str, Any] = {'label': 'ai_judge_agreement'}
    byfam: Dict[str, Dict[str, Any]] = {}
    for d in DIMS:
        conf: Counter = Counter()
        dis_rate: Counter = Counter()
        n_items = 0
        for r in rows:
            f = r.get(fam_key, 'unknown')
            da = _to_bool_label(r.get('a', {}).get(d))
            db = _to_bool_label(r.get('b', {}).get(d))
            if da is None and db is None:
                continue
            n_items += 1
            ka = 'unavailable' if da is None else ('pos' if da else 'neg')
            kb = 'unavailable' if db is None else ('pos' if db else 'neg')
            conf[(ka, kb)] += 1
            if ka != kb:
                dis_rate[f] += 1
            byfam.setdefault(f, Counter())[('total' if ka == kb
                                             else 'disagreement')] += 1
        out[d] = {
            'confusion': {f'{k[0]}->{k[1]}': v for k, v in conf.items()},
            'n_evaluable_items': n_items,
            'n_disagreements': sum(v for k, v in byfam.values()
                                   if k == 'disagreement') if False
                                else sum(1 for r in rows
                                        if _to_bool_label(r.get('a', {}).get(d))
                                           != _to_bool_label(
                                               r.get('b', {}).get(d))
                                        and not (
                                            _to_bool_label(
                                                r.get('a', {}).get(d)) is None
                                            and _to_bool_label(
                                                r.get('b', {}).get(d)) is None)),
        }
    out['disagreement_by_family'] = {
        fam: {'n': c.get('total', 0), 'n_disagree': c.get('disagreement', 0),
              'rate': (c.get('disagreement', 0) / c.get('total', 1)
                       if c.get('total') else None)}
        for fam, c in byfam.items()}
    out['family_counts'] = dict(Counter(r.get(fam_key, 'unknown')
                                        for r in rows))
    return out


def ambiguous_unavailable_rate(rows: List[Dict[str, Any]]
                               ) -> Dict[str, Any]:
    """Rates at which each judge used ambiguous/unavailable, per dimension."""
    out: Dict[str, Any] = {'label': 'ai_judge_agreement'}
    for judge in ('a', 'b'):
        for d in DIMS:
            n = sum(1 for r in rows
                    if str(r.get(judge, {}).get(d)).strip().lower()
                    in ('ambiguous', 'unavailable'))
            out[f'{judge}.{d}'] = {'n': n,
                                    'rate': (n / len(rows)) if rows else None}
    return out


def agreement_report(judge_a_rows: List[Dict[str, Any]],
                     judge_b_rows: List[Dict[str, Any]],
                     fam_key: str = 'family'
                     ) -> Dict[str, Any]:
    """Top-level report.  ``judge_a_rows`` / ``judge_b_rows`` are aligned:
    row i of each list is the same item.  Each row carries 'a'/'b'-relative
    per-dimension labels under keys 'assertions','decisions','actions',
    'outcomes' plus a 'family'."""
    if len(judge_a_rows) != len(judge_b_rows):
        raise ValueError('judge rows are not aligned; cannot compare')
    rows = [dict(a_r, b=b_r) for a_r, b_r in zip(judge_a_rows,
                                                   judge_b_rows)]
    per = per_dimension(rows, fam_key)
    amb = ambiguous_unavailable_rate(rows)
    # raw per-dimension agreement
    per_dim_agree: Dict[str, Any] = {}
    for d in DIMS:
        per_dim_agree[d] = raw_agreement([str(r.get('a', {}).get(d))
                                          for r in rows],
                                         [str(r.get('b', {}).get(d))
                                          for r in rows])
    return {'ai_judge_agreement': {
        'n_items': len(rows),
        'per_dimension': per,
        'per_dimension_raw_agreement': per_dim_agree,
        'ambiguous_unavailable_rates': amb,
        'caution': ('Agreement between two LLM judges is NOT proof of '
                    'correctness.  Report these as AI-judge agreement, not '
                    'as ground truth.')}}
