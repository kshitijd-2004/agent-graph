"""
Research-synthesis (SciFact) verdict extraction — corrected (v6).

CONFIRMED DEFECTS THIS REPLACES (freeze SHA 6f0f7ff3 / 75f1e77f, V4.1):

  D1  "I cannot say Claim 1 is supported."            -> SUPPORTED   (WRONG)
      V4.1 strips to the `Claim 1` reference and matches the tail
      "is supported"; the modal "cannot say" is outside the reference and is
      dropped.  Correct answer: UNAVAILABLE (the agent declined to assert).

  D2  "Claim 1 is supported only in one study; the
       evidence is inconclusive."                     -> SUPPORTED   (WRONG)
      V4.1 takes the first matching tail.  The agent's operative verdict is
      NEI.  Correct answer: NOT_ENOUGH.

  D3  "Claim 1: <restated claim body>. This is supported."  -> UNAVAILABLE
      A restated claim body between the reference and the verdict hides the
      verdict.  Correct answer: SUPPORTED.

Design:
  * Work per SENTENCE, and require the verdict to be in the same sentence (or
    an immediately adjacent one) as the claim reference.
  * Apply negation/modality scoping BEFORE polarity assignment -- a negated
    SUPPORT tail is CONTRADICT, and a modal-denied clause is UNAVAILABLE.
  * Record an evidence span for every observation.
  * Return explicit AMBIGUOUS when two sentences give genuinely conflicting
    operative verdicts (rather than silently taking the last).

STATES: observed(value in SUPPORTED/CONTRADICTED/NOT_ENOUGH) | absent |
        ambiguous | unavailable.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import (ABSENT, AMBIGUOUS, OBSERVED, UNAVAILABLE, Feature,
                          _norm, assert_gt_free_spec, modal_denial,
                          negated_prefix, sentences)

SUPPORTED = 'SUPPORTED'
CONTRADICTED = 'CONTRADICTED'
NOT_ENOUGH = 'NOT_ENOUGH'

#: canonical verdict vocabulary, incl. the frozen schema's NOT_ENOUGH/NEI.
#: The audit's "NEI vs NOT_ENOUGH mapping" correction is applied here: the
#: frozen grader compared a 6-character prefix against "NEI", so any agent
#: verdict not sharing those exact characters failed.  v6 normalises every
#: surface form to the canonical three-class vocabulary FIRST.
_VERDICT_SURFACE = {
    SUPPORTED: [
        'supported', 'support', 'supports', 'corroborated', 'corroborates',
        'confirmed', 'confirms', 'consistent with', 'valid', 'true',
        'holds', 'established', 'conclusive', 'correct',
    ],
    CONTRADICTED: [
        'contradicted', 'contradicts', 'contradict', 'refuted', 'refutes',
        'inconsistent with', 'inconsistent', 'falsified', 'false',
        'does not hold', 'does not apply', 'not supported', 'incorrect',
        'disproven', 'disproved',
    ],
    NOT_ENOUGH: [
        'not enough information', 'not enough evidence', 'insufficient',
        'inconclusive', 'undetermined', 'unclear', 'not enough',
        'cannot be determined', 'cannot be established', 'not conclusive',
        'nei', 'not enough info', 'unknown', 'indeterminate',
        'no sufficient information', 'lacks sufficient',
        'lacks information', 'lacks evidence',
    ],
}

#: ordering matters: longest surface first so "not supported" beats "supported"
_SURFACE_ORDER = sorted(
    ((s, v) for v, ss in _VERDICT_SURFACE.items() for s in ss),
    key=lambda p: -len(p[0]))


def _verdict_hits(clause: str) -> List[Tuple[int, str, str]]:
    """(position, canonical_verdict, matched_surface) for a clause."""
    hits: List[Tuple[int, str, str]] = []
    low = clause
    for surf, verdict in _SURFACE_ORDER:
        for m in re.finditer(r'(?<![a-z])' + re.escape(surf) + r'(?![a-z])',
                             low):
            hits.append((m.start(), verdict, surf))
    hits.sort(key=lambda h: h[0])
    # drop overlapping shorter matches contained in a longer one
    out: List[Tuple[int, str, str]] = []
    for h in hits:
        if any(o[0] <= h[0] < o[0] + len(o[2]) for o in out):
            continue
        out.append(h)
    return out


def _claim_ref_spans(text: str, ref: str) -> List[Tuple[int, int, str]]:
    """Spans of 'claim N' style references to one claim key.

    ``ref`` is the fixture-side identifier (e.g. ``claim_1_verdict``).  We
    accept 'claim 1', 'claim1', 'c1', 'the first claim', and a bare float
    index.  NO ground truth is used -- only the claim's ordinal.
    """
    m = re.search(r'(\d+)', ref or '')
    if not m:
        return []
    n = int(m.group(1))
    pats = [rf'\bclaim\s*{n}\b', rf'\bclaim{n}\b']
    ordinals = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth'}
    if n in ordinals:
        pats.append(rf'\bthe\s+{ordinals[n]}\s+claim\b')
    spans = []
    for p in pats:
        for mm in re.finditer(p, text, re.I):
            spans.append((mm.start(), mm.end(), mm.group(0)))
    spans.sort()
    return spans


_COLLECTIVE = re.compile(
    r'(?i)\b(?:both|all(?:\s+of)?|every|each(?:\s+of)?)\s*'
    r'(?:of\s+)?(?:the\s+)?(?:\w+\s+)?(?:claims?|statements?)\b')


def _collective_verdict(text: str) -> Optional[Tuple[str, Tuple[int, int]]]:
    """A verdict applied to ALL claims at once, e.g. "Both claims are NEI".

    Only fires when the collective phrase is followed by a verdict in the SAME
    sentence.  Required for real traces that answer collectively rather than
    per claim.
    """
    for m in _COLLECTIVE.finditer(text):
        for (s0, s1) in sentences(text):
            if not (s0 <= m.start() < s1):
                continue
            clause = text[s0:s1]
            if modal_denial(clause):
                return None
            hits = _verdict_hits(clause)
            if hits:
                pos, verdict, surf = hits[-1]
                if negated_prefix(clause, pos) and verdict == SUPPORTED:
                    verdict = CONTRADICTED
                return verdict, (s0 + pos, s0 + pos + len(surf))
    return None


def extract_claim_verdicts(text: str,
                           spec: Optional[Dict[str, Any]]
                           ) -> Tuple[Dict[str, Feature], List[str]]:
    """Per-claim operative verdict from agent-authored text.

    Sentence-scoped, negation-aware, with explicit ambiguity handling.
    """
    assert_gt_free_spec(spec, 'research extractor')
    out: Dict[str, Feature] = {}
    diags: List[str] = []
    facts = (spec or {}).get('required_facts') or {}
    claims = [k for k in facts if 'claim' in k.lower()] or sorted(facts)
    norm_text = _norm(text or '')
    sents = sentences(norm_text)

    for key in claims:
        refs = _claim_ref_spans(norm_text, key)
        if not refs:
            # COLLECTIVE VERDICT: agents often write "Both claims are marked as
            # 'NOT ENOUGH INFO'" or "All claims are supported" without naming
            # each claim.  That IS a verdict on every claim.  Found on a real
            # trace (res_scifact_280_1058_572) where v6 first said `absent`.
            coll = _collective_verdict(norm_text)
            if coll is not None:
                out[key] = Feature.obs(coll[0], f'{key}:collective', coll[1])
                diags.append(f'{key}:observed:collective')
                continue
            out[key] = Feature.absent(f'{key}:claim_not_discussed')
            diags.append(f'{key}:absent:no_reference')
            continue
        # candidate (sentence-local) verdicts, in document order
        cands: List[Tuple[int, str, str, Tuple[int, int]]] = []
        for (rs, re_, _surface) in refs:
            sent_i = None
            for i, (s0, s1) in enumerate(sents):
                if s0 <= rs < s1:
                    sent_i = i
                    break
            if sent_i is None:
                continue
            # the reference sentence, plus the next one (agents often write
            # "Claim 1: <body>. This is supported.")
            for j in (sent_i, sent_i + 1):
                if j >= len(sents):
                    continue
                s0, s1 = sents[j]
                clause = norm_text[s0:s1]
                if modal_denial(clause):
                    # agent declined to assert -> no verdict, not SUPPORTED
                    continue
                # CROSS-CLAIM BLEED GUARD: in the lookahead sentence, a verdict
                # that sits after a reference to a DIFFERENT claim belongs to
                # that claim ("Claim 1 is supported. Claim 2 is contradicted."
                # must not make claim 1 CONTRADICTED).
                other_ref_pos = None
                if j != sent_i:
                    for other in claims:
                        if other == key:
                            continue
                        for (os_, oe_, _sf) in _claim_ref_spans(norm_text, other):
                            if s0 <= os_ < s1:
                                other_ref_pos = (os_ - s0 if other_ref_pos is None
                                                 else min(other_ref_pos, os_ - s0))
                for (pos, verdict, surf) in _verdict_hits(clause):
                    if other_ref_pos is not None and pos >= other_ref_pos:
                        continue
                    # scope: a negator before the verdict flips SUPPORT
                    neg = negated_prefix(clause, pos)
                    v = verdict
                    if neg and verdict == SUPPORTED:
                        v = CONTRADICTED
                        surf = 'negated:' + surf
                    elif neg and verdict == NOT_ENOUGH:
                        pass  # "not enough" is already polarity-correct
                    cands.append((s0 + pos, v, surf, (s0 + pos, s0 + pos + len(surf))))

        if not cands:
            # referenced but no scoped verdict found -> explicit, not silent
            out[key] = Feature.unavail(f'{key}:referenced_no_verdict')
            diags.append(f'{key}:unavailable:no_verdict')
            continue
        # dedupe identical (verdict) at the same position
        uniq = {}
        for pos, v, surf, span in cands:
            uniq.setdefault((pos, v), (v, surf, span))
        vals = list(uniq.values())
        # operative verdict = LAST assertion in document order (a revision
        # supersedes), matching the audit's documented "later explicit
        # statements supersede earlier ones" intent -- but now surface any
        # genuine conflict as AMBIGUOUS instead of dropping it.
        vals_sorted = sorted(vals, key=lambda x: x[2][0])
        last = vals_sorted[-1]
        distinct = {v for v, _, _ in vals_sorted}
        if len(distinct) > 1:
            # a real revision reads as "X ... but Y"; a scattered conflict
            # reads as ambiguity.  Distinguish by adjacency of the last two.
            prev = vals_sorted[-2]
            if last[2][0] - prev[2][0] <= 400:
                out[key] = Feature.obs(last[0], f'{key}:revised:{last[1]}',
                                       last[2])
                diags.append(f'{key}:observed:revision')
            else:
                out[key] = Feature.ambig(
                    f'{key}:conflicting_verdicts:{sorted(distinct)}', last[2])
                diags.append(f'{key}:ambiguous:conflict')
            continue
        out[key] = Feature.obs(last[0], f'{key}:{last[1]}', last[2])
        diags.append(f'{key}:observed:{last[0]}')
    return out, diags
