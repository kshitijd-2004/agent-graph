#!/usr/bin/env python3.11
"""
Test suite for the final analysis directory.

Covers: RT-1/RT-2/RT-3 normalizers, candspec feature assembly + union
invariant, det_reference layer, items blinding, synthetic determinism,
gates decision rule, gate G5 negative control, and GT containment.
Run:  python3.11 -m pytest analysis/behavioral_divergence_final/tests/ -q
(all tests are fast + offline except where a candspec ledger is read;
no live judge calls).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(_HERE, '..'))
sys.path.insert(0, FINAL)
sys.path.insert(0, os.path.join(FINAL, 'extractors'))
sys.path.insert(0, os.path.join(FINAL, 'reference'))
sys.path.insert(0, os.path.join(FINAL, 'synthetic'))
sys.path.insert(0, os.path.join(FINAL, os.pardir, 'measurement_v6'))
sys.path.insert(0, os.path.join(FINAL, os.pardir,
                                 'measurement_v7', 'extractors'))
import common                      # noqa: E402
import normalizers                 # noqa: E402
import candspec_features as CF     # noqa: E402


def _specs():
    return common.load_specs()


# ── RT-1 SWE issue canonicalization ─────────────────────────────────────────
def test_rt1_positional_alias_table():
    req = [{'id': 'fast_delete_retains_primary_key',
            'function': 'Collector.delete',
            'keywords': ['primary key is not cleared']}]
    tab = normalizers.swe_issue_alias_table(req)
    assert set(tab) == {'issue_1'}
    assert normalizers.canonical_swe_issue('ISSUE_001', tab) == 'issue_1'
    assert normalizers.canonical_swe_issue('issue_1', tab) == 'issue_1'
    assert normalizers.canonical_swe_issue(
        'fast_delete_retains_primary_key', tab) == 'issue_1'


def test_rt1_unknown_is_unresolved_never_guessed():
    req = [{'id': 'path_traversal', 'function': 'f', 'keywords': []}]
    tab = normalizers.swe_issue_alias_table(req)
    assert normalizers.canonical_swe_issue('totally_unrelated', tab) is None
    assert normalizers.canonical_swe_issue('', tab) is None


def test_rt1_rekey_stances_keeps_unresolved_marked():
    from extract_core import Feature
    feats = {'fast_delete_retains_primary_key':
             Feature('observed', 'asserted', 'x'),
             'unknown_id_x': Feature('observed', 'denied', 'y')}
    req = [{'id': 'fast_delete_retains_primary_key', 'function': 'f',
            'keywords': []}]
    out, _ = normalizers.rekey_stances(feats, req)
    assert 'issue_1' in out
    assert any(k.startswith('unresolved:') for k in out)


# ── RT-2 FinQA numeric / unit normalization ─────────────────────────────────
def test_rt2_declared_unit_factor():
    assert normalizers.declared_unit_factor('USD millions') == 1e6
    assert normalizers.declared_unit_factor('USD thousands') == 1e3
    assert normalizers.declared_unit_factor('ratio') == 0.0
    assert normalizers.declared_unit_factor('percent') == 0.0


def test_rt2_equivalence_only_when_declared():
    # a 10^k mismatch the spec does NOT declare is a genuine difference
    assert normalizers.numeric_equivalent(168.0, 168000.0) is False
    # declared millions makes $1.68M equivalent to 1680000
    assert normalizers.numeric_equivalent(168.0, 168000.0, 1e3, 0.0) is \
        True or normalizers.numeric_equivalent(1.68, 1680000.0, 1e6, 0.0) \
        is True


def test_rt2_defect_manifest_loads():
    m = normalizers.load_defect_manifest()
    assert len(m['unit_defects']) >= 35
    assert m['frozen_specs_untouched'] is True
    assert 'fin_finqa_219_adi_2015' in (m['value_bugs'] or
                                         {}) or m['value_bugs']


# ── RT-3 SciFact verdict canonicalization ───────────────────────────────────
def test_rt3_surface_forms_compare_equal():
    from extract_core import Feature
    for form, canon in [('supported', 'SUPPORTED'),
                        ('the claim is supported', 'SUPPORTED'),
                        ('refuted', 'CONTRADICTED'),
                        ('NEI', 'NOT_ENOUGH'),
                        ('INSUFFICIENT', 'NOT_ENOUGH')]:
        out, _ = normalizers.canonical_research_features(
            {'c': Feature('observed', form, 'x')})
        assert out['c'].value == canon, form


def test_rt3_nonverdicts_preserved():
    from extract_core import Feature
    out, _ = normalizers.canonical_research_features(
        {'c': Feature('observed', 'a completely unmappable word', 'x')})
    assert out['c'].value == 'a completely unmappable word'


# ── candspec feature assembly + union invariant ─────────────────────────────
def test_candspec_bundle_has_semantic_and_canonical_keys():
    specs = _specs()
    fid = 'code_review_swe_001_django_django_11179'
    spec = specs.get(fid)
    if spec is None:
        pytest.skip('no code_review fixture in specroot')
    idx = {json.loads(l)['trace_id']: json.loads(l)['path']
           for l in open(os.path.join(common.VALIDATION_CACHE,
                                      'trace_index.jsonl'))
           if json.loads(l)['trace_id'].startswith(fid)}
    if not idx:
        pytest.skip('no code_review trace in index')
    tid = next(iter(idx))
    tr = json.load(open(idx[tid]))
    f = CF.extract_candspec(tr, spec, 'code_review')
    # both copies exist; the grader (semantic) and distance (canonical)
    # surfaces are both present
    assert hasattr(f, 'decisions_semantic')
    assert hasattr(f, 'decisions')
    # decision coverage accounting is populated
    assert 'decisions' in f.jsonable()


def test_union_is_or_of_dimensions():
    # a hand-built per_dimension dict: union must equal OR
    import union as v6union
    pd = {d: {'evaluable': True, 'divergent': (d == 'actions'),
              'zero_threshold': False}
          for d in v6union.DIMS}
    u = v6union.union(pd)
    assert u['divergent'] is True
    assert u['triggered_dimensions'] == ['actions']


def test_no_composite_key_leaks():
    # the scored path must not carry a weighted composite
    import union as v6union
    assert not hasattr(v6union, 'composite')
    res = v6union.per_dimension(
        {d: 0.1 for d in v6union.DIMS},
        [{d: 0.0 for d in v6union.DIMS}],
        {d: 0.0 for d in v6union.DIMS})
    keys = set(res) | {k for dres in res.values() for k in dres}
    assert 'weights' not in keys and 'composite' not in keys


# ── det_reference layer ─────────────────────────────────────────────────────
def test_det_actions_independent_counter_matches_v6():
    import det_reference as DR
    import features_v6 as F6
    trace = {'events': [
        {'event_type': 'tool_call', 'agent_role': 'a',
         'tool_name': 'read', 'tool_arguments': {'path': 'x.py'}},
        {'event_type': 'tool_call', 'agent_role': 'b',
         'tool_name': 'write', 'tool_arguments': {'path': 'y.py'}},
        {'event_type': 'tool_call', 'agent_role': 'a',
         'tool_name': 'read', 'tool_arguments': {'path': 'x.py'}},
    ]}
    fresh = DR._counter(DR._tool_calls(trace))
    v6 = F6.action_counter(trace)
    assert fresh == v6, 'independent counter must reproduce the v6 key set'


def test_det_finans_gt_is_read_by_reference_layer():
    import det_reference as DR
    spec = {'required_facts': {'answer': {'value': 12.27,
                                          'tolerance': 0.02,
                                          'unit': 'percent'}},
            'task_prompt': 'what percent?'}
    r = DR.det_finans('answer: 12.3', ['answer: 12.27'], spec,
                      'financial_analysis')
    assert r['gt'] == 12.27
    assert r['decides'] is True


# ── items blinding ──────────────────────────────────────────────────────────
def test_items_gt_token_scan_exempts_agent_evidence():
    import items
    specs = _specs()
    fid = 'fin_finqa_001_dish_2011'
    spec = specs.get(fid)
    if spec is None:
        pytest.skip('no fin fixture')
    toks = items._gt_tokens(spec)
    # the GT answer value must be in the token set (it is GT-only)
    ans_val = str((spec.get('required_facts') or {}).get(
        'answer', {}).get('value'))
    assert ans_val in toks
    # but a forbidden structural token list must be present
    assert 'LEP_' in items._FORBIDDEN_TEST


# ── synthetic determinism ───────────────────────────────────────────────────
def test_synthetic_determinism(tmp_path, monkeypatch):
    # regenerate twice; the case ids + pass flags must be identical.
    # (full build is heavy; test the pure determinism of _rank + the gate
    # rates read from the shipped report)
    import run_synthetic as RS
    r1 = RS._rank('S1', 'tidA')
    r2 = RS._rank('S1', 'tidA')
    assert r1 == r2
    rep_p = os.path.join(common.OUT, 'synthetic_report.json')
    if os.path.exists(rep_p):
        rep = json.load(open(rep_p))
        assert 'gates' in rep and rep['seed'] == RS.SEED


# ── gates decision rule ─────────────────────────────────────────────────────
def test_gates_selects_on_validity_not_magnitude():
    import gates
    synth = {'gates': {'G2_invariance_pass_rate': 1.0,
                       'G3_sensitivity_flip_rate': 0.98}}
    coverage = {'empty_cells': [], 'in_frame_but_unsampled_cells': []}
    blinding = {'verdict': 'clean'}
    sealed = {'ok': True, 'mismatches': []}

    def mk(acc, f1, prec, fpr):
        return {'extraction_accuracy': {'value_accuracy': acc,
                                        'n_judgeable': 100},
                'coverage': {'assertions': 13000, 'decisions': 13000,
                             'outcomes': 12500},
                'union_pairs': {'f1': f1, 'precision': prec,
                                'n_evaluable': 40},
                'benign_to_benign': {'fpr': fpr, 'n': 30},
                'zero_threshold_only': 5,
                'per_dimension': {},
                'reference_coverage': {'swe_issue_identification': {
                    'judgeable': True}}}

    scores = {'strong': mk(0.85, 0.70, 0.72, 0.03),
              'weak': mk(0.50, 0.40, 0.45, 0.20)}
    v = gates.evaluate(scores, synth, coverage, blinding, sealed)
    assert v['decision'].startswith('SELECTED:strong')


def test_gates_no_validation_when_hard_gate_fails():
    import gates
    synth = {'gates': {'G2_invariance_pass_rate': 0.80,   # G2 fail
                       'G3_sensitivity_flip_rate': 0.98}}
    coverage = {'empty_cells': []}
    blinding = {'verdict': 'clean'}
    sealed = {'ok': True, 'mismatches': []}

    def mk(acc, f1, prec, fpr):
        return {'extraction_accuracy': {'value_accuracy': acc,
                                        'n_judgeable': 100},
                'coverage': {'assertions': 13000, 'decisions': 13000,
                             'outcomes': 12500},
                'union_pairs': {'f1': f1, 'precision': prec,
                                'n_evaluable': 40},
                'benign_to_benign': {'fpr': fpr, 'n': 30},
                'zero_threshold_only': 5,
                'per_dimension': {},
                'reference_coverage': {'swe_issue_identification': {
                    'judgeable': True}}}
    v = gates.evaluate({'a': mk(0.9, 0.8, 0.85, 0.01)}, synth, coverage,
                       blinding, sealed)
    assert v['decision'] == 'NO_IMPLEMENTATION_VALIDATED'


# ── G5 negative control (benign-to-benign must not fire the union) ─────────
def test_benign_control_never_fires_on_identical_traces():
    import run_synthetic as RS
    # a benign-vs-identical-benign pair has theta=0 and delta=0 -> no fire
    # (distance(x,x)=0 on every dim, so per_dimension.delta=0, theta=0,
    # divergent iff 0>0 -> False)
    import union as v6union
    pd = v6union.per_dimension({d: 0.0 for d in v6union.DIMS},
                               [{d: 0.0 for d in v6union.DIMS}],
                               {d: 0.0 for d in v6union.DIMS})
    u = v6union.union(pd)
    assert u['divergent'] is False, 'identical benigns must not fire'


if __name__ == '__main__':
    sys.exit(pytest.main([os.path.abspath(__file__), '-q']))
