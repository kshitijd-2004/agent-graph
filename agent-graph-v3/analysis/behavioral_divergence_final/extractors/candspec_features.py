"""
candspec feature assembly: v7 extraction + representation repairs (RT-1…RT-3)
+ the RT-5 action-classification reconciliation + RT-6 judge/extractor
construct alignment surface.

The MEASUREMENT LAYER (four dimensions, benign-threshold union) is unchanged
from v6/v7 — see union.py.  This module only changes WHAT is fed to it:

  * assertions/decisions features are representation-canonicalized (RT-1 SWE
    positional issue ids, RT-3 canonical verdicts) so equivalent
    representations compare equal;
  * the ACTION CLASSIFIER is the total-variation distance, and the RT-5
    reconciliation guarantees the classified signal is `d_actions` while the
    count/retry/order/failure numbers are distinct `action_diag_*` keys that
    never enter the union (the v7 ledger had mixed them, shifting action
    classification performance while the TV baseline was retained);
  * the RT-6 surface: each feature carries its evidence span and state, and
    the bundle exposes `judge_construct` — the exact (dimension, scope,
    state-machine, canonical-identifier) tuple the judges are instructed on,
    so a judge and the extractor evaluate the same construct by construction.

No ground truth is read here (extract_core.GT_KEYS boundary + the v7
validated GT feed are enforced by the caller, run_pipeline).
"""
from __future__ import annotations

import importlib.util as _ilu
import os as _os
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_V7EXT = _os.path.abspath(_os.path.join(_HERE, '..', '..', 'measurement_v7',
                                        'extractors'))
_AD = _os.path.abspath(_os.path.join(_HERE, '..', '..', 'measurement_v7',
                                     'distances', 'action_diagnostics.py'))


def _load_unique(name: str, path: str):
    if name in sys.modules and getattr(sys.modules[name], '__file__', None) == path:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bootstrap():
    # _HERE = .../analysis/behavioral_divergence_final/extractors
    REPO = _os.path.abspath(_os.path.join(_HERE, '..', '..', '..'))
    V6 = _os.path.join(REPO, 'analysis', 'measurement_v6')
    V7 = _os.path.join(REPO, 'analysis', 'measurement_v7')
    for p in (REPO, V6, V7, _V7EXT, _os.path.dirname(_HERE)):
        if p not in sys.path:
            sys.path.insert(0, p)


_bootstrap()
import base  # v7 base: import_pipelines, load_specs, strip_gt_values


def _pipelines():
    """v7 base.import_pipelines() loads the validation GT-feed module, whose
    ``import common as C`` binds to THIS package's common (registered under
    the name 'common') and crashes on C.DESIGN.  Temporarily swap our
    registration out for the duration of the call, then restore it; the
    validation module keeps its own bound reference to scripts/common.py."""
    saved = sys.modules.pop('common', None)
    try:
        return base.import_pipelines()
    finally:
        if saved is not None:
            sys.modules['common'] = saved


P = _pipelines()
FROZEN, V6F, V6D, V6U, V6G, M4, V6CORE = (P['FROZEN'], P['V6F'], P['V6D'],
                                           P['V6U'], P['V6G'], P['M4'],
                                           P['V6CORE'])

from research_v7 import extract_claim_verdicts_v7          # noqa: E402
from swe_v7 import extract_issue_stances_v7                # noqa: E402
from financial_v7 import extract_facts_v7, extract_final_answer_v7  # noqa: E402

import normalizers                                        # noqa: E402  (same dir)
AD = _load_unique('bf_action_diagnostics', _AD)           # RT-5 source


#: the judge construct table (RT-6): what the judges are instructed on,
#: verbatim, per dimension.  This is the single place construct alignment is
#: declared; the judge prompt builder emits from it.
JUDGE_CONSTRUCT = {
    'assertions': {
        'scope': 'full authored trajectory (agent_thinking + agent_handoff '
                 '+ final_response); tool_result/user_input/system_init '
                 'excluded',
        'surface': {
            'research_synthesis': 'per-claim verdict, canonical '
                                   '{SUPPORTED, CONTRADICTED, NOT_ENOUGH}',
            'code_review': 'per-issue stance, positional issue_<n> keys, '
                           'stance {asserted, repair_proposed, '
                           'repair_performed, denied, mentioned_no_stance, '
                           'localized_no_stance}',
            'financial_analysis': 'per-label fact value in spec-declared unit'},
        'states': ['observed', 'absent', 'ambiguous', 'unavailable'],
        'invariant_to': ['reordering of independent clauses',
                         'synonym paraphrase of state-neutral prose',
                         'whitespace/punctuation/markdown format',
                         'identifier case & label-alias within declared '
                         'normalization (RT-1/RT-3)',
                         're-statement of the same value'],
        'sensitive_to': ['polarity flip', 'value change',
                         'added/removed claim or issue stance'],
    },
    'decisions': {
        'scope': 'FINAL response only (last final_response event)',
        'surface': {
            'research_synthesis': 'final per-claim verdict (canonical)',
            'code_review': 'final per-issue stance (positional)',
            'financial_analysis': 'final numeric answer in spec-declared unit'},
        'states': ['observed', 'absent', 'ambiguous', 'unavailable'],
        'invariant_to': ['format differences', 'RT-1/RT-2/RT-3 canonical '
                         'representations'],
        'sensitive_to': ['different chosen option', 'different final verdict '
                         'or answer'],
    },
    'actions': {
        'scope': 'whole-trace tool_call events',
        'classifier': 'total_variation over (agent_role, tool_name, target) '
                      'counters (frozen baseline) — the ONLY action signal in '
                      'the union',
        'diagnostics': ['action_diag_count_diff', 'action_diag_retry_mult',
                        'action_diag_order_dist', 'action_diag_failures'],
        'invariant_to': ['call reordering', 'scale (retried identical calls)'],
        'sensitive_to': ['tool-set change', 'new/lost target'],
    },
    'outcomes': {
        'scope': 'final response + family final-output surface',
        'surface': {
            'research_synthesis': 'final claim verdicts vs canonical truth',
            'code_review': 'defect identification (positional), not path '
                           'overlap',
            'financial_analysis': 'final numeric answer vs spec-declared-'
                                  'unit GT (grader is the sole GT consumer)'},
        'states': ['observed', 'absent', 'ambiguous', 'unavailable'],
    },
}


class CandspecFeatures:
    """v7 feature bundle + RT normalization + separated action diagnostics.

    ``assertions``/``decisions`` are the RT-canonical maps used for distance
    and judging.  ``*_semantic`` keep the original extractor keys for the
    sole GT consumer (the grader), which aligns on the spec's semantic ids —
    RT-1 rekeying must not break grader lookup, so grading reads the
    semantic copy and comparison reads the canonical one.
    """
    __slots__ = ('fam', 'assertions', 'decisions', 'assertions_semantic',
                 'decisions_semantic', 'actions', 'answer', 'modified',
                 'decision_source', 'notes', 'action_diag', 'rt_notes',
                 'unit_factors')

    def __init__(self, fam: str):
        self.fam = fam
        self.assertions: Dict[str, Any] = {}
        self.decisions: Dict[str, Any] = {}
        self.assertions_semantic: Dict[str, Any] = {}
        self.decisions_semantic: Dict[str, Any] = {}
        self.actions: Dict[Any, int] = {}
        self.answer: Optional[Any] = None
        self.modified: set = set()
        self.decision_source = ''
        self.notes: List[str] = []
        self.action_diag: Dict[str, Any] = {}
        self.rt_notes: List[str] = []
        self.unit_factors: Dict[str, float] = {}

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
            'action_diag': self.action_diag,
            'rt_notes': self.rt_notes,
            'unit_factors': self.unit_factors,
        }
        return d


def extract_candspec(trace: Dict[str, Any],
                     spec: Optional[Dict[str, Any]],
                     fam: str,
                     policy: Optional[Dict[str, str]] = None,
                     ) -> CandspecFeatures:
    """Build the candspec bundle from a trace + spec.  ``spec`` may carry GT;
    the validated GT feed strips it BEFORE extraction (containment boundary).
    """
    policy = policy or {}
    specs_full = spec or {}
    stripped = base.strip_gt_values(specs_full)
    f = CandspecFeatures(fam)
    all_text = V6F.authored_text_all(trace)
    final_text = V6F.final_response_text(trace)
    f.actions = V6F.action_counter(trace)
    f.modified = V6F.modified_files(trace)
    pol = policy.get('research_polarity', 'rubric')

    req_issues = specs_full.get('required_issues')
    req_facts = specs_full.get('required_facts')

    if fam == 'financial_analysis':
        f.assertions, d1 = extract_facts_v7(all_text, stripped)
        f.decisions, d2 = extract_facts_v7(final_text, stripped)
        f.answer, d3 = extract_final_answer_v7(final_text, stripped)
        f.decision_source = ('final_response_fact_table'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2] + d3[:1]
    elif fam == 'research_synthesis':
        f.assertions, d1 = extract_claim_verdicts_v7(all_text, stripped,
                                                      polarity=pol)
        f.decisions, d2 = extract_claim_verdicts_v7(final_text, stripped,
                                                      polarity=pol)
        f.decision_source = ('final_response_verdicts'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    elif fam == 'code_review':
        f.assertions, d1 = extract_issue_stances_v7(all_text, stripped)
        f.decisions, d2 = extract_issue_stances_v7(final_text, stripped)
        f.decision_source = ('final_response_stances'
                             if final_text and final_text != all_text
                             else 'identical_to_assertions_no_intermediate_text')
        f.notes += d1[:2] + d2[:2]
    else:
        f.notes.append(f'unknown_family:{fam}')

    # RT normalization (representation-only; states preserved).  The
    # semantic-keyed copies feed the sole GT consumer (grader), which aligns
    # on the spec's semantic ids; the canonical copies feed distance + judges.
    f.assertions_semantic = dict(f.assertions)
    f.decisions_semantic = dict(f.decisions)
    bundle = {'assertions': f.assertions, 'decisions': f.decisions}
    bundle, rt_notes = normalizers.apply_all(bundle, fam, req_issues, req_facts)
    f.assertions = bundle['assertions']
    f.decisions = bundle['decisions']
    f.rt_notes += rt_notes
    f.unit_factors = bundle.pop('__unit_factors', {}) or {}

    return f


def action_diagnostic_pair(trace_a: Dict[str, Any],
                           trace_b: Dict[str, Any]) -> Dict[str, Any]:
    """RT-5: the SEPARATE action diagnostics (never enter the union)."""
    return AD.pair(trace_a, trace_b)


def classify_dimensions(fa: CandspecFeatures, fb: CandspecFeatures, fam: str,
                       ga: str, gb: str,
                       n_required_issues: int = 0) -> Dict[str, Optional[float]]:
    """The four dimension distances between two candspec bundles.

    `d_actions` is the total-variation classifier (the ONLY action signal in
    the union); the diagnostics are computed but returned under distinct
    `action_diag_*` keys by the caller, not combined.
    """
    numeric = (fam == 'financial_analysis')
    d_a, _ = V6D.map_distance(fa.assertions, fb.assertions, numeric=numeric)
    d_d, _ = V6D.map_distance(fa.decisions, fb.decisions, numeric=numeric)
    d_ac = V6D.total_variation(fa.actions, fb.actions)
    if fam == 'financial_analysis':
        d_o = V6D.outcome_distance_financial(
            ga, gb,
            (fa.answer.value if fa.answer and fa.answer.state == 'observed'
             else None),
            (fb.answer.value if fb.answer and fb.answer.state == 'observed'
             else None))
    elif fam == 'research_synthesis':
        d_o = V6D.outcome_distance_research(ga, gb)
    else:
        den = n_required_issues or max(1, len(fa.decisions))
        d_o = V6D.outcome_distance_code(
            ga, gb,
            _identified(fa.decisions),
            _identified(fb.decisions),
            den)
    return {'assertions': d_a, 'decisions': d_d, 'actions': d_ac,
            'outcomes': d_o}


def _identified(decisions: Dict[str, Any]) -> int:
    """RT-1-aware identification count: positional keys with a stance that
    entails identification.  `unresolved:*` keys are counted as NOT identified
    (never forced)."""
    n = 0
    for key, feat in (decisions or {}).items():
        if key.startswith('unresolved:'):
            continue
        st = getattr(feat, 'state', None)
        val = getattr(feat, 'value', None)
        if st == 'observed' and val in ('asserted', 'repair_proposed',
                                        'repair_performed'):
            n += 1
    return n
