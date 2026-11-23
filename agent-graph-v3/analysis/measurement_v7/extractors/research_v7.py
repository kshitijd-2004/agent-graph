"""
v7 research (SciFact) verdict extraction.

v7 = v6 base extractor + explicitly named correction passes.  Every pass is
isolated and recorded on the returned feature basis so a downstream reader
can see exactly which rule fired.  The v6 regression floor (84 checks,
incl. D1/D2/D3/D16) still has to pass on the v6 layer itself; the v7 passes
are additive and each has its own regression test.

NAMED PASSES
------------
  R-HEDGE   Hedged verdicts are AMBIGUOUS, not definitive.  "likely
             supported", "appears to be true", "evidence suggests the claim
             holds" must not be promoted to SUPPORTED, and must not be
             demoted to unavailable.  The two competing readings are
             recorded in the basis (the annotation guide §4.2 rule).
  R-NEG     Negated support is NOT contradiction (annotation guide §4.2:
             "The evidence does not support X" != "the evidence contradicts
             X"; the first is at most NOT ENOUGH INFO).  v6 mapped a
             negated SUPPORT tail to CONTRADICTED; the v7 primary policy
             (polarity='rubric') remaps negated-support to NOT_ENOUGH
             UNLESS an active contradicting verb (contradict/refute/
             falsify/inconsistent/disprove) is present in the same clause.
             polarity='v6' keeps the historical mapping for sensitivity.
  R-NEITHER Collective negation "neither claim is supported" is a verdict
             on EVERY claim (NOT_ENOUGH under the rubric policy), not
             `absent` (v6 D16 fixed the positive collective; the negative
             collective was still `absent`).

STATES are unchanged from v6: observed | absent | ambiguous | unavailable.
NO GROUND TRUTH is read.  The spec is already the stripped feed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import Feature
from extract_research import (NOT_ENOUGH, SUPPORTED, CONTRADICTED,
                             _claim_ref_spans, _norm, sentences)

#: hedge markers that sit BETWEEN the claim reference and the verdict tail
_HEDGE_RE = re.compile(
    r'(?i)\b(?:likely|probably|possibly|perhaps|appears\s+(?:to\s+)?|'
    r'seems?\s+(?:to\s+)?|may(?:be|well)?\s|might\s|tentative\w*\s*|'
    r'suggests?\b|suggesting\b|indicat\w+\b|evidence\s+(?:suggests?|'
    r'indicates?)\b|could\s+be\b|somewhat\b|largely\b|mostly\b)\s*')

#: active contradicting verbs: when one of these is operative in the clause,
#: a negated-support construction genuinely IS a contradiction, and the
#: verdict stays CONTRADICTED even under the rubric policy.
_ACTIVE_CONTRADICT_RE = re.compile(
    r'(?i)\b(?:contradict\w*|refut\w*|falsif\w*|inconsistent\w*|'
    r'disprov\w*|refutes?|falsifies?)\b')

_NEITHER_RE = re.compile(
    r'(?i)\bneither\s+(?:of\s+)?(?:the\s+)?(?:\w+\s+)?claims?\b')


def _clause_has_active_contradict(clause: str) -> bool:
    return bool(_ACTIVE_CONTRADICT_RE.search(clause))


def _hedge_in_sentence(sent: str, verdict_pos: int) -> Optional[str]:
    """The hedge marker governing the verdict at ``verdict_pos``, or None.

    Only markers that sit BEFORE the verdict in the same sentence count:
    "evidence suggests the claim is supported" is hedged; "the claim is
    supported, likely to be confirmed" is not.
    """
    prefix = sent[:verdict_pos]
    for m in _HEDGE_RE.finditer(prefix):
        return m.group(0).strip()
    return None


def extract_claim_verdicts_v7(text: str,
                              spec: Optional[Dict[str, Any]],
                              polarity: str = 'rubric'
                              ) -> Tuple[Dict[str, Feature], List[str]]:
    """v7 claim-verdict extraction (see module docstring for the passes)."""
    import extract_research as V6R
    base, diags = V6R.extract_claim_verdicts(text, spec)
    out: Dict[str, Feature] = {}
    notes: List[str] = []
    norm_text = V6R._norm(text or '')
    sents = sentences(norm_text)
    facts = (spec or {}).get('required_facts') or {}
    claims = [k for k in facts if 'claim' in k.lower()] or sorted(facts)

    # R-NEITHER: "neither claim is supported" -> NOT_ENOUGH on every claim.
    nm = _NEITHER_RE.search(norm_text)
    if nm and polarity == 'rubric':
        sent_i = next((i for i, (s0, s1) in enumerate(sents)
                        if s0 <= nm.start() < s1), None)
        none_referenced = all(not _claim_ref_spans(norm_text, k)
                              for k in claims)
        if sent_i is not None and none_referenced:
            s0, s1 = sents[sent_i]
            span = (s0 + nm.start(), s0 + nm.end())
            for key in claims:
                out[key] = Feature.obs(NOT_ENOUGH,
                                       f'{key}:collective_negation:neither',
                                       span)
                diags.append(f'{key}:observed:collective_negation')
            notes.append('R-NEITHER: collective negation applied to all claims')
            return out, diags + notes

    for key in claims:
        f = base.get(key)
        if f is None:
            out[key] = Feature.unavail(f'{key}:no_v6_feature')
            diags.append(f'{key}:unavailable:no_v6_feature')
            continue
        # R-HEDGE: demote a definitive verdict that is actually hedged.
        if f.state == 'observed' and f.value in (SUPPORTED, CONTRADICTED) \
                and f.span:
            vs, ve = f.span
            sent_i = next((i for i, (s0, s1) in enumerate(sents)
                           if s0 <= vs < s1), None)
            if sent_i is not None:
                s0, s1 = sents[sent_i]
                clause = norm_text[s0:s1]
                pos_in_clause = vs - s0
                marker = _hedge_in_sentence(clause, pos_in_clause)
                if marker:
                    upper = ('SUPPORTED' if f.value == SUPPORTED
                             else 'CONTRADICTED')
                    out[key] = Feature.ambig(
                        f'{key}:hedge:{marker}'
                        f':between:{upper}|NOT_ENOUGH', f.span)
                    diags.append(f'{key}:ambiguous:hedge:{marker}')
                    notes.append(f'R-HEDGE:{key}={marker}')
                    continue
        # R-NEG: negated-support remap (rubric policy only)
        if (polarity == 'rubric' and f.value == CONTRADICTED
                and 'negated:supported' in (f.basis or '') and f.span):
            s0c, s1c = f.span
            sent = next((norm_text[s0: s1] for (s0, s1) in sents
                         if s0 <= s0c < s1),
                        norm_text[max(0, s0c - 200): s1c + 200])
            if not _clause_has_active_contradict(sent):
                out[key] = Feature.obs(NOT_ENOUGH,
                                       f'{key}:negated_support:NEI', f.span)
                diags.append(f'{key}:observed:negated_support->NEI')
                notes.append(f'R-NEG:{key} negated-support remapped')
                continue
        out[key] = f
        diags.append(f'{key}:{f.state}:{f.value or "-"}')
    return out, diags + notes
