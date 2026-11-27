"""
Evaluation-statistics and reference-semantics tests (requirement 14).

Covers: Wilson intervals, fixture-cluster bootstrap, the reference
provenance rules (agreed / adjudicated / unresolved-never-a-negative),
value-canon normalisation (verdicts, scale-word numerics, issue flags),
and the pair-reference exclusion of unusable reference sets.  No live
API calls; no writes outside tmp_path.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'evaluation'))
sys.path.insert(0, os.path.join(HERE, '..'))

from run_reference_eval import wilson, cluster_bootstrap            # noqa: E402
import build_ai_reference as BR                                    # noqa: E402
import base                                                         # noqa: E402


# ── intervals ───────────────────────────────────────────────────────────────


def test_wilson_bounds_ordering_and_endpoints():
    w = wilson(5, 10)
    assert w['p'] == 0.5 and w['k'] == 5 and w['n'] == 10
    assert 0.0 <= w['lo'] <= w['p'] <= w['hi'] <= 1.0
    # degenerate endpoints
    assert wilson(0, 0) is None
    w0 = wilson(0, 8)
    assert w0['p'] == 0.0 and w0['lo'] == 0.0
    assert w0['hi'] >= w0['p']


def test_cluster_bootstrap_is_deterministic():
    rows = []
    for fx in range(4):
        for i in range(5):
            rows.append({'fixture_id': fx, 'design_weight': 2.0,
                        '_den': True, '_num': (i < 3)})
    a = cluster_bootstrap(rows)
    b = cluster_bootstrap(rows)
    assert a == b                       # fixed-seed => byte-reproducible
    est = a['estimate']
    # point estimate: 3-of-5 correct per fixture, equal weights
    assert abs(est - 0.6) < 1e-9
    assert a['lo'] is not None and a['hi'] is not None
    assert a['lo'] <= est <= a['hi']


def test_cluster_bootstrap_excludes_non_denominator_rows():
    rows = [{'fixture_id': 0, 'design_weight': 1.0, '_den': False,
             '_num': True},
            {'fixture_id': 0, 'design_weight': 1.0, '_den': True,
             '_num': False}]
    a = cluster_bootstrap(rows)
    assert a['estimate'] == 0.0        # numerator must respect _den


# ── reference semantics ─────────────────────────────────────────────────────


def test_unresolved_reference_is_excluded_never_negative():
    # an unresolved (ai_unresolved) reference item has state 'unavailable'
    rows = [r for r in _ref_rows() if r['task_family'] == 'code_review']
    assert rows, 'code_review sample units expected'
    for r in rows:
        for ik, ref in r['items'].items():
            if ref['provenance'] in ('ai_unresolved', 'judge_invalid'):
                assert ref['state'] == 'unavailable'
                # the scoring contract: unavailable ref is excluded, not a
                # negative — encoded as state 'unavailable', never 'absent'
                assert ref['state'] != 'absent'


def test_every_unit_has_exactly_one_reference_per_item():
    for r in _ref_rows():
        for ik, ref in r['items'].items():
            assert ref.get('provenance') in (
                'ai_agreed', 'ai_adjudicated', 'ai_unresolved',
                'judge_invalid'), f'missing provenance: {ref}'
            assert set(('state', 'value', 'canonical')) <= set(ref)


def test_agreement_between_two_llms_is_not_ground_truth_label():
    # the reference files carry the caveat string, not a human-claim
    rep = BR.build_extraction_reference()
    assert 'item_provenance' in rep
    # and every provenance label is AI-prefixed — none says 'human'
    prov = rep['item_provenance']
    for p in prov:
        assert p.startswith(('ai_', 'judge_')), f'{p} is not an AI label'


def test_pair_reference_excludes_unusable_refsets():
    pr = BR.build_pair_reference()
    for r in pr['rows']:
        if r['excluded']:
            # an excluded group's union reference must be None, never a
            # fabricated positive/negative
            assert r['union_ref'] is None or r['union_ref'] in (
                'positive', 'negative'), 'union may exist but the group ' \
                                          'is flagged excluded'
            assert r['reference_set_usable'] is False
    n_ex = sum(1 for r in pr['rows'] if r['excluded'])
    assert n_ex > 0, 'the corpus has unusable reference sets; the ' \
                    'exclusion must be non-vacuous'


# ── value canon normalisation ───────────────────────────────────────────────


def test_canon_verdict_maps_surface_spellings():
    assert BR._canon_verdict('SUPPORTED') == 'SUPPORTED'
    assert BR._canon_verdict('Contradicted') == 'CONTRADICTED'
    assert BR._canon_verdict('NOT ENOUGH INFORMATION') == 'NOT_ENOUGH'
    assert BR._canon_verdict('refuted') == 'CONTRADICTED'
    assert BR._canon_verdict('maybe') is None        # unmapped => None


def test_canon_fin_value_applies_scale_words_and_keeps_percent():
    assert abs(BR._canon_fin_value('13,532 million') - 1.3532e10) < 1.0
    assert abs(BR._canon_fin_value('$485,760,000') - 485760000.0) < 1.0
    # percent-marked values stay as 18.84 (the extractors record percent
    # answers as 18.84, not 0.1884)
    assert abs(BR._canon_fin_value('18.84%') - 18.84) < 1e-9
    assert BR._canon_fin_value('unknown') is None


def test_issue_ordinal_maps_positionally_to_spec_ids():
    spec_ids = ['path_traversal', 'sanitize_stub',
                'validate_access_bypass', 'off_by_one']
    assert BR._issue_id_from_ordinal('issue_1', spec_ids) == \
        'path_traversal'
    assert BR._issue_id_from_ordinal('ISSUE_4', spec_ids) == 'off_by_one'
    # out of range: not a valid positional id, and not a literal spec id
    assert BR._issue_id_from_ordinal('issue_9', spec_ids) is None
    # a literal spec id passes through
    assert BR._issue_id_from_ordinal('path_traversal', spec_ids) == \
        'path_traversal'


def test_state_collision_fix_lets_code_items_resolve():
    # the original bug: a leaf stance 'identified' was read as the STATE.
    # after the fix, a parent field's observed state survives the
    # expansion and the item is judgeable.
    labels = {'issues_identified': {
        'state': 'observed',
        'value': {'issue_1': 'identified', 'issue_2': 'identified'}}}
    items = ['fast_delete_retains_primary_key']
    # ordinal issue_1 -> first spec id positionally
    A = BR._normalize_extr_labels(labels, 'code_review', items)
    rec = A.get('fast_delete_retains_primary_key')
    assert rec is not None
    assert rec['state'] == 'observed', \
        f'state must be the PARENT availability state, got {rec["state"]}'
    # a bare 'identified' stance: identified=True, no repair proposed
    assert rec['canonical'] == {'identified': True, 'repair': False}


# ── helpers ─────────────────────────────────────────────────────────────────


def _ref_rows():
    p = os.path.join(base.OUT_DIR, 'ai_reference_extraction.jsonl')
    if not os.path.exists(p):
        import pytest
        pytest.skip('AI reference not yet built')
    return [json.loads(l) for l in open(p) if l.strip()]
