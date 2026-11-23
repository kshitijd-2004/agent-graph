"""
Financial-analysis (FinQA) extraction — corrected (v6).

CONFIRMED DEFECTS THIS REPLACES (V4.1):

  D7  A trailing sentence period kills numeric extraction.
      V4.1 `_NUM_RE` = r'(?<![\\d,.])([-+]?\\d[\\d,]*(?:\\.\\d+)?)\\s*([%‰])?'
                       r'(?![\\d,.])'
      The trailing lookahead `(?![\\d,.])` rejects a period that ends the
      sentence:
          "Total revenue was 1,234.5."  -> 0 numbers found  -> not_stated
          "Total revenue was 1,234.5 "  -> 1 number found   -> observed
      The most natural English phrasing therefore silently loses the fact.
      v6 drops the period from the trailing lookahead while still rejecting a
      numeric continuation ("1,234.5.6", "1.2.3").

  D8  Final answers marked unavailable despite being present.
      V4.1 `_ANSWER_LINE` requires an explicit "answer:"/"ratio:" cue.  Agents
      frequently state the final figure as the last numeric sentence
      ("Therefore the ratio is 0.42.").  v6 adds a documented fallback that
      only fires when exactly ONE numeric candidate exists in the final
      response -- otherwise it abstains (never guesses).

  D9  Missing information confused with genuine omission.
      V4.1 returns `unavailable:not_stated` for BOTH "the agent never
      discussed this fact" and "the label did not localize".  v6 separates:
        absent      -- the fact's label signature is genuinely not discussed
        unavailable -- the fact was discussed but no unambiguous value bound
      so a grader can tell omission from extractor failure.

STATES: observed(value, span) | absent | ambiguous | unavailable.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import (ABSENT, AMBIGUOUS, OBSERVED, UNAVAILABLE, Feature,
                          _norm, assert_gt_free_spec)

#: sentence-final period is NOT a numeric continuation any more (D7).
#: Still reject a digit/comma on either side so "1,234.5.6" and "a1.2" fail.
_NUM_RE = re.compile(r'(?<![\d,])([-+]?\d[\d,]*(?:\.\d+)?)\s*([%‰])?(?![\d,])')

_SCALE = {'thousand': 1e3, 'thousandth': 1e-3, 'million': 1e6,
          'millionth': 1e-6, 'billion': 1e9, 'trillion': 1e12}
_SCALE_RE = re.compile(
    r'(?i)\b(?:in\s+|expressed\s+in\s+)?'
    r'(thousands?|millions?|billions?|trillions?)\b')

_YEAR_RE = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')


_MONTHS = (r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)'
           r'[a-z]*')
_DATE_CTX = re.compile(
    r'(?i)(?:(?:' + _MONTHS + r')\s+\d{1,2}\s*,?\s*(?:19|20)\d{2}|'
    r'\d{1,2}\s+(?:' + _MONTHS + r')\s*(?:19|20)\d{2}|'
    r'(?:19|20)\d{2}\s*[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2}|'
    r'(?:19|20)\d{2})')


def _date_fragment(norm_text: str,
                   cand: Tuple[int, bool, int, float, Tuple[int, int],
                               int]) -> bool:
    """True when this candidate is part of a month/date construct.

    A label such as "s&p 500 (december 31, 2014)" otherwise matches 31 and
    2014 as if they were financial figures.
    """
    s, e = cand[4]
    ctx = norm_text[max(0, s - 26):min(len(norm_text), e + 26)]
    m = _DATE_CTX.search(ctx)
    if not m:
        return False
    # the candidate itself must fall inside the date phrase
    offset = max(0, s - 26)
    return m.start() + offset <= s and e <= m.end() + offset


def unit_hint_from_task(spec: Optional[Dict[str, Any]]) -> Optional[str]:
    """'percent'/'ratio' when the QUESTION wording asks for one.

    Reads ONLY the task-side prompt/label text, so it cannot leak the answer.
    """
    txt = ' '.join(str((spec or {}).get(k) or '')
                   for k in ('task_prompt', 'description'))
    txt = txt.lower()
    if re.search(r'\b(?:what|which)\s+(?:percent|percentage|proportion|share)\b',
                 txt) or re.search(r'\bpercent(?:age)?\s+of\b', txt):
        return 'percent'
    if re.search(r'\bratio\b', txt) or re.search(r'\btimes\b', txt):
        return 'ratio'
    return None


def _restatement(vals: List[Tuple[float, Tuple[int, int]]]) -> bool:
    """True when the later figures merely restate the leading one.

    Judges only the NUMBER PAIR, so the rule stays GT-free: either the two
    magnitudes are equal (a restatement), or one equals the other's relative
    change (a "X, representing a Y% increase" rephrasing).
    """
    head = abs(vals[0][0])
    if head == 0:
        return False
    for v, _ in vals[1:]:
        v = abs(v)
        if abs(v - head) <= 1e-9:
            continue
        # percent-change restatement: |v| == |head - base| / base * 100 has no
        # base available, so accept the common "same digits, different scale"
        # and near-equal relative forms only.
        if head and abs(v - head) / head < 0.02:
            continue
        return False
    return True


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(',', ''))
    except (TypeError, ValueError):
        return None


def _sig(label: Optional[str]) -> Tuple[List[str], List[str]]:
    """(alpha tokens, year tokens) of a fact label.

    Years are BINDING qualifiers, so "net sales (2003)" cannot bind to the
    2004 figure.  Identical in intent to V4.1 but kept local so the frozen
    module is untouched.
    """
    norm = _norm((label or '').replace('&', ' '))
    if not norm:
        return [], []
    toks = re.findall(r'[a-z0-9]+', norm)
    years = [t for t in toks if t.isdigit() and 1900 <= int(t) <= 2100]
    sig = [t for t in toks if not t.isdigit()]
    return sig, years


def _score(sig: List[str], pre: str) -> int:
    """Signature score in a preceding window (>=5-char tokens count double)."""
    return sum(2 if len(t) >= 5 else 1 for t in sig
               if re.search(r'\b' + re.escape(t) + r'\b', pre))


_MIN_SCORE = 2


def extract_facts(text: str, spec: Optional[Dict[str, Any]]
                  ) -> Tuple[Dict[str, Feature], List[str]]:
    """Per-fact numeric value with explicit states and evidence spans."""
    assert_gt_free_spec(spec, 'financial extractor')
    out: Dict[str, Feature] = {}
    diags: List[str] = []
    facts = (spec or {}).get('required_facts') or {}
    if not facts:
        return out, ['no_required_facts']
    norm_text = _norm(text or '')

    # Pre-compute numeric candidates once: (canonical_value, start, end, line)
    lines = norm_text.split('\n')
    line_starts, off = [], 0
    for ln in lines:
        line_starts.append(off)
        off += len(ln) + 1
    nums: List[Tuple[float, int, int, str]] = []
    for li, ln in enumerate(lines):
        base = line_starts[li]
        for m in _NUM_RE.finditer(ln):
            v = _to_float(m.group(1))
            if v is None:
                continue
            nums.append((v, base + m.start(), base + m.end(), ln))

    for key, ent in facts.items():
        ent = ent or {}
        label = ent.get('label')
        sig, years = _sig(label)
        if not sig:
            out[key] = Feature.unavail(f'{key}:no_label_signature')
            diags.append(f'{key}:unavailable:no_label')
            continue
        cands: List[Tuple[int, bool, int, float, Tuple[int, int], int]] = []
        for (v, s, e, ln) in nums:
            # binding window: the label must be in the SAME sentence, or in
            # the preceding 90 chars.  Widening to whole-paragraph made every
            # figure bind to every label (audit finding: value/label misbind).
            sent_start = max(norm_text.rfind('.', 0, s),
                             norm_text.rfind('\n', 0, s)) + 1
            pre_local = norm_text[sent_start:s]
            pre_wide = norm_text[max(0, s - 90):s]
            sc = _score(sig, pre_local)
            proximity = 0
            if sc < _MIN_SCORE:
                sc = _score(sig, pre_wide)
                if sc < _MIN_SCORE:
                    continue
                proximity = 1  # weaker: label is in the previous sentence
            # BULLET-LIST / INLINE binding: agents commonly emit the required
            # facts as ONE PER LINE, either
            #   "- Printing papers (2005): $658M"
            # or inline
            #   "printing papers (2005) = $658M;"
            # In both layouts the label sits in the SAME clause as its value.
            # Score against the true line containing the value, and against
            # the clause immediately before it; either is a far stronger
            # binding than a label merely in the preceding 90-char window, so
            # it dominates the tie-break instead of leaving a 4-7-way tie.
            # Clause-level only.  Line-level was wrong: a bullet block puts
            # ALL required facts on one line, so every value inherited its
            # neighbours' labels and every candidate looked "local".
            line_start = norm_text.rfind('\n', 0, s) + 1
            clause_start = max(norm_text.rfind(';', 0, s),
                               norm_text.rfind('.', 0, s),
                               norm_text.rfind(':', 0, s),
                               norm_text.rfind('|', 0, s),
                               line_start - 1) + 1
            span_start = max(line_start, clause_start)
            sc_clause = _score(sig, norm_text[span_start:s])
            near = sc_clause
            # A local (same line/clause) match is a SEPARATE, decisive tier.
            # Encoding it as a score boost let several values tie at the
            # boosted level; as a flag it cleanly separates "the label is right
            # here" from "the label was mentioned somewhere upstream".
            is_local = near >= _MIN_SCORE
            if is_local:
                sc = near
                proximity = 0
            sc_eff = sc - proximity
            # binding year qualifier
            if years and not any(y in norm_text[max(0, s - 90):min(len(norm_text), e + 30)]
                                for y in years):
                # a year in the label must be near the figure, either side
                if not any(y in norm_text[max(0, s - 160):min(len(norm_text), e + 60)]
                           for y in years):
                    continue
            # local scale word fixes presentation
            local = 1.0
            msc = _SCALE_RE.search(norm_text[max(0, s - 40):e + 40])
            if msc:
                stem = msc.group(1).lower().rstrip('s')
                local = _SCALE.get(stem, 1.0)
            cands.append((sc_eff, is_local, s, v * local, (s, e), proximity))

        if not cands:
            # D9: distinguish "label never discussed" from "discussed, no bind"
            if any(re.search(r'\b' + re.escape(t) + r'\b', norm_text)
                   for t in sig):
                out[key] = Feature.unavail(f'{key}:label_present_no_value')
                diags.append(f'{key}:unavailable:no_value')
            else:
                out[key] = Feature.absent(f'{key}:label_not_discussed')
                diags.append(f'{key}:absent:not_discussed')
            continue

        # TIER 1: values whose own line/clause names the label.  TIER 2: the
        # best signature score otherwise.  A locally-bound value always wins
        # over an upstream mention, which is what breaks the bullets ties.
        local_c = [c for c in cands if c[1]]
        pool = local_c if local_c else cands
        top = max(c[0] for c in pool)
        comps = [c for c in pool if c[0] == top]
        distinct = {round(c[3], 10) for c in comps}
        if len(distinct) > 1:
            # YEAR / DATE DISAMBIGUATION.  Labels of the form
            # "printing papers (2005)" or "s&p 500 (december 31, 2014)" carry a
            # BINDING period qualifier.  When several values score equally on
            # the name signature, keep only those whose local context also
            # contains the label's year, and drop the rest.  Measured on real
            # traces this removed 5-8-way ties down to a single value.
            if years:
                yr = [c for c in comps
                      if any(y in norm_text[max(0, c[2] - 90):
                                            min(len(norm_text), c[4][1] + 60)]
                             for y in years)]
                if yr and len({round(c[3], 10) for c in yr}) == 1:
                    comps = yr
                    distinct = {round(comps[0][3], 10)}
            if len(distinct) > 1:
                # DATE-FRAGMENT DISAMBIGUATION: "december 31, 2014" makes
                # "31" and "2014" look like values.  Drop numbers that are part
                # of a month/date construct -- but only when doing so leaves at
                # least one candidate.
                pruned = [c for c in comps if not _date_fragment(norm_text, c)]
                if pruned:
                    comps = pruned
                    distinct = {round(c[3], 10) for c in comps}
        if len(distinct) > 1:
            out[key] = Feature.ambig(
                f'{key}:{len(distinct)}_values_same_context', comps[0][4])
            diags.append(f'{key}:ambiguous:multi_value')
            continue
        chosen = sorted(comps, key=lambda c: c[2])[-1]
        out[key] = Feature.obs(chosen[3], f'{key}:score={top}', chosen[4])
        diags.append(f'{key}:observed:score={top}')
    return out, diags


#: NOTE the tail is [^0-9\n]{0,40} (not [^.\n]): a '.' must be allowed so the
#: cue can reach across a decimal point.  Blocking '.' truncated the cue at
#: "0." and left the window starting at "42", reading 0.42 as 42.
#: Numeric identifiers that are NOT answer values.  These must match the
#: number's OWN immediate context ("S&P 500", "Form 10-K"), not merely appear
#: somewhere nearby -- an earlier version matched a 36-char window and vetoed
#: 119 legitimate answers in traces that merely cite the 10-K as a source.
_NON_ANSWER_LEFT = re.compile(
    r'(?i)(?:\bs\s*&\s*p\s*$|\bs&p\s*$|^s&p\s*|\bform\s*$|\bexhibit\s*$|'
    r'\bnote\s*$|\bsection\s*$|\bpage\s*$|\bfigure\s*$|\btable\s*$|'
    r'\bq[1-4]\s*$|\bfy\s*$)')
_NON_ANSWER_RIGHT = re.compile(
    r'(?i)^\s*(?:-?\s*k\b|-?\s*q\b|\s*-\s*k\b)')


def _is_answer_candidate(text: str, start: int, end: int) -> bool:
    """Reject years and immediately-adjacent numeric identifiers."""
    # NOTE: _NUM_RE may capture a trailing thousands-comma ("2017,"), so strip
    # every non-digit before the year test.  Testing the raw match let years
    # through and "2017"/"2014"/"2018" were extracted as answers in real
    # traces.
    raw = re.sub(r'[^0-9.\-]', '', text[start:end])
    if _YEAR_RE.fullmatch(raw):
        return False
    left = text[max(0, start - 14):start]
    right = text[end:end + 8]
    if _NON_ANSWER_LEFT.search(left):
        return False
    if _NON_ANSWER_RIGHT.search(right):
        return False
    return True


_ANSWER_CUE = re.compile(
    r'(?i)\b(?:final\s+answer|overall\s+answer|the\s+answer|answer|'
    r'the\s+ratio|ratio|the\s+result|the\s+value|therefore|thus|hence|'
    r'in\s+conclusion|so\s+the\s+(?:answer|value|ratio|result))\b'
    r'[^0-9\n]{0,40}')


def extract_final_answer(text: str,
                         spec: Optional[Dict[str, Any]]
                         ) -> Tuple[Feature, List[str]]:
    """The agent's final numeric answer.

    Cue-directed first (D8 fix: wider cue set).  Falls back to the single
    numeric candidate ONLY when exactly one exists -- otherwise abstains.
    """
    assert_gt_free_spec(spec, 'financial final-answer extractor')
    norm_text = _norm(text or '')
    if not norm_text:
        return Feature.unavail('final_answer:empty_response'), ['empty']
    # Strip source citations BEFORE analysis: "(source: documents/report.md)"
    # and "(from documents/report.md)" carry no answer content, and their
    # filenames/paths otherwise pollute the numeric window.
    norm_text = re.sub(r'\((?i:source|from|see|per)\b[^)]*\)', ' ',
                       norm_text)
    norm_text = re.sub(r'(?i)\b(?:source|from)\s*:\s*\S+', ' ', norm_text)

    # 1) cue-directed: last number following an answer cue
    hits: List[Tuple[int, float, Tuple[int, int]]] = []
    for m in _ANSWER_CUE.finditer(norm_text):
        window = norm_text[m.end():m.end() + 120]
        # the cue may end mid-number ("answer is 0.42" -> cue consumes "is"
        # then the window starts at "0.42"); take the FIRST complete numeric
        # token in the window, but reject a leading fragment (',42', '.42')
        # left over from a partially-consumed number.
        for nm in _NUM_RE.finditer(window):
            if nm.start() == 0 and window[:1] in (',', '.'):
                continue
            gs = m.end() + nm.start()
            ge = m.end() + nm.end()
            if not _is_answer_candidate(norm_text, gs, ge):
                continue
            v = _to_float(nm.group(1))
            if v is None:
                continue
            hits.append((gs, v, (gs, ge)))
            break
    if hits:
        s, v, span = sorted(hits)[-1]
        return Feature.obs(v, 'final_answer:cue', span), ['cue']

    # 2) LAST-SENTENCE strategy (grounded in the real trace layout: financial
    #    traces enumerate the required figures as a bullet list and state the
    #    computed answer in the closing sentence).  Year tokens and date
    #    fragments are excluded -- a 4-digit year is a label qualifier, not an
    #    answer, and the audit found years latching onto facts.
    sents = [s for s in re.split(r'(?<=[.!?])\s+|\n+', norm_text) if s.strip()]
    sent_offsets = []
    off = 0
    for s in sents:
        i = norm_text.find(s, off)
        i = off if i < 0 else i
        sent_offsets.append((s, i))
        off = i + len(s)
    for sent, base in reversed(sent_offsets[-3:] if len(sent_offsets) >= 3
                               else sent_offsets):
        vals = []
        for nm in _NUM_RE.finditer(sent):
            gs, ge = base + nm.start(), base + nm.end()
            if not _is_answer_candidate(norm_text, gs, ge):
                continue
            v = _to_float(nm.group(1))
            if v is None:
                continue
            vals.append((v, (gs, ge)))
        distinct = {round(v, 10) for v, _ in vals}
        if len(distinct) == 1:
            v, (s0, s1) = vals[-1]
            return (Feature.obs(v, 'final_answer:last_sentence', (s0, s1)),
                    ['last_sentence'])
        # ANSWER-TYPED PREFERENCE: when the QUESTION asks for a percent or a
        # ratio, prefer the percent-marked value in the closing sentence over
        # the enumerated source figures ("... in 2005: 57.0%" vs "$1,137M").
        # The hint is read from the TASK-SIDE prompt, never from the answer
        # entry -- this module must stay ground-truth-free.
        want = unit_hint_from_task(spec)
        if want in ('percent', 'ratio'):
            pct = [(v, sp) for (v, sp) in vals
                   if '%' in sent[sp[0] - base:sp[1] - base + 1]
                   or '%' in norm_text[sp[1]:sp[1] + 2]]
            if len({round(v, 10) for v, _ in pct}) == 1 and pct:
                v, (s0, s1) = pct[-1]
                return (Feature.obs(v, 'final_answer:percent_preferred',
                                    (s0, s1)), ['percent_preferred'])
        # TWO distinct values in one closing sentence: the answer is the FIRST
        # figure; the rest restates or derives it.  Real patterns:
        #   "... is $168, representing a 168% increase."      (restatement)
        #   "... is $168, calculated as $268 - $100."          (arithmetic)
        # Accept the leading value when the remainder is a restatement OR an
        # explicit computation ("calculated as", "= A - B").
        if len(vals) >= 2:
            if _restatement(vals):
                v, (s0, s1) = vals[0]
                return (Feature.obs(v, 'final_answer:last_sentence_head',
                                    (s0, s1)), ['last_sentence_head'])
            # The computation cue must sit BETWEEN the head value and the
            # later values ("$168, calculated as $268 - $100").  A bare
            # "computed" earlier in the sentence ("I computed several
            # values: 1, 2, 3") must NOT license picking the head.
            between = sent[vals[0][1][1] - base:vals[1][1][0] - base]
            if re.search(r'(?i)\b(?:calculat|comput|deriv|obtain|arriv|'
                         r'formula)\w*\b|\s=\s|\bequals\b', between):
                v, (s0, s1) = vals[0]
                return (Feature.obs(v, 'final_answer:last_sentence_computed',
                                    (s0, s1)), ['last_sentence_computed'])

    # 3) unambiguous single-number fallback over the whole response
    allnums = [(m.start(), _to_float(m.group(1)),
                (m.start(), m.end())) for m in _NUM_RE.finditer(norm_text)]
    allnums = [x for x in allnums if x[1] is not None
               and _is_answer_candidate(norm_text, x[2][0], x[2][1])]
    distinct = {round(x[1], 10) for x in allnums}
    if len(distinct) == 1:
        s, v, span = allnums[-1]
        return Feature.obs(v, 'final_answer:single_candidate', span), ['single']
    if not allnums:
        return Feature.absent('final_answer:no_numeric'), ['none']
    return Feature.ambig(f'final_answer:{len(distinct)}_candidates',
                         allnums[-1][2]), ['ambiguous']
