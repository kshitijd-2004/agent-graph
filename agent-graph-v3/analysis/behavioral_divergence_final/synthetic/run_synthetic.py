#!/usr/bin/env python3.11
"""
Synthetic mutation suite (brief §5; MEASUREMENT_SPEC §9 G2/G3).

Controlled-correctness tests: real trace pairs from the DEV partition are
mutated IN-MEMORY (never written to the trace corpus) and pushed through the
real candspec code path (extract_candspec -> CF.classify_dimensions ->
union.per_dimension/union).  Expected behavior is known by construction, so
these establish CONTROLLED correctness, not real-trace accuracy — reported
separately, as the brief requires.

  G2 INVARIANCE  — a surface-only transform must NOT move the semantic
                   dimensions:
    S1 whitespace/markdown reflow of authored text
    S2 identifier alias (code_review RT-1 positional/semantic equivalence)
    S3 numeric punctuation (financial 1,234.5 == 1234.5)
    S4 verdict surface form (research RT-3 canonical equality)
    S5 tool-call reordering (TV is order-invariant)
  G3 SENSITIVITY — a semantic mutation MUST move the target dimension:
    S6 polarity flip (a research verdict SUPPORTED<->CONTRADICTED)
    S7 value change (a financial fact)
    S8 claim/stance removed (a research verdict set to absent)
    S9 tool-set change (drop a tool the agent called)
    S10 answer change (the financial final answer)
  SEPARATE (not G-gated):
    S11 negation/contradiction (v6 D1 regression on real snippets)
    S12 missing evidence (stripping the final response -> outcomes
        unavailable, not a negative)
    S13 benign-to-benign negative control (union FPR on unmutated pairs)

Deterministic: every draw is a seeded rank (SHA-256, never builtin hash);
regenerating the report is byte-identical (verified by a test).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
import common
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
sys.path.insert(0, os.path.join(FINAL, 'reference'))
sys.path.insert(0, os.path.join(common.V7_DIR, 'extractors'))
import candspec_features as CF
from features_v6 import action_counter
import normalizers

SEED = 'agentprop-final-sampling-20261207'


def _rank(tag: str, tid: str) -> int:
    return int(hashlib.sha256(f'{SEED}:{tag}:{tid}'.encode()).hexdigest()[:14],
               16)


# ── real-trace loading (dev-side only) ──────────────────────────────────────
def _load_trace_index() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in open(os.path.join(common.VALIDATION_CACHE,
                                  'trace_index.jsonl')):
        d = json.loads(line)
        out[d['trace_id']] = d['path']
    return out


def _trace(tid: str, idx: Dict[str, str]) -> Optional[Dict]:
    p = idx.get(tid)
    if not p or not os.path.exists(p):
        return None
    return json.load(open(p))


def _dev_frame_rows() -> List[Dict]:
    parts = json.load(open(os.path.join(common.OUT,
                                       'partitions_final.json')))
    ho = {fx for fx, rec in parts['fixtures'].items()
          if rec.get('partition') == 'heldout'}
    from candspec_pipeline import load_frame_rows
    return [r for r in load_frame_rows()
            if common.fixture_id_of(r['trace_id']) not in ho]


def _pairs(fam: str, n: int, idx: Dict[str, str],
           frame: List[Dict]) -> List[Dict]:
    """n deterministic LEP-vs-first-benign pairs of a family."""
    sel = [r for r in frame if r.get('task_family') == fam and
           r.get('reference_set')]
    sel = sorted(sel, key=lambda r: _rank(fam, r['trace_id']))[:n]
    out = []
    for r in sel:
        lep = _trace(r['trace_id'], idx)
        ben = _trace(r['reference_set'][0], idx)
        if lep is None or ben is None:
            continue
        out.append({'trace_id': r['trace_id'], 'fam': fam,
                    'lep': lep, 'benign': ben, 'row': r})
    return out


# ── G2: extractor-level invariance ──────────────────────────────────────────
def _feats(trace: Dict, spec: Dict, fam: str,
           policy: Optional[Dict] = None) -> CF.CandspecFeatures:
    return CF.extract_candspec(trace, spec, fam, policy=policy)


def S1_whitespace(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Whitespace/markdown reflow must not change assertion/decision values."""
    cases = []
    for p in pairs:
        fa = _feats(p['lep'], specs.get(
            common.fixture_id_of(p['trace_id']), {}), p['fam'])
        # mutate a COPY: collapse whitespace in every authored event
        mut = json.loads(json.dumps(p['lep']))
        for e in mut.get('events') or []:
            if e.get('event_type') in ('reasoning', 'agent_handoff',
                                       'final_response'):
                e['output_text'] = ' '.join(
                    (e.get('output_text') or '').split())
        fb = _feats(mut, specs.get(
            common.fixture_id_of(p['trace_id']), {}), p['fam'])
        same = (fa.assertions == fb.assertions and
                fa.decisions == fb.decisions)
        cases.append({'id': f'S1:{p["trace_id"]}', 'class': 'S1',
                      'mutation': 'whitespace_collapse',
                      'expected': 'invariant',
                      'observed': 'invariant' if same else 'moved',
                      'pass': same, 'gate_relevant': True,
                      'detail': {
                          'assertions_equal': fa.assertions == fb.assertions,
                          'decisions_equal': fa.decisions == fb.decisions}})
    return cases


def S2_identifier_alias(specs: Dict) -> List[Dict]:
    """RT-1: surface forms of the same SWE issue rekey to one positional id."""
    cases = []
    n = 0
    for fid, spec in list(specs.items()):
        if spec.get('task_family') != 'code_review':
            continue
        req = spec.get('required_issues')
        if not req:
            continue
        table = normalizers.swe_issue_alias_table(req)
        sem = list(table.values())
        if not sem:
            continue
        n += 1
        if n > 20:
            break
        key = f'issue_{1}'
        ent = table.get(key)
        if not ent:
            continue
        # forms that MUST resolve to issue_1 (task-side alias forms only)
        must_resolve = [f'ISSUE_001', f'issue_1', f'issue #1',
                       ent['semantic_id']]
        # forms that must NOT resolve (RT-1: never guess) — a bare position
        # with no declared 'issue_N' literal in the alias table is ambiguous
        must_not_resolve = ['totally_unrelated_identifier', '']
        ok = True
        resolved = {}
        for f in must_resolve:
            r = normalizers.canonical_swe_issue(str(f), table)
            resolved[str(f)] = r
            if r != key:
                ok = False
        for f in must_not_resolve:
            r = normalizers.canonical_swe_issue(str(f), table)
            resolved[str(f) or '<empty>'] = r
            if r is not None:
                ok = False
        cases.append({'id': f'S2:{fid}', 'class': 'S2',
                      'mutation': 'identifier_alias',
                      'expected': 'aliases->issue_1, unknowns->unresolved',
                      'observed': 'consistent' if ok else 'divergent',
                      'pass': ok, 'gate_relevant': True,
                      'detail': {'resolved': resolved}})
    return cases


def S3_numeric_punctuation(specs: Dict) -> List[Dict]:
    """Financial numeric punctuation is extractor-invariant; unit-scale
    equivalence is a grader/RT-2 property (verified separately)."""
    cases = []
    for form, canon in [
            ('1,234.5', '1234.5'), ('1234.5.', '1234.5'),
            (' 1,234.5 ', '1234.5'), ('$1,234.5', '1234.5')]:
        v = _fin_number_in_context(form, 'revenue was')
        vc = _fin_number_in_context(canon, 'revenue was')
        ok = (v == 1234.5 and vc == 1234.5 and v == vc)
        cases.append({'id': f'S3:{form}', 'class': 'S3',
                      'mutation': 'numeric_punctuation',
                      'expected': 'value_1234.5', 'observed':
          f'{form}->{v}', 'pass': ok, 'gate_relevant': True,
          'detail': {'form': form, 'parsed': v}})
    # unit-scale equivalence (RT-2) as a documented grader property
    ok_unit = normalizers.numeric_equivalent(1.68, 1680000.0, 1e6, 0.0)
    cases.append({'id': 'S3:unit_scale', 'class': 'S3',
                  'mutation': 'unit_scale_equivalence',
                  'expected': '$1.68M==1680000 when spec declares millions',
                  'observed': str(ok_unit), 'pass': ok_unit,
                  'detail': {'note': 'grader-side (RT-2), not extractor'}})
    return cases


def _fin_number_in_context(text: str, cue: str) -> Optional[float]:
    """Independent parse of the headline numeric figure in a context:
    strip currency mark / thousands commas / trailing sentence period,
    then scale by any declared scale-word in the context."""
    import det_reference as DR
    s = text.strip().replace('$', '').replace(',', '').rstrip('.')
    v = DR._to_float(s)
    if v is None:
        return None
    return DR._apply_scale(v, text)


def S4_verdict_surface(specs: Dict) -> List[Dict]:
    """RT-3: verdict surface forms compare equal on the canonical enum."""
    cases = []
    for form, canon in [
            ('SUPPORTED', 'SUPPORTED'), ('supported', 'SUPPORTED'),
            ('the claim is supported', 'SUPPORTED'),
            ('CONTRADICTED', 'CONTRADICTED'), ('refuted', 'CONTRADICTED'),
            ('NEI', 'NOT_ENOUGH'), ('not enough info', 'NOT_ENOUGH'),
            ('INSUFFICIENT', 'NOT_ENOUGH')]:
        c = normalizers.canonical_research_features(
            {'c': _feat_obs(form)})[0].get('c')
        val = getattr(c, 'value', None)
        ok = (val == canon)
        cases.append({'id': f'S4:{form!r}', 'class': 'S4',
                      'mutation': 'verdict_surface_form',
                      'expected': canon, 'observed': str(val),
                      'pass': ok, 'gate_relevant': True, 'detail': {}})
    return cases


def _feat_obs(value: str):
    from extract_core import Feature
    return Feature('observed', value, 'synthetic')


def S5_call_reordering(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Tool-call reordering is invisible to TV (order-invariant)."""
    import random
    cases = []
    for p in pairs:
        orig = action_counter(p['lep'])
        mut = json.loads(json.dumps(p['lep']))
        evs = [e for e in mut.get('events') or []
               if e.get('event_type') == 'tool_call']
        rng = __import__('random').Random(_rank('S5', p['trace_id']))
        rng.shuffle(evs)
        # rebuild event list: non-tool events keep order; tool events
        # reinserted in the shuffled order
        base = [e for e in (mut.get('events') or [])
                if e.get('event_type') != 'tool_call']
        mut['events'] = base + evs if evs else base
        resh = action_counter(mut)
        same = (orig == resh)
        cases.append({'id': f'S5:{p["trace_id"]}', 'class': 'S5',
                      'mutation': 'call_reorder',
                      'expected': 'TV_invariant',
                      'observed': 'invariant' if same else 'moved',
                      'pass': same, 'gate_relevant': True,
                      'detail': {'n_calls': len(orig)}})
    return cases


# ── G3: sensitivity via self-comparison (differential flip + specificity) ───
def _flip_case(cls: str, p: Dict, specs: Dict, target: str,
               non_targets: List[str], mutate, reason_if_skip: str
               ) -> Dict:
    """Self-comparison flip test.

    base   = distance(x, x)   = 0.0 on every evaluable dimension (identical)
    mutated= distance(x, x')  where x' = mutate(x)
    PASS  = the TARGET dimension moves strictly off 0 AND every
             NON-TARGET dimension stays at 0 (specificity: a semantic change
             in one construct must not leak into the independent ones).
    EXCLUDED when the target dimension is not evaluable on this pair.
    """
    fam = p['fam']
    spec = specs.get(common.fixture_id_of(p['trace_id']), {})
    fa = _feats(p['lep'], spec, fam)
    nri = _nri(spec, fam)
    x, x2 = mutate(fa)
    if x is x2:
        return {'id': f'{cls}:{p["trace_id"]}', 'class': cls,
                'mutation': reason_if_skip,
                'expected': f'{target}_dim_moved',
                'observed': 'excluded (no applicable mutation)',
                'pass': None, 'gate_relevant': False,
                'detail': {'reason': 'mutation_no_op'}}
    gx, gx2 = _grade1(x, spec, fam), _grade1(x2, spec, fam)
    d = CF.classify_dimensions(x2, x, fam, gx2, gx, nri)
    tgt = d.get(target)
    if tgt is None:
        return {'id': f'{cls}:{p["trace_id"]}', 'class': cls,
                'mutation': reason_if_skip,
                'expected': f'{target}_dim_moved', 'observed': 'excluded',
                'pass': None, 'gate_relevant': False,
                'detail': {'reason': f'{target}_not_evaluable'}}
    moved = tgt > 1e-9
    specific = all((d.get(o) is None) or (d.get(o) <= 1e-9)
                   for o in non_targets)
    ok = moved and specific
    detail = {k: d.get(k) for k in ('assertions', 'decisions', 'actions',
                                    'outcomes')}
    detail.update({'target': target, 'moved': moved, 'specific': specific})
    return {'id': f'{cls}:{p["trace_id"]}', 'class': cls,
            'mutation': reason_if_skip,
            'expected': f'{target}_moved_and_others_unchanged',
            'observed': ('pass' if ok else
                         ('moved_not_specific' if moved else
                          'target_not_moved')),
            'pass': ok, 'gate_relevant': True, 'detail': detail}


def S6_polarity_flip(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """A research verdict polarity flip moves the assertion dimension
    (self-comparison: distance(x, x')>0 on assertions, 0 on the
    INDEPENDENT dimensions).  Decisions legitimately co-move (the verdict
    lives in the final response and the assertion scope includes it), so it
    is excluded from the specificity check — that overlap is the documented
    M1/M2 double-count, not a failure."""
    return [c for c in
            (_flip_case('S6', p, specs, 'assertions',
                       ['actions', 'outcomes'],
                       _mut_polarity, 'polarity_flip')
             for p in pairs if p['fam'] == 'research_synthesis')
            if c]


def S7_value_change(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """A financial fact value change moves the assertion dimension."""
    return [c for c in
            (_flip_case('S7', p, specs, 'assertions',
                       ['actions'], _mut_value, 'value_change')
             for p in pairs if p['fam'] == 'financial_analysis')
            if c]


def S8_claim_removed(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Removing a research verdict (-> absent) moves the decision dim.
    The outcomes dim co-moves legitimately (the research grade is verdict-
    based); the independence check is therefore on actions only."""
    return [c for c in
            (_flip_case('S8', p, specs, 'decisions',
                       ['actions'], _mut_remove_claim,
                       'claim_removed')
             for p in pairs if p['fam'] == 'research_synthesis')
            if c]


def S9_toolset_change(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Dropping a tool the agent called moves the action dimension."""
    return [c for c in
            (_flip_case('S9', p, specs, 'actions',
                       ['assertions', 'decisions', 'outcomes'],
                       _mut_toolset, 'toolset_change')
             for p in pairs)
            if c]


def S10_answer_change(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Changing the financial final answer moves the outcome dimension."""
    return [c for c in
            (_flip_case('S10', p, specs, 'outcomes',
                       ['assertions', 'decisions', 'actions'],
                       _mut_answer, 'answer_change')
             for p in pairs if p['fam'] == 'financial_analysis')
            if c]


# ── mutation functions for the G3 flip cases: (x, x') pairs ──────────────
def _mut_polarity(fa: CF.CandspecFeatures):
    x = _clone_feats(fa)
    key = _first_observed_key(x.assertions)
    if key is None:
        return fa, fa
    newv = ('CONTRADICTED' if str(getattr(x.assertions[key], 'value', ''))
            .upper().startswith('SUPPORT') else 'SUPPORTED')
    x.assertions[key] = _feat_obs(newv)
    return fa, x


def _mut_value(fa: CF.CandspecFeatures):
    x = _clone_feats(fa)
    key = None
    for k in sorted(x.assertions):
        v = getattr(x.assertions[k], 'value', None)
        if isinstance(v, (int, float)):
            key = k
            break
    if key is None:
        return fa, fa
    x.assertions[key] = _feat_obs(getattr(x.assertions[key], 'value')
                                  + 100.0)
    return fa, x


def _mut_remove_claim(fa: CF.CandspecFeatures):
    x = _clone_feats(fa)
    key = _first_observed_key(x.decisions)
    if key is None:
        return fa, fa
    x.decisions[key] = _feat_absent()
    return fa, x


def _mut_toolset(fa: CF.CandspecFeatures):
    x = _clone_feats(fa)
    if not x.actions:
        return fa, fa
    key = next(iter(x.actions))
    x.actions = {k: v for k, v in x.actions.items() if k[1] != key[1]}
    return fa, x


def _mut_answer(fa: CF.CandspecFeatures):
    x = _clone_feats(fa)
    if x.answer is None or x.answer.state != 'observed':
        return fa, fa
    v = x.answer.value
    # delta beyond any relative-tolerance window: |err|/|GT| can never be
    # <= tol (0.02) when err = 10*max(1,|v|)
    x.answer = _feat_obs(v + 10.0 * max(1.0, abs(v)))
    return fa, x


def _first_observed_key(feats: Dict) -> Optional[str]:
    for k in sorted(feats):
        if getattr(feats[k], 'state', None) == 'observed':
            return k
    return None


def _clone_feats(fa: CF.CandspecFeatures) -> CF.CandspecFeatures:
    import copy
    return copy.deepcopy(fa)


def _feat_absent():
    from extract_core import Feature
    return Feature('absent', None, 'synthetic_removal')


def S11_negation() -> List[Dict]:
    """v6 D1-class regression on real snippets (FINDINGS §E)."""
    import extract_research as V6R
    cases = []
    for text, must_not in [
            ('I cannot say Claim 1 is supported.', ('SUPPORTED',)),
            ('Claim 1 is not supported by the provided papers.',
             ('SUPPORTED',)),
            ('Both claims are marked as NOT ENOUGH INFO.', ('absent',
                                                            None))]:
        spec = {'required_facts': {
            'claim_1_verdict': {'description': 'Claim 1'},
            'claim_2_verdict': {'description': 'Claim 2'}}}
        feats, _ = V6R.extract_claim_verdicts(text, spec)
        f1 = feats.get('claim_1_verdict')
        state = getattr(f1, 'state', None)
        val = getattr(f1, 'value', None)
        # D1: modal denial never SUPPORTED; D2: 'not supported' never
        # SUPPORTED; D16: collective verdict resolves to NOT_ENOUGH, not
        # absent/unavailable.
        if must_not and None in must_not:
            ok = state not in ('absent', 'unavailable')
        else:
            ok = val not in must_not
        cases.append({'id': f'S11:{text[:20]}', 'class': 'S11',
                     'mutation': 'negation',
                     'expected': f'not in {must_not}',
                     'observed': f'{state}:{val}', 'pass': ok,
                     'gate_relevant': False, 'detail': {'text': text}})
    return cases


def S12_missing_evidence(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """Stripping the final response -> outcomes unavailable, not a negative."""
    cases = []
    for p in pairs:
        if p['fam'] != 'financial_analysis':
            continue
        spec = specs.get(common.fixture_id_of(p['trace_id']), {})
        mut = json.loads(json.dumps(p['benign']))
        for e in mut.get('events') or []:
            if e.get('event_type') == 'final_response':
                e['output_text'] = ''
        fb = _feats(mut, spec, p['fam'])
        ok = (fb.answer is None) or (fb.answer.state != 'observed')
        cases.append({'id': f'S12:{p["trace_id"]}', 'class': 'S12',
                      'mutation': 'final_response_stripped',
                      'expected': 'outcomes_unavailable',
                      'observed': (fb.answer.state if fb.answer
                                   else 'no_answer_key'),
                      'pass': ok, 'gate_relevant': False,
                      'detail': {}})
    return cases


def S13_benign_control(pairs: List[Dict], specs: Dict) -> List[Dict]:
    """The TRUE benign-to-benign negative control (feeds gate G5).

    Build a pseudo-row where the 'LEP' side is itself a benign execution and
    its reference set is the OTHER benigns of the same cell, then run the
    full frozen union machinery (theta_d = max over benign-to-benign pairs,
    delta_d = lep-mean - theta_d, divergent iff delta_d > theta_d, union =
    OR).  A benign execution must not fire the union: any fire here is a
    false positive of the measurement on a pure negative control.  Reported
    as a rate, NOT a per-implementation P/R (it is the control population).
    """
    P = common.pipelines()
    V6U = P['V6U']
    cases = []
    fired = 0
    n = 0
    for p in pairs:
        spec = specs.get(common.fixture_id_of(p['trace_id']), {})
        refs = p['row'].get('reference_set') or []
        if len(refs) < 2:
            continue
        # 'LEP' side = benign refs[0]; reference set = the other benigns
        lep_tr = _trace(refs[0], _IDX)
        ref_trs = [_trace(r, _IDX) for r in refs[1:] if _trace(r, _IDX)]
        if lep_tr is None or len(ref_trs) < 1:
            continue
        fam = p['fam']
        fl = _feats(lep_tr, spec, fam)
        gl = _grade1(fl, spec, fam)
        ben_feats, ben_dists = [], []
        for rb in ref_trs:
            fb = _feats(rb, spec, fam)
            gb = _grade1(fb, spec, fam)
            ben_feats.append(fb)
            ben_dists.append(CF.classify_dimensions(fl, fb, fam, gl, gb,
                                                    _nri(spec, fam)))
        if not ben_dists:
            continue
        # benign-to-benign thresholds
        b2b = []
        for i in range(len(ben_feats)):
            for j in range(i + 1, len(ben_feats)):
                gi = _grade1(ben_feats[i], spec, fam)
                gj = _grade1(ben_feats[j], spec, fam)
                b2b.append(CF.classify_dimensions(ben_feats[i], ben_feats[j],
                                                  fam, gi, gj,
                                                  _nri(spec, fam)))
        thr = V6U.benign_thresholds(b2b)
        lep_mean = {d: _mean([x.get(d) for x in ben_dists])
                    for d in V6U.DIMS}
        pd = V6U.per_dimension(lep_mean, b2b, thr, aggregation='mean')
        u = V6U.union(pd)
        n += 1
        fire = bool(u.get('divergent'))
        if fire:
            fired += 1
        cases.append({'id': f'S13:{p["trace_id"]}', 'class': 'S13',
                      'mutation': 'benign_to_benign',
                      'expected': 'no_union_fire',
                      'observed': 'fired' if fire else 'no_fire',
                      'pass': not fire, 'gate_relevant': False,
                      'detail': {'triggered': u.get('triggered_dimensions'),
                                 'n_refs': len(ref_trs)}})
    rate = (fired / n) if n else None
    for c in cases:
        c['detail']['b2b_fpr'] = rate
    return cases


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


# ── helpers ──────────────────────────────────────────────────────────────────


def _grade(fa, fb, spec, fam):
    P = common.pipelines()
    V6G, M4 = P['V6G'], P['M4']
    ga, _ = _outcome(fa, spec, fam, V6G)
    gb, _ = _outcome(fb, spec, fam, V6G)
    return ga, gb


def _grade1(feats, spec, fam):
    P = common.pipelines()
    g, _ = _outcome(feats, spec, fam, P['V6G'])
    return g


def _outcome(feats, spec, fam, V6G):
    if fam == 'financial_analysis':
        return V6G.grade_financial(feats.answer, spec)
    if fam == 'research_synthesis':
        return V6G.grade_research(feats.decisions, spec)
    if fam == 'code_review':
        return V6G.grade_code(feats.decisions_semantic, spec)
    return 'ungradeable', {}


def _nri(spec, fam):
    if fam == 'code_review':
        return len(spec.get('required_issues') or [])
    return 0


# ── driver ───────────────────────────────────────────────────────────────────
def build() -> Dict[str, Any]:
    global _IDX
    _IDX = {}
    specs = common.load_specs()
    idx = _load_trace_index()
    _IDX = idx
    frame = _dev_frame_rows()
    n_pair = 15
    res_p = _pairs('research_synthesis', n_pair, idx, frame)
    fin_p = _pairs('financial_analysis', n_pair, idx, frame)
    code_p = _pairs('code_review', 5, idx, frame)
    all_p = res_p + fin_p + code_p

    all_cases: List[Dict] = []
    all_cases += S1_whitespace(res_p + fin_p + code_p, specs)
    all_cases += S2_identifier_alias(specs)
    all_cases += S3_numeric_punctuation(specs)
    all_cases += S4_verdict_surface(specs)
    all_cases += S5_call_reordering(res_p + fin_p + code_p, specs)
    all_cases += S6_polarity_flip(res_p, specs)
    all_cases += S7_value_change(fin_p, specs)
    all_cases += S8_claim_removed(res_p, specs)
    all_cases += S9_toolset_change(all_p, specs)
    all_cases += S10_answer_change(fin_p, specs)
    all_cases += S11_negation()
    all_cases += S12_missing_evidence(fin_p, specs)
    all_cases += S13_benign_control(fin_p + res_p, specs)

    def _rate(cls_ids: List[str]) -> Optional[float]:
        rel = [c for c in all_cases if c['class'] in cls_ids
               and c.get('gate_relevant') is True]
        if not rel:
            return None
        return sum(1 for c in rel if c['pass']) / len(rel)

    g2 = _rate(['S1', 'S2', 'S3', 'S4', 'S5'])
    g3 = _rate(['S6', 'S7', 'S8', 'S9', 'S10'])
    classes = {}
    for cls in sorted({c['class'] for c in all_cases}):
        cl = [c for c in all_cases if c['class'] == cls]
        rel = [c for c in cl if c.get('gate_relevant') is True]
        classes[cls] = {
            'n_cases': len(cl),
            'n_gate_relevant': len(rel),
            'pass_rate': ((sum(1 for c in rel if c['pass']) / len(rel))
                          if rel else
                          (sum(1 for c in cl if c['pass']) / len(cl)
                           if cl else None)),
            'all_pass': (all(c['pass'] for c in cl if c['pass'] is not None)
                         and all(c['pass'] for c in cl)),
            'excluded': sum(1 for c in cl if c.get('gate_relevant')
                            is False)}
    report = {
        'seed': SEED,
        'n_cases': len(all_cases),
        'gates': {'G2_invariance_pass_rate': g2,
                  'G3_sensitivity_flip_rate': g3,
                  'G2_gate_pass': (g2 == 1.0),
                  'G3_gate_pass': (g3 is not None and g3 >= 0.95)},
        'classes': classes,
        'cases': all_cases,
        'note': ('Synthetic tests establish CONTROLLED correctness (the '
                 'measurement is invariant to surface and sensitive to '
                 'semantic change on the SAME code path), not real-trace '
                 'accuracy. Invariance failures are G2 findings; the '
                 'gate passes only at 100%.'),
    }
    common.write_json(report, 'synthetic_report.json')
    print(json.dumps({'n_cases': report['n_cases'],
                      'G2_invariance': g2,
                      'G3_sensitivity': g3,
                      'G2_pass': report['gates']['G2_gate_pass'],
                      'G3_pass': report['gates']['G3_gate_pass']},
                     indent=1))
    return report


if __name__ == '__main__':
    build()
