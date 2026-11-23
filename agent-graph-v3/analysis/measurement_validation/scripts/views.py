"""Normalise the three answer sources into ONE comparable shape.

WHY
---
The validation has to grade three things that were produced by three
different code paths:

  1. the FROZEN V4.1 extractor's deliverable,
  2. the v6 extractor's deliverable, and
  3. a HUMAN annotator's label.

Each of those stores its answer under a different key with a different state
vocabulary.  If grading is written three times, an observed difference between
them could be a difference in the EXTRACTOR or a difference in the GRADER, and
the validation could never tell which.  So all three are mapped onto one
``{answers: {key: value}, states: {key: state}}`` view and graded by ONE
function (``graders.grade_family``).

STATE VOCABULARY
----------------
The extractors and the annotation schema use overlapping but not identical
state names.  The canonical set is

    observed | absent | ambiguous | unavailable | not_applicable

with these documented mappings:

  * ``None``            -> ``unavailable``   (field never populated)
  * ``'missing'``       -> ``unavailable``
  * ``'not_observed'``  -> ``unavailable``
  * ``'not_applicable'``-> ``not_applicable``  (kept distinct: an item that
                               does not apply to this trace is NOT the same as
                               one the extractor failed to find, and collapsing
                               them would hide a coverage defect)
  * ``'observed'``      -> ``observed``

Mapping ``None`` to ``unavailable`` rather than to ``absent`` is deliberate
and is the central anti-pattern this module exists to prevent: an extractor
that never looked is not an extractor that looked and found nothing, and
neither is a correct negative.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

CANONICAL_STATES = ('observed', 'absent', 'ambiguous', 'unavailable',
                    'not_applicable')

_STATE_MAP = {
    None: 'unavailable',
    '': 'unavailable',
    'missing': 'unavailable',
    'not_observed': 'unavailable',
    'not_available': 'unavailable',
    'unavailable': 'unavailable',
    'none': 'unavailable',
    'absent': 'absent',
    'not_found': 'absent',
    'not_applicable': 'not_applicable',
    'n/a': 'not_applicable',
    'ambiguous': 'ambiguous',
    'uncertain': 'ambiguous',
    'observed': 'observed',
    'present': 'observed',
}


def canon_state(s: Any) -> str:
    key = s if s is None else str(s).strip().lower()
    return _STATE_MAP.get(key, 'unknown')


def _fv(v: Any) -> Tuple[Any, str]:
    """(value, canonical state) from either a FeatureValue or a raw value."""
    if isinstance(v, dict):
        return v.get('value'), canon_state(v.get('state'))
    if v is None:
        return None, 'unavailable'
    return v, 'observed'


# ── deliverable key sets, per family, per source ─────────────────────────────
def deliverable_keys(fam: str, source: str) -> Tuple[str, ...]:
    """Which keys constitute the FINAL DELIVERABLE for (family, source).

    Research and code carry their deliverable in the assertion map; financial
    carries it in a dedicated answer field.  The frozen and v6 extractors
    populate different maps, so the source is part of the key.
    """
    if fam == 'financial_analysis':
        return ('final_answer',)
    # research/code: the assertion map holds one entry per claim / issue
    return ('__all_assertions__',)


def _assertion_entries(blk: Dict[str, Any], fam: str) -> Dict[str, Any]:
    amap = blk.get('amap') or {}
    if fam == 'financial_analysis':
        return {k: v for k, v in amap.items()
                if str(k).startswith('fact_')}
    return amap


def frozen_view(fam: str, blk: Dict[str, Any]) -> Dict[str, Any]:
    """Normalised view of the FROZEN extractor's output."""
    answers: Dict[str, Any] = {}
    states: Dict[str, str] = {}
    if fam == 'financial_analysis':
        v, s = _fv(blk.get('final_answer'))
        answers['final_answer'] = v
        states['final_answer'] = s
        for k, fv in (blk.get('amap') or {}).items():
            answers[k], states[k] = _fv(fv)
        return {'answers': answers, 'states': states,
                'deliverable_keys': ('final_answer',),
                'deliverable_states': {'final_answer': s}}
    if fam == 'research_synthesis':
        cv = blk.get('claim_verdicts') or {}
        src = cv or blk.get('final_claims') or blk.get('amap') or {}
        dstates = {}
        for k, fv in src.items():
            answers[k], states[k] = _fv(fv)
            dstates[k] = states[k]
        for k, fv in (blk.get('amap') or {}).items():
            answers.setdefault(k, _fv(fv)[0])
            states.setdefault(k, _fv(fv)[1])
        return {'answers': answers, 'states': states,
                'deliverable_keys': tuple(sorted(src)),
                'deliverable_states': dstates}
    # code_review
    src = blk.get('final_stances') or blk.get('amap') or {}
    dstates = {}
    for k, fv in src.items():
        answers[k], states[k] = _fv(fv)
        dstates[k] = states[k]
    return {'answers': answers, 'states': states,
            'deliverable_keys': tuple(sorted(src)),
            'deliverable_states': dstates}


def v6_view(fam: str, blk: Dict[str, Any]) -> Dict[str, Any]:
    """Normalised view of the v6 extractor's output."""
    answers: Dict[str, Any] = {}
    states: Dict[str, str] = {}
    if fam == 'financial_analysis':
        v, s = _fv(blk.get('answer'))
        answers['final_answer'] = v
        states['final_answer'] = s
        for k, fv in (blk.get('amap') or {}).items():
            answers[k], states[k] = _fv(fv)
        return {'answers': answers, 'states': states,
                'deliverable_keys': ('final_answer',),
                'deliverable_states': {'final_answer': s}}
    amap = blk.get('amap') or {}
    dstates = {}
    for k, fv in amap.items():
        answers[k], states[k] = _fv(fv)
        dstates[k] = states[k]
    for k, fv in (blk.get('dmap') or {}).items():
        answers.setdefault(k, _fv(fv)[0])
        states.setdefault(k, _fv(fv)[1])
    return {'answers': answers, 'states': states,
            'deliverable_keys': tuple(sorted(amap)),
            'deliverable_states': dstates}


def human_view(fam: str, labels: Dict[str, Any]) -> Dict[str, Any]:
    """Normalised view of a HUMAN annotation record.

    ``labels`` is one annotator's ``deliverable`` block from the annotation
    template: ``{item_key: {'state': ..., 'value': ...}}``.
    """
    answers, states = {}, {}
    for k, ent in (labels or {}).items():
        if isinstance(ent, bool):
            # the SciFact verdicts in the template are stored as an explicit
            # state enum; a bare bool means the item was asserted
            answers[k] = ent
            states[k] = 'observed'
            continue
        if not isinstance(ent, dict):
            answers[k], states[k] = ent, 'observed'
            continue
        if 'value' in ent or 'state' in ent:
            answers[k] = ent.get('value')
            states[k] = canon_state(ent.get('state'))
        else:
            answers[k], states[k] = ent, 'observed'
    return {'answers': answers, 'states': states,
            'deliverable_keys': tuple(sorted(labels or {})),
            'deliverable_states': dict(states)}


def view_for(source: str, fam: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if source == 'frozen':
        return frozen_view(fam, payload)
    if source == 'v6':
        return v6_view(fam, payload)
    if source == 'human':
        return human_view(fam, payload)
    raise ValueError(f'unknown view source: {source}')


# ── pairwise comparison used by the extraction-agreement metrics ─────────────
def compare_views(a: Dict[str, Any], b: Dict[str, Any],
                  keys: Optional[Tuple[str, ...]] = None,
                  numeric_tol: float = 0.0) -> Dict[str, Any]:
    """Item-level agreement between two normalised views.

    Returns per-key outcomes under the taxonomy the report needs:

        exact_match | value_differs | state_differs | both_unavailable
        | one_unavailable | ambiguous_on_one_side | one_not_applicable

    ``one_unavailable`` is kept SEPARATE from ``value_differs``: a row where
    the extractor did not look must never be scored as a disagreement about
    the content, and must never be scored as agreement either.
    """
    ks = keys or tuple(sorted(set(a['answers']) | set(b['answers'])))
    out: Dict[str, Any] = {}
    tally: Dict[str, int] = {}
    for k in ks:
        sa, sb = a['states'].get(k, 'unavailable'), b['states'].get(k, 'unavailable')
        va, vb = a['answers'].get(k), b['answers'].get(k)
        if sa == 'unavailable' and sb == 'unavailable':
            o = 'both_unavailable'
        elif 'unavailable' in (sa, sb):
            o = 'one_unavailable'
        elif 'not_applicable' in (sa, sb):
            o = 'one_not_applicable'
        elif 'ambiguous' in (sa, sb):
            o = 'ambiguous_on_one_side' if sa != sb else 'both_ambiguous'
        elif va == vb:
            o = 'exact_match'
        elif numeric_tol > 0:
            try:
                fa, fb = float(va), float(vb)
                o = ('numeric_within_tolerance'
                     if abs(fa - fb) <= numeric_tol else 'value_differs')
            except (TypeError, ValueError):
                o = 'value_differs'
        elif sa != sb:
            o = 'state_differs'
        else:
            o = 'value_differs'
        out[k] = {'outcome': o, 'state_a': sa, 'state_b': sb,
                  'a': va, 'b': vb}
        tally[o] = tally.get(o, 0) + 1
    return {'per_key': out, 'tally': tally, 'n_keys': len(ks)}
