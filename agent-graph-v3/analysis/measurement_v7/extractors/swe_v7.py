"""
v7 code-review (SWE-bench) issue-stance extraction.

v7 = v6 base extractor + explicitly named correction passes:

  C-LOCAL     Governance of the anchor.  v6's D5 fix widens the localisation
              window to 320 chars around an anchor mention; a defect frame
              ("causes", "incorrect", "is missing") that sits at the far edge
              of that window may describe something else entirely, and v6
              would still credit the issue as `asserted`.  The v7 governance
              pass requires the defect frame to sit within GOVERNANCE_RADIUS
              of the anchor MENTION itself (not the window edge), and
              records the firing frame on the basis so it is auditable.
              Frames farther out are demoted to mention-only unless a
              denial/proposal/performance frame fired.
  C-MENTION   Mention is not identification.  Where the issue's location is
              merely named (path or symbol appears) with no defect, denial,
              proposal or performance frame, the stance is reported as the
              EXPLICIT state `mentioned_no_stance` (distinct from
              unavailable: the extractor localised the issue but found no
              stance).  The v6 layer collapses this into `unavailable`,
              which defeats false-negative diagnosis (D9-class).

The stance vocabulary matches the frozen artifact vocabulary exactly
(asserted | repair_proposed | repair_performed | denied); an explicit enum
and a validated grading-vocabulary mapping prevent the silent zero-count
failure documented in the audit (D17) where the grader vocabulary
(`incorrect`) did not meet the artifact vocabulary (`failure`).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import Feature, _norm
from extract_code import (ASSERTED, DENIED, REPAIR_PERFORMED, REPAIR_PROPOSED,
                          _anchors, _windows, extract_issue_stances as
                          _v6_issue_stances,
                          _ASSERT_FRAMES, _DENIAL, _PERFORMED, _PROPOSAL)
from extract_research import negated_prefix

#: distance between an anchor mention and the nearest defect frame for the
#: frame to "govern" that mention.  A frame 300 chars away in a 640-char
#: window is evidence it may describe a different construct.
GOVERNANCE_RADIUS = 80

MENTIONED_NO_STANCE = 'mentioned_no_stance'

#: explicit enum of the stance vocabulary, mapped to the frozen artifact
#: values.  Any value outside this enum is a defect, not a silent zero.
STANCE_ENUM = (ASSERTED, REPAIR_PROPOSED, REPAIR_PERFORMED, DENIED)

#: grading vocabulary -> artifact vocabulary (the D17 correction, made
#: explicit).  A grader that emits 'incorrect' for a task grade must be
#: normalised through this map before it meets the frozen 'failure' field.
GRADE_VOCAB_MAP = {
    'correct': 'correct',
    'incorrect': 'failure',
    'failure': 'failure',
    'failed': 'failure',
    'wrong': 'failure',
    'ungradeable': 'ungradeable',
}


def _frame_hits(clause: str, frames: List[str]) -> List[Tuple[int, int, str]]:
    hits: List[Tuple[int, int, str]] = []
    for p in frames:
        for m in re.finditer(p, clause):
            hits.append((m.start(), m.end(), p))
    return hits


def extract_issue_stances_v7(text: str, spec: Optional[Dict[str, Any]],
                             governance_radius: int = GOVERNANCE_RADIUS
                             ) -> Tuple[Dict[str, Feature], List[str]]:
    """v7 issue-stance extraction (see module docstring for the passes)."""
    base, diags = _v6_issue_stances(text, spec)
    out: Dict[str, Feature] = {}
    notes: List[str] = []
    req = (spec or {}).get('required_issues') or []
    if isinstance(req, dict):
        issues = []
        for k, v in req.items():
            ent = dict(v or {})
            ent.setdefault('id', k)
            issues.append(ent)
    else:
        issues = [dict(x) for x in req]
    norm_text = _norm(text or '')

    for ent in issues:
        key = str(ent.get('id') or ent.get('key') or 'issue')
        f = base.get(key)
        if f is None:
            out[key] = Feature.unavail(f'{key}:no_v6_feature')
            diags.append(f'{key}:unavailable:no_v6_feature')
            continue
        # C-MENTION: v6 reports localisation-without-stance as unavailable;
        # v7 reports it as the explicit mentioned_no_stance state so the two
        # failure modes are distinguishable in coverage accounting.
        if f.state == 'unavailable' and 'localized_no_stance' in (f.basis or ''):
            out[key] = Feature('mentioned_no_stance', None,
                               f'{key}:mentioned_no_stance')
            diags.append(f'{key}:mentioned_no_stance')
            notes.append(f'C-MENTION:{key}')
            continue
        # C-LOCAL: re-verify the governance of an `asserted` verdict.  v6
        # credits an assertion frame that fired anywhere in a merged window;
        # v7 requires the frame to govern the anchor mention itself.
        if f.state == 'observed' and f.value == ASSERTED:
            anchors = _anchors(ent, spec)
            mention_spans: List[Tuple[int, int]] = []
            for a in anchors:
                for m in re.finditer(r'(?<![A-Za-z0-9_])' + re.escape(a) +
                                     r'(?![A-Za-z0-9_])', norm_text, re.I):
                    mention_spans.append((m.start(), m.end()))
            wins = _windows(norm_text, anchors)
            frame_ok = False
            for (s0, s1) in wins:
                clause = norm_text[s0:s1]
                for (fs, fe, _p) in _frame_hits(clause, _ASSERT_FRAMES):
                    if negated_prefix(clause, fs):
                        continue
                    # distance from the frame to the nearest anchor mention
                    dmin = (min(min(abs(fs - ms), abs(fe - me))
                               for ms, me in mention_spans)
                            if mention_spans else 0)
                    if dmin <= governance_radius:
                        frame_ok = True
                        break
                if frame_ok:
                    break
            if frame_ok:
                out[key] = Feature.obs(ASSERTED,
                                       f'{key}:asserted:governed', f.span)
                diags.append(f'{key}:observed:asserted:governed')
            else:
                # the assertion frame sat outside the governance radius:
                # demote to mention-only; the v6 verdict is kept on the
                # basis for audit
                out[key] = Feature('mentioned_no_stance', None,
                                   f'{key}:assertion_not_governing'
                                   f':v6={f.basis}')
                diags.append(f'{key}:mentioned_no_stance:not_governing')
                notes.append(f'C-LOCAL:{key} demoted (v6 said asserted)')
            continue
        out[key] = f
        diags.append(f'{key}:{f.state}:{f.value or "-"}')
    return out, diags + notes


def map_grade_vocab(g: Optional[str]) -> Optional[str]:
    """Explicit grader-vocabulary mapping (D17).  Unknown values are a loud
    error, never a silent zero-count."""
    if g is None:
        return None
    s = str(g).strip().lower()
    if s not in GRADE_VOCAB_MAP:
        raise ValueError(f'unknown grade vocabulary {g!r}; extend '
                         f'GRADE_VOCAB_MAP explicitly, do not coerce')
    return GRADE_VOCAB_MAP[s]
