"""
M4 corrections (v6): benign-matched degradation.

REQUIREMENT 5.  Two metrics are reported side by side, never conflated:

  M4 (historical, retained for comparability)
      M3-supported AND LEP task grade == 'failure'
      This does NOT establish that the perturbation made the outcome worse.

  M4-matched (PRIMARY)
      M3-supported AND LEP grade == 'failure'
      AND at least one COMPATIBLE benign reference grade == 'correct'
      A benign success demonstrates the task IS solvable in that
      configuration; a LEP failure against that background is stronger
      evidence of perturbation-ASSOCIATED degradation.

CAUSALITY CAVEAT (stated wherever M4-matched is reported): matched degradation
is stronger evidence, not proof.  The benign references are separate
executions, not counterfactuals of the same run.

FIVE-WAY CLASSIFICATION (requirement 5) per M3-supported LEP row:
    degraded            LEP failure, >=1 gradeable benign correct
    persistent_failure  LEP failure, all gradeable benign failure
    persistent_correct  LEP correct, >=1 gradeable benign correct
    improved            LEP correct, all gradeable benign failure
    mixed_or_unavailable  contradictory benign grades that preclude a clean
                          label, or no gradeable benign reference at all

Aggregation over multiple benign references is explicit (requirement 5):
    degraded  uses ANY-correct  -- one benign success suffices to show
                                   solvability (the conservative rule).
    persistent_failure requires ALL-gradeable-failure, so a single benign
                                   success reclassifies the row as degraded
                                   rather than being averaged away.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

DEGRADED = 'degraded'
PERSISTENT_FAILURE = 'persistent_failure'
PERSISTENT_CORRECT = 'persistent_correct'
IMPROVED = 'improved'
MIXED_UNAVAILABLE = 'mixed_or_unavailable'

CORRECT = 'correct'
FAILURE = 'failure'
UNGRADEABLE = 'ungradeable'

#: ``graders.py`` emits 'incorrect'; the frozen downstream artifacts use
#: 'failure'.  Both mean "the task's deliverable was wrong", so they are
#: normalised here at the boundary rather than forcing every caller to pick one
#: vocabulary.  Without this, every M3-supported row fell through to
#: mixed_or_unavailable and M4 reported a false 0 on the full corpus.
_FAILURE_SYNONYMS = frozenset({'failure', 'incorrect', 'failed', 'wrong'})
_CORRECT_SYNONYMS = frozenset({'correct', 'success', 'passed', 'pass'})


def normalise_grade(g: Optional[str]) -> Optional[str]:
    """Map any grader vocabulary onto 'correct' / 'failure' / 'ungradeable'."""
    if g is None:
        return None
    s = str(g).strip().lower()
    if s in _FAILURE_SYNONYMS:
        return FAILURE
    if s in _CORRECT_SYNONYMS:
        return CORRECT
    return UNGRADEABLE


M3_KINDS = ('verified', 'plausible')


def classify_matched(lep_grade: Optional[str],
                     benign_grades: Sequence[Optional[str]],
                     m3_supported: bool) -> Dict[str, Any]:
    """Classify one M3-supported row into the five-way taxonomy."""
    lep_grade = normalise_grade(lep_grade)
    benign_grades = [normalise_grade(g) for g in benign_grades]
    gradeable = [g for g in benign_grades if g in (CORRECT, FAILURE)]
    det: Dict[str, Any] = {
        'm3_supported': bool(m3_supported),
        'lep_grade': lep_grade,
        'benign_grades': list(benign_grades),
        'n_benign_gradeable': len(gradeable),
        'n_benign_correct': sum(1 for g in gradeable if g == CORRECT),
        'n_benign_failure': sum(1 for g in gradeable if g == FAILURE),
    }
    if not m3_supported:
        det['class'] = 'not_m3_supported'
        return det
    if lep_grade == UNGRADEABLE or lep_grade is None:
        det['class'] = MIXED_UNAVAILABLE
        det['reason'] = 'lep_not_gradeable'
        return det
    if not gradeable:
        det['class'] = MIXED_UNAVAILABLE
        det['reason'] = 'no_gradeable_benign_reference'
        return det
    any_correct = det['n_benign_correct'] > 0
    all_correct = det['n_benign_correct'] == len(gradeable)
    all_failure = det['n_benign_failure'] == len(gradeable)
    if lep_grade == FAILURE:
        if any_correct:
            det['class'] = DEGRADED
            det['reason'] = ('all_benign_correct' if all_correct
                             else 'mixed_benign_with_a_success')
        else:
            det['class'] = PERSISTENT_FAILURE
            det['reason'] = 'all_benign_failed'
    elif lep_grade == CORRECT:
        if all_failure:
            det['class'] = IMPROVED
            det['reason'] = 'all_benign_failed_lep_correct'
        elif any_correct:
            det['class'] = PERSISTENT_CORRECT
            det['reason'] = ('all_benign_correct' if all_correct
                             else 'mixed_benign_no_degradation')
        else:
            det['class'] = MIXED_UNAVAILABLE
            det['reason'] = 'indeterminate'
    else:
        det['class'] = MIXED_UNAVAILABLE
        det['reason'] = f'unknown_lep_grade:{lep_grade}'
    return det


def m4_historical(lep_grade: Optional[str], m3_supported: bool) -> bool:
    return bool(m3_supported and normalise_grade(lep_grade) == FAILURE)


def m4_matched(lep_grade: Optional[str],
               benign_grades: Sequence[Optional[str]],
               m3_supported: bool) -> bool:
    return classify_matched(lep_grade, benign_grades,
                            m3_supported)['class'] == DEGRADED


def summarise(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate counts, overall and split by verified / plausible M3 support.

    Requirement 5: report verified and plausible separately.
    """
    from collections import Counter
    cls = Counter()
    by_tier = {'verified': Counter(), 'plausible': Counter()}
    n = 0
    m4h = m4m = 0
    for r in rows:
        n += 1
        c = r['class']
        if c == 'not_m3_supported':
            continue
        cls[c] += 1
        tier = r.get('m3_reach_class')
        if tier in by_tier:
            by_tier[tier][c] += 1
        if m4_historical(r.get('lep_grade'), True):
            m4h += 1
        if c == DEGRADED:
            m4m += 1
    den = max(1, n)
    return {
        'n_rows': n,
        'n_m3_supported': sum(cls.values()),
        'class_counts': dict(cls),
        'class_rates_over_m3': {k: v / max(1, sum(cls.values()))
                                for k, v in cls.items()},
        'M4_historical_count': m4h,
        'M4_historical_rate': m4h / den,
        'M4_matched_count': m4m,
        'M4_matched_rate': m4m / den,
        'M4_matched_fraction_of_M4': (m4m / m4h) if m4h else None,
        'M4_matched_rate_over_m3': m4m / max(1, sum(cls.values())),
        'by_tier': {t: dict(c) for t, c in by_tier.items()},
        'M4_matched_by_tier': {
            t: by_tier[t][DEGRADED] for t in ('verified', 'plausible')},
        'M4_historical_by_tier': {
            t: (by_tier[t][DEGRADED] + by_tier[t][PERSISTENT_FAILURE] +
                by_tier[t][MIXED_UNAVAILABLE])
            for t in ('verified', 'plausible')},
        'caveat': ('M4-matched is perturbation-ASSOCIATED degradation, not '
                   'proof of causation: benign references are separate '
                   'executions, not counterfactuals of the same run.'),
    }
