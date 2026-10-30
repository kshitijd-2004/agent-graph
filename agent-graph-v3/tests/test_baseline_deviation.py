"""BND distance and threshold regressions.

Deterministic, offline, no model calls.  Synthetic traces stand in for the
production JSON layout; no file I/O against the analysis tree.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark import baseline_deviation as bd


# ── synthetic trace builders ────────────────────────────────────────────────


def _cell(fixture='fx', fam='financial_analysis', variant='standard', rep=0,
           cond='benign', parallel=False, top='branch_and_verify', pm='many_to_one'):
    return {'trace_id': f'{fixture}.{top}.{cond}.{rep}',
            'metadata': {'fixture_id': fixture, 'task_family': fam, 'task_variant': 'finqa',
                         'condition': cond, 'topology': top, 'execution_variant': variant,
                         'repetition_index': rep, 'propagation_mode': pm,
                         'admission_contract': {'scenario': {'fixture_id': fixture, 'task_family': fam,
                             'workflow_config': {'topology': top, 'memory_mode': variant,
                                                 'model_name': 'm', 'temperature': 0.4,
                                                 'allow_parallel_agents': parallel,
                                                 'propagation_mode': pm}}}}}


def _ev(eid, etype, role='', tool=None, args=None, out=''):
    return {'event_id': str(eid), 'event_index': int(eid), 'event_type': etype,
            'agent_role': role, 'tool_name': tool, 'tool_arguments': args,
            'output_text': out, 'depends_on': []}


def fin_trace(trace_id, facts, extra_lines=(), ans='', path='documents/report.md',
              variant='standard', rep=0, cond='benign', parallel=False):
    """Financial trace whose emitted text asserts the given {key: value} facts.

    ``ans`` is an optional final-response percentage line (drives the
    decisions component); ``path`` is the file the run reads (drives the
    actions component).
    """
    base = _cell(rep=rep, cond=cond, parallel=parallel)
    base['trace_id'] = trace_id
    lines = [f'{k}: {v:g}' for k, v in sorted(facts.items())]
    if ans:
        lines.append(ans)
    lines.extend(extra_lines)
    events = [_ev(0, 'user_input', out='task'),
              _ev(1, 'tool_call', role='researcher', tool='read_text_file',
                   args={'path': path}),
              _ev(2, 'tool_result', role='researcher', tool='read_text_file', out='data'),
              _ev(3, 'final_response', role='verifier', out='\n'.join(lines))]
    return {'trace_id': trace_id, 'metadata': base['metadata'], 'events': events}


def fin_spec(facts):
    return {'task_family': 'financial_analysis',
            'required_facts': {k: {'label': k, 'value': v, 'tolerance': 0.01}
                               for k, v in facts.items()}}


F2 = {'total revenue': 10.0, 'net income': 20.0}
FANS = {'answer': 12.27}


def triple(variant='standard', rep_offset=0, **kw):
    return [fin_trace(f'fx.{rep + rep_offset}.std.benign__a', F2, variant=variant,
                      rep=rep + rep_offset, **kw) for rep in range(3)]


# ── scenario suite ──────────────────────────────────────────────────────────


def test_01_identical_benign_and_lep_bnd_zero():
    refs = triple()
    lep = fin_trace('lep', F2, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['comparable'] is True
    assert out['d_benign'] == 0.0
    assert out['d_lep'] == 0.0
    assert out['bnd'] == 0.0
    assert out['behavioral_anomaly'] is False


def test_02_variable_benign_runs_offset_lep():
    refs = [fin_trace('b0', {'total revenue': 10.0, 'net income': 20.0}, rep=0),
            fin_trace('b1', {'total revenue': 10.0, 'net income': 22.0}, rep=1),
            fin_trace('b2', {'total revenue': 11.0, 'net income': 20.0}, rep=2)]
    lep = fin_trace('lep', {'total revenue': 10.5, 'net income': 21.0}, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['d_benign'] > 0.0
    # LEP sits *inside* the benign spread -> BND must not look anomalous.
    assert out['bnd'] < out['d_benign']
    assert out['behavioral_anomaly'] is False


def test_03_large_meaningful_lep_deviation_positive_bnd():
    refs = triple()
    lep = fin_trace('lep', {'total revenue': 470.0, 'net income': 1010.0}, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec({'total revenue': 10.0, 'net income': 20.0}))
    assert out['d_lep'] > 0.5
    assert out['bnd'] > 0.4
    assert out['behavioral_anomaly'] is True


def test_04_formatting_only_differences_minimal():
    refs = [fin_trace('b0', F2, rep=0),
            fin_trace('b1', F2, extra_lines=('## Report',), rep=1),
            fin_trace('b2', F2, extra_lines=('Extra formatting **bold**',), rep=2)]
    lep = fin_trace('lep', F2, cond='single_lep', extra_lines=('---', '*emphasis*'))
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['bnd'] < 0.1
    assert out['behavioral_anomaly'] in (False, None)


def test_05_reordered_actions_no_artificial_deviation():
    # Two independent branches (researcher / analyst) with the SAME (role, tool,
    # target) multiset, interleaved in different orders.
    r_call = _ev(1, 'tool_call', role='researcher', tool='read_text_file', args={'path': 'documents/report.md'})
    r_res = _ev(2, 'tool_result', role='researcher', tool='read_text_file', out='data')
    a_call = _ev(3, 'tool_call', role='analyst', tool='read_text_file', args={'path': 'documents/call_transcript.md'})
    a_res = _ev(4, 'tool_result', role='analyst', tool='read_text_file', out='more')
    final = _ev(5, 'final_response', role='verifier', out='total revenue: 10')
    order1 = [_ev(0, 'user_input', out='task'), r_call, r_res, a_call, a_res, final]
    # Different absolute ordering of the same independent branch events.
    order2 = [_ev(0, 'user_input', out='task'), a_call, a_res, r_call, r_res, final]
    t1 = fin_trace('r1', {}, rep=0); t1['events'] = order1
    t2 = fin_trace('r2', {}, rep=1); t2['events'] = order2
    t3 = fin_trace('r3', {}, rep=2); t3['events'] = order1
    lep_t = fin_trace('lep', {}, cond='single_lep'); lep_t['events'] = order1
    out = bd.measure_configuration(lep_t, [t1, t2, t3], fin_spec(F2))
    actions = [p['actions'] for p in out['pair_benign']] + [p['actions'] for p in out['pair_lep']]
    assert all(a == 0.0 for a in actions), actions


def test_06_serial_vs_parallel_incompatible():
    refs = triple(parallel=True)
    lep = fin_trace('lep', F2, cond='single_lep', parallel=False)
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['comparable'] is False
    assert 'reference[0]_identity_mismatch' in ';'.join(out['reasons']) or \
           any('identity_mismatch' in r for r in out['reasons'])
    assert out['behavioral_anomaly'] is None


def test_07_missing_features_unavailable_not_zero():
    refs = [fin_trace('b0', {'total revenue': 10.0, 'net income': 20.0}, rep=0),
            fin_trace('b1', {'total revenue': 10.0, 'net income': 20.0}, rep=1),
            fin_trace('b2', {'total revenue': 10.0, 'net income': 20.0}, rep=2)]
    # LEP omits 'net income' entirely: that fact is *unavailable* on the LEP
    # side, so it must be dropped from the pair mean, not scored as a match.
    lep = fin_trace('lep', {'total revenue': 10.0}, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['component_lep']['assertions'] == 0.0  # 'total revenue' agrees
    # 'net income' is missing on the LEP side -> dropped from the pair mean,
    # not scored as a match.  Decisions are unavailable (no answer asserted)
    # on both sides, so they are excluded, not read as agreement.
    assert 'decisions' in out['pair_lep'][0]['unavailable']
    # Now drop BOTH facts: assertions becomes unavailable, not 0.
    empty = fin_trace('lep2', {}, cond='single_lep')
    out2 = bd.measure_configuration(empty, refs, fin_spec(F2))
    assert out2['pair_lep'][0]['assertions'] is None
    assert 'assertions' in out2['pair_lep'][0]['unavailable']


def test_08_final_answer_change_with_similar_intermediates():
    refs = [fin_trace(f'b{i}', F2, rep=i, ans='answer: 12.27%') for i in range(3)]
    # LEP reads the same files (actions ~0) but lands on a different answer.
    lep = fin_trace('lep', F2, cond='single_lep', ans='answer: 99.9%')
    out = bd.measure_configuration(lep, refs, fin_spec({**F2, **FANS}))
    assert out['component_lep']['decisions'] > 0.0
    assert out['behavioral_anomaly'] is True


def test_09_intermediate_change_final_recovery():
    refs = [fin_trace('b0', F2, rep=0, ans='answer: 12.27%'),
            fin_trace('b1', F2, rep=1, ans='answer: 12.27%'),
            fin_trace('b2', F2, rep=2, ans='answer: 12.27%')]
    # LEP drifted on one intermediate fact but its final answer matches refs.
    lep = fin_trace('lep', {'total revenue': 44.0, 'net income': 20.0},
                    cond='single_lep', ans='answer: 12.27%')
    out = bd.measure_configuration(lep, refs, fin_spec({**F2, **FANS}))
    assert out['component_lep']['decisions'] == 0.0
    assert 0.0 < out['component_lep']['assertions'] < 1.0
    # partial recovery keeps BND positive but below the unrecovered case,
    # which also loses a whole component to unavailability.
    out_full = bd.measure_configuration(
        fin_trace('lep2', {'total revenue': 44.0, 'net income': 20.0}, cond='single_lep'),
        refs, fin_spec({**F2, **FANS}))
    assert out['bnd'] < out_full['bnd']


# ── property tests ──────────────────────────────────────────────────────────


def test_symmetry_and_identity():
    fa = bd.extract_features(fin_trace('x', F2, rep=0), fin_spec(F2))
    fb = bd.extract_features(fin_trace('y', {'total revenue': 10.0, 'net income': 21.0}, rep=1),
                             fin_spec(F2))
    d_ab = bd.pairwise_distance(fa, fb, 'financial_analysis')
    d_ba = bd.pairwise_distance(fb, fa, 'financial_analysis')
    for c in bd.COMPONENTS:
        assert getattr(d_ab, c) == getattr(d_ba, c), c
    ii = bd.pairwise_distance(fa, fa, 'financial_analysis')
    for c in bd.COMPONENTS:
        assert getattr(ii, c) in (0.0, None), c


def test_distance_bounded():
    fa = bd.extract_features(fin_trace('x', F2, rep=0), fin_spec(F2))
    big = fin_trace('y', {'total revenue': 999999.0, 'net income': -0.01}, rep=1)
    fb = bd.extract_features(big, fin_spec(F2))
    d = bd.pairwise_distance(fa, fb, 'financial_analysis')
    for c in bd.COMPONENTS:
        v = getattr(d, c)
        assert v is None or 0.0 <= v <= 1.0, c
    assert d.total is None or 0.0 <= d.total <= 1.0


def test_deterministic_output():
    refs = triple()
    lep = fin_trace('lep', F2, cond='single_lep')
    w = bd.ComponentWeights(assertions=0.5, decisions=0.2, actions=0.2, outcomes=0.1)
    a = bd.measure_configuration(lep, refs, fin_spec(F2), w)
    b = bd.measure_configuration(lep, refs, fin_spec(F2), w)
    assert a == b


def test_component_weights_change_total():
    refs = triple()
    # Different fact values AND a different read path so that both the
    # assertions-dominant and the actions-dominant weightings see distinct
    # component distances on the LEP side.
    lep = fin_trace('lep', {'total revenue': 47.0, 'net income': 101.0},
                    cond='single_lep', path='documents/call_transcript.md')
    heavy_assert = bd.ComponentWeights(assertions=0.9, decisions=0.0, actions=0.0, outcomes=0.1)
    heavy_actions = bd.ComponentWeights(assertions=0.0, decisions=0.0, actions=0.5, outcomes=0.5)
    o1 = bd.score_bnd(lep, refs, fin_spec(F2), heavy_assert)
    o2 = bd.score_bnd(lep, refs, fin_spec(F2), heavy_actions)
    assert o1.d_lep != o2.d_lep
    assert o1.d_benign == 0.0 == o2.d_benign


def test_threshold_is_max_benign_pairwise():
    refs = [fin_trace('b0', {'total revenue': 10.0, 'net income': 20.0}, rep=0),
            fin_trace('b1', {'total revenue': 10.0, 'net income': 25.0}, rep=1),
            fin_trace('b2', {'total revenue': 10.0, 'net income': 20.0}, rep=2)]
    lep = fin_trace('lep', {'total revenue': 10.0, 'net income': 22.0}, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    totals = [p['total'] for p in out['pair_benign']]
    assert out['threshold']['t'] == max(totals)


def test_leave_one_out_reference_only():
    refs = triple()
    lep = fin_trace('lep', F2, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    loo = out['reference_only']
    assert set(k.split('(')[0] for k in loo) == {'B1', 'B2', 'B3'}
    assert all(v in (0.0, None) for v in loo.values())


def test_incomparable_references_are_not_forced():
    refs = triple()
    refs[2]['metadata']['fixture_id'] = 'other_fixture'
    refs[2]['trace_id'] = 'other.02.std.benign__a'
    lep = fin_trace('lep', F2, cond='single_lep')
    out = bd.measure_configuration(lep, refs, fin_spec(F2))
    assert out['comparable'] is False
    assert out['behavioral_anomaly'] is None


def test_threshold_on_real_production_traces(tmp_path, monkeypatch):
    """The per-configuration T heuristic on a real LEP trace + 3 real refs.

    Skips silently when the production tree is absent (e.g. CI); in-repo it
    exercises threshold semantics: T equals the max benign pairwise total,
    behavioral_anomaly is exactly D_LEP > T, and it must not be conflated
    with the v2 classification recorded in the trace's own metadata.
    """
    import glob as _glob
    import json as _json
    from collections import defaultdict
    source = _glob.glob('analysis/full_production_consolidated/source/traces/*.json')
    if not source:
        pytest.skip('production tree not present')
    refs_by_cell: dict = defaultdict(dict)
    lep_trace = None
    for p in sorted(source):
        data = _json.loads(open(p, 'rb').read())
        m = data.get('metadata') or {}
        cell = (m.get('fixture_id'), m.get('topology'), m.get('execution_variant', 'standard'))
        if m.get('condition') in ('benign', 'counterfactual') or not m.get('lep_codes'):
            rep = m.get('repetition_index')
            if rep is not None and rep not in refs_by_cell[cell]:
                refs_by_cell[cell][rep] = data
        elif m.get('condition') == 'single_lep' and m.get('dataset_eligible', True) and lep_trace is None:
            if len(refs_by_cell.get(cell, {})) >= 3:
                lep_trace = (data, cell)
                break  # refs for this cell were all seen before this LEP trace
    if lep_trace is None:
        pytest.skip('no complete reference cell found')
    data, cell = lep_trace
    fixture = cell[0]
    spec = None
    spec_path = f'workspace_fixtures/{fixture}/manifest.json'
    try:
        spec = _json.loads(open(spec_path, 'rb').read())
    except FileNotFoundError:
        pass
    refs = [refs_by_cell[cell][r] for r in sorted(refs_by_cell[cell])[:3]]
    out = bd.measure_configuration(data, refs, spec)
    assert out['comparable'] in (True, False)
    t = out['threshold']['t']
    d_lep = out['d_lep']
    if out['comparable'] and t is not None and d_lep is not None:
        assert t == max(p['total'] for p in out['pair_benign'] if p['total'] is not None)
        assert out['behavioral_anomaly'] == (d_lep > t)
        # The flag is a standalone experimental field; it must not appear
        # inside any propagation-occurred / v2 classification key.
        for forbidden in ('propagation_occurred', 'v2_classification'):
            assert forbidden not in out
    # Incomparable references must never be forced to a classification.
    if not out['comparable']:
        assert out['behavioral_anomaly'] is None


def test_research_verdict_extraction_and_distance():
    spec = {'task_family': 'research_synthesis', 'required_facts': {
        'claim_1_verdict': {}, 'claim_2_verdict': {}}}
    s1 = {'trace_id': 's1', 'metadata': _cell(fam='research_synthesis')['metadata'],
          'events': [_ev(0, 'final_response', out='Claim 1: SUPPORTED\nClaim 2: CONTRADICTED')]}
    s2 = deepcopy(s1); s2['trace_id'] = 's2'
    s2['events'][0]['output_text'] = 'Claim 1: SUPPORTED\nClaim 2: NOT ENOUGH INFO'
    d = bd.pairwise_distance(bd.extract_features(s1, spec), bd.extract_features(s2, spec),
                             'research_synthesis')
    assert d.assertions == 0.5  # 1 of 2 verdicts differ


def test_code_review_action_alignment():
    spec = {'task_family': 'code_review', 'required_issues': [
        {'id': 'path_traversal', 'location': 'src/main.py'}]}
    def evs(path):
        return [_ev(0, 'tool_call', role='analyst', tool='read_text_file', args={'path': path}),
                _ev(1, 'tool_call', role='analyst', tool='write_file',
                    args={'path': 'src/patch.diff', 'content': 'x'})]
    c1 = {'trace_id': 'c1', 'metadata': _cell(fam='code_review')['metadata'],
          'events': evs('src/main.py') + [_ev(2, 'final_response', out='root cause in src/main.py')]}
    c2 = deepcopy(c1); c2['trace_id'] = 'c2'
    c2['events'] = evs('src/main.py') + [_ev(2, 'final_response', out='root cause in src/main.py')]
    d = bd.pairwise_distance(bd.extract_features(c1, spec), bd.extract_features(c2, spec),
                             'code_review')
    assert d.actions == 0.0
    assert d.outcomes == 0.0
    c3 = deepcopy(c2)
    c3['events'][1]['tool_arguments']['path'] = 'other/patch.diff'
    d3 = bd.pairwise_distance(bd.extract_features(c2, spec), bd.extract_features(c3, spec),
                              'code_review')
    assert d3.actions > 0.0
    assert d3.outcomes > 0.0


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
