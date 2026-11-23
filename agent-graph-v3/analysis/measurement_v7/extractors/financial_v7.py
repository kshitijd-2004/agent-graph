"""
v7 financial (FinQA) extraction.

v7 = v6 base extractors + explicitly named correction passes:

  F-BARE    Bare-number answer discipline.  The whole-response fallback
             (v6 ``single_candidate``) can still pick a BARE number that has
             no unit, cue, percent, currency or scale-word context anywhere
             in the response; that is a guess.  F-BARE downgrades such a
             fallback to AMBIGUOUS ("the answer cannot be determined"),
             preserving ambiguity rather than fabricating a value.  The
             cue-directed and last-sentence bases are untouched, so v6's
             D8 improvements are preserved.
  F-OPND    Operands are not answers.  When a response carries a
             derivation trail ("calculated as", "=", computed...) and the
             fallback picked a figure that is NOT the first figure in the
             response, the head value (which precedes its operands) is the
             answer and the later figures are derivation.  v6 already did
             this within the closing sentence; F-OPND extends it to the
             global fallback.

Separation of concerns (requirement 5): extraction produces
``Feature(state, value, basis, span)`` and NOTHING else.  Correctness
grading (tolerance, GT value, unit conversion) is the graders' job; this
module is GT-free.

D9 discipline (absent vs unavailable) is inherited from v6 and pinned by a
regression test so a future edit cannot collapse the two states.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import Feature, _norm, assert_gt_free_spec
from extract_financial import (extract_facts as _v6_extract_facts,
                               extract_final_answer as _v6_extract_final_answer,
                               _NUM_RE, _to_float, _is_answer_candidate,
                               _ANSWER_CUE, _SCALE_RE)

#: currency marks that give a bare number unit context.  A ``$``, ``USD``,
#: ``€`` or ``£`` adjacent to the figure means it is a monetary quantity,
#: not a bare guess.
_CUR_MARK = re.compile(r'(?i)(?:\$|usd|eur|€|£|million|thousand|billion|'
                       r'trillion)\s*[-\d.]')


def extract_facts_v7(text: str, spec: Optional[Dict[str, Any]]
                    ) -> Tuple[Dict[str, Feature], List[str]]:
    """v7 fact extraction.  Currently the v6 base; the D9 discipline
    (absent vs unavailable) is inherited and pinned by its own test."""
    return _v6_extract_facts(text, spec)


def _global_fallback_candidates(norm_text: str
                                ) -> Tuple[List[Tuple[int, float,
                                                      Tuple[int, int]]],
                                           bool]:
    """(candidates, saw_operation) over the whole response."""
    allnums = [(m.start(), _to_float(m.group(1)), (m.start(), m.end()))
               for m in _NUM_RE.finditer(norm_text)]
    allnums = [x for x in allnums if x[1] is not None
               and _is_answer_candidate(norm_text, x[2][0], x[2][1])]
    saw_op = bool(re.search(r'(?i)\b(?:calculat\w*|comput\w*|deriv\w*|'
                            r'obtain\w*|arriv\w*|formula\w*|equals)\b|\s=\s',
                            norm_text))
    return allnums, saw_op


def extract_final_answer_v7(text: str, spec: Optional[Dict[str, Any]]
                            ) -> Tuple[Feature, List[str]]:
    """v7 final-answer extraction (F-BARE + F-OPND over the v6 base).

    Both passes fire ONLY on the whole-response ``single_candidate`` basis,
    so they cannot undo v6's cue/last-sentence behaviour.  The passes are
    abstentions or head-preferences: they never invent a value.
    """
    assert_gt_free_spec(spec, 'financial v7 extractor')
    base, diags = _v6_extract_final_answer(text, spec)
    notes: List[str] = []
    basis = base.basis or ''
    if base.state != 'observed' or basis != 'final_answer:single_candidate':
        return base, diags
    norm_text = _norm(text or '')
    allnums, saw_op = _global_fallback_candidates(norm_text)
    val = base.value
    pos = base.span[0] if base.span else -1

    # F-OPND: with a derivation trail, a figure that is NOT the first value
    # in the response was chosen over its own head.  The head precedes its
    # operands; prefer it.
    if saw_op and allnums and pos > allnums[0][0] and val != allnums[0][1]:
        head = allnums[0]
        out = Feature.obs(head[1], 'final_answer:F-OPND:head_of_derivation',
                          head[2])
        notes.append(f'F-OPND: {val} -> head {head[1]}')
        return out, diags + notes

    # F-BARE: the only fallback in the whole response is a bare number with
    # no cue, no percent mark, no currency/scale mark, and no scale word.
    # That is "the answer cannot be determined" — abstain (requirement 5:
    # preserve ambiguity rather than guess).
    has_cue = bool(_ANSWER_CUE.search(norm_text))
    has_pct = bool(re.search(r'[%‰]', norm_text))
    has_money = bool(_CUR_MARK.search(norm_text))
    has_scale = bool(_SCALE_RE.search(norm_text))
    if not (has_cue or has_pct or has_money or has_scale):
        out = Feature.ambig('final_answer:F-BARE:bare_number_no_context',
                            base.span)
        notes.append(f'F-BARE: bare {val} abstained')
        return out, diags + notes
    return base, diags + notes
