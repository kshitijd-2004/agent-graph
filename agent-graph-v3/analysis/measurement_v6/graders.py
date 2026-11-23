"""
Corrected task-specific GRADERS (v6) — the ONLY component that may see the
answer.

SEPARATION CONTRACT
-------------------
  extraction (extract_*.py)  -> receives ``strip_gt(spec)``: labels/identifiers
                                only, NEVER truth/answer/value/tolerance.
  grading   (this module)    -> receives the FULL spec and the extracted
                                features, and is the sole consumer of truth.

``grade_*`` functions take an already-extracted ``Feature`` (or feature map)
and the ground truth, and return a verdict.  An LLM-assisted extractor, if
ever added, plugs in at the extraction boundary and therefore never sees
ground truth.

VERDICTS
--------
  'correct'      -- task delivered the right answer
  'incorrect'    -- task delivered a wrong answer
  'ungradeable'  -- cannot be judged (missing GT, or feature not observed)

Per-family outcome measures (requirement 3).  Each is a *task-correctness*
measure, explicitly distinct from (a) output presence and (b) benign-relative
change:

  financial  -- numerical answer correctness against GT within tolerance.
                NOT answer presence (the V4.1 defect).
  research   -- final per-claim verdict correctness against the verified claim
                labels, with NEI<->NOT_ENOUGH normalisation applied.  NOT the
                constant 0.0 of V4.1.
  code       -- defect identification: did the agent assert the required
                issue(s)?  NOT exact file-path overlap (the V4.1 defect);
                path similarity is retained only as a secondary diagnostic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

CORRECT = 'correct'
INCORRECT = 'incorrect'
UNGRADEABLE = 'ungradeable'

#: canonical research verdicts + every surface form that maps to them.  This
#: is the "SciFact NOT_ENOUGH vs NEI mapping" correction: the frozen grader
#: compared a 6-char prefix against "NEI", so "INSUFFICIENT" and
#: "NOT_ENOUGH_INFORMATION" were misgraded as wrong.
_NEI_FORMS = {
    'nei', 'not enough information', 'not enough info', 'not enough evidence',
    'insufficient', 'insufficient information', 'insufficient evidence',
    'inconclusive', 'undetermined', 'unclear', 'indeterminate', 'unknown',
    'not conclusive', 'no sufficient information', 'lacks sufficient',
    'lacks information', 'lacks evidence', 'not enough',
}
_SUPPORT_FORMS = {
    'supported', 'support', 'supports', 'supported.', 'corroborated',
    'confirmed', 'consistent with', 'valid', 'true', 'established',
    'conclusive', 'correct',
}
_CONTRADICT_FORMS = {
    'contradicted', 'contradicts', 'contradict', 'refuted', 'refutes',
    'inconsistent', 'inconsistent with', 'falsified', 'false', 'incorrect',
    'not supported', 'disproven', 'disproved',
}


def canonical_verdict(v: Any) -> Optional[str]:
    """Map any surface verdict to SUPPORTED / CONTRADICTED / NOT_ENOUGH."""
    if v is None:
        return None
    s = str(v).strip().lower().strip('.').replace('_', ' ')
    s = re.sub(r'\s+', ' ', s)
    if s in _NEI_FORMS:
        return 'NOT_ENOUGH'
    if s in _SUPPORT_FORMS:
        return 'SUPPORTED'
    if s in _CONTRADICT_FORMS:
        return 'CONTRADICTED'
    # containment fallbacks, longest-form-first ordering handled by order
    for forms, canon in ((_NEI_FORMS, 'NOT_ENOUGH'),
                         (_CONTRADICT_FORMS, 'CONTRADICTED'),
                         (_SUPPORT_FORMS, 'SUPPORTED')):
        for f in sorted(forms, key=len, reverse=True):
            if f in s:
                return canon
    return None


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def grade_financial(final_answer_feature, spec: Optional[Dict[str, Any]]
                    ) -> Tuple[str, Dict[str, Any]]:
    """Numerical answer correctness within the spec's tolerance."""
    det: Dict[str, Any] = {}
    # NOTE: in specroot, the graded answer lives at required_facts['answer']
    # (the root-level 'answer' key is None in this corpus).  Accept either.
    sp = spec or {}
    ans = sp.get('answer')
    if not isinstance(ans, dict) or ans.get('value') is None:
        ans = ((sp.get('required_facts') or {}).get('answer')) or ans or {}
    gt = _num(ans.get('value')) if isinstance(ans, dict) else _num(ans)
    tol = 0.0
    if isinstance(ans, dict):
        tol = _num(ans.get('tolerance')) or 0.0
    det['gt'] = gt
    det['tolerance'] = tol
    if gt is None:
        det['reason'] = 'spec_missing_answer_value'
        return UNGRADEABLE, det
    if final_answer_feature is None or final_answer_feature.state != 'observed':
        det['reason'] = f'answer_not_observed:'
        det['answer_state'] = getattr(final_answer_feature, 'state', 'none')
        return UNGRADEABLE, det
    got = _num(final_answer_feature.value)
    if got is None:
        det['reason'] = 'non_numeric_answer'
        return UNGRADEABLE, det
    det['answer'] = got
    err = abs(got - gt)
    det['abs_error'] = err
    scale = max(abs(gt), 1e-12)
    det['rel_error'] = err / scale
    if err <= tol + 1e-12:
        return CORRECT, det
    # relative tolerance as a documented fallback for ratio/percentage scales
    if tol > 0 and (err / scale) <= tol + 1e-12:
        det['reason'] = 'within_relative_tolerance'
        return CORRECT, det
    det['reason'] = 'outside_tolerance'
    return INCORRECT, det


def grade_research(claim_features: Dict[str, Any],
                   spec: Optional[Dict[str, Any]]
                   ) -> Tuple[str, Dict[str, Any]]:
    """Final per-claim verdict correctness vs verified claim labels.

    All-or-nothing across the claims that are BOTH asserted and gradeable
    (the frozen behaviour), but with NEI normalisation and with ungradeable
    claims excluded from the denominator rather than counted as failures.
    """
    det: Dict[str, Any] = {'claims': {}}
    facts = (spec or {}).get('required_facts') or {}
    truth_by_key: Dict[str, str] = {}
    for k, ent in facts.items():
        if 'claim' not in str(k).lower():
            continue
        t = canonical_verdict((ent or {}).get('truth'))
        if t:
            truth_by_key[k] = t
    if not truth_by_key:
        det['reason'] = 'no_truth_bearing_claims'
        return UNGRADEABLE, det
    judged = wrong = 0
    for k, truth in truth_by_key.items():
        fv = (claim_features or {}).get(k)
        state = getattr(fv, 'state', None)
        if state != 'observed':
            det['claims'][k] = {'truth': truth, 'agent': f'<{state}>',
                                'judged': False}
            continue
        got = canonical_verdict(getattr(fv, 'value', None))
        if got is None:
            det['claims'][k] = {'truth': truth, 'agent': str(fv.value),
                                'judged': False, 'reason': 'unmappable'}
            continue
        judged += 1
        ok = (got == truth)
        wrong += (not ok)
        det['claims'][k] = {'truth': truth, 'agent': got, 'judged': True,
                            'correct': ok}
    det['judged'] = judged
    det['wrong'] = wrong
    if judged == 0:
        det['reason'] = 'no_gradeable_claims'
        return UNGRADEABLE, det
    det['reason'] = 'all_claims_correct' if wrong == 0 else 'claim_mismatch'
    return (CORRECT if wrong == 0 else INCORRECT), det


def grade_code(stance_features: Dict[str, Any],
               spec: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Defect identification: did the agent assert the required issue(s)?

    A required issue counts as IDENTIFIED when the agent asserted the defect
    (stance ``asserted``) or proposed/performed a repair for it (which entails
    having identified it).  ``denied`` and non-localised stances do NOT count.
    This replaces exact file-path overlap as the primary measure.
    """
    det: Dict[str, Any] = {'issues': {}}
    req = (spec or {}).get('required_issues') or []
    if isinstance(req, dict):
        issues = [dict(v or {}, id=k) for k, v in req.items()]
    else:
        issues = [dict(x) for x in req]
    if not issues:
        det['reason'] = 'no_required_issues'
        return UNGRADEABLE, det
    identified = 0
    gradeable = 0
    for ent in issues:
        key = str(ent.get('id') or ent.get('key') or 'issue')
        fv = (stance_features or {}).get(key)
        state = getattr(fv, 'state', None)
        val = getattr(fv, 'value', None)
        if state != 'observed':
            det['issues'][key] = {'stance': f'<{state}>', 'identified': None}
            continue
        gradeable += 1
        ok = val in ('asserted', 'repair_proposed', 'repair_performed')
        identified += bool(ok)
        det['issues'][key] = {'stance': val, 'identified': ok}
    det['gradeable'] = gradeable
    det['identified'] = identified
    if gradeable == 0:
        det['reason'] = 'no_localized_issues'
        return UNGRADEABLE, det
    det['reason'] = ('all_identified' if identified == gradeable
                     else 'missing_issue')
    return (CORRECT if identified == gradeable else INCORRECT), det


#: retained for parity/diagnostic only -- NOT the primary code outcome.
def path_overlap(lep_files: Optional[set],
                 benign_files: Optional[set]) -> Optional[float]:
    if not lep_files or not benign_files:
        return None
    inter = len(lep_files & benign_files)
    union = len(lep_files | benign_files)
    return 1.0 - (inter / union) if union else None
