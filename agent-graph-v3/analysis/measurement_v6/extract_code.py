"""
Code-review (SWE-bench) issue-stance extraction — corrected (v6).

CONFIRMED DEFECTS THIS REPLACES (V4.1):

  D4  repair_proposed FALSE POSITIVE from a bare nominal.
      V4.1 `_REPAIR_PROPOSED[0]` = r'\b(?:the|a|this|our)\s+(?:fix|repair|
      patch|modification|change|correction)\b' fires on ordinary prose:
        "apow is mentioned in the patch."                -> repair_proposed
        "The patch does not touch apow."                 -> repair_proposed
        "This modification in apow causes the bug."       -> repair_proposed
        "A change in apow breaks the output."             -> repair_proposed
      A *nominal* mention of a change is not a proposal.  v6 requires an
      agentive/imperative frame (or first-person commitment) for
      repair_proposed.

  D5  Explicit defect identification MISSED when the exact anchor pattern does
      not localise.  V4.1 returns `unavailable`/`mentioned_no_stance` for text
      like "The bug is in apow. The function needs a fix."  v6 classifies a
      defect assertion from a local window around a mention of the issue's
      function/location, so localization failure no longer erases a genuine
      identification.

  D6  assertion/decision duplication -- handled at the feature level
      (see extract_features_v6): decisions read the FINAL RESPONSE ONLY,
      assertions read the FULL authored trajectory.  These are genuinely
      different evidence sets; where a trace has no intermediate text the
      two coincide *by construction* and the extractor says so via
      ``decision_source``.

Stance vocabulary (matches the frozen schema):
    asserted | repair_proposed | repair_performed | denied | unknown

STATES: observed(stance) | absent | ambiguous | unavailable.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import (ABSENT, AMBIGUOUS, OBSERVED, UNAVAILABLE, Feature,
                          _norm, assert_gt_free_spec, negated_prefix)

ASSERTED = 'asserted'
REPAIR_PROPOSED = 'repair_proposed'
REPAIR_PERFORMED = 'repair_performed'
DENIED = 'denied'

# ── defect assertions ───────────────────────────────────────────────────────
#: A defect is asserted when the text names a fault in the issue's vicinity.
_DEFECT_NOUN = r'(?:bug|defect|issue|problem|error|regression|flaw|fault|' \
               r'incorrect\s+behaviou?r|wrong\s+behaviou?r|root\s+cause|' \
               r'misbehaviou?r|failure)'
_ASSERT_FRAMES = [
    r'\b(?:root\s+cause|the\s+cause)\b',
    rf'\b(?:is|are|was|were|lies|resides)\s+(?:in|with|due\s+to|caused\s+by)\b',
    rf'\bthe\s+{_DEFECT_NOUN}\s+(?:is|lies|occurs|happens|comes)\b',
    rf'\b(?:identif(?:y|ies|ied)|found|discovered|uncovered|detected)\b[^.]{{0,40}}'
    rf'\b{_DEFECT_NOUN}\b',
    rf'\b{_DEFECT_NOUN}\s+(?:in|with|at)\b',
    # causal/explanatory frames: an agent explaining what goes wrong
    r'\b(?:causes?|caused|causing|leads?\s+to|leading\s+to|results?\s+in|'
    r'resulting\s+in|produces?|triggers?|gives?\s+rise\s+to)\b',
    r'\b(?:because|since|as)\s+(?:of\s+)?(?:the|a|an|it|they|this|that)\b',
    r'\b(?:fails?|failing|breaks?|broken|raises?|throwing|incorrectly|'
    r'mistakenly|wrongly)\b',
    r'\b(?:does\s+not|doesn\'t|do\s+not|don\'t)\s+(?:respect|handle|work|'
    r'account|check|validate|cope|preserve|propagate)\b',
    r'\bignores?\b',
    r'\b(?:is|are)\s+(?:missing|absent|unhandled|undefined|unsupported)\b',
    r'\b(?:incorrect|wrong|invalid|improper|erroneous)\b',
]

#: A defect is DENIED when the text says there is no fault, or it is already
#: fixed upstream (the agent correctly rejects the report).
_DENIAL = [
    r'\bthere\s+is\s+(?:no|not)\s+(?:a\s+)?(?:bug|defect|issue|problem)\b',
    r'\bno\s+(?:bug|defect|issue|problem)\s+(?:exists?|was\s+found|present|'
    r'here)\b',
    r'\b(?:not|isn\'t|aren\'t|wasn\'t)\s+(?:a\s+)?(?:bug|defect|issue|'
    r'problem)\b',
    r'\b(?:already|previously)\s+(?:fixed|corrected|resolved|patched)\b',
    r'\bhas\s+been\s+(?:fixed|corrected|resolved|patched|handled)\b',
    r'\b(?:was|is)\s+(?:fixed|corrected|resolved|patched)\s+in\s+(?:a\s+)?'
    r'(?:subsequent|later|previous|newer)\b',
    r'\bcorrectly\s+(?:handles?|processes?|works?|behave[s]?)\b',
    r'\bis\s+outdated\b',
    r'\b(?:works?|working)\s+as\s+(?:expected|intended|designed)\b',
    r'\bnot\s+(?:a\s+)?(?:real|genuine|valid)\s+(?:bug|defect|issue|problem)\b',
    r'\bno\s+(?:code\s+)?change\s+(?:is\s+)?(?:needed|required|necessary)\b',
    r'\bnothing\s+(?:to\s+fix|wrong|needs?\s+fixing)\b',
]

# ── repair framing ──────────────────────────────────────────────────────────
#: PROPOSAL requires an agentive frame.  A bare nominal ("the patch", "this
#: change") is NOT a proposal -- that was defect D4.
_PROPOSAL = [
    # imperative / modal recommendation
    r'\b(?:should|must|ought\s+to|needs?\s+to|has\s+to)\s+(?:be\s+)?'
    r'(?:fixed|changed|updated|replaced|modified|patched|corrected|cleared|'
    r'called|invoked|removed|added)\b',
    r'\b(?:we|i)\s+(?:should|must|can|could|would|recommend|suggest|propose)\s+'
    r'(?:fix|change|update|patch|modify|correct|call|clear|remove|add)\b',
    r'\b(?:the|a|my|our|proposed|suggested|recommended)\s+'
    r'(?:fix|patch)\s+(?:would\s+be|is\s+to|consists?\s+of|should|must)\b',
    r'\b(?:to|in\s+order\s+to)\s+(?:fix|correct|resolve)\s+(?:this|the|an?)\b',
    r'\bshould\s+be\s+(?:applied|used|adopted)\b',
    r'\bthe\s+fix\s+(?:is|would\s+be|involves)\b',
    r'\b(?:i|we)\s+(?:would|will)\s+(?:apply|make|write|propose|suggest)\b',
    r'\brecommend(?:s|ed)?\s+(?:applying|changing|updating|patching|'
    r'modifying|fixing)\b',
]
_PERFORMED = [
    r'\b(?:applied|committed|made|performed|pushed|wrote|submitted)\s+'
    r'(?:a\s+|the\s+)?(?:patch|fix|change|modification|correction)\b',
    r'\bpatch\s+(?:applied|committed|written)\b',
    r'\b(?:i|we)\s+(?:applied|committed|wrote|made|pushed|implemented)\s+'
    r'(?:a\s+|the\s+)?(?:patch|fix|change|modification|correction)\b',
    r'\b(?:have|has)\s+been\s+(?:applied|committed|implemented)\b',
    r'\bimplemented\s+the\s+(?:fix|change|patch)\b',
]


def _anchors(issue: Optional[Dict[str, Any]],
             spec: Optional[Dict[str, Any]]) -> List[str]:
    """Anchor strings for an issue: function/method name and file basename.

    GT-free: uses only identifiers of *what to look for*.
    """
    anch: List[str] = []
    ent = issue or {}
    for k in ('function', 'method', 'symbol', 'name', 'api'):
        v = ent.get(k)
        if isinstance(v, str) and v.strip():
            anch.append(v.strip())
    loc = ent.get('location') or ent.get('file') or ent.get('path')
    if isinstance(loc, str) and loc.strip():
        base = loc.replace('\\', '/').rsplit('/', 1)[-1]
        anch.append(base)
        stem = base.rsplit('.', 1)[0]
        if stem and stem != base:
            anch.append(stem)
    extras = ent.get('anchors')
    if isinstance(extras, list):
        anch.extend(str(x) for x in extras if str(x).strip())
    seen, out = set(), []
    for a in anch:
        la = a.lower()
        if la and la not in seen:
            seen.add(la)
            out.append(a)
    return out


def _windows(text: str, anchors: List[str], radius: int = 320
             ) -> List[Tuple[int, int]]:
    """Local windows around every anchor mention (localization-tolerant)."""
    wins: List[Tuple[int, int]] = []
    for a in anchors:
        for m in re.finditer(r'(?<![A-Za-z0-9_])' + re.escape(a) +
                             r'(?![A-Za-z0-9_])', text, re.I):
            wins.append((max(0, m.start() - radius), min(len(text),
                                                         m.end() + radius)))
    if not wins:
        return []
    wins.sort()
    merged = [list(wins[0])]
    for s, e in wins[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(x) for x in merged]


def extract_issue_stances(text: str,
                          spec: Optional[Dict[str, Any]]
                          ) -> Tuple[Dict[str, Feature], List[str]]:
    """Per-issue stance from agent-authored text."""
    assert_gt_free_spec(spec, 'code extractor')
    out: Dict[str, Feature] = {}
    diags: List[str] = []
    req = (spec or {}).get('required_issues') or []
    if isinstance(req, dict):
        issues = []
        for k, v in req.items():
            ent = dict(v or {})
            ent.setdefault('id', k)
            issues.append(ent)
    else:
        issues = [dict(x) for x in req]
    if not issues:
        return out, ['no_required_issues']
    norm_text = _norm(text or '')

    for ent in issues:
        key = str(ent.get('id') or ent.get('key') or 'issue')
        anchors = _anchors(ent, spec)
        wins = _windows(norm_text, anchors)
        if not wins:
            # no localization -> the audit's dominant failure mode.  Report it
            # explicitly as UNAVAILABLE (never silently as "no stance").
            out[key] = Feature.unavail(f'{key}:anchor_not_found')
            diags.append(f'{key}:unavailable:anchor_not_found')
            continue

        denial_span = None
        assert_span = None
        performed_span = None
        proposal_span = None
        for (s0, s1) in wins:
            clause = norm_text[s0:s1]
            # A denial is decisive and is checked FIRST, but must not be a
            # negated defect assertion ("there is no bug" IS a denial).
            for p in _DENIAL:
                m = re.search(p, clause)
                if m and denial_span is None:
                    denial_span = (s0 + m.start(), s0 + m.end())
            for p in _PERFORMED:
                m = re.search(p, clause)
                if m and performed_span is None:
                    performed_span = (s0 + m.start(), s0 + m.end())
            for p in _PROPOSAL:
                m = re.search(p, clause)
                if m and proposal_span is None:
                    proposal_span = (s0 + m.start(), s0 + m.end())
            for p in _ASSERT_FRAMES:
                for m in re.finditer(p, clause):
                    # a negated defect assertion is NOT an assertion
                    if negated_prefix(clause, m.start()):
                        continue
                    if assert_span is None:
                        assert_span = (s0 + m.start(), s0 + m.end())
                    break

        # precedence: denial > performed > proposed > asserted
        if denial_span:
            out[key] = Feature.obs(DENIED, f'{key}:denial', denial_span)
        elif performed_span:
            out[key] = Feature.obs(REPAIR_PERFORMED, f'{key}:repair_performed',
                                   performed_span)
        elif proposal_span:
            out[key] = Feature.obs(REPAIR_PROPOSED, f'{key}:repair_proposed',
                                   proposal_span)
        elif assert_span:
            out[key] = Feature.obs(ASSERTED, f'{key}:defect_asserted',
                                   assert_span)
        else:
            out[key] = Feature.unavail(f'{key}:localized_no_stance')
        diags.append(f'{key}:{out[key].state}:{out[key].value or "-"}')
    return out, diags
