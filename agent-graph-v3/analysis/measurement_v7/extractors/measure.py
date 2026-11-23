"""
v7 feature assembly: trace -> the four measurement dimensions.

Same methodology as v6 (requirement 3): four INDEPENDENT dimensions, each
with its own extractor; NO weighted composite; aggregation happens only in
the shared ``union.py`` via per-dimension benign thresholds and a union.

What changes vs v6:
  * the task extractors are the v7 layers (v6 base + named passes):
        research  -> extractors/research.py  (R-HEDGE, R-NEG, R-NEITHER)
        code      -> extractors/code.py      (C-LOCAL, C-MENTION)
        financial -> extractors/financial.py (F-BARE, F-OPND)
  * ACTIONS gains a set of SEPARATE DIAGNOSTIC measurements (in
    ``distances/action_diagnostics.py``), reported individually and never
    combined into a score:
        count_diff            absolute tool-call count difference
        retry_multiplicity    retry-multiplicity per trace
        order_distance        tool-call ordering distance
        failure_evidence      observable failures, only where recorded
                             (``tool_error`` is never populated in this
                             corpus, so the diagnostic reports
                             'not_recorded' rather than fabricating errors)
    The historical total-variation distance remains the ACTIONS *distance*
    (baseline); the diagnostics are separate measurements on the same trace.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from extract_core import Feature, strip_gt
from features_v6 import (authored_text_all, final_response_text,
                         action_counter, modified_files)

from research_v7 import extract_claim_verdicts_v7
from swe_v7 import extract_issue_stances_v7
from financial_v7 import extract_facts_v7, extract_final_answer_v7

# the separate action-diagnostic measurements (never a composite).
#
# NOTE on imports: the bare name ``distances`` is owned by the v6 module
# (``analysis/measurement_v6/distances.py``) on sys.path; this package's
# ``distances/`` directory is deliberately imported under the unique name
# ``v7_dist`` so the two cannot shadow each other in a shared process.
import importlib.util as _ilu
import os as _os
_AD_PATH = _os.path.abspath(_os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    'distances', 'action_diagnostics.py'))
_v7d_spec = _ilu.spec_from_file_location('v7_dist_action_diagnostics',
                                          _AD_PATH)
_AD = _ilu.module_from_spec(_v7d_spec)
_v7d_spec.loader.exec_module(_AD)

pair_diagnostics = _AD.pair


class FeaturesV7:
    """v7 feature bundle: the four dimensions + the action diagnostics."""
    __slots__ = ('fam', 'assertions', 'decisions', 'actions', 'answer',
                 'modified', 'decision_source', 'notes', 'diagnostics')

    def __init__(self, fam: str):
        self.fam = fam
        self.assertions: Dict[str, Feature] = {}
        self.decisions: Dict[str, Feature] = {}
        self.actions: Dict[Tuple[str, str, str], int] = {}
        self.answer: Optional[Feature] = None
        self.modified: set = set()
        self.decision_source: str = ''
        self.notes: List[str] = []
        self.diagnostics: Dict[str, Any] = {}

    def jsonable(self) -> Dict[str, Any]:
        d = {
            'fam': self.fam,
            'assertions': {k: v.jsonable() for k, v in self.assertions.items()},
            'decisions': {k: v.jsonable() for k, v in self.decisions.items()},
            'n_action_keys': len(self.actions),
            'answer': self.answer.jsonable() if self.answer else None,
            'n_modified_files': len(self.modified),
            'decision_source': self.decision_source,
            'notes': self.notes,
        }
        if self.diagnostics:
            d['diagnostics'] = self.diagnostics
        return d


def extract_features_v7(trace: Dict[str, Any],
                        spec: Optional[Dict[str, Any]],
                        fam: str,
                        policy: Dict[str, str] = None) -> FeaturesV7:
    """Build the four dimensions with the v7 extractors.

    ``spec`` may carry GT; it is stripped FIRST (the containment boundary).
    ``policy`` toggles the named sensitivity analyses, e.g.
    ``{'research_polarity': 'rubric'|'v6'}``.  The default keeps the v7
    primary behaviour; every non-default is a separately named variant,
    never a silent methodology change.
    """
    policy = policy or {}
    f = FeaturesV7(fam)
    gt_free = strip_gt(spec)
    all_text = authored_text_all(trace)
    final_text = final_response_text(trace)
    f.actions = action_counter(trace)
    f.modified = modified_files(trace)
    pol = policy.get('research_polarity', 'rubric')

    if fam == 'financial_analysis':
        f.assertions, d1 = extract_facts_v7(all_text, gt_free)
        f.decisions, d2 = extract_facts_v7(final_text, gt_free)
        f.answer, d3 = extract_final_answer_v7(final_text, gt_free)
        f.decision_source = ('final_response_fact_table'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2] + d3[:1]
    elif fam == 'research_synthesis':
        f.assertions, d1 = extract_claim_verdicts_v7(all_text, gt_free,
                                                      polarity=pol)
        f.decisions, d2 = extract_claim_verdicts_v7(final_text, gt_free,
                                                     polarity=pol)
        f.decision_source = ('final_response_verdicts'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    elif fam == 'code_review':
        f.assertions, d1 = extract_issue_stances_v7(all_text, gt_free)
        f.decisions, d2 = extract_issue_stances_v7(final_text, gt_free)
        f.decision_source = ('final_response_stances'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    else:
        f.notes.append(f'unknown_family:{fam}')
    return f
