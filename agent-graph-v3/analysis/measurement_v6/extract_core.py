"""
AgentProp measurement v6 — corrected task-specific extraction.

VERSIONED CORRECTION LAYER.  The frozen V4.1 extractor
(``analysis/bnd_v41_cw_swe200_provisional/frozen_run/benchmark/
baseline_deviation_v41.py``) and the live ``benchmark/baseline_deviation_v41.py``
are NEVER modified.  This module imports the frozen helpers it can reuse
(provenance view, action counters, TV kernel) and overrides only the three
task-specific extractors that the independent audit found defective:

  * financial  -- numeric facts missed by punctuation / narrow windows;
                  final answers marked unavailable despite being present.
  * research   -- negated verdicts read as positive ("I cannot say Claim 1 is
                  supported" -> SUPPORTED); assertion/decision duplication.
  * code       -- explicit defect identifications missed when exact issue
                  anchors do not localize; "the patch"/"this change" read as
                  repair proposals; assertion/decision duplication.

Every extracted feature carries an explicit state:
    observed   -- value extracted, with an evidence span
    absent     -- extractor positively determined the agent said nothing
    ambiguous  -- competing values / competing stances, explicit abstention
    unavailable-- extractor could not localize; NEVER silently scored as 0

DESIGN CONSTRAINT (measurement-methodology preservation):
This module produces *features only*.  It does not weight, average, or
composite.  Aggregation is the unchanged four-dimensional per-component
benign-threshold union implemented in ``union.py``.

NO GROUND TRUTH IS CONSUMED HERE.  ``truth``/``answer``/``value``/``tolerance``
fields of a spec are read ONLY by ``graders.py`` (a separate component).
Extraction receives labels/identifiers (what to look for) but never answers
(what the answer should be).  ``assert_gt_free_spec()`` enforces this at the
call boundary.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ── states ───────────────────────────────────────────────────────────────────
OBSERVED = 'observed'
ABSENT = 'absent'
AMBIGUOUS = 'ambiguous'
UNAVAILABLE = 'unavailable'
NOT_APPLICABLE = 'not_applicable'

#: spec keys that carry the ANSWER.  Extraction must never see these.
#:
#: ``pattern`` is included because in this corpus each required_facts entry
#: carries a per-fact regex with the GROUND-TRUTH LITERAL compiled into it:
#:     "printing papers (2005)" -> "...(?<![\\d.])[-(]?\\$?\\s?(658(?:\\.0+)?)(?![\\d])"
#: Using that pattern to "find" the value is not extraction, it is lookup, and
#: it would make every downstream divergence measurement circular.
GT_KEYS = frozenset({'truth', 'answer', 'answer_calculation', 'value',
                     'tolerance', 'grading', 'gold', 'expected',
                     'pattern', 'source_text', 'value_strings'})


class GroundTruthLeak(RuntimeError):
    """Raised when a ground-truth-bearing spec reaches an extractor."""


def assert_gt_free_spec(spec: Optional[Dict[str, Any]],
                        who: str = 'extractor') -> None:
    """Fail loudly if an extractor was handed the answer.

    Recurses into ``required_facts`` / ``required_issues`` entries.  A spec
    entry may keep its keys (so callers can align by key) but the GT-bearing
    *values* must have been stripped by ``strip_gt()``.
    """
    if not spec:
        return
    for bucket in ('required_facts', 'required_issues'):
        bucket_val = spec.get(bucket) or {}
        ents = (bucket_val.items() if isinstance(bucket_val, dict)
                else enumerate(bucket_val))
        for key, ent in ents:
            if not isinstance(ent, dict):
                continue
            leaked = GT_KEYS.intersection(ent.keys())
            if leaked:
                raise GroundTruthLeak(
                    f'{who}: spec entry {bucket}.{key} exposes ground truth '
                    f'keys {sorted(leaked)}. Pass strip_gt(spec).')
    leaked = GT_KEYS.intersection(spec.keys())
    if leaked:
        raise GroundTruthLeak(
            f'{who}: spec root exposes ground truth keys {sorted(leaked)}.')


def strip_gt(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a deep copy of ``spec`` with every GT-bearing key removed.

    This is the ONLY form an extractor may consume.  Grading reads the
    original spec; extraction reads this.
    """
    import copy
    out = copy.deepcopy(spec or {})
    out.pop('answer', None)
    out.pop('answer_calculation', None)
    out.pop('grading', None)
    for bucket in ('required_facts', 'required_issues'):
        ents = out.get(bucket)
        if isinstance(ents, dict):
            # the graded financial answer is a *fact entry* named 'answer';
            # it must not reach the extractor as a fact to look for.
            if bucket == 'required_facts' and 'answer' in ents:
                ents.pop('answer', None)
            for ent in ents.values():
                if isinstance(ent, dict):
                    for k in list(ent.keys()):
                        if k in GT_KEYS:
                            ent.pop(k, None)
        elif isinstance(ents, list):
            for ent in ents:
                if isinstance(ent, dict):
                    for k in list(ent.keys()):
                        if k in GT_KEYS:
                            ent.pop(k, None)
    return out


# ── evidence span ────────────────────────────────────────────────────────────
class Feature:
    """One extracted feature with an explicit state and (when observed) a span.

    ``span`` is ``(start, end)`` into the text that was searched, so every
    claimed observation is auditable against the trace.
    """
    __slots__ = ('state', 'value', 'basis', 'span', 'text_hash')

    def __init__(self, state: str = UNAVAILABLE, value: Any = None,
                 basis: str = '', span: Optional[Tuple[int, int]] = None):
        self.state = state
        self.value = value
        self.basis = basis
        self.span = span

    def __repr__(self) -> str:
        return (f'Feature({self.state}, value={self.value!r}, '
                f'basis={self.basis!r}, span={self.span})')

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, Feature) and self.state == other.state
                and self.value == other.value)

    def __hash__(self) -> int:
        return hash((self.state, self.value))

    def jsonable(self) -> Dict[str, Any]:
        d = {'state': self.state, 'basis': self.basis}
        if self.state == OBSERVED:
            d['value'] = self.value
        if self.span is not None:
            d['span'] = list(self.span)
        return d

    @classmethod
    def obs(cls, value: Any, basis: str = '',
            span: Optional[Tuple[int, int]] = None) -> 'Feature':
        return cls(OBSERVED, value, basis, span)

    @classmethod
    def absent(cls, basis: str = '') -> 'Feature':
        return cls(ABSENT, None, basis)

    @classmethod
    def ambig(cls, basis: str = '',
              span: Optional[Tuple[int, int]] = None) -> 'Feature':
        return cls(AMBIGUOUS, None, basis, span)

    @classmethod
    def unavail(cls, basis: str = '') -> 'Feature':
        return cls(UNAVAILABLE, None, basis)

    def usable(self) -> bool:
        """OBSERVED or ABSENT carry information; AMBIGUOUS/UNAVAILABLE do not."""
        return self.state in (OBSERVED, ABSENT)


def _norm(t: str) -> str:
    t = (t or '').replace('’', "'").replace('‘', "'")
    t = t.replace('“', '"').replace('”', '"')
    t = re.sub(r'\s+', ' ', t)
    return t.strip().lower()


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def sentences(text: str) -> List[Tuple[int, int]]:
    """Character spans of sentences, so verdict scope can be bounded.

    Sentence boundaries are what let a negation in one clause bind to the
    verdict it governs instead of leaking to a neighbouring claim.
    """
    spans, start = [], 0
    for m in _SENT_SPLIT.finditer(text or ''):
        spans.append((start, m.start()))
        start = m.end()
    if start < len(text or ''):
        spans.append((start, len(text)))
    return spans


_NEGATORS = re.compile(
    r"\b(?:not|no|never|cannot|can\s*not|can't|nor|neither|"
    r"without|fail(?:s|ed)?\s+to|unable\s+to|"
    r"isn't|aren't|wasn't|weren't|doesn't|don't|didn't|couldn't|wouldn't)\b")


def negated_prefix(clause: str, upto: int) -> bool:
    """True when a negator governs position ``upto`` inside ``clause``.

    Bounded to the clause, so "I cannot say that Claim 1 is supported" is
    negation-scoped, while "Claim 1 is supported; I cannot reproduce Table 2"
    is not.  Handles the "cannot say/confirm/state ..." modal idiom, which was
    the confirmed false positive in the audit.
    """
    pre = clause[:upto]
    if _NEGATORS.search(pre):
        return True
    return False


_MODAL_DENY = re.compile(
    r"\b(?:cannot|can\s*not|can't|couldn't|could\s+not|"
    r"unable\s+to|not\s+able\s+to|hard\s+to|difficult\s+to|"
    r"impossible\s+to|no\s+way\s+to)\s+"
    r"(?:say|tell|confirm|determine|establish|verify|conclude|assert|"
    r"decide|judge|claim|show|prove|be\s+sure)\b")


def modal_denial(clause: str) -> bool:
    """'I cannot say/confirm/determine ...' -- the agent abstains.

    This idiom asserts nothing about the claim; treating it as SUPPORTED was
    the confirmed research defect.
    """
    return bool(_MODAL_DENY.search(clause))
