"""Corrected financial final-answer extractor (v5).

New, versioned module. The frozen extractor
(``analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/
baseline_deviation_v41.py``) is NOT modified; this module is independent and
importable in parallel.

Two clearly separated stages (per the approved design B2):

  Stage 1 — GT-free extraction
      ``extract_answer_v5(response, unit_sem) -> Extraction``
      Selects the candidate answer from the agent's final response WITHOUT
      consulting the expected numerical answer.  Uses the task's *unit
      semantics* (dimension / declared unit class) only to choose a canonical
      scale — never the expected value.  Returns a candidate value + sign +
      scale + evidence span + status, or an explicit ambiguity / abstention.

  Stage 2 — correctness evaluation
      ``grade_v5(extraction, answer_meta, tol) -> Verdict``
      Compares the canonical candidate against the corrected ground truth with
      the spec tolerance.  ``value_strings`` are NEVER a correctness shortcut
      (they are used only for spec-integrity and regression tests, elsewhere).

Design rationale (corrections to the V4.1 defects):
  D1  V4.1 required a literal "answer"/"ratio" label word (_ANSWER_LINE) and
      dropped every answer without it.  v5 scores numbers by *answer-position*
      (conclusion cues, recency, non-citation context) so a correctly stated
      figure is captured with or without the label word.
  D2  V4.1's _NUM_RE lookahead ``(?![\\d,.])`` made a number invisible when a
      period/comma followed ("...is 2.62." -> no match) and dropped the '%'
      when a comma followed ("18.54%, ...").  v5 parses punctuation-aware.
  D3  Dimensional/ratio answers were declared in currency units, so V4.1
      divided a correct dimensionless value by 1e3/1e6 (2.62 -> 2.62e-06).
      v5 takes the canonical scale from the *corrected* unit semantics, so a
      dimensionless/percent/share-count answer is not force-scaled.
  D4  V4.1 took the FIRST qualifying number in the window, which latched a
      source figure instead of the computed conclusion.  v5 prefers the final
      conclusion and treats enumerated input figures (with citations) as
      non-answer unless no conclusion exists.
  D5  Negative / directional quantities ("a decrease of 485,760") were lost.
      v5 preserves sign from an explicit '-' or directional language.

Deterministic: pure regex + ordering, no randomness, no model calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── availability states (mirror the frozen M6 states, decoupled copy) ──────
OBSERVED = 'observed'
UNAVAILABLE = 'unavailable'
AMBIGUOUS = 'ambiguous'
REFUSED = 'refused'          # explicit refusal / "cannot determine"
NOT_APPLICABLE = 'not_applicable'


@dataclass
class Extraction:
    """Stage-1 result: a GT-free candidate answer (or an explicit abstention)."""
    status: str                       # observed/ambiguous/unavailable/refused
    value: Optional[float] = None     # canonical number (scale-normalised)
    raw: Optional[str] = None        # the literal token as written
    sign: int = 1                    # +1 / -1
    percent: bool = False            # value is a percent (e.g. 12.3 for '12.3%')
    scale: float = 1.0               # canonical scale factor applied (1e6=millions)
    decimals: Optional[int] = None   # decimal places the AGENT stated (rounding)
    evidence: Optional[str] = None   # evidence span (surrounding text)
    basis: str = ''                  # which rule selected it
    ambiguity: Optional[str] = None  # reason when status==ambiguous
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    # ------------------------------------------------------------------
    def jsonable(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != 'candidates'}


@dataclass
class Verdict:
    """Stage-2 result: correctness against corrected ground truth."""
    label: str                       # correct / incorrect / ungradeable
    extraction_status: str
    extracted: Optional[float] = None
    expected: Optional[float] = None
    tolerance: Optional[float] = None
    reason: str = ''
    # ------------------------------------------------------------------
    def jsonable(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# ─────────────────────────────────────────────────────────────────────────────
# Unit semantics (task-side, GT-free).  A UnitSemantics object encodes the
# canonical scale for the requested quantity, derived from the *corrected*
# declared unit — never from the expected value.
# ─────────────────────────────────────────────────────────────────────────────
DIMENSIONLESS = 'dimensionless'     # ratio / rate / margin / leverage / fraction
PERCENT = 'percent'                 # value already a percent (34.33)
USD = 'usd'                         # currency; scale = 1e3/1e6/1e9 for k/M/B
SHARES = 'shares'                   # a share count (in thousands/millions)
CURRENCY_PER_SHARE = 'per_share'    # dollars per share (EPS)
PER_TRANSACTION = 'per_txn'         # dollars per transaction
COUNT = 'count'                     # unit-less integer count
SIGNED_DELTA = 'signed_delta'       # a signed change/ difference (currency scale)


_MAG = {'thousand': 1e3, 'million': 1e6, 'billion': 1e9}


@dataclass
class UnitSemantics:
    """Task-side unit semantics for the requested quantity (GT-free).

    ``unit_scale`` is the magnitude of the task unit in base units (dollars,
    shares, …): 1e3/1e6/1e9 for a currency or count unit declared in
    thousands/millions/billions, else 1.0.  It is used ONLY to bring an
    agent-stated figure into the task unit (``_canonical``).  It NEVER reads
    the expected value.
    """
    dimension: str = DIMENSIONLESS
    unit_scale: float = 1.0          # magnitude of the task unit in base units
    percent: bool = False
    signed: bool = False
    note: str = ''

    @classmethod
    def from_corrected_unit(cls, unit: Optional[str], label: Optional[str] = None,
                            corrected: Optional[Dict[str, Any]] = None) -> 'UnitSemantics':
        """Build semantics from the CORRECTED unit field (see fin_spec_revised).

        ``corrected`` may override the unit (for the 35 confirmed defects) so a
        dimensionless answer is NOT force-scaled by the defective declared
        currency unit.
        """
        if corrected and corrected.get('unit'):
            unit = corrected['unit']
        u = (unit or '').lower()
        signed = bool(re.search(r'\b(change|difference|delta|net|decrease|'
                                 r'increase|drop|decline)\b', (label or '').lower()))
        if 'dimensionless' in u:
            return cls(DIMENSIONLESS, 1.0, note=f'dimensionless unit: {unit}')
        if 'percent' in u or u == '%':
            return cls(PERCENT, 1.0, percent=True, note=f'percent unit: {unit}')
        if 'per share' in u or 'per-share' in u or u == 'per share':
            return cls(CURRENCY_PER_SHARE, 1.0, note=f'per-share unit: {unit}')
        if 'per transaction' in u:
            return cls(PER_TRANSACTION, 1.0, note=f'per-txn unit: {unit}')
        m = re.search(r'\b(thousand|million|billion)s?\b', u)
        mag = _MAG[m.group(1).lower()] if m else 1.0
        if 'share' in u or 'count' in u or u.startswith('number of'):
            return cls(SHARES, mag, note=f'share-count unit: {unit}')
        return cls(USD, mag, signed=signed, note=f'currency unit: {unit}')


# ─────────────────────────────────────────────────────────────────────────────
# Punctuation-aware number parser (correction D2).  Every parse records:
# raw token, value, sign, percent flag, an inline thousands/millions scale,
# and character coordinates for the evidence span.
# ─────────────────────────────────────────────────────────────────────────────
# A number: optional sign, digits with optional commas / a decimal point,
# optional scientific-notation part, an optional unit word or '%' / '‰'.
_NUM_FULL = re.compile(
    r'(?<![\w.,])'
    r'(?P<sign>[-–+]?)'
    r'(?P<int>\d[\d,]*(?:\.\d+)?)'
    r'\s*(?P<scib>[eE]\s*[+\-]?\d+)?'          # optional scientific tail
    r'(?:\s*(?P<unit>\b(?:thousand|thousands|million|millions|billion|billions)\b'
    r'|[%‰]))?'
    r'(?!\w)'
)
_SCALE = {'thousand': 1e3, 'thousands': 1e3, 'million': 1e6,
          'millions': 1e6, 'billion': 1e9, 'billions': 1e9}
_REFUSE = re.compile(
    r'(?i)\b(?:cannot\s+determine|could\s+not\s+determine|inconclusive|'
    r'not\s+(?:clearly\s+)?(?:available|determinable|establishable)|'
    r'no\s+(?:clear|determinate|unambiguous)\s+answer|'
    r'cannot\s+be\s+determined|undetermined)\b')


def _to_num(tok: str) -> Optional[float]:
    t = tok.replace(',', '').strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


@dataclass
class NumOcc:
    value: float            # raw magnitude as written (commas stripped, no scale)
    sign: int               # +1 / -1
    percent: bool
    inline_scale: float     # 1e3/1e6/1e9 if a unit word is glued, else 1.0
    start: int
    end: int
    raw: str
    context: str            # +/- 60 chars, normalised


def parse_numbers(text: str) -> List[NumOcc]:
    out: List[NumOcc] = []
    norm = text.replace('\n', ' ')
    for m in _NUM_FULL.finditer(text):
        mag = _to_num(m.group('int'))
        if mag is None:
            continue
        sci = m.group('scib')
        if sci:
            mag = mag * (10 ** _to_num(sci.replace(' ', '')) if _to_num(sci.replace(' ', '')) else 1)
        sign = -1 if m.group('sign') in ('-', '–') else 1
        u = m.group('unit')
        percent = u in ('%', '‰')
        inline = _SCALE[u.lower()] if (u and u.lower() in _SCALE) else 1.0
        ctx = norm[max(0, m.start() - 60):m.end() + 60]
        out.append(NumOcc(value=mag, sign=sign, percent=percent,
                          inline_scale=inline, start=m.start(), end=m.end(),
                          raw=m.group(0).strip(), context=ctx))
    return out


# ── answer-position scoring (correction D1/D4) ───────────────────────────────
# A number is a strong *answer* candidate when it sits in a conclusion context:
# a result cue word is within ~40 chars before it, OR it is the final figure in
# the response, OR it is the RHS of an explicit calculation ("... = 2.62").
# Terminal cues: the LAST word(s) before the number must be one of these for
# the number to be in "conclusion position" (fixes "is approximately 34.33").
_TERM_CUE = re.compile(
    r'(?i)\b(?:is|was|are|were|equals?|=|:|≈|~|approximately|about|roughly|'
    r'around|nearly|exactly|gives?|giving|yields?|yielding|computed|calculated'
    r'|determined|found|shown|stands?\s+(?:as|at)|therefore|thus|hence|'
    r'consequently|so)\s*[:=]?\s*[\$€]?\s*$')
_CALC_RHS = re.compile(r'(?i)[\s(]*=[\s≈]*')          # " = 2.62"
# quantity-cue earlier in the same sentence ("the ratio ... is", "percentage of")
_QTY_CUE = re.compile(
    r'(?i)\b(?:final\s+|overall\s+)?(?:the\s+)?(?:answer|result|ratio|rate|'
    r'margin|leverage|portion|percentage|percent|change|difference|net\s+change|'
    r'total|average|value|figure|amount|return|multiple|proportion|'
    r'decrease|decline|drop|reduction|increase|growth)\b[^.!?]{0,80}')
# citation / document-identifier context (a number that is a reference, not a
# value: "10-K", "10-Q", "page 243", "(source: ...)", "form 10-Q")
_CITE = re.compile(
    r'(?i)(?:source:|from\s+`?documents?/|page\s+\d|exhibit\s+\d|form\s+10|'
    r'\(source|per\s+the\s+(?:report|10-k|transcript)|fiscal\s+\d{4})\s*$')
_CITE_10KQ = re.compile(r'(?i)\b10\s*[-–]?\s*[kq]\b')
# directional language carrying a negative sign on the quantity ("decrease of
# 485,760" is -485,760; "a decline of 10%" is -10).  The cue must sit
# IMMEDIATELY before the figure (short window, no intervening figure), so a
# range endpoint ("from $1,438,471 to $952,711") is NOT demoted by the
# directional word that described the earlier delta figure.
_DIREC_NEG = re.compile(
    r'(?i)(?:decrease|decline|drop|reduction|reduced|fell|loss|negative|'
    r'decreased|lowered|shrank|net\s+decrease)\b\s*(?:of|in|by)?\s*[\$€]?\s*$')
_LIST_ITEM = re.compile(r'(?m)^\s*[-*•]\s')
# arithmetic-operand context: the number is a factor of an explicit
# calculation ("2,055 / 5,986 * 100", "2,698 units x $47.59"), not the
# stated result
_OPERAND_PRE = re.compile(
    r'(?i)(?:\d[\d,\.]*\s*[x×*÷/+\-]\s*|(?:units?|shares?)?\s*[x×]\s*[\$€]?\s*)$')
_OPERAND_POST = re.compile(r'(?i)^\s*(?:units?|shares?)?\s*[x×]\s*[\$€]?\d')
# a rejected / superseded figure ("but that figure is preliminary...")
_REJECT_CUE = re.compile(
    r'(?i)(?:superseded|preliminary|rejected|outdated|wrong|incorrect|'
    r'revised|but|however|actually|correction:|does\s+not\s+match|differs?)\s*[,\.]?\s*$')
# range endpoints: "from $X to $Y" — the Y endpoint is not the conclusion
_FROM_RANGE = re.compile(r'(?i)\bfrom\b')
# hedge / competing-readings cue: the agent ITSELF signals two readings
# ("Both figures are reported.", "the two bases give different values").
# Deliberately narrow: a later *revision* ("but on recalculation ... is X")
# is NOT a hedge — the revision path resolves it.
_HEDGE = re.compile(
    r'(?i)(?:both|two|either)\b[^.]{0,30}\b(?:are|is|were|was)\b[^.]{0,24}\b'
    r'(?:reported|shown|possible|stated|consistent|plausible|figures?)'
    r'|figures?\s+(?:differ|conflict|are\s+discrepant|are\s+inconsisten)'
    r'|\btwo\s+(?:possible|competing|conflicting|plausible|valid|different)\s+'
    r'(?:readings?|values?|figures?|answers?)'
    r'|\beither\b[^.]{0,40}\bor\b[^.]{0,40}\b(?:figures?|values?|answers?|readings?)\b'
    r'|on\s+(?:a\s+)?\w+\s+basis\b[^.]{0,60}\bbut\b[^.]{0,60}\bon\s+(?:the\s+|a\s+)?\w*\s*basis\b'
    r'|competing\s+(?:readings?|values?|answers?|figures?)')
# year-context words (frozen V4.1's temporal-qualifier guard, widened)
_YEAR_PRE = re.compile(
    r'(?i)\b(?:in|for|of|from|to|fiscal|as\s+of|since|until|during|by|quarter'
    r'|q[1-4]|period|year|ended|december|january|jan|feb|mar|apr|jun|jul|'
    r'aug|sep|oct|nov)\s*$')
_REVISION_CUE = re.compile(
    r'(?i)\b(?:corrected\s+to|revised\s+to|should\s+be|updated\s+to|'
    r'recalculat(?:e|ed)|on\s+recalculation|upon\s+recalculation|'
    r'correct\s+(?:value|figure|amount)\s+(?:is|was|of)|'
    r'final(?:ly)?\s*(?:figure|value|number|amount)\s+(?:is|was|of))\s*$')


def _is_year_token(o: NumOcc, text: str) -> bool:
    """4-digit 1900-2100 integer used as a temporal qualifier, never a value."""
    if o.percent or o.inline_scale != 1.0 or o.value != int(o.value):
        return False
    if not (1900 <= o.value <= 2100):
        return False
    pre = text[max(0, o.start - 16):o.start]
    post = text[o.end:o.end + 16]
    if _YEAR_PRE.search(pre):
        return True
    return bool(re.match(r'(?i)^\s*(?:sales|revenue|income|fiscal|year|quarter'
                         r'|q[1-4]|ended|period)\b', post))


def _is_citation(o: NumOcc, text: str) -> bool:
    """A document identifier, not a figure (frozen _is_citation_number,
    punctuation-safe): "10-K", "10-Q", "page 243", "(source: ...)"."""
    pre = text[max(0, o.start - 12):o.start]
    if re.search(r'(?i)(?:page|pages|pp?\.?|exhibit|accession|docket|form|'
                 r'note|footnote|schedule)\s*$', pre):
        return True
    if _CITE_10KQ.search(text[o.end - 4:o.end + 6]) and o.value in (10, 1):
        return True
    if re.search(r'(?i)\b10\s*[-–]?\s*[kq]\b', text[max(0, o.start - 12):o.end + 3]) \
            and abs(o.value - 10) < 1e-9:
        return True
    return False


def _is_footnote_marker(o: NumOcc, text: str) -> bool:
    """A parenthesised short integer used as a footnote reference
    ("debt (1)", "(2) visa inc."), NOT a value.  Answer values in these
    responses are never bare 1-digit footnote indices; a real parenthesised
    answer carries magnitude or a decimal ("(385)", "(2.62)")."""
    if o.percent or o.inline_scale != 1.0:
        return False
    if o.value != int(o.value) or not (1 <= o.value <= 12):
        return False
    return bool(re.search(r'\($', text[max(0, o.start - 2):o.start])
                or re.search(r'^\)', text[o.end:o.end + 2]))


def _conclusion_score(o: NumOcc, norm: str, is_last: bool,
                      full: Optional[str] = None) -> int:
    s = 0
    pre = norm[max(0, o.start - 45):o.start]
    pre_sentence = norm[max(0, o.start - 140):o.start]
    post = norm[o.end:o.end + 60]
    if _TERM_CUE.search(pre) or _CALC_RHS.search(pre):
        s += 3
    if _QTY_CUE.search(pre_sentence):
        s += 2
    # directional delta: "a decrease of 485,760 thousand" — the magnitude
    # following the directional cue IS the signed change (correction D5).
    if _DIREC_NEG.search(pre):
        s += 3
    # recency: later in the response is more likely the final conclusion
    if o.start > 0.85 * len(norm):
        s += 2
    elif o.start > 0.7 * len(norm):
        s += 1
    if is_last:
        s += 2
    # demote input/citation/list contexts (enumerated source figures, D4)
    if _CITE.search(pre):
        s -= 4
    if _LIST_ITEM.search(norm[:o.start].split('\n')[-1] if '\n' in norm[:o.start] else ''):
        s -= 1
    # demote arithmetic operands ("2,055 / 5,986 * 100": the 2,055 / 5,986 /
    # 100 are calculation inputs, not the stated result)
    if _OPERAND_PRE.search(pre) or _OPERAND_POST.search(post):
        s -= 2
    # demote range endpoints: "from $1,438,471 to $952,711" — the 'to'
    # endpoint is a second input, not the delta
    if re.search(r'(?i)\bto\s*[\$€]?\s*$', pre):
        s -= 2
    # demote rejected/superseded figures: "5,652, but that figure is
    # preliminary and was superseded"
    if _REJECT_CUE.search(pre) or re.search(
            r'(?i)^\s*,?\s*(?:but|however)\b[^.]{0,40}\b(?:superseded|'
            r'preliminary|outdated|incorrect|wrong|revised|does\s+not\s+'
            r'match|differs?)\b', post):
        s -= 3
    return s


def _is_refused(norm: str) -> bool:
    return bool(_REFUSE.search(norm[-400:]))


def extract_answer_v5(response: Optional[str],
                      unit: UnitSemantics,
                      ) -> Extraction:
    """Stage 1: GT-free candidate selection for the requested quantity."""
    if not response or not response.strip():
        return Extraction(UNAVAILABLE, basis='final_answer_event_missing')
    norm = response.lower().replace('\n', ' ')
    if _is_refused(norm):
        return Extraction(REFUSED, basis='explicit_refusal_or_not_determinable',
                          evidence=norm[-160:])
    occ = parse_numbers(response)
    # drop year tokens, citation tokens, and footnote markers (document
    # identifiers / footnote refs, none of which are answer values)
    occ = [o for o in occ
           if not _is_year_token(o, response)
           and not _is_citation(o, response)
           and not _is_footnote_marker(o, response)]
    if not occ:
        return Extraction(UNAVAILABLE, basis='no_numeric_answer_present')

    is_last_index = len(occ) - 1
    scored = [(o, _conclusion_score(o, norm, i == is_last_index))
              for i, o in enumerate(occ)]
    scored.sort(key=lambda t: (t[1], -t[0].start), reverse=True)
    top_score = scored[0][1]
    best = [o for o, s in scored if s == top_score]
    # a candidate must be in a plausible answer context (score >= 1) unless it
    # is the ONLY number in the response.
    if top_score < 1 and len(occ) > 1:
        return Extraction(AMBIGUOUS, ambiguity='no_answer_position_context',
                          candidates=[_cand(o, s) for o, s in scored[:5]],
                          basis='ambiguous_no_cue')
    # collapse distinct numeric values among the top candidates
    def _signed(o: NumOcc) -> float:
        sgn = -1 if _DIREC_NEG.search(norm[max(0, o.start - 45):o.start]) \
            else o.sign
        return sgn * _canonical(o, unit)
    distinct = _distinct_values([_signed(o) for o in best])
    if len(best) > 1 and len(distinct) > 1:
        # a later revision supersedes an earlier competing value
        rev = [o for o in best if _REVISION_CUE.search(
            norm[max(0, o.start - 60):o.start])]
        if rev:
            best = [max(rev, key=lambda o: o.start)]
        else:
            return Extraction(AMBIGUOUS,
                              ambiguity='multiple_competing_answer_positions',
                              candidates=[_cand(o, s) for o, s in scored[:6]],
                              basis='ambiguous_multiple')
    # single determinate candidate (or a resolved revision)
    o = best[0]
    sgn = -1 if _DIREC_NEG.search(norm[max(0, o.start - 45):o.start]) else o.sign
    val = sgn * abs(_canonical(o, unit))
    basis = f'answer_position_score={top_score}'
    # percent-last fallback: for percent-declared dimensions, when no
    # conclusion-position candidate exists, the LAST percent figure in the
    # response is the answer (mirrors the frozen fallback, punctuation-safe).
    if top_score < 1 or basis.startswith('ambiguous'):
        pcts = [o2 for o2 in occ if o2.percent]
        if pcts and unit.percent:
            o = pcts[-1]
            sgn = -1 if _DIREC_NEG.search(norm[max(0, o.start - 45):o.start]) \
                else o.sign
            val = sgn * o.value
            basis = 'percent_last'
    dec = _stated_decimals(o.raw)
    # hedge / competing-readings: the agent itself signals two readings
    # ("on a 2018 basis ... 1.51, but on the pro forma basis ... 0.84.
    #  Both figures are reported.") -> do NOT silently pick one.  When the
    # hedge cue is present, look at ALL candidates in a genuine conclusion
    # context (score >= 3), not just the top-tied group: an earlier reading
    # and a later competing reading may differ in recency score yet both
    # be stated answers.
    if _HEDGE.search(norm):
        strong = [c for c, s in scored if s >= 3]
        strong_distinct = _distinct_values([_signed(c) for c in strong])
        if len(strong_distinct) > 1:
            return Extraction(AMBIGUOUS,
                              ambiguity='competing_readings_signalled',
                              candidates=[_cand(c, s) for c, s in scored[:6]],
                              basis='ambiguous_hedge')
    return Extraction(OBSERVED, value=val, raw=o.raw,
                      sign=sgn, percent=(o.percent or unit.percent),
                      scale=unit.unit_scale, decimals=dec,
                      evidence=norm[max(0, o.start - 60):o.end + 60],
                      basis=basis, candidates=[_cand(o, top_score)])


def _cand(o: NumOcc, score: int) -> Dict[str, Any]:
    return {'value': o.value, 'sign': o.sign, 'percent': o.percent,
            'inline_scale': o.inline_scale, 'raw': o.raw, 'score': score,
            'ctx': o.context}


def _stated_decimals(raw: str) -> int:
    """The decimal places the agent actually wrote on this figure.

    Only a true decimal point counts ("12.3" -> 1, "2.62" -> 2, "485,760" ->
    0).  This is the agent's stated rounding fidelity; it is used ONLY in
    stage-2 grading (rounding-equivalence) and never for candidate selection.
    """
    m = re.search(r'\.(\d+)', raw or '')
    return len(m.group(1)) if m else 0


def _distinct_values(vals: List[float]) -> List[float]:
    seen: List[float] = []
    for v in vals:
        if v is None:
            continue
        if all(abs(v - s) > 1e-9 * max(1.0, abs(v)) for s in seen):
            seen.append(v)
    return seen


def _canonical(o: NumOcc, unit: UnitSemantics) -> float:
    """Bring the agent-stated figure into the TASK unit.  GT-free.

    Single formula, derived from the finq task convention ("figures are
    reported in the task's declared units", e.g. "in thousands", "in
    millions"):

        canonical_task_units = stated * (inline_scale / task_unit_scale)

    * stated with NO inline unit word -> task_unit_scale cancels -> the
      stated number is already in the task's declared units
      ("$485,760" on a USD-thousands task, "2.62" on a dimensionless task);
    * stated WITH an inline unit word ("$485,760 thousand", "16.087 million")
      -> the inline word confirms/converts the magnitude, still landing in the
      task unit when the word matches the task unit;
    * an inline word that conflicts with the task unit is a genuine
      unit-ambiguity for the annotator to adjudicate (recorded, not
      resolved by the GT).

    Sign is applied by the caller (directional language).
    """
    return o.value * (o.inline_scale / (unit.unit_scale or 1.0))


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: correctness against CORRECTED ground truth (GT is consulted HERE
# only; candidate selection above is GT-free).
# ─────────────────────────────────────────────────────────────────────────────
def grade_v5(ex: Extraction, answer_meta: Optional[Dict[str, Any]],
             ) -> Verdict:
    """Stage 2.  ``answer_meta`` = corrected {value, tolerance, unit,...}."""
    if ex.status in (UNAVAILABLE, REFUSED):
        return Verdict('ungradeable', ex.status, extracted=ex.value,
                       expected=_gt(answer_meta),
                       reason=f'extraction_status={ex.status}')
    if ex.status == AMBIGUOUS:
        return Verdict('ungradeable', AMBIGUOUS, extracted=None,
                       expected=_gt(answer_meta),
                       reason=f'ambiguous:{ex.ambiguity}')
    if ex.value is None:
        return Verdict('ungradeable', ex.status, extracted=None,
                       expected=_gt(answer_meta), reason='no_candidate_value')
    gt = _gt(answer_meta)
    tol = (answer_meta or {}).get('tolerance', 0.02)
    if gt is None:
        return Verdict('ungradeable', ex.status, extracted=ex.value,
                       expected=None, reason='spec_has_no_answer_value')
    diff = abs(ex.value - gt)
    # relative-when-large guard: use the max of absolute tol and a small
    # relative band so big figures aren't graded on an absurdly tight band.
    band = max(tol, 1e-6 * abs(gt))
    if diff <= band:
        return Verdict('correct', ex.status, extracted=ex.value, expected=gt,
                       tolerance=tol,
                       reason=f'|{ex.value:g} - {gt:g}| = {diff:.4g} <= {band:.4g}')
    # Rounding-equivalence fallback: the agent may state a correctly-rounded
    # form of the true value ("approximately 12.3%" for GT 12.27).  This is a
    # DISTINCT, auditable acceptance branch: it fires only when the extracted
    # value equals round(GT, agent_stated_decimals) exactly.  It never widens
    # the tolerance band for an unrelated figure (e.g. "12.4" does not match
    # round(12.27,1)=12.3).  The spec's own value_strings already accept
    # multiple written forms of a value; this codifies the numeric one.
    if ex.decimals is not None and ex.decimals >= 0:
        rounded_gt = round(gt, ex.decimals)
        if abs(ex.value - rounded_gt) < 1e-9 * max(1.0, abs(rounded_gt)):
            return Verdict('correct', ex.status, extracted=ex.value,
                           expected=gt, tolerance=tol,
                           reason=f'rounded_form: {ex.value:g} == '
                                  f'round({gt:g},{ex.decimals})')
    return Verdict('incorrect', ex.status, extracted=ex.value, expected=gt,
                   tolerance=tol,
                   reason=f'|{ex.value:g} - {gt:g}| = {diff:.4g} > {band:.4g}')


def _gt(meta: Optional[Dict[str, Any]]) -> Optional[float]:
    if not meta:
        return None
    v = meta.get('value')
    return float(v) if isinstance(v, (int, float)) else None


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: run both stages for one execution, mirroring the frozen
# extract_final_answer_v4(trace, spec) call shape for drop-in comparison.
# ─────────────────────────────────────────────────────────────────────────────
def run_v5(final_response_text: Optional[str],
           answer_meta: Optional[Dict[str, Any]],
           corrected_unit: Optional[Dict[str, Any]] = None,
           ) -> Dict[str, Any]:
    """Extract (GT-free) then grade (GT).  Returns a flat, JSON-able dict."""
    us = UnitSemantics.from_corrected_unit(
        (answer_meta or {}).get('unit'),
        (answer_meta or {}).get('label'), corrected_unit)
    ex = extract_answer_v5(final_response_text, us)
    v = grade_v5(ex, answer_meta)
    return {'extraction': ex.jsonable(), 'verdict': v.jsonable(),
            'unit_semantics': {'dimension': us.dimension, 'scale': us.unit_scale,
                               'percent': us.percent, 'signed': us.signed,
                               'note': us.note}}
