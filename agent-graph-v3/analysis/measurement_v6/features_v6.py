"""
Feature assembly (v6): trace -> the four measurement dimensions.

METHODOLOGY PRESERVED (requirement 1):
    assertions | decisions | actions | outcomes
No weights.  No averaging.  No composite.  Aggregation happens ONLY in
``union.py`` via per-dimension benign thresholds and a union.

WHAT CHANGES vs V4.1, and why:

  1. ASSERTIONS vs DECISIONS are now genuinely distinct evidence sets:
       assertions <- the agent's FULL authored trajectory
                      (reasoning + handoffs + final response)
       decisions  <- the FINAL RESPONSE ONLY
     V4.1 fed the same content to both for research_synthesis (94% identical
     distances) and code_review (97% identical).  Where a trace contains no
     intermediate authored text the two are *necessarily* identical; the
     reason is recorded on ``decision_source`` so this is documented rather
     than hidden.  Financial decisions remain a different *object* (a scalar
     answer vs the fact table) and were already distinct.
     NOTE: for FINANCIAL, assertions are the fact table and decisions are the
     final answer -- genuinely different constructs.

  2. Assertion/decision extraction uses the corrected task extractors.

  3. Outcomes move from "presence / path overlap" to real task correctness
     (see graders.py), with explicit UNGRADEABLE rather than a fabricated 0.

  4. Missing dimensions are NEVER scored as zero divergence: a feature whose
     state is unavailable/ambiguous yields a ``None`` component distance, and
     union.py treats None as "not evaluable", not as 0.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from extract_core import (ABSENT, AMBIGUOUS, NOT_APPLICABLE, OBSERVED,
                          UNAVAILABLE, Feature, _norm, strip_gt)
from extract_code import extract_issue_stances
from extract_financial import extract_facts, extract_final_answer
from extract_research import extract_claim_verdicts

# ── trace views ──────────────────────────────────────────────────────────────
AUTHORED_EVENT_TYPES = ('reasoning', 'agent_handoff', 'final_response')


def authored_events(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Agent-authored events only.

    ``tool_result`` / ``user_input`` / ``system_init`` are EXCLUDED: they carry
    tool- or system-provided content, and admitting them would let a tool's own
    text be read as the agent's assertion (audit finding).
    """
    out = []
    for e in trace.get('events') or []:
        if e.get('event_type') in AUTHORED_EVENT_TYPES:
            txt = e.get('output_text')
            if txt:
                out.append(e)
    return out


def authored_text_all(trace: Dict[str, Any]) -> str:
    """Full authored trajectory, in event order."""
    return '\n'.join(e.get('output_text') or ''
                     for e in authored_events(trace))


def final_response_text(trace: Dict[str, Any]) -> str:
    """The LAST final_response event's text.

    V4.1 ``_final_response_text`` took the FIRST match; taking the last is
    correct when a workflow emits an updated deliverable.
    """
    latest = ''
    for e in trace.get('events') or []:
        if e.get('event_type') == 'final_response':
            t = e.get('output_text') or ''
            if t:
                latest = t
    return latest


def action_counter(trace: Dict[str, Any]) -> Dict[Tuple[str, str, str], int]:
    """{(role, tool_name, target): count} from recorded tool_call events.

    ``tool_arguments`` IS present in schema 3.0.0 (verified across the corpus:
    keys path/summary/report_path/target_agent/source_paths/content).  The
    target is the first available of those, so semantically identical calls
    with different formatting collapse, and different targets separate.
    NOTE: ``tool_error`` is never populated in this corpus, so failed and
    retried calls cannot be distinguished -- recorded as a documented
    limitation, not silently ignored.
    """
    ctr: Dict[Tuple[str, str, str], int] = {}
    for e in trace.get('events') or []:
        if e.get('event_type') != 'tool_call':
            continue
        role = e.get('agent_role') or ''
        name = e.get('tool_name') or ''
        args = e.get('tool_arguments') or {}
        target = ''
        if isinstance(args, dict):
            for k in ('path', 'report_path', 'target_agent', 'pattern'):
                v = args.get(k)
                if isinstance(v, str) and v:
                    target = v
                    break
            if not target:
                sp = args.get('source_paths')
                if isinstance(sp, list) and sp:
                    target = '|'.join(sorted(str(x) for x in sp))
                elif isinstance(sp, str):
                    target = sp
            if not target:
                s = args.get('summary')
                if isinstance(s, str) and s:
                    target = '<summary:' + str(len(s)) + '>'
        ctr[(role, name, target)] = ctr.get((role, name, target), 0) + 1
    return ctr


def modified_files(trace: Dict[str, Any]) -> set:
    """Paths the agent wrote, from tool_call arguments."""
    files = set()
    for e in trace.get('events') or []:
        if e.get('event_type') != 'tool_call':
            continue
        args = e.get('tool_arguments') or {}
        if not isinstance(args, dict):
            continue
        for k in ('path', 'report_path'):
            v = args.get(k)
            if isinstance(v, str) and v:
                files.add(v)
    return files


# ── per-dimension feature extraction ─────────────────────────────────────────
class FeaturesV6:
    __slots__ = ('fam', 'assertions', 'decisions', 'actions', 'answer',
                 'modified', 'decision_source', 'notes')

    def __init__(self, fam: str):
        self.fam = fam
        self.assertions: Dict[str, Feature] = {}
        self.decisions: Dict[str, Feature] = {}
        self.actions: Dict[Tuple[str, str, str], int] = {}
        self.answer: Optional[Feature] = None
        self.modified: set = set()
        self.decision_source: str = ''
        self.notes: List[str] = []

    def jsonable(self) -> Dict[str, Any]:
        return {
            'fam': self.fam,
            'assertions': {k: v.jsonable() for k, v in self.assertions.items()},
            'decisions': {k: v.jsonable() for k, v in self.decisions.items()},
            'n_action_keys': len(self.actions),
            'answer': self.answer.jsonable() if self.answer else None,
            'n_modified_files': len(self.modified),
            'decision_source': self.decision_source,
            'notes': self.notes,
        }


def extract_features_v6(trace: Dict[str, Any],
                        spec: Optional[Dict[str, Any]],
                        fam: str) -> FeaturesV6:
    """Build the four dimensions.  ``spec`` may carry GT; it is stripped FIRST."""
    f = FeaturesV6(fam)
    gt_free = strip_gt(spec)
    all_text = authored_text_all(trace)
    final_text = final_response_text(trace)
    f.actions = action_counter(trace)
    f.modified = modified_files(trace)

    if fam == 'financial_analysis':
        f.assertions, d1 = extract_facts(all_text, gt_free)
        f.decisions, d2 = extract_facts(final_text, gt_free)
        f.answer, d3 = extract_final_answer(final_text, gt_free)
        # assertions == the fact table; decisions == the same table read from
        # the final response.  Where the agent's only authored text IS the
        # final response, the two coincide by construction -- say so.
        f.decision_source = ('final_response_fact_table'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2] + d3[:1]
    elif fam == 'research_synthesis':
        f.assertions, d1 = extract_claim_verdicts(all_text, gt_free)
        f.decisions, d2 = extract_claim_verdicts(final_text, gt_free)
        f.decision_source = ('final_response_verdicts'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    elif fam == 'code_review':
        f.assertions, d1 = extract_issue_stances(all_text, gt_free)
        f.decisions, d2 = extract_issue_stances(final_text, gt_free)
        f.decision_source = ('final_response_stances'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    return f
