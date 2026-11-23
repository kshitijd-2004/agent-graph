"""Correctness grading, kept strictly SEPARATE from extraction.

WHY THIS MODULE EXISTS AT ALL
-----------------------------
The pre-existing graders (``analysis/measurement_v6/graders.py``) take an
extractor's FEATURE OBJECT as their first argument.  That couples grading to
which extractor ran, which makes it impossible to ask the validation's central
question: "is the disagreement a bad EXTRACTION or a bad GRADE?"

This module re-expresses the SAME grading rules against a PLAIN VALUE + STATE
pair, so:
  * the frozen extractor's answer, the v6 extractor's answer, and a HUMAN
    annotator's answer can all be graded by one code path;
  * a grading error and an extraction error become separable by construction;
  * the frozen grader can be called without instantiating an extractor.

The rule semantics are taken verbatim from the frozen pipelines on purpose —
this is a re-expression, not a re-design.  Where this module differs from
``measurement_v6/graders.py`` the difference is listed in
``GRADING_RULES`` so the divergence is auditable, and
``tests/test_graders_parity.py`` asserts parity against the v6 grader on the
sample.

The three rules:

  **financial_analysis** — numeric answer within the spec's absolute
  tolerance; relative tolerance as a documented fallback.
      frozen (baseline_deviation_v41::_grade, financial branch):
          gradeable iff final_answer is observed and the spec carries a
          numeric value; correct iff |got - gt| <= tolerance.
      v6 (measurement_v6/graders.py::grade_financial): adds the relative
      fallback and normalises the spec lookup (`required_facts.answer`).
      RETAINED HERE: both, since the relative fallback only ever WIDENS
      correctness and the spec-lookup normalisation is required for this
      corpus (root-level `answer` is null in specroot).

  **research_synthesis** — SciFact claim verdicts, all-or-nothing across the
  claims that are BOTH asserted and gradeable.
      frozen: compares with a 6-character prefix match on the uppercased
      string, counts unasserted claims as correct.
      v6: canonicalises NOT ENOUGH INFO / NEI before comparing and EXCLUDES
      ungradeable claims from the denominator.
      RETAINED HERE: the v6 form, because the frozen prefix match treats
      'NOT ENOUGH INFO' and 'NOT_ENOUGH_INFO' as the same while ALSO
      accepting 'NOTH' as a verdict, and counting an unasserted claim as
      correct inflates benign pass rates — the benign failure baseline is
      one of the quantities under validation, so it must not be inflated.

  **code_review** — the LEP final deliverable identifies the required issues.
      frozen: an issue counts as identified unless its stance string is
      empty / 'unavailable' / 'none' / contains 'not_found' or
      'mentioned_no_stance'; failure iff fewer than all must-be-cited issues.
      v6: same, on the normalised stance map.
      RETAINED HERE: the stance-based rule, with the identification/
      repair separation exposed as its own field (task §4 requires the
      annotation task to separate "identified" from "repaired"; the
      grader only ever scores identification, and `repair_proposed` is
      reported as a diagnostic, never folded into correctness).

EVERY grader returns ``(grade, detail)`` with grade in
{'correct', 'incorrect', 'ungradeable'}.  An ungradeable row is NEVER folded
into 'incorrect' — the whole point of the validator is to keep that
distinction visible.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

CORRECT = 'correct'
INCORRECT = 'incorrect'
UNGRADEABLE = 'ungradeable'

NEI_ALIASES = {
    'NOT ENOUGH INFO', 'NOT_ENOUGH_INFO', 'NEI', 'NOT ENOUGH INFORMATION',
    'INSUFFICIENT INFO', 'INSUFFICIENT EVIDENCE', 'NO EVIDENCE',
}
SUPPORTED_ALIASES = {'SUPPORTED', 'SUPPORT', 'CONFIRMED', 'TRUE'}
CONTRADICTED_ALIASES = {'CONTRADICTED', 'CONTRADICT', 'REFUTED', 'FALSE'}

GRADING_RULES = {
    'financial_analysis': {
        'rule': 'abs(answer - gt) <= tolerance, with a relative-tolerance '
                'fallback when tolerance > 0',
        'gradeable_iff': 'the spec carries a numeric answer AND the answer '
                         'feature is observed',
        'diverges_from_frozen': (
            'adds the relative-tolerance fallback and the '
            'required_facts.answer spec lookup (frozen reads only the '
            'root-level answer key, which is null in specroot)'),
        'diverges_from_v6': 'none (re-expressed against value+state)',
    },
    'research_synthesis': {
        'rule': 'claim verdicts compared after canonicalising NEI aliases; '
                'all-or-nothing over claims that are asserted AND gradeable',
        'gradeable_iff': 'the spec carries at least one truth-bearing claim '
                         'AND at least one claim is asserted',
        'diverges_from_frozen': (
            'frozen uses a 6-char uppercased prefix match and counts an '
            'unasserted claim as correct; this counts it as not-graded, '
            'and requires the full canonical verdict'),
        'diverges_from_v6': 'none',
    },
    'code_review': {
        'rule': 'an issue counts as identified unless its stance is blank / '
                'unavailable / none / not_found / mentioned_no_stance; '
                'failure iff not every must-be-cited issue is identified',
        'gradeable_iff': 'the spec declares at least one required issue',
        'diverges_from_frozen': 'none for the pass/fail verdict',
        'diverges_from_v6': 'none; adds a repair_proposed diagnostic that is '
                            'never folded into the verdict',
    },
}


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def canonical_verdict(v: Any) -> Optional[str]:
    """Map a raw SciFact verdict string onto SUPPORTED / CONTRADICTED / NEI."""
    if v is None:
        return None
    s = str(v).strip().upper().replace('_', ' ').replace('-', ' ')
    s = ' '.join(s.split())
    if s in NEI_ALIASES:
        return 'NOT ENOUGH INFO'
    if s in SUPPORTED_ALIASES:
        return 'SUPPORTED'
    if s in CONTRADICTED_ALIASES:
        return 'CONTRADICTED'
    if s.startswith('SUPPORT'):
        return 'SUPPORTED'
    if s.startswith('CONTRADICT') or s.startswith('REFUT'):
        return 'CONTRADICTED'
    if s.startswith('NOT ENOUGH') or s.startswith('NEI') or \
            s.startswith('INSUFFICIENT'):
        return 'NOT ENOUGH INFO'
    return None


def _spec_answer(spec: Optional[Dict[str, Any]]) -> Tuple[Optional[float],
                                                          Optional[float]]:
    """(value, tolerance) from either spec layout; (None, None) if absent."""
    sp = spec or {}
    ans = sp.get('answer')
    if not isinstance(ans, dict) or ans.get('value') is None:
        ans = ((sp.get('required_facts') or {}).get('answer')) or ans or {}
    if not isinstance(ans, dict):
        return _num(ans), None
    return _num(ans.get('value')), _num(ans.get('tolerance'))


# ── financial ────────────────────────────────────────────────────────────────
def grade_financial(answer_value: Any, answer_state: Optional[str],
                    spec: Optional[Dict[str, Any]]
                    ) -> Tuple[str, Dict[str, Any]]:
    gt, tol = _spec_answer(spec)
    det: Dict[str, Any] = {'gt': gt, 'tolerance': tol,
                           'rule': 'financial_analysis'}
    if gt is None:
        det['reason'] = 'spec_has_no_numeric_answer'
        return UNGRADEABLE, det
    if answer_state != 'observed' or answer_value is None:
        det['reason'] = 'answer_not_observed'
        det['answer_state'] = answer_state
        return UNGRADEABLE, det
    got = _num(answer_value)
    if got is None:
        det['reason'] = 'non_numeric_answer'
        det['raw'] = str(answer_value)[:120]
        return UNGRADEABLE, det
    err = abs(got - gt)
    scale = max(abs(gt), 1e-12)
    det.update({'answer': got, 'abs_error': err, 'rel_error': err / scale,
                'signed_error': got - gt})
    t = tol or 0.0
    if err <= t + 1e-12:
        det['reason'] = 'within_absolute_tolerance'
        return CORRECT, det
    if t > 0 and (err / scale) <= t + 1e-12:
        det['reason'] = 'within_relative_tolerance'
        return CORRECT, det
    det['reason'] = 'outside_tolerance'
    return INCORRECT, det


def classify_numeric_error(got: Optional[float], gt: Optional[float],
                           tol: Optional[float]) -> str:
    """Unit/scale-aware error taxonomy for a WRONG numeric answer.

    Distinguishes a genuine arithmetic error from the two failure modes that
    look like one but are not: a FACTOR error (unit or scale mismatch, e.g.
    reporting thousands where the spec wants millions) and a SIGN error.
    """
    if got is None or gt is None:
        return 'not_comparable'
    if abs(got - gt) <= (tol or 0.0) + 1e-12:
        return 'within_tolerance'
    if gt != 0 and abs(got / gt) in (0.001, 0.01, 0.1, 10.0, 100.0, 1000.0):
        return 'unit_or_scale_factor'
    if gt != 0 and abs(got / gt - round(got / gt, 3)) < 1e-6 and \
            abs(round(got / gt, 3)) in (0.001, 0.01, 0.1, 10.0, 100.0, 1000.0):
        return 'unit_or_scale_factor'
    if gt != 0 and abs(got + gt) <= max(abs(gt) * 0.01, 1e-9):
        return 'sign_error'
    if gt != 0 and abs((got - gt) / gt) <= 0.05:
        return 'rounding_or_precision'
    return 'genuine_arithmetic_error'


# ── research synthesis (SciFact) ─────────────────────────────────────────────
def grade_research(verdicts: Dict[str, Any], states: Dict[str, Optional[str]],
                   spec: Optional[Dict[str, Any]]
                   ) -> Tuple[str, Dict[str, Any]]:
    facts = (spec or {}).get('required_facts') or {}
    truth: Dict[str, str] = {}
    for k, ent in facts.items():
        t = canonical_verdict((ent or {}).get('truth'))
        if t:
            truth[k] = t
    det: Dict[str, Any] = {'rule': 'research_synthesis',
                           'claims': {}, 'n_truth_bearing': len(truth)}
    if not truth:
        det['reason'] = 'no_truth_bearing_claims'
        return UNGRADEABLE, det
    asserted = wrong = 0
    for k, tr in sorted(truth.items()):
        st = (states or {}).get(k)
        raw = (verdicts or {}).get(k)
        cv = canonical_verdict(raw)
        if st != 'observed' or cv is None:
            det['claims'][k] = {'truth': tr, 'got_raw': raw, 'got': cv,
                                'state': st, 'graded': False,
                                'why': 'not_asserted_or_unparsed'}
            continue
        asserted += 1
        ok = (cv == tr)
        wrong += (not ok)
        det['claims'][k] = {'truth': tr, 'got_raw': raw, 'got': cv,
                            'state': st, 'graded': True, 'correct': ok}
    det['n_asserted_and_graded'] = asserted
    det['n_wrong'] = wrong
    if asserted == 0:
        det['reason'] = 'no_claim_asserted_and_gradeable'
        return UNGRADEABLE, det
    det['reason'] = 'all_asserted_claims_correct' if wrong == 0 \
        else 'at_least_one_wrong_claim'
    return (CORRECT if wrong == 0 else INCORRECT), det


# ── code review ──────────────────────────────────────────────────────────────
_IDENTIFIED_ABSENT = ('', 'unavailable', 'none')


def _identified(raw: Any) -> bool:
    o = str(raw or '').strip().lower()
    if o in _IDENTIFIED_ABSENT:
        return False
    if 'not_found' in o or 'mentioned_no_stance' in o:
        return False
    if o.startswith('unavailable'):
        return False
    return True


_REPAIR_MARKERS = ('fix', 'patch', 'repair', 'correct', 'modif',
                   'change', 'rewrite', 'edit', 'implement')


def grade_code(stances: Dict[str, Any], states: Dict[str, Optional[str]],
               spec: Optional[Dict[str, Any]]
               ) -> Tuple[str, Dict[str, Any]]:
    issues = (spec or {}).get('required_issues') or []
    must = [i.get('id') for i in issues if i.get('must_be_cited')] or \
           [i.get('id') for i in issues]
    must = [m for m in must if m]
    det: Dict[str, Any] = {'rule': 'code_review', 'issues': {},
                           'n_must_be_cited': len(must)}
    if not must:
        det['reason'] = 'spec_has_no_required_issues'
        return UNGRADEABLE, det
    identified, repair_only = 0, 0
    for iid in must:
        raw = (stances or {}).get(iid)
        st = (states or {}).get(iid)
        ident = _identified(raw)
        # repair_proposed is a DIAGNOSTIC.  The grader scores identification
        # only; a stance that mentions only a repair is reported but not
        # credited, which is the frozen semantics.
        rp = bool(ident and any(m in str(raw).lower() for m in _REPAIR_MARKERS))
        if ident:
            identified += 1
            if rp:
                repair_only += 1
        det['issues'][iid] = {'got_raw': raw, 'state': st,
                              'identified': ident,
                              'repair_marker_present': rp}
    det['n_identified'] = identified
    det['n_must_be_cited'] = len(must)
    det['n_with_repair_marker'] = repair_only
    det['reason'] = ('all_required_issues_identified'
                     if identified >= len(must) else 'missing_required_issue')
    return (CORRECT if identified >= len(must) else INCORRECT), det


# ── family dispatch ──────────────────────────────────────────────────────────
def grade_family(fam: str, feats: Dict[str, Any], spec: Optional[Dict[str, Any]]
                 ) -> Tuple[str, Dict[str, Any]]:
    """Grade a NORMALISED feature view (see ``views.py``).

    ``feats`` must carry {'answers': {key: value}, 'states': {key: state}}.
    """
    answers = feats.get('answers') or {}
    states = feats.get('states') or {}
    if fam == 'financial_analysis':
        key = 'final_answer'
        return grade_financial(answers.get(key), states.get(key), spec)
    if fam == 'research_synthesis':
        return grade_research(answers, states, spec)
    if fam == 'code_review':
        return grade_code(answers, states, spec)
    return UNGRADEABLE, {'reason': f'unknown_family:{fam}'}


#: Family -> the required-fact / required-issue keys the grader reads, used by
#: the leakage audit to confirm grading is the ONLY consumer of these fields.
def gt_fields_read(fam: str) -> Tuple[str, ...]:
    if fam == 'financial_analysis':
        return ('answer', 'required_facts.answer.value',
                'required_facts.answer.tolerance')
    if fam == 'research_synthesis':
        return ('required_facts.*.truth',)
    if fam == 'code_review':
        return ('required_issues[].id', 'required_issues[].must_be_cited')
    return ()
