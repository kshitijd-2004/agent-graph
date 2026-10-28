"""Baseline-normalized behavioral deviation (BND) — an independent, experimental
analyzer for AgentProp production traces.

This module measures, for one perturbed (LEP) execution P and its three matched
benign reference executions B1..B3, how far P deviates from normal agent
behavior *relative to* how far the benign executions deviate from one another.
It is NOT a propagation detector and does NOT attribute any deviation to the
LEP; it is a baseline-relative, task-aware behavioral distance.

Distance model
--------------
For each execution we extract a small set of task-aware features (never raw
trace text):

* assertions   — the *content* the run actually produced: named financial
                 fact values, paper/study verdicts and their evidence, or
                 identified SWE repair targets.
* decisions    — the *judgments* made: overall claim verdicts, repair
                 verdicts, or "which issue is the root cause".
* actions      — the *observable behavior*: the multiset of relevant tool
                 actions, aligned by (agent_role, tool_name, target), not by
                 absolute sequence index. Reordering independent actions must
                 not move this feature.
* outcomes     — the *final deliverable*: whether/what final answer, verdict
                 line, or patch was actually emitted.

Pairwise distance d(A,B) is a transparent weighted mean of the four
component distances, each already normalized to [0,1] via a bounded kernel
(clamp / Jaccard / total-variation). Formatting, verbosity, or trace length
cannot exceed [0,1] by construction; only task-meaningful differences move the
score.

BND definition
--------------
    D_benign = mean( d(B1,B2), d(B1,B3), d(B2,B3) )
    D_LEP    = mean( d(P,B1), d(P,B2), d(P,B3) )
    BND      = D_LEP - D_benign

A positive BND is *excess deviation relative to the observed benign spread*,
not a claim that the LEP propagated. It is an experimental, unvalidated score;
no universal threshold is asserted.

Isolation
---------
This file does not import, subclass, or modify
``benchmark/propagation_analysis.py``, ``benchmark/behavioral_anomaly.py``,
``benchmark/id_trc_v2.py``, ``benchmark/evidence_propagation.py``, or
``scripts/analyze_propagation.py``. It reads traces and specs read-only.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = 'agentprop-baseline-deviation-v1'

# ── component names, fixed order for deterministic output ───────────────────
COMPONENTS: Tuple[str, ...] = ('assertions', 'decisions', 'actions', 'outcomes')

#: Experimental starting weights. These are NOT validated; they are a tunable
#: knob that callers can override via ``ComponentWeights``.
DEFAULT_WEIGHTS: Dict[str, float] = {
    'assertions': 0.40,
    'decisions':  0.25,
    'actions':    0.25,
    'outcomes':   0.10,
}


@dataclass(frozen=True)
class ComponentWeights:
    """Configurable component weights. Default weights are experimental."""
    assertions: float = DEFAULT_WEIGHTS['assertions']
    decisions:  float = DEFAULT_WEIGHTS['decisions']
    actions:    float = DEFAULT_WEIGHTS['actions']
    outcomes:   float = DEFAULT_WEIGHTS['outcomes']

    def as_dict(self) -> Dict[str, float]:
        return {'assertions': self.assertions, 'decisions': self.decisions,
                'actions': self.actions, 'outcomes': self.outcomes}

    @classmethod
    def from_kwargs(cls, **kw: float) -> 'ComponentWeights':
        valid = {f for f in cls.__dataclass_fields__}
        bad = [k for k in kw if k not in valid]
        if bad:
            raise ValueError(f'unknown weight key(s): {bad}')
        return cls(**kw)


# ── generic bounded kernels (all map to [0,1] by construction) ───────────────


def clamp_unit(x: Any) -> float:
    """Clamp a raw kernel value to [0, 1]. NaN/None collapse to 1.0 (max)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 1.0
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return float(x)


def bounded_ratio(x: Any, scale: float) -> float:
    """|x| / max(|x|, |scale|) clamped to [0,1]; safe when both are ~0."""
    if x is None:
        return 0.0
    denom = max(abs(float(x)), abs(float(scale)) if scale else 0.0)
    if denom <= 0:
        return 0.0
    return clamp_unit(abs(float(x)) / denom)


def jaccard_dist(a: Iterable, b: Iterable) -> float:
    """Set distance: 1 - |A∩B| / |A∪B|. 0 when both are the same (incl. ∅)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return 1.0 - len(sa & sb) / len(union)


def total_variation(a: Dict[Any, float], b: Dict[Any, float]) -> float:
    """0.5 * Σ_k |p_k - q_k| over the union of keys, p,q normalized.

    This is the TV distance on (role, tool, target) action counters; reordering
    independent actions within a branch leaves the multiset unchanged, so it
    does not contribute.
    """
    keys = set(a) | set(b)
    sa, sb = sum(a.values()), sum(b.values())
    if sa == 0 and sb == 0:
        return 0.0
    half = 0.0
    for k in keys:
        pa = (a.get(k, 0) / sa) if sa else 0.0
        pb = (b.get(k, 0) / sb) if sb else 0.0
        half += abs(pa - pb)
    return clamp_unit(half / 2.0)


# ── light trace access helpers (work on raw dict traces) ────────────────────


def trace_cell(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Identity fields that must match for a valid baseline comparison.

    Values fall back to the admission-contract scenario when the top-level
    metadata lacks them; fields that disagree between LEP and references for
    a legitimate reason (``condition``) are excluded from the match set.
    """
    m = trace.get('metadata', {})
    scen = (m.get('admission_contract') or {}).get('scenario') or {}
    wf = scen.get('workflow_config') or {}
    return {
        'fixture_id': m.get('fixture_id') or scen.get('fixture_id'),
        'task_family': m.get('task_family') or scen.get('task_family'),
        'task_variant': m.get('task_variant') or scen.get('task_variant'),
        'topology': m.get('topology') or wf.get('topology'),
        'execution_variant': m.get('execution_variant') or wf.get('memory_mode'),
        'propagation_mode': m.get('propagation_mode') or wf.get('propagation_mode'),
        'model_name': m.get('model_name') or wf.get('model_name'),
        'temperature': m.get('temperature', wf.get('temperature')),
        'seed': m.get('seed', wf.get('seed')),
        'parallel': bool(wf.get('allow_parallel_agents')),
    }


def execution_shape(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Recorded scheduling skeleton (schedule-compatible, order-free).

    Serial serial-worker schedules and coordinator-first parallel schedules
    execute the same *operations* but with a different dependency structure;
    comparing them as if they were the same workflow would turn scheduling
    noise into "deviation".  This mirrors the v2 shape check read-only:
    cross-role transfer links, initial roles, and fan-out multiplicities,
    which are unaffected by event interleaving inside a branch.
    """
    import collections
    events = trace.get('events', [])
    by_id = {e.get('event_id'): e for e in events}
    transfers = set()
    roots: List[str] = []
    for e in events:
        if e.get('event_type') != 'topology_transition':
            continue
        role = e.get('agent_role') or e.get('agent_id') or '?'
        for p in e.get('depends_on') or []:
            src = by_id.get(p)
            src_role = (src.get('agent_role') or src.get('agent_id')) if src else None
            if src_role and src_role != role:
                transfers.add((src_role, role))
        if not (e.get('depends_on') or []):
            roots.append(role)
    return {
        'initial_roles': sorted(set(roots)),
        'transfers': sorted(transfers),
    }


def _tool_calls(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for e in trace.get('events', []):
        if e.get('event_type') == 'tool_call' and e.get('tool_name'):
            out.append(e)
    return out


def _final_response_text(trace: Dict[str, Any]) -> str:
    for e in trace.get('events', []):
        if e.get('event_type') == 'final_response':
            return e.get('output_text') or ''
    return ''


def _emitted_text(trace: Dict[str, Any]) -> str:
    """Agent-emitted natural-language content only (final, handoffs, memory writes).

    Tool results and LEP boundaries are deliberately excluded: those are
    inputs the agent observed, not behavior it produced.
    """
    parts: List[str] = []
    for e in trace.get('events', []):
        et = e.get('event_type')
        if et == 'final_response':
            parts.append(e.get('output_text') or '')
        elif et == 'agent_handoff':
            parts.append(e.get('output_text') or '')
        elif et == 'memory_write' and e.get('tool_arguments'):
            args = e.get('tool_arguments') or {}
            if isinstance(args.get('content'), str):
                parts.append(args['content'])
        elif et == 'tool_call' and e.get('tool_name') == 'write_file':
            args = e.get('tool_arguments') or {}
            if isinstance(args.get('content'), str):
                parts.append(args['content'])
    return '\n'.join(p for p in parts if p)


def _norm(s: Optional[str]) -> str:
    if not s:
        return ''
    import re
    s = s.lower()
    s = s.replace(' ', ' ')
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def _code_paths(trace: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for e in _tool_calls(trace):
        if e.get('tool_name') == 'write_file':
            args = e.get('tool_arguments') or {}
            p = args.get('path')
            if isinstance(p, str) and p:
                out.append(p)
    return sorted(set(out))


def _action_counters(trace: Dict[str, Any]) -> Dict[Tuple[str, str, str], int]:
    """(agent_role, tool_name, target) -> count.  Task-aligned, index-free."""
    import collections
    c: 'collections.Counter' = collections.Counter()
    for e in _tool_calls(trace):
        role = e.get('agent_role') or e.get('agent_id') or '?'
        tool = e.get('tool_name') or '?'
        args = e.get('tool_arguments') or {}
        target = (args.get('path')
                  or e.get('document_path')
                  or e.get('memory_key')
                  or args.get('key')
                  or args.get('query')
                  or args.get('target_agent')
                  or '')
        if not isinstance(target, str):
            target = ''
        c[(role, tool, target)] += 1
    return dict(c)


# ── task-aware feature extraction ────────────────────────────────────────────


@dataclass
class Features:
    """Extracted task-aware features for one trace.

    Each of the four component fields may be None when no task-meaningful
    information is available (reported, never silently treated as 0).
    """
    trace_id: str
    condition: str

    # assertions
    financial_facts: Optional[Dict[str, Any]] = None   # key -> float|None
    claim_verdicts:  Optional[Dict[str, str]] = None   # claim_id -> verdict
    repair_targets:   Optional[Dict[str, bool]] = None  # issue_id -> mentioned
    # decisions
    overall_verdict: Optional[str] = None
    decision: Optional[str] = None
    # actions
    action_counters: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    # outcomes
    final_answer_present: bool = False
    final_answer_value: Optional[float] = None
    verdict_lines_present: bool = False
    patch_files: List[str] = field(default_factory=list)

    diagnostics: List[str] = field(default_factory=list)

    def coverage(self) -> Dict[str, bool]:
        """Which component families produced usable information."""
        return {
            'assertions': any(v is not None for v in
                              (self.financial_facts, self.claim_verdicts, self.repair_targets)),
            'decisions': self.overall_verdict is not None or self.decision is not None,
            'actions': bool(self.action_counters),
            'outcomes': self.final_answer_present or self.verdict_lines_present
                        or bool(self.patch_files),
        }


_NUM = r'[-+]?\d[\d,]*(?:\.\d+)?'


def _extract_financial_facts(text: str, spec: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Map each spec ``required_facts`` key to a float value if resolvable.

    A fact key's *label* anchors its signature (the label's non-year words).
    Candidate numbers are those on a line that carries the signature, which
    covers both "label (year): $N" and "$N label" phrasings; the label's own
    year tokens are excluded so "(2012)" inside the label is never captured
    as the fact value.  Figures are rescaled to the spec's unit scale when
    the difference is an exact power of 1000 (unit phrasing is not
    behavioral deviation).  Facts that are not stated are left None
    (reported as unavailable, never scored as 0).
    """
    import re
    out: Dict[str, Optional[float]] = {}
    facts = (spec or {}).get('required_facts') or {}
    lines = [_norm(line) for line in (text or '').splitlines() if _norm(line)]
    num_re = re.compile(r'(?<![\d,.])([-+]?\d[\d,]*(?:\.\d+)?)(?![\d,.])')
    for key, meta in facts.items():
        if key in ('answer', 'answer_calculation'):
            continue
        label = meta.get('label') if isinstance(meta, dict) else None
        if not label:
            out[key] = None
            continue
        label_tokens = re.findall(r'[a-z0-9]+', _norm(str(label)).replace('&', ' '))
        # Numeric label tokens (years "(2012)", accession ids "(349615)",
        # footnote markers "(1)") are part of the label, never the value.
        numeric_tokens = {t for t in label_tokens if t.isdigit()}
        sig = [t for t in label_tokens if not t.isdigit()]
        out[key] = None
        if not sig:
            continue

        def line_score(line: str) -> int:
            return sum(2 if len(t) >= 5 else 1
                       for t in sig if re.search(r'\b' + t + r'\b', line))

        # Candidate numbers are those on a line that carries the fact's own
        # signature words.  The value-after-label phrasing ("label (2012):
        # $6 million") is preferred over value-before-label, so a
        # source-citation figure like "page 93" never wins.  Label-internal
        # numeric tokens are never captured as the fact value.
        best_after, best_before = 0, 0
        val_after = val_before = None
        for line in lines:
            s = line_score(line)
            if s < 2:
                continue
            sig_positions = [mm.end() for t in sig for mm in re.finditer(r'\b' + t + r'\b', line)]
            sig_end = max(sig_positions) if sig_positions else 0
            for m in num_re.finditer(line):
                raw = m.group(1).replace(',', '')
                if raw in numeric_tokens:
                    continue
                # A source-citation number ("page 93", "125 pages") is not a
                # fact value even when it sits on the label's own line.
                if re.search(r'(?i)(?:page|pages|pp?\.?)\s*$', line[:m.start()].rstrip()) or \
                   re.match(r'(?i)^\s*(?:pages?)\b', line[m.end():]):
                    continue
                val = _to_float(m.group(1))
                if val is None:
                    continue
                # "after": the number follows the signature on this line
                if sig_end and m.start() > sig_end:
                    if s > best_after:
                        best_after, val_after = s, val
                elif s > best_before:
                    best_before, val_before = s, val
        if best_after >= 2:
            out[key] = _normalize_fact_scale(val_after, meta)
        elif best_before >= 2:
            out[key] = _normalize_fact_scale(val_before, meta)
    return out


def _normalize_fact_scale(value: Optional[float], meta: Dict[str, Any]) -> Optional[float]:
    """Align an extracted figure to the spec's numeric scale when obvious.

    Agents state "$4,338,589,000" for a spec truth of 4,338,589 thousands.
    When the extracted value differs from the spec value by an *exact power
    of 1000*, both sides of any later pairwise comparison are rescaled to
    the spec unit so unit phrasing does not read as behavioral deviation.
    Values that are not a clean power-of-1000 multiple of the truth are
    left untouched: those are genuine (or corrupted) figures.
    """
    if value is None or 'value' not in (meta or {}):
        return value
    truth = meta.get('value')
    if not isinstance(truth, (int, float)) or truth == 0:
        return value
    ratio = value / truth
    for factor in (1e-9, 1e-6, 1e-3, 1.0, 1e3, 1e6, 1e9):
        if abs(ratio - factor) / max(abs(factor), 1e-12) < 1e-3:
            return value / factor
    return value


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).replace(',', ''))
    except (ValueError, TypeError):
        return None


_VERDICT_KW = {
    'NOT_ENOUGH':     ('not enough', 'insufficient', 'lacks sufficient', 'cannot determine', 'inconclusive', 'undetermined', 'no evidence'),
    'CONTRADICTED':   ('contradict', 'refut', 'inconsistent', 'fals'),
    'SUPPORTED':      ('support', 'corroborat', 'confirmed', 'consistent with', 'is valid'),
}


def _extract_claims(text: str, spec: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """claim_N -> SUPPORTED | CONTRADICTED | NOT_ENOUGH | UNKNOWN."""
    facts = (spec or {}).get('required_facts') or {}
    claim_ids = [k for k in facts if isinstance(k, str) and k.startswith('claim_') and k.endswith('_verdict')]
    if not claim_ids:
        claim_ids = ['claim_1', 'claim_2']
    import re
    norm_text = _norm(text)
    out: Dict[str, str] = {}
    for cid in claim_ids:
        claim_idx = cid.replace('claim_', '').replace('_verdict', '')
        # Window: "claim <idx> ... " up to the next claim marker.  Boundary on a
        # non-digit so "claim 1" never matches "claim 10".
        verdict = 'UNKNOWN'
        m = re.search(r'claim ' + re.escape(claim_idx) + r'(?!\d)', norm_text)
        if m:
            cut = re.search(r'claim \d+(?!\d)', norm_text[m.end():])
            tail = norm_text[m.end(): m.end() + (cut.start() if cut else 400)]
            for label in ('NOT_ENOUGH', 'CONTRADICTED', 'SUPPORTED'):
                if any(kw in tail for kw in _VERDICT_KW[label]):
                    verdict = label
                    break
        out[cid] = verdict
    return out


def _extract_repair_targets(text: str, spec: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """For each required SWE issue, whether its signature appears in the text."""
    issues = (spec or {}).get('required_issues') or []
    norm_text = _norm(text)
    out: Dict[str, bool] = {}
    for issue in issues:
        iid = str(issue.get('id') or issue.get('key') or issue.get('function') or len(out))
        sigs = [s for s in (issue.get('location'), issue.get('function'), issue.get('id')) if s]
        out[iid] = any(_norm(str(s)) in norm_text for s in sigs)
    return out


def extract_features(trace: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> Features:
    fam = trace_cell(trace).get('task_family')
    condition = trace_cell(trace).get('condition')
    emitted = _emitted_text(trace)
    feats = Features(trace_id=trace.get('trace_id', ''), condition=condition or '')
    feats.action_counters = _action_counters(trace)

    if fam in ('financial_analysis',):
        feats.financial_facts = _extract_financial_facts(emitted, spec)
        fr = _final_response_text(trace)
        import re
        m = re.findall(r'([-+]?\d[\d,]*(?:\.\d+)?)\s*%', _norm(fr))
        if m:
            feats.final_answer_value = _to_float(m[-1])
            feats.final_answer_present = True
        elif fr.strip():
            feats.final_answer_present = True
        if not feats.financial_facts:
            feats.diagnostics.append('no_financial_facts_in_spec')

    elif fam == 'research_synthesis':
        feats.claim_verdicts = _extract_claims(emitted, spec)
        fr = _final_response_text(trace)
        for verdict in ('SUPPORTED', 'CONTRADICTED', 'NOT_ENOUGH'):
            if any(kw in _norm(fr) for kw in _VERDICT_KW[verdict]):
                feats.overall_verdict = verdict
                feats.verdict_lines_present = True
                break
        if not feats.claim_verdicts:
            feats.diagnostics.append('no_claims_in_spec')

    elif fam == 'code_review':
        feats.repair_targets = _extract_repair_targets(emitted, spec)
        feats.patch_files = _code_paths(trace)
        fr = _norm(_final_response_text(trace))
        if any(kw in fr for kw in ('no bug', 'not a bug', 'no defect', 'already fixed')):
            feats.decision = 'NO_DEFECT'
        elif any(kw in fr for kw in ('root cause', 'fix', 'patch', 'bug')):
            feats.decision = 'DEFECT_IDENTIFIED'
        if not feats.repair_targets:
            feats.diagnostics.append('no_required_issues_in_spec')
        if feats.decision is None:
            feats.diagnostics.append('code_review_decision_unresolved')

    else:
        feats.diagnostics.append(f'unhandled_task_family:{fam}')

    return feats


# ── pairwise distance ────────────────────────────────────────────────────────


@dataclass
class Distance:
    """Task-aware bounded distance between two executions (each value in [0,1]).

    Any component value of ``None`` means that component could not be computed
    for this pair (unavailable) and is NOT folded into the aggregate as 0.
    """
    trace_a: str
    trace_b: str
    assertions: Optional[float] = None
    decisions: Optional[float] = None
    actions: Optional[float] = None
    outcomes: Optional[float] = None
    unavailable: Tuple[str, ...] = ()
    total: Optional[float] = None

    def by_component(self, weights: ComponentWeights) -> Dict[str, Optional[float]]:
        return {
            'assertions': self.assertions,
            'decisions': self.decisions,
            'actions': self.actions,
            'outcomes': self.outcomes,
        }

    def total_with_weights(self, weights: ComponentWeights,
                           available_components: Iterable[str] = None) -> Optional[float]:
        """Weighted mean over the components that are available for this pair.

        If *available_components* is given (e.g. the union over all three B_i),
        it fixes the denominator so every pair in the same comparison uses the
        same scale — this is what makes D_benign and D_LEP comparable.
        """
        base = self.by_component(weights)
        avail = [c for c in COMPONENTS
                 if (available_components is None or c in available_components)
                 and base[c] is not None and weights.as_dict()[c] > 0]
        if not avail:
            return None
        wsum = sum(weights.as_dict()[c] for c in avail)
        if wsum <= 0:
            return None
        val = sum(weights.as_dict()[c] * base[c] for c in avail) / wsum
        return clamp_unit(val)


def _pair_assertions(fa: Features, fb: Features, fam: str) -> Optional[float]:
    if fam in ('financial_analysis',):
        a = fa.financial_facts or {}
        b = fb.financial_facts or {}
        if not a or not b:
            return None
        keys = set(a) | set(b)
        vals = []
        for k in keys:
            va, vb = a.get(k), b.get(k)
            if va is None or vb is None:
                continue
            # Relative kernel: difference scaled by the larger magnitude so a
            # 1.0 absolute change on a $1,000,000 figure is ~0, not 1.
            vals.append(bounded_ratio(va - vb, max(abs(va), abs(vb), 1.0)))
        if not vals:
            return None
        return clamp_unit(sum(vals) / len(vals))

    if fam == 'research_synthesis':
        a = fa.claim_verdicts or {}
        b = fb.claim_verdicts or {}
        if not a or not b:
            return None
        keys = set(a) | set(b)
        vals = [0.0 if a.get(k) == b.get(k) else 1.0 for k in keys]
        return clamp_unit(sum(vals) / len(vals))

    if fam == 'code_review':
        a = fa.repair_targets or {}
        b = fb.repair_targets or {}
        if not a or not b:
            return None
        keys = set(a) | set(b)
        miss = sum(1 for k in keys if a.get(k) != b.get(k))
        return clamp_unit(miss / len(keys))

    return None


def _pair_decisions(fa: Features, fb: Features, fam: str) -> Optional[float]:
    if fam == 'financial_analysis':
        # No discrete decision family; use the final numeric answer if present.
        va, vb = fa.final_answer_value, fb.final_answer_value
        if va is None or vb is None:
            return None
        return bounded_ratio(va - vb, max(abs(va), abs(vb), 1.0))
    if fam == 'research_synthesis':
        if fa.overall_verdict is None or fb.overall_verdict is None:
            return None
        return 0.0 if fa.overall_verdict == fb.overall_verdict else 1.0
    if fam == 'code_review':
        if fa.decision is None or fb.decision is None:
            return None
        return 0.0 if fa.decision == fb.decision else 1.0
    return None


def _pair_actions(fa: Features, fb: Features) -> Optional[float]:
    a, b = fa.action_counters, fb.action_counters
    if not a and not b:
        # No observable actions in either: nothing to compare.  Report 0 rather
        # than unavailable, because both runs are *genuinely* action-silent.
        return 0.0
    if not a or not b:
        # One side is action-silent, the other is not: that is a real
        # behavioral difference, not missing data.
        return 1.0
    return total_variation(a, b)


def _pair_outcomes(fa: Features, fb: Features, fam: str) -> Optional[float]:
    if fam == 'code_review':
        return jaccard_dist(set(fa.patch_files), set(fb.patch_files))
    if fam == 'research_synthesis':
        va, vb = fa.verdict_lines_present, fb.verdict_lines_present
        return 0.0 if va == vb else 1.0
    if fam in ('financial_analysis',):
        va, vb = fa.final_answer_present, fb.final_answer_present
        if not va and not vb:
            return None
        return 0.0 if va == vb else 1.0
    return None


def pairwise_distance(fa: Features, fb: Features, fam: str,
                      weights: ComponentWeights = None,
                      available_components: Iterable[str] = None) -> Distance:
    """Task-aware bounded distance between two extracted feature sets."""
    weights = weights or ComponentWeights()
    d = Distance(trace_a=fa.trace_id, trace_b=fb.trace_id)
    d.assertions = _pair_assertions(fa, fb, fam)
    d.decisions = _pair_decisions(fa, fb, fam)
    d.actions = _pair_actions(fa, fb)
    d.outcomes = _pair_outcomes(fa, fb, fam)
    d.unavailable = tuple(c for c in COMPONENTS if getattr(d, c) is None)
    d.total = d.total_with_weights(weights, available_components)
    return d


# ── comparability / gating ───────────────────────────────────────────────────


HARD_IDENTITY_FIELDS = ('fixture_id', 'task_family', 'task_variant', 'topology', 'parallel')
# Soft: reported as diagnostics, never hard gates.  Propagation mode is a
# scenario-level property of the LEP condition; benign references are run
# single-origin only, so a many_to_one/one_to_many LEP against single_origin
# references is the expected matched population, not an identity mismatch
# (the execution-shape gate catches genuinely incompatible structures).
SOFT_IDENTITY_FIELDS = ('execution_variant', 'model_name', 'temperature', 'seed',
                       'propagation_mode')


def identity_mismatches(lep_cell: Dict[str, Any], ref_cell: Dict[str, Any]) -> Dict[str, List[str]]:
    """Hard and soft identity disagreements with one reference.

    Hard fields must agree when both sides carry values; a field that is
    missing on either side (e.g. a reference written without an admission
    contract) is *unverifiable* and is reported as a soft diagnostic, not a
    hard mismatch, because the cell assignment (fixture/topology/variant/
    repetition) already verified the grouping.
    """
    hard, soft = [], []
    for field in HARD_IDENTITY_FIELDS:
        if lep_cell.get(field) != ref_cell.get(field):
            hard.append(field)
    for field in SOFT_IDENTITY_FIELDS:
        lv, rv = lep_cell.get(field), ref_cell.get(field)
        if lv == rv:
            continue
        if lv is None or rv is None:
            soft.append(f'{field}:unverifiable')
        else:
            # A soft field that is present on both sides but differs is a
            # reported diagnostic, not a gate.  The execution-shape check is
            # the structural arbiter; e.g. a many_to_one LEP against
            # single_origin references is the expected matched population.
            soft.append(f'{field}:mismatch({lv} vs {rv})')
    return {'hard': hard, 'soft': soft}


def is_comparable(lep: Dict[str, Any], refs: Sequence[Dict[str, Any]],
                  max_benign_only_spread: float = 1.0) -> Dict[str, Any]:
    """Gating check for a meaningful BND comparison.

    Returns a dict with:
      * ``comparable`` — bool
      * ``reasons`` — list of strings explaining why NOT (empty when comparable)
      * ``population_ok`` — bool, whether the references genuinely share one cell
      * ``structural_ok`` — bool, whether their execution shapes agree
      * ``spread_ok`` — bool, whether their pairwise BND spread is bounded
    """
    reasons: List[str] = []
    if len(refs) < 3:
        reasons.append(f'need_at_least_3_benign_references:{len(refs)}')
        return {'comparable': False, 'reasons': reasons, 'soft_diagnostics': [],
                'population_ok': False, 'structural_ok': False, 'spread_ok': False}

    lep_cell = trace_cell(lep)
    soft_diagnostics: List[str] = []
    for i, r in enumerate(refs):
        mismatch = identity_mismatches(lep_cell, trace_cell(r))
        if mismatch['hard']:
            reasons.append(f'reference[{i}]_identity_mismatch:{mismatch["hard"]}')
        for s in mismatch['soft']:
            soft_diagnostics.append(f'reference[{i}]_unverifiable:{s}')
    # population: all refs must share one cell.  A field missing on some
    # references is unverifiable, not a population split (the cell key used
    # for grouping already verified fixture/topology/variant/rep equality).
    ref_cells = [trace_cell(r) for r in refs]
    pop_mismatches: set = set()
    for k in ('fixture_id', 'task_family', 'topology'):
        vals = {c.get(k) for c in ref_cells if c.get(k) is not None}
        if len(vals) > 1:
            pop_mismatches.add(k)
    population_ok = not pop_mismatches
    if pop_mismatches:
        reasons.append(f'reference_population_mismatch:{sorted(pop_mismatches)}')
    # structural: the recorded scheduling skeleton must agree across all refs
    # and the LEP trace.  Serial vs coordinator-first parallel references
    # have different shapes even when they execute the same operations.
    shapes = [execution_shape(lep)] + [execution_shape(r) for r in refs]
    structural_ok = all(s == shapes[0] for s in shapes)
    if not structural_ok:
        reasons.append('execution_structure_mismatch')
    return {
        'comparable': not reasons,
        'reasons': reasons,
        'soft_diagnostics': soft_diagnostics,
        'population_ok': population_ok,
        'structural_ok': structural_ok,
    }


# ── BND scoring ──────────────────────────────────────────────────────────────


@dataclass
class BNDResult:
    trace_id: str
    lep_code: str
    task_family: str
    topology: str
    propagation_mode: str
    references: List[str]
    comparable: bool
    reasons: List[str]
    soft_diagnostics: List[str]

    d_lep: Optional[float]
    d_benign: Optional[float]
    bnd: Optional[float]
    # per-component distances for P vs B_i
    component_lep: Dict[str, Optional[float]]
    component_benign: Dict[str, Optional[float]]
    # per-pair distances
    pair_benign: List[Distance]
    pair_lep: List[Distance]

    def summary(self) -> Dict[str, Any]:
        return {
            'trace_id': self.trace_id,
            'lep_code': self.lep_code,
            'task_family': self.task_family,
            'topology': self.topology,
            'propagation_mode': self.propagation_mode,
            'references': self.references,
            'comparable': self.comparable,
            'reasons': self.reasons,
            'soft_diagnostics': self.soft_diagnostics,
            'd_lep': self.d_lep,
            'd_benign': self.d_benign,
            'bnd': self.bnd,
            'component_lep': self.component_lep,
            'component_benign': self.component_benign,
        }

    def payload(self, weights: ComponentWeights = None,
                features: Dict[str, Dict[str, Any]] = None) -> Dict[str, Any]:
        """JSON-serializable full measurement (raw scores, never a binary-only view).

        ``features`` maps trace_id -> extracted Features, included so the
        per-trace diagnostic rows carry the raw evidence behind each score.
        """
        weights = weights or ComponentWeights()
        # The threshold/anomaly flag is only meaningful when the baseline is
        # comparable; on incomparable references it must not be forced.
        decision = threshold_decision(self) if self.comparable else {'t': None, 'behavioral_anomaly': None}
        loo = reference_only_scores(self)
        out = {
            'version': VERSION,
            'trace_id': self.trace_id,
            'lep_code': self.lep_code,
            'task_family': self.task_family,
            'topology': self.topology,
            'propagation_mode': self.propagation_mode,
            'references': self.references,
            'comparable': self.comparable,
            'reasons': self.reasons,
            'soft_diagnostics': self.soft_diagnostics,
            'd_lep': self.d_lep,
            'd_benign': self.d_benign,
            'bnd': self.bnd,
            'component_lep': _jsonable(self.component_lep),
            'component_benign': _jsonable(self.component_benign),
            'weights': weights.as_dict(),
            'pair_benign': [{'a': d.trace_a, 'b': d.trace_b,
                             'assertions': d.assertions, 'decisions': d.decisions,
                             'actions': d.actions, 'outcomes': d.outcomes,
                             'unavailable': list(d.unavailable), 'total': d.total}
                            for d in self.pair_benign],
            'pair_lep': [{'b': d.trace_b,
                          'assertions': d.assertions, 'decisions': d.decisions,
                          'actions': d.actions, 'outcomes': d.outcomes,
                          'unavailable': list(d.unavailable), 'total': d.total}
                         for d in self.pair_lep],
            'threshold': decision,
            'behavioral_anomaly': decision['behavioral_anomaly'],
            'reference_only': _jsonable(loo),
            'features': {k: _jsonable(v) for k, v in (features or {}).items()},
        }
        return out


def threshold_decision(result: BNDResult) -> Dict[str, Optional[float]]:
    """Experimental per-configuration anomaly threshold T and the D_LEP > T flag.

    T is the largest observed pairwise benign distance in this configuration.
    The flag is an *exploratory heuristic* (baseline-relative, not a
    statistically validated threshold) and is kept strictly separate from
    any propagation classification.
    """
    t = max((d.total for d in result.pair_benign if d.total is not None), default=None)
    if result.d_lep is None or t is None:
        return {'t': None, 'behavioral_anomaly': None}
    return {'t': t, 'behavioral_anomaly': bool(result.d_lep > t)}


def reference_only_scores(result: BNDResult) -> Dict[str, Optional[float]]:
    """Leave-one-out reference-only deviations (diagnostic, not a p-value).

    With three references the pairwise list is [(0,1),(0,2),(1,2)]; the
    leave-one-out score for B_i is the mean of the pairwise distances that
    include i. These bound the *reference-only* spread; comparing the LEP
    score against them is a diagnostic check, never a calibrated p-value.
    """
    if len(result.pair_benign) != 3:
        return {}
    pairs = [(0, 1), (0, 2), (1, 2)]
    include: Dict[int, List[Optional[float]]] = {0: [], 1: [], 2: []}
    for (i, j), d in zip(pairs, result.pair_benign):
        if d.total is None:
            continue
        include[i].append(d.total)
        include[j].append(d.total)
    ids = result.references
    out: Dict[str, Optional[float]] = {}
    for k in range(3):
        name = f'B{k + 1}' + (f'({ids[k]})' if len(ids) == 3 else '')
        out[name] = (sum(include[k]) / len(include[k])) if include[k] else None
    return out


def score_bnd(lep: Dict[str, Any], refs: Sequence[Dict[str, Any]],
               spec: Optional[Dict[str, Any]],
               weights: ComponentWeights = None) -> BNDResult:
    """Full BND computation for one LEP trace against its three benign refs.

    No LEP-specific assumption is baked in: the perturbed trace is compared to
    the benign references on exactly the same task-aware features.
    """
    weights = weights or ComponentWeights()
    fam = trace_cell(lep).get('task_family') or ''
    lep_cell = trace_cell(lep)
    gate = is_comparable(lep, refs)

    # If we cannot gate-compare, still compute the distances we can so the
    # *unavailable* status is explicit rather than a silent 0.
    lep_feats = extract_features(lep, spec)
    ref_feats = [extract_features(r, spec) for r in refs]
    fam2 = trace_cell(refs[0]).get('task_family') if refs else fam
    fam_use = fam if fam else fam2

    pair_benign: List[Distance] = []
    for i in range(len(ref_feats)):
        for j in range(i + 1, len(ref_feats)):
            pair_benign.append(pairwise_distance(ref_feats[i], ref_feats[j], fam_use, weights))
    pair_lep: List[Distance] = []
    for f in ref_feats:
        pair_lep.append(pairwise_distance(lep_feats, f, fam_use, weights))

    # Use the union of available components across ALL pairs as the fixed
    # denominator so every pair contributes on the same scale.
    union_avail: set = set(COMPONENTS)
    for d in pair_benign + pair_lep:
        union_avail -= set(d.unavailable)
    avail = tuple(c for c in COMPONENTS if c in union_avail)
    # Recompute totals with the fixed denominator.
    for d in pair_benign:
        d.total = d.total_with_weights(weights, avail)
    for d in pair_lep:
        d.total = d.total_with_weights(weights, avail)

    def _mean(xs: Sequence[Optional[float]]) -> Optional[float]:
        vals = [x for x in xs if x is not None]
        return (sum(vals) / len(vals)) if vals else None

    d_benign = _mean([d.total for d in pair_benign])
    d_lep = _mean([d.total for d in pair_lep])

    comp_benign = {c: _mean([getattr(d, c) for d in pair_benign]) for c in COMPONENTS}
    comp_lep = {c: _mean([getattr(d, c) for d in pair_lep]) for c in COMPONENTS}

    bnd = (d_lep - d_benign) if (d_lep is not None and d_benign is not None) else None

    lep_code = (lep_cell.get('propagation_mode') or '')
    m_lep = lep.get('metadata', {})
    lcodes = m_lep.get('lep_codes') or []
    return BNDResult(
        trace_id=lep.get('trace_id', ''),
        lep_code=(lcodes[0] if lcodes else (m_lep.get('condition') or 'unknown')),
        task_family=fam_use,
        topology=lep_cell.get('topology') or '',
        propagation_mode=lep_cell.get('propagation_mode') or '',
        references=[r.get('trace_id', '') for r in refs],
        comparable=gate['comparable'],
        reasons=gate['reasons'],
        soft_diagnostics=gate.get('soft_diagnostics', []),
        d_lep=d_lep,
        d_benign=d_benign,
        bnd=bnd,
        component_lep=comp_lep,
        component_benign=comp_benign,
        pair_benign=pair_benign,
        pair_lep=pair_lep,
    )


def measure_configuration(lep: Dict[str, Any], refs: Sequence[Dict[str, Any]],
                          spec: Optional[Dict[str, Any]],
                          weights: ComponentWeights = None) -> Dict[str, Any]:
    """Full per-trace BND measurement: score + threshold + reference-only diagnostics.

    The LEP trace is scored against its three matched benign references exactly
    as the references score against each other.  ``behavioral_anomaly`` (the
    experimental D_LEP > T heuristic) is preserved alongside the raw continuous
    values and is deliberately NOT conflated with any propagation classification.
    """
    weights = weights or ComponentWeights()
    result = score_bnd(lep, refs, spec, weights)
    lep_feats = extract_features(lep, spec)
    ref_feats = {r.get('trace_id', ''): extract_features(r, spec) for r in refs}
    features_map = {lep.get('trace_id', ''): {
        'coverage': lep_feats.coverage(), 'diagnostics': lep_feats.diagnostics,
        'action_count': sum(lep_feats.action_counters.values()),
    }}
    for tid, f in ref_feats.items():
        features_map[tid] = {
            'coverage': f.coverage(), 'diagnostics': f.diagnostics,
            'action_count': sum(f.action_counters.values()),
        }
    payload = result.payload(weights, features=features_map)
    payload['attempt_id'] = (lep.get('metadata') or {}).get('attempt_id') or result.trace_id
    return payload


def _jsonable(obj: Any) -> Any:
    def _conv(o: Any):
        if isinstance(o, float) and math.isnan(o):
            return None
        if isinstance(o, tuple):
            return list(o)
        if isinstance(o, set):
            return sorted(o)
        if isinstance(o, dict):
            return {str(k): _jsonable(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_jsonable(x) for x in o]
        if isinstance(o, (Distance, BNDResult, Features)):
            return _jsonable(getattr(o, 'summary', None) or o)
        return o
    return _conv(obj)


if __name__ == '__main__':
    print(json.dumps({'module': 'benchmark.baseline_deviation', 'version': VERSION}))
