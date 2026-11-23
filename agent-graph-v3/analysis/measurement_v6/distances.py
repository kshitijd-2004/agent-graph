"""
Distance kernels (v6).

METHODOLOGY PRESERVED: no weights, no averaging across dimensions.  Each
dimension produces its OWN distance in [0,1] (or None when not evaluable).

REQUIREMENT 6: "Do not silently treat missing dimensions as zero divergence."
Enforced here -- every kernel returns ``None`` when the comparison cannot be
made, and ``union.py`` propagates None as NOT EVALUABLE.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from extract_core import AMBIGUOUS, OBSERVED, UNAVAILABLE


def clamp01(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:            # NaN
        return None
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def rel_diff(a: float, b: float) -> float:
    """Symmetric relative difference, bounded [0,1] by construction."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return 1.0
    denom = max(abs(a), abs(b))
    if denom == 0.0:
        return 0.0
    return min(1.0, abs(a - b) / denom)


def categorical(a: Any, b: Any) -> float:
    return 0.0 if a == b else 1.0


# ── map distance (assertions / decisions) ────────────────────────────────────
def map_distance(a: Dict[str, Any], b: Dict[str, Any],
                 numeric: bool = False) -> Tuple[Optional[float],
                                                 Dict[str, Any]]:
    """Mean per-key distance over keys comparable in BOTH maps.

    Rules (deliberately conservative):
      observed vs observed  -> value distance (numeric or categorical)
      absent   vs absent    -> 0.0        (both agree: not discussed)
      observed vs absent    -> 1.0        (a real behavioural difference)
      ambiguous / unavailable on EITHER side -> key is NOT COMPARABLE

    When NO key is comparable the whole dimension is None (not evaluable).
    This is the fix for the audit's "missing dimension scored as zero" risk.
    """
    keys = sorted(set(a) | set(b))
    dists = []
    detail = {}
    for k in keys:
        fa, fb = a.get(k), b.get(k)
        sa = getattr(fa, 'state', None)
        sb = getattr(fb, 'state', None)
        if sa in (AMBIGUOUS, UNAVAILABLE) or sb in (AMBIGUOUS, UNAVAILABLE):
            detail[k] = {'state_a': sa, 'state_b': sb, 'd': None,
                         'reason': 'not_comparable'}
            continue
        if sa is None and sb is None:
            continue
        if sa == OBSERVED and sb == OBSERVED:
            d = (rel_diff(fa.value, fb.value) if numeric
                 else categorical(fa.value, fb.value))
        elif sa == 'absent' and sb == 'absent':
            d = 0.0
        elif (sa == OBSERVED and sb == 'absent') or (sa == 'absent'
                                                     and sb == OBSERVED):
            d = 1.0
        else:
            detail[k] = {'state_a': sa, 'state_b': sb, 'd': None,
                         'reason': 'unhandled_state'}
            continue
        dists.append(d)
        detail[k] = {'state_a': sa, 'state_b': sb, 'd': d}
    if not dists:
        return None, detail
    return clamp01(sum(dists) / len(dists)), detail


# ── action distance (total variation) ────────────────────────────────────────
def total_variation(a: Dict[Any, float], b: Dict[Any, float]
                    ) -> Optional[float]:
    """(1/2) * sum_k |a_k/|a| - b_k/|b||

    Bounded [0,1], symmetric, order-invariant.

    DOCUMENTED LIMITATIONS (requirement 4):
      * scale-invariant: {x:1} vs {x:20} = 0.0 -- a retry storm is invisible.
      * order discarded entirely.
      * ``tool_error`` is never populated in this corpus, so failed calls are
        counted identically to successful ones.
    """
    sa, sb = sum(a.values()), sum(b.values())
    if sa <= 0 and sb <= 0:
        return None            # neither trace used any tool -> not evaluable
    if sa <= 0 or sb <= 0:
        return 1.0             # one used tools, the other did not
    keys = set(a) | set(b)
    return clamp01(0.5 * sum(abs(a.get(k, 0) / sa - b.get(k, 0) / sb)
                             for k in keys))


# ── outcome distance ─────────────────────────────────────────────────────────
def outcome_distance_financial(grade_a: str, grade_b: str,
                               ans_a: Optional[float],
                               ans_b: Optional[float]) -> Optional[float]:
    """Task-correctness distance for FinQA.

    correct vs incorrect -> 1.0 ; same grade -> graded numeric gap
    (so two wrong answers of different magnitude are not treated as identical).
    Returns None when either side is ungradeable.
    """
    if grade_a == 'ungradeable' or grade_b == 'ungradeable':
        return None
    if grade_a != grade_b:
        return 1.0
    if grade_a == 'correct':
        return 0.0
    if ans_a is None or ans_b is None:
        return 0.0
    return clamp01(rel_diff(ans_a, ans_b))


def outcome_distance_research(grade_a: str, grade_b: str) -> Optional[float]:
    if grade_a == 'ungradeable' or grade_b == 'ungradeable':
        return None
    return 0.0 if grade_a == grade_b else 1.0


def outcome_distance_code(grade_a: str, grade_b: str,
                          id_a: Optional[int], id_b: Optional[int],
                          n_issues: int) -> Optional[float]:
    """Task-correctness distance for code review, graded on DEFECT
    IDENTIFICATION (not path overlap).

    Two correct outcomes -> 0.0 (even if the file sets differ).  This is the
    requirement-3 correction: identical file sets with different code changes
    previously received 0.0, and superficial file-set changes could produce
    large distances without meaningful behavioural difference.
    """
    if grade_a == 'ungradeable' or grade_b == 'ungradeable':
        return None
    if grade_a != grade_b:
        return 1.0
    if grade_a == 'correct':
        return 0.0
    if id_a is None or id_b is None or not n_issues:
        return 0.0
    return clamp01(abs(id_a - id_b) / float(n_issues))


def jaccard_distance(a: set, b: set) -> Optional[float]:
    """Retained as a DIAGNOSTIC only (not the primary code outcome)."""
    if not a and not b:
        return None
    union = a | b
    if not union:
        return None
    return clamp01(1.0 - len(a & b) / len(union))
