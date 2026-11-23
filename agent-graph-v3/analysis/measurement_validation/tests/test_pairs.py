"""Tests for matched-pair validity.

The pair set is what the divergence-classification validation rests on.  A pair
that silently substitutes one reference out of three, or that compares a LEP
execution against a benign execution from a different fixture, would make the
human/pipeline comparison meaningless while looking entirely normal.

NOTE on predictions in ``pair_manifest.jsonl``
----------------------------------------------
The pair manifest DOES carry pipeline outputs (``union_v6``,
``v6_per_dimension``, ``m4_matched_v6``, ...).  That is intentional: it is the
internal join table that makes disagreement analysis possible.  It is not the
annotator-facing artifact.

Blinding is therefore tested where it belongs — on the generated PACKETS
(``test_packets_are_blind``), not on the manifest.  Testing the manifest for
prediction keys would be testing the wrong file, and would "pass" by deleting
the very fields the evaluation needs.
"""
import collections
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, HERE)

import common as C  # noqa: E402

PAIRS = os.path.join(C.VALIDATION_ROOT, 'pair_manifest.jsonl')
FRAME = os.path.join(C.CACHE_DIR, 'frame_units.jsonl')
TEMPLATES = os.path.join(C.VALIDATION_ROOT, 'annotation_templates')

needs_pairs = pytest.mark.skipif(not os.path.isfile(PAIRS),
                                 reason='pairs not built')
needs_frame = pytest.mark.skipif(not os.path.isfile(FRAME),
                                 reason='frame not built')


@pytest.fixture(scope='module')
def pairs():
    return C.read_jsonl(PAIRS)


@pytest.fixture(scope='module')
def frame():
    return {r['unit_id']: r for r in C.read_jsonl(FRAME)}


# ── size and coverage ──────────────────────────────────────────────────────
@needs_pairs
def test_pair_set_is_about_one_hundred_fifty(pairs):
    assert 130 <= len(pairs) <= 170, f'{len(pairs)} pairs, target ~150'
    ids = [p['pair_id'] for p in pairs]
    assert len(ids) == len(set(ids)), 'duplicate pair ids'


@needs_pairs
def test_every_pair_has_a_nonempty_reference_set(pairs):
    for p in pairs:
        refs = p.get('benign_reference_set')
        assert isinstance(refs, list) and refs, (
            f'{p["pair_id"]}: no benign reference')
        assert p.get('n_benign_references') == len(refs), (
            f'{p["pair_id"]}: n_benign_references disagrees with the list')


@needs_pairs
def test_all_pairs_keep_multiple_references(pairs):
    """The pipeline averages distance over the reference set.  A pair reduced
    to one reference would be compared against a different quantity than the
    pipeline computed."""
    multi = [p for p in pairs if len(p['benign_reference_set']) > 1]
    assert multi, 'no multi-reference pair retained'
    # every pair must match the frame's own set size
    assert len(multi) == len(pairs), (
        f'{len(pairs) - len(multi)} pairs carry a single reference while the '
        f'pipeline averages over the full set')


@needs_pairs
@needs_frame
def test_reference_sets_match_the_frame_exactly(pairs, frame):
    """The most important pair test.

    If the manifest carried a SUBSET of the frame's reference set, the human
    judgment would be compared against a different quantity than the pipeline
    computed — and nothing in the report would reveal it.
    """
    mismatches = []
    for p in pairs:
        row = frame.get(p['unit_id'])
        if row is None:
            continue
        want = list(row.get('reference_set') or [])
        got = list(p.get('benign_reference_set') or [])
        if sorted(want) != sorted(got):
            mismatches.append((p['pair_id'], want, got))
    assert not mismatches, (
        f'{len(mismatches)} pairs carry a reference set differing from the '
        f'frame: {mismatches[:2]}')


@needs_pairs
def test_references_share_the_targets_fixture(pairs):
    """A benign reference from another fixture is not a valid comparison."""
    bad = []
    for p in pairs:
        for ref in p.get('benign_reference_set') or []:
            # trace ids are prefixed with the fixture id
            if not ref.startswith(p['fixture_id']):
                bad.append((p['pair_id'], ref))
    assert not bad, f'references from a different fixture: {bad[:3]}'


@needs_pairs
def test_target_is_never_one_of_its_own_references(pairs):
    for p in pairs:
        assert p['trace_id'] not in (p.get('benign_reference_set') or [])


@needs_pairs
def test_pairs_cover_the_required_strata(pairs):
    """The pair set must cover zero- and nonzero-threshold positives,
    negatives, and disagreements — not just the diagnostic-looking cases."""
    classes = collections.Counter(p.get('class') for p in pairs)
    assert len(classes) >= 4, f'only {len(classes)} pair classes: {classes}'
    expected_substrings = ('zero_threshold', 'nonzero_threshold', 'negative',
                           'disagreement')
    joined = ' '.join(classes)
    missing = [s for s in expected_substrings if s not in joined]
    assert not missing, (
        f'pair classes lack required strata {missing}; present: {sorted(classes)}')


@needs_pairs
def test_every_pair_has_exactly_one_class(pairs):
    """Overlapping classes would let a row be counted twice in a per-class
    rate, inflating whichever class it was double-filed under."""
    for p in pairs:
        assert isinstance(p.get('class'), str) and p['class']
        assert p.get('class_description'), (
            f'{p["pair_id"]} has no human-readable class description')


@needs_pairs
def test_pairs_span_all_families_and_leps(pairs):
    fams = {p['task_family'] for p in pairs}
    assert fams == {'code_review', 'financial_analysis',
                    'research_synthesis'}, fams
    leps = {p.get('lep_code') for p in pairs if p.get('lep_code')}
    assert leps == {'TOOL_RESULT_CORRUPTION', 'INDIRECT_PROMPT_INJECTION',
                    'HANDOFF_CORRUPTION', 'INPUT_DISREGARD'}, leps


@needs_pairs
def test_pairs_are_not_all_from_one_topology(pairs):
    tops = collections.Counter(p['topology'] for p in pairs)
    assert len(tops) >= 2, f'pairs cover only {list(tops)}'


@needs_pairs
def test_pair_design_states_the_multi_reference_semantics():
    """Annotators cannot map a judgment onto an averaged comparison unless
    the averaging is stated, so the design must record it."""
    d = C.read_json(os.path.join(C.VALIDATION_ROOT, 'PAIR_DESIGN.json'))
    blob = json.dumps(d).lower()
    assert 'averag' in blob or 'mean' in blob, (
        'pair design does not state that distance is averaged over the '
        'reference set')
    assert 'theta' in blob or 'threshold' in blob


@needs_pairs
def test_pair_design_documents_how_human_judgments_map_to_the_pipeline():
    """The task requires this mapping be documented explicitly."""
    d = C.read_json(os.path.join(C.VALIDATION_ROOT, 'PAIR_DESIGN.json'))
    keys = json.dumps(d).lower()
    assert 'human' in keys, 'pair design does not discuss human judgments'
    assert 'reference' in keys


# ── the artifact that actually faces annotators ────────────────────────────
def _packets():
    if not os.path.isdir(TEMPLATES):
        return {}
    return {f: os.path.join(TEMPLATES, f) for f in sorted(os.listdir(TEMPLATES))
            if f.endswith('.jsonl')}


@needs_pairs
def test_packets_are_blind():
    """THE blinding test: the annotator-facing packets must carry no
    prediction, no LEP name, and no benign/perturbed role."""
    pk = _packets()
    if not pk:
        pytest.skip('packets not generated')
    forbidden = ('union_v6', 'union_frozen', 'v6_per_dimension',
                 'frozen_per_dimension', 'm1_v6', 'm1_frozen', 'm4_matched',
                 'LEP_', 'MEMORY_POISONING', 'TOOL_RESULT_CORRUPTION',
                 'INDIRECT_PROMPT_INJECTION', 'HANDOFF_CORRUPTION',
                 'INPUT_DISREGARD', 'benign', 'divergent')
    leaks = []
    for name, path in pk.items():
        blob = json.dumps(C.read_jsonl(path))
        for tok in forbidden:
            if tok in blob:
                leaks.append((name, tok, blob.count(tok)))
    assert not leaks, f'annotation packets leak: {leaks[:6]}'


@needs_pairs
def test_packets_use_opaque_tokens_not_real_ids():
    pk = _packets()
    if not pk:
        pytest.skip('packets not generated')
    for name, path in pk.items():
        blob = json.dumps(C.read_jsonl(path))
        for tok in ('code_review_swe_', 'fin_finqa_', 'res_scifact_'):
            assert tok not in blob, f'{name} exposes real fixture ids'


@needs_pairs
def test_pair_packets_hide_the_target_role():
    """Every pair group must be symmetric: the annotator is not told which
    member is the perturbed target.

    ``agent_role`` (analyst/researcher/verifier) legitimately appears — it is
    part of the observable behaviour.  What must NOT appear is any statement
    of which member is the target or which is a reference.
    """
    path = os.path.join(TEMPLATES, 'pair_packet_annotator_A.jsonl')
    if not os.path.isfile(path):
        pytest.skip('pair packet not generated')
    for item in C.read_jsonl(path):
        blob = json.dumps(item).lower()
        for tok in ('is_target', '"role": "target"', '"role":"target"',
                    '"role": "reference"', 'target_member',
                    'benign_reference_set', '"role":'):
            assert tok not in blob, (
                f'pair packet exposes the member role via {tok!r}')
        members = item.get('members') or []
        assert len(members) >= 2, 'a pair group needs at least two members'
        assert [m.get('member_index') for m in members] == list(
            range(len(members))), 'member indices are not a clean 0..n-1 run'


@needs_pairs
def test_the_target_is_not_always_the_first_member():
    """If the target were always member 0, the shuffle would be decorative
    and an annotator could infer the role from position."""
    path = os.path.join(TEMPLATES, 'pair_packet_annotator_A.jsonl')
    roles_path = os.path.join(C.CACHE_DIR, 'blind_pair_roles.json')
    if not (os.path.isfile(path) and os.path.isfile(roles_path)):
        pytest.skip('pair packet or role key not generated')
    roles = C.read_json(roles_path)['roles']['A']
    positions = []
    for item in C.read_jsonl(path):
        for m in item['members']:
            r = roles.get(m['item_token'], {}).get('role')
            if r == 'target':
                positions.append(m['member_index'])
    assert positions, 'no target member identified in the role key'
    assert len(set(positions)) > 1, (
        'the target is always at the same member_index; the group order is '
        'not actually randomised')


@needs_pairs
def test_annotation_packets_are_order_randomised_per_annotator():
    """Two annotators must not see the same presentation order, or a shared
    positional heuristic would produce spurious agreement.

    Order is compared by TARGET IDENTITY, not by token: each annotator gets
    independently keyed pseudonyms, so token strings differ by design and
    comparing them would test nothing.
    """
    a = os.path.join(TEMPLATES, 'extraction_packet_annotator_A.jsonl')
    b = os.path.join(TEMPLATES, 'extraction_packet_annotator_B.jsonl')
    bm = os.path.join(C.CACHE_DIR, 'blind_id_map.json')
    if not (os.path.isfile(a) and os.path.isfile(b) and os.path.isfile(bm)):
        pytest.skip('packets or key file not generated')
    maps = C.read_json(bm)['maps']
    # the key file is real_id -> token; invert it for token -> real_id
    inv = {w: {t: r for r, t in maps[w].items()} for w in ('A', 'B')}
    oa = [inv['A'][r['item_token']] for r in C.read_jsonl(a)]
    ob = [inv['B'][r['item_token']] for r in C.read_jsonl(b)]
    assert oa != ob, 'both annotators received the identical presentation order'
    assert set(oa) == set(ob), 'the two packets cover different real units'


@needs_pairs
def test_both_annotators_receive_the_same_item_set():
    """Both annotators judge the SAME executions (under different tokens), or
    there would be nothing to agree about."""
    a = os.path.join(TEMPLATES, 'extraction_packet_annotator_A.jsonl')
    b = os.path.join(TEMPLATES, 'extraction_packet_annotator_B.jsonl')
    bm = os.path.join(C.CACHE_DIR, 'blind_id_map.json')
    if not (os.path.isfile(a) and os.path.isfile(b) and os.path.isfile(bm)):
        pytest.skip('packets or key file not generated')
    maps = C.read_json(bm)['maps']
    inv = {w: {t: r for r, t in maps[w].items()} for w in ('A', 'B')}
    ua = {inv['A'][r['item_token']] for r in C.read_jsonl(a)}
    ub = {inv['B'][r['item_token']] for r in C.read_jsonl(b)}
    assert ua == ub, (
        f'{len(ua ^ ub)} real units are not shared by the two annotators')


@needs_pairs
def test_blind_id_map_is_complete_and_confidential():
    """An empty key file would make the annotations unjoinable, which would
    only surface at Phase C — after both annotators' work was finished."""
    p = os.path.join(C.CACHE_DIR, 'blind_id_map.json')
    if not os.path.isfile(p):
        pytest.skip('blind map not generated')
    d = C.read_json(p)
    maps = d['maps']
    for who in ('A', 'B'):
        assert maps.get(who), (
            f'the key file is EMPTY for annotator {who}: no annotation could '
            f'ever be joined back to its execution')
    note = (d.get('note') or '').lower()
    assert 'confidential' in note or 'never' in note, (
        'the token->id key file does not warn that it must not ship with the '
        'packet')
    # every packet token must be resolvable (key file is real_id -> token)
    a = os.path.join(TEMPLATES, 'extraction_packet_annotator_A.jsonl')
    if os.path.isfile(a):
        tokens = set(maps['A'].values())
        for r in C.read_jsonl(a):
            assert r['item_token'] in tokens, (
                f'packet token {r["item_token"]} is not in the key file, so '
                f'this annotation could never be joined back')
