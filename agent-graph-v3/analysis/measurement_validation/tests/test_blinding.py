"""Tests for the ground-truth containment boundary and annotation blinding.

These are the two properties that, if broken, would invalidate the entire
validation while every other test still passed.  They are tested adversarially:
the tests try to find a leak rather than confirming a clean run.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, HERE)

import common as C  # noqa: E402

TEMPLATES = os.path.join(C.VALIDATION_ROOT, 'annotation_templates')

#: Substrings that must not appear in any annotation packet.
FORBIDDEN = (
    'LEP_', 'HANDOFF_CORRUPTION', 'INDIRECT_PROMPT', 'INPUT_DISREGARD',
    'TOOL_RESULT_CORRUPTION', 'MEMORY_POISONING',
    'single_origin', 'many_to_one', 'one_to_many',
    'review_loop', 'branch_and_verify', 'coordinator_workers',
    'code_review', 'financial_analysis', 'research_synthesis',
    'finq', 'scifact', 'swe_',
)


def _packet_paths():
    if not os.path.isdir(TEMPLATES):
        return []
    return [os.path.join(TEMPLATES, f) for f in sorted(os.listdir(TEMPLATES))
            if f.startswith(('extraction_packet_', 'pair_packet_'))]


# ── GT containment ─────────────────────────────────────────────────────────
#: The canonical guard lives in the v6 package.  Importing it (rather than
#: reimplementing a blacklist here) is deliberate: a local copy could drift
#: from the one the extractors actually enforce.
sys.path.insert(0, os.path.join(C.VALIDATION_ROOT, '..', 'measurement_v6'))


def _gt():
    import extract_core
    return extract_core


def test_strip_gt_removes_the_keys_it_declares():
    """Pin the CURRENT behaviour of v6's strip_gt so a change is noticed."""
    ec = _gt()
    spec = {'answer': 5, 'answer_calculation': 'x', 'grading': {},
            'fixture_id': 'keep_me'}
    out = ec.strip_gt(spec)
    for k in ('answer', 'answer_calculation', 'grading'):
        assert k not in out
    assert out['fixture_id'] == 'keep_me'


def test_strip_gt_strips_gt_inside_required_facts():
    """The guard recurses into the per-fact buckets, which is where the
    answer literals actually live in a real fixture spec."""
    ec = _gt()
    spec = {'required_facts': {
        'fact_1': {'key': 'fact_1', 'answer': 28.663, 'tolerance': 0.01,
                   'source_text': 'the answer is 28.663'}}}
    out = ec.strip_gt(spec)
    ent = out['required_facts']['fact_1']
    for k in ('answer', 'tolerance', 'source_text'):
        assert k not in ent, f'nested GT key {k} survived strip_gt'


def test_strip_gt_does_not_mutate_the_input():
    ec = _gt()
    spec = {'answer': 1.0, 'keep': 2,
            'required_facts': {'f': {'answer': 3.0}}}
    before = json.dumps(spec, sort_keys=True)
    ec.strip_gt(spec)
    assert json.dumps(spec, sort_keys=True) == before


def test_assert_gt_free_spec_raises_on_a_leaked_root_key():
    ec = _gt()
    with pytest.raises(ec.GroundTruthLeak):
        ec.assert_gt_free_spec({'tolerance': 0.01}, 'test')


def test_assert_gt_free_spec_raises_on_a_leaked_nested_key():
    ec = _gt()
    with pytest.raises(ec.GroundTruthLeak):
        ec.assert_gt_free_spec(
            {'required_facts': {'f1': {'answer': 5}}}, 'test')


def test_assert_gt_free_spec_accepts_a_stripped_spec():
    ec = _gt()
    spec = {'required_facts': {'f1': {'answer': 5, 'tolerance': 0.1}},
            'answer': 5}
    ec.assert_gt_free_spec(ec.strip_gt(spec), 'test')   # must not raise


# ── DOCUMENTED DEFECT in v6 strip_gt (not modified: out of task scope) ──────
def test_known_defect_v6_strip_gt_leaves_root_gt_keys():
    """KNOWN DEFECT, pinned deliberately.

    ``extract_core.strip_gt`` pops only three literal root keys (``answer``,
    ``answer_calculation``, ``grading``) and then strips nested GT keys ONLY
    inside ``required_facts`` / ``required_issues``.  Every other declared
    ``GT_KEYS`` member survives at the root.

    This test asserts the DEFECTIVE behaviour so that the day v6 is fixed the
    suite fails loudly and the validation report is regenerated, rather than
    the report silently describing a boundary that has since changed.
    """
    ec = _gt()
    survivors = [k for k in ('truth', 'value', 'tolerance', 'gold', 'expected',
                             'pattern', 'source_text', 'value_strings')
                 if k in ec.strip_gt({k: 'x'})]
    assert survivors, (
        'v6 strip_gt now removes these root keys — UPDATE the validation '
        'report and this pin. Survivors expected but found none.')
    assert len(survivors) == 8


def test_known_defect_v6_strip_gt_ignores_gt_nested_elsewhere():
    """Second half of the same defect: GT nested outside the two recognised
    buckets is untouched."""
    ec = _gt()
    out = ec.strip_gt({'meta': {'answer': 5}, 'nested': [{'tolerance': 0.1}]})
    assert out['meta']['answer'] == 5
    assert out['nested'][0]['tolerance'] == 0.1


# ── the hardened wrapper the validation itself relies on ───────────────────
def test_hardened_strip_closes_the_root_and_nesting_gaps():
    """``extractors.strip_gt_hardened`` is the form the VALIDATION uses.

    The v6 defect above means trusting ``strip_gt`` alone would leave eight
    declared GT keys reachable at the root.  The hardened version removes
    EVERY declared GT key at EVERY depth, so containment does not depend on
    where in the spec the answer happens to sit.
    """
    import extractors as E
    ec = _gt()
    spec = {'truth': 1, 'value': 2, 'tolerance': 0.1, 'gold': 3,
            'expected': 4, 'pattern': 'x', 'source_text': 'y',
            'value_strings': ['z'], 'answer': 5,
            'meta': {'answer': 5},
            'nested': [{'tolerance': 0.1}],
            'fixture_id': 'keep_me',
            'required_facts': {'f': {'answer': 1, 'key': 'f'}}}
    out = E.strip_gt_hardened(spec)
    blob = json.dumps(out)
    for k in ec.GT_KEYS:
        assert f'"{k}"' not in blob, f'hardened strip left {k} somewhere'
    assert out['fixture_id'] == 'keep_me'
    assert out['required_facts']['f']['key'] == 'f'


def test_hardened_strip_is_idempotent():
    import extractors as E
    spec = {'answer': 1, 'meta': {'gold': 2}, 'keep': 3}
    once = E.strip_gt_hardened(spec)
    twice = E.strip_gt_hardened(once)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


def test_run_both_feeds_the_gt_value_stripped_spec():
    """The measurement feed must be strip_gt_values (remove GT-value fields,
    keep task-side label/unit).  A regression that feeds raw GT, or that
    uses the aggressive key-removal form (which breaks frozen financial
    coverage), would invalidate the measurements."""
    import inspect
    import extractors as E
    src = inspect.getsource(E.run_both)
    assert 'strip_gt_values' in src, (
        'run_both no longer feeds strip_gt_values as the default containment '
        'boundary')
    # strip_gt_values must keep the task-side fields the frozen extractor
    # legitimately reads
    spec = {'required_facts': {'answer': {'label': 'L', 'unit': 'percent',
                                           'value': 1.0, 'tolerance': 0.1,
                                           'pattern': r'x', 'source_text': 's'}}}
    stripped = E.strip_gt_values(spec)
    ans = stripped['required_facts']['answer']
    assert ans.get('label') == 'L' and ans.get('unit') == 'percent', (
        'strip_gt_values dropped task-side label/unit that the frozen '
        'extractor reads')
    for k in ('value', 'tolerance', 'pattern', 'source_text'):
        assert k not in ans, f'strip_gt_values left GT-value field {k}'


def test_strip_gt_values_is_idempotent():
    import extractors as E
    spec = {'required_facts': {'answer': {'label': 'L', 'unit': 'u',
                                          'value': 1}, 'f1': {'value': 2}}}
    once = E.strip_gt_values(spec)
    twice = E.strip_gt_values(once)
    assert once == twice


def test_strip_gt_hardened_is_the_audit_primitive_not_the_feed():
    """strip_gt_hardened removes every v6 GT key aggressively (audit).  It
    must NOT be the run_both feed — that over-removes and breaks frozen
    financial coverage.  Pin the distinction so it cannot silently collapse."""
    import inspect
    import extractors as E
    run_src = inspect.getsource(E.run_both)
    assert 'strip_gt_hardened' not in run_src, (
        'run_both feeds the aggressive key-removal strip; it must use '
        'strip_gt_values')
    # the audit primitive still over-removes (removes label/unit too)
    spec = {'required_facts': {'f1': {'label': 'L', 'unit': 'u', 'value': 1}}}
    h = E.strip_gt_hardened(spec)
    # v6 GT_KEYS includes 'value'; aggressive strip removes it (and would
    # remove label/unit only if they were in GT_KEYS, which they are not)
    assert 'value' not in (h['required_facts']['f1']), \
        'audit strip no longer removes GT-value fields'


# ── the differential leak probe ────────────────────────────────────────────
def _research_spec():
    return {
        'claim_keys': ['claim_1'],
        'required_facts': {
            'claim_1': {'key': 'claim_1', 'source_text': 'x',
                        'answer': 'SUPPORTED'}},
    }


def test_leak_probe_result_shape():
    from extractors import leak_probe
    trace = {'final_response': {'content': 'Claim 1 is SUPPORTED.'},
             'events': []}
    r = leak_probe(trace, _research_spec(), 'research_synthesis')
    assert 'leaked' in r and isinstance(r['leaked'], bool)
    assert 'method' in r


def test_leak_probe_detects_a_planted_leak(monkeypatch):
    """The probe is only meaningful if it CAN detect a leak, so this test
    plants one — an extractor that echoes a GT-value field into its output —
    and requires a positive."""
    import extractors as E

    class _EchoSpec:
        """A stand-in feature object that leaks the spec's GT-value field into
        the extracted output.  The canaried arm echoes the canary; the
        value-stripped arm echoes nothing.  That difference is a leak."""
        def __init__(self, spec):
            self.spec = spec

    # tolerant of the real v6 extractor object (which has no .spec): the v6
    # arm is the control; only the frozen arm is faked to leak.
    def fake_jsonable(f):
        return {'echoed_answer': (getattr(f, 'spec', None) or {}).get('answer')}

    monkeypatch.setattr(E, '_frozen_jsonable', fake_jsonable)
    monkeypatch.setattr(E.FROZEN, 'extract_features_v4',
                        lambda trace, spec: _EchoSpec(spec))
    monkeypatch.setattr(E, '_v6_jsonable', fake_jsonable)

    trace = {'final_response': {'content': 'x'}, 'events': []}
    spec = {'answer': 42, 'claim_keys': ['c1']}
    r = E.leak_probe(trace, spec, 'research_synthesis')
    assert r['leaked'] is True, (
        f'probe failed to detect a planted GT-value leak: {r}')

    # behavioural check: the canaried arm must actually differ from the
    # value-stripped arm, or the differential is vacuous by construction.
    canaried = E._canarify_gt_values(spec)
    stripped = E.strip_gt_values(spec)
    assert canaried != stripped, 'canary and stripped arms are identical'


def test_leak_probe_runs_on_a_realistic_clean_fixture():
    from extractors import leak_probe
    trace = {'final_response': {'content': 'Claim 1 is SUPPORTED.'},
             'events': []}
    r = leak_probe(trace, _research_spec(), 'research_synthesis')
    assert r['leaked'] is False, f'false leak on a clean fixture: {r}'


def test_leak_probe_is_not_vacuous_on_a_gt_bearing_spec():
    """A probe that replaces nothing cannot detect anything.

    This is the specific failure mode that would make the containment report
    a rubber stamp: if run_both stripped GT on BOTH arms of the differential,
    the two extractions would be identical by construction and ``leaked``
    would be False no matter how badly an extractor leaked.
    """
    from extractors import leak_probe
    trace = {'final_response': {'content': 'Claim 1 is SUPPORTED.'},
             'events': []}
    r = leak_probe(trace, _research_spec(), 'research_synthesis')
    assert r['vacuous'] is False, (
        'probe canaried no GT-value fields: it would pass even against a '
        'leaking extractor')
    assert r['n_gt_value_fields_canaried'] > 0
    assert r['substantive'] is True


def test_v6_guard_refuses_a_gt_bearing_spec():
    """v6's OWN containment guard must refuse a raw GT-bearing spec — the
    extractor-level backstop that the differential probe complements.

    Pins a known v6 defect the audit primitive documents:
    ``assert_gt_free_spec`` still fires on v6's *own* ``strip_gt(spec)`` when a
    GT-value field (``tolerance``) sits at the spec ROOT, because v6's strip
    removes GT keys from the root only for 3 literal names and 8 of its 11
    declared GT keys survive at the root.  That is exactly why the validation
    feeds ``strip_gt_values`` instead of trusting v6's strip — pin both the
    defect and the escape here so it cannot be silently fixed or regressed
    without regenerating reports.
    """
    import extractors as E
    import extract_core as V6CORE
    # root-level GT: the guard must fire on the full spec...
    spec_root = {'answer': 42, 'tolerance': 0.0, 'claim_keys': ['c1']}
    r1 = E.v6_containment_check(spec_root)
    assert r1['raises_on_full_spec'] is True, 'v6 guard should refuse root GT'
    # ...and the documented defect: v6's own strip leaves root `tolerance`.
    assert r1['raises_on_stripped'] is True, (
        'if v6 strip_gt now removes all root GT keys, this defect is fixed '
        'and the audit record must be regenerated')
    # nested-only GT: v6's strip handles it correctly
    spec_nested = {'claim_keys': ['c1'],
                   'required_facts': {'c1': {'value': 5, 'tolerance': 1,
                                              'label': 'L', 'unit': 'u'}}}
    r2 = E.v6_containment_check(spec_nested)
    assert r2['raises_on_full_spec'] is True
    assert r2['raises_on_stripped'] is False, (
        'nested GT should be stripped by v6; a regression here changes v6')
    # the value-stripped form the validation actually feeds is ALWAYS accepted
    # by v6's guard (verified 0/16,773 corpus rows trip it)
    for sp in (spec_root, spec_nested):
        V6CORE.assert_gt_free_spec(E.strip_gt_values(sp), 'validation feed')


def test_probe_reports_a_method_string_for_audit():
    from extractors import leak_probe
    trace = {'final_response': {'content': 'x'}, 'events': []}
    r = leak_probe(trace, {'claim_keys': ['c1']}, 'research_synthesis')
    assert isinstance(r.get('method'), str) and r['method']


def test_run_both_strips_at_entry_and_can_prove_it():
    """The default path must hand the extractors a GT-value-clean spec and
    say so."""
    import extractors as E
    trace = {'final_response': {'content': 'The answer is 42.'}, 'events': []}
    spec = {'answer': 42, 'gold': 42, 'meta': {'tolerance': 0.1},
            'required_facts': {'f': {'key': 'f', 'value': 1}},
            'claim_keys': ['c1']}
    st = E.run_both(trace, spec, 'research_synthesis')
    assert st['stripped_at_entry'] is True
    assert st['spec_gt_value_free'] is True
    assert st['spec_residual_gt_value_fields'] == []


def test_run_both_reports_residual_gt_value_when_stripping_is_disabled():
    """With strip=False the caller can SEE GT-value fields reaching the
    extractor — this is what makes the differential probe meaningful."""
    import extractors as E
    trace = {'final_response': {'content': 'x'}, 'events': []}
    spec = {'claim_keys': ['c1'], 'meta': {'tolerance': 0.1}}
    st = E.run_both(trace, spec, 'research_synthesis', strip=False)
    assert st['stripped_at_entry'] is False
    assert 'tolerance' in st['spec_residual_gt_value_fields']


# ── blinding ───────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_no_packet_leaks_a_condition_or_lep_token():
    hits = []
    for p in _packet_paths():
        txt = open(p, encoding='utf-8').read().lower()
        for tok in FORBIDDEN:
            if tok.lower() in txt:
                hits.append((os.path.basename(p), tok))
    assert not hits, f'annotation packets leak identifying tokens: {hits}'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_packets_do_not_contain_a_source_path():
    hits = []
    for p in _packet_paths():
        txt = open(p, encoding='utf-8').read()
        if 'workspace_fixtures' in txt or 'consolidated_' in txt:
            hits.append(os.path.basename(p))
    assert not hits, f'packets expose source paths: {hits}'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_packets_do_not_contain_a_trace_id():
    """Real trace ids are family+topology+LEP composites; showing one would
    disclose the condition in one glance."""
    sample = C.read_jsonl(os.path.join(C.VALIDATION_ROOT,
                                       'sample_manifest.jsonl'))
    real = {r['trace_id'] for r in sample}
    hits = []
    for p in _packet_paths():
        txt = open(p, encoding='utf-8').read()
        for tid in real:
            if tid in txt:
                hits.append((os.path.basename(p), tid))
                break
    assert not hits, f'packets expose real trace ids: {hits[:3]}'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_extraction_packet_does_not_contain_the_expected_answer():
    """Hard GT containment: no packet may carry an expected value field."""
    bad_keys = ('expected_answer', 'gold_answer', '"gold"', 'tolerance',
                'expected_verdicts', 'gold_verdicts', 'answer_key')
    hits = []
    for p in _packet_paths():
        txt = open(p, encoding='utf-8').read()
        for k in bad_keys:
            if k in txt:
                hits.append((os.path.basename(p), k))
    assert not hits, f'packets carry ground-truth fields: {hits}'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_annotators_receive_different_orders():
    """Two annotators must not see the same presentation order, or an
    order-dependent bias would be shared and invisible.

    Each annotator gets independently keyed pseudonyms, so the two packets
    share no token strings by design; order must be compared through the
    key file, on the underlying real ids.
    """
    bm = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      '..', '.cache', 'blind_id_map.json')
    if not os.path.isfile(bm):
        pytest.skip('key file not built')
    maps = json.load(open(bm))['maps']
    inv = {w: {t: r for r, t in maps[w].items()} for w in ('A', 'B')}
    a = [inv['A'][json.loads(l)['item_token']]
         for l in open(os.path.join(TEMPLATES,
                                    'extraction_packet_annotator_A.jsonl'))]
    b = [inv['B'][json.loads(l)['item_token']]
         for l in open(os.path.join(TEMPLATES,
                                    'extraction_packet_annotator_B.jsonl'))]
    assert sorted(a) == sorted(b), 'annotators must label the same item set'
    assert a != b, 'annotators must receive different presentation orders'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_pair_groups_hide_which_member_is_the_target():
    """The group must be symmetric: no field may mark the target."""
    p = os.path.join(TEMPLATES, 'pair_packet_annotator_A.jsonl')
    if not os.path.isfile(p):
        pytest.skip('pair packet not built')
    for line in open(p, encoding='utf-8'):
        rec = json.loads(line)
        for m in rec['members']:
            assert 'item_kind' not in m, 'member carries an item_kind marker'
            assert 'role' not in m, 'member carries a role marker'
            assert 'is_target' not in m
            assert isinstance(m['member_index'], int)


def test_blind_pair_roles_file_holds_the_truth_and_is_not_a_packet():
    """The role map must exist (so evaluation can recover the target) and must
    live OUTSIDE the packet directory (so annotators never get it)."""
    p = os.path.join(C.CACHE_DIR, 'blind_pair_roles.json')
    if not os.path.isfile(p):
        pytest.skip('pair roles not built')
    assert os.path.dirname(p) == C.CACHE_DIR
    assert not os.path.abspath(p).startswith(os.path.abspath(TEMPLATES))
    d = C.read_json(p)
    roles = {v['role'] for m in d['roles'].values() for v in m.values()}
    assert roles == {'target', 'reference'}


def test_blind_id_map_is_confidential_and_complete():
    p = os.path.join(C.CACHE_DIR, 'blind_id_map.json')
    if not os.path.isfile(p):
        pytest.skip('blind id map not built')
    assert not os.path.abspath(p).startswith(os.path.abspath(TEMPLATES))
    d = C.read_json(p)
    for who in ('A', 'B'):
        m = d['maps'][who]
        assert m, f'annotator {who} has no id map'
        # tokens must be unique within an annotator
        assert len(set(m.values())) == len(m)
        # and must not resemble the real ids
        for real, tok in m.items():
            assert tok != real
            assert 'LEP' not in tok and 'benign' not in tok


def test_annotator_tokens_are_per_annotator_distinct():
    """The same unit must not get the same token for both annotators, so a
    token cannot be cross-referenced between the two blindings."""
    p = os.path.join(C.CACHE_DIR, 'blind_id_map.json')
    if not os.path.isfile(p):
        pytest.skip('blind id map not built')
    d = C.read_json(p)
    a = d['maps']['A']
    b = d['maps']['B']
    shared = set(a) & set(b)
    assert shared, 'both annotators should label the same units'
    same = [k for k in shared if a[k] == b[k]]
    assert not same, f'{len(same)} units share a token across annotators'


# ── response-template integrity ────────────────────────────────────────────
@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_response_template_lines_up_with_its_packet():
    for who in ('A', 'B'):
        pkt = os.path.join(TEMPLATES, f'extraction_packet_annotator_{who}.jsonl')
        rsp = os.path.join(TEMPLATES, f'responses_extraction_annotator_{who}.jsonl')
        if not (os.path.isfile(pkt) and os.path.isfile(rsp)):
            continue
        pt = [json.loads(l)['item_token'] for l in open(pkt)]
        rt = [json.loads(l)['item_token'] for l in open(rsp)]
        assert pt == rt, f'annotator {who}: response template out of order'


@pytest.mark.skipif(not _packet_paths(), reason='annotation packets not built')
def test_response_template_offers_an_explicit_unavailable_option():
    """The abstention mechanism must be present in every template, not just
    documented in prose."""
    rsp = os.path.join(TEMPLATES, 'responses_extraction_annotator_A.jsonl')
    if not os.path.isfile(rsp):
        pytest.skip('no template')
    rec = json.loads(next(open(rsp)))
    for key, slot in rec['labels'].items():
        assert 'state' in slot
        assert slot['state'] is None, 'template must start unanswered'
