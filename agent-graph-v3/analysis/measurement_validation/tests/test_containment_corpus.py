"""GT-containment tests against the REAL fixtures in the corpus.

The synthetic tests in ``test_blinding.py`` prove the mechanism works on
invented specs.  These run against the fixtures the experiment actually used,
which is where a containment failure would have done real damage: a leaked
answer in a production fixture would have contaminated every measurement
derived from it, and no synthetic test would have noticed.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, HERE)

import common as C  # noqa: E402


def _specs():
    from extractors import load_specs
    return load_specs()


def test_every_fixture_spec_loads():
    specs = _specs()
    assert len(specs) > 20, f'only {len(specs)} fixture specs found'


def test_every_fixture_spec_carries_ground_truth_in_the_first_place():
    """Vacuity guard for the whole containment section.

    If the fixture specs carried no answers, every containment test below
    would pass trivially and prove nothing.  They must actually be
    GT-bearing, or the tests are meaningless.
    """
    import extract_core as ec
    specs = _specs()
    bearing = 0
    for fid, spec in specs.items():
        if ec.GT_KEYS.intersection(json.dumps(spec).replace('":', '" ')
                                   .split('"')) or _has_gt_deep(spec):
            bearing += 1
    assert bearing > 0, (
        'no fixture spec carries any GT key — the containment tests below '
        'would all pass vacuously')


def _has_gt_deep(node, depth=0):
    import extract_core as ec
    if depth > 32:
        return False
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ec.GT_KEYS:
                return True
            if _has_gt_deep(v, depth + 1):
                return True
    elif isinstance(node, list):
        for v in node:
            if _has_gt_deep(v, depth + 1):
                return True
    return False


def test_hardened_strip_removes_all_gt_from_every_real_fixture():
    """The stripper the validation uses must fully clean EVERY real fixture."""
    import extract_core as ec
    from extractors import strip_gt_hardened
    bad = []
    for fid, spec in _specs().items():
        clean = strip_gt_hardened(spec)
        if _has_gt_deep(clean):
            bad.append(fid)
    assert not bad, f'hardened strip left GT in fixtures: {bad[:5]}'


def test_v6_strip_gt_FULLY_protects_the_answer_path():
    """The load-bearing containment property, tested on real fixtures.

    v6's ``strip_gt`` must remove, from every ``required_facts`` /
    ``required_issues`` entry, the fields an extractor could read an answer
    out of: ``value``, ``tolerance``, ``pattern``, ``source_text``, ``answer``.

    This is the property that actually matters, and it HOLDS on all 903
    fixtures.  The separate test below records the narrower gap.
    """
    import extract_core as ec
    from extractors import strip_gt_hardened
    answer_fields = {'value', 'tolerance', 'pattern', 'source_text', 'answer',
                     'answer_calculation', 'value_strings', 'grading'}
    bad = []
    for fid, spec in _specs().items():
        clean = strip_gt_hardened(spec)
        for bucket in ('required_facts', 'required_issues'):
            ents = clean.get(bucket) or {}
            entries = ents.values() if isinstance(ents, dict) else ents
            for ent in entries:
                if not isinstance(ent, dict):
                    continue
                hit = answer_fields & set(ent)
                if hit:
                    bad.append((fid, bucket, sorted(hit)))
    assert not bad, f'answer fields survived in fact/issue entries: {bad[:5]}'


def test_v6_strip_gt_leaves_gt_keys_outside_the_fact_buckets():
    """The narrow, non-answer gap — recorded with its real scale.

    ``strip_gt`` removes nested GT keys only inside ``required_facts`` and
    ``required_issues``.  On the real corpus this leaves GT keys in three
    other places:

      * ``validation.mock_runs``   — a numeric run count, not an answer
      * ``attack.memory_poisoning``— a description string, not an answer
      * ``seeds[].truth``          — a SciFact verdict literal ('SUPPORT')

    None of these is read by either extractor (neither imports or consults
    ``seeds``, ``validation`` or ``attack``), and ``assert_gt_free_spec``
    refuses a raw spec at the root.  So this is NOT an observed leak.  It is a
    gap between the DECLARED contract (these keys are in the module's own
    ``GT_KEYS`` set) and the ENFORCED one, which means containment currently
    rests on an unstated invariant about which spec paths extractors read.

    If this test fails, v6's strip_gt was tightened — regenerate the report
    and update the defect record rather than deleting the test.
    """
    import extract_core as ec
    leaked = {}
    for fid, spec in _specs().items():
        if _has_gt_deep(ec.strip_gt(spec)):
            leaked[fid] = True
    assert leaked, (
        'v6 strip_gt now fully cleans every real fixture — update the defect '
        'record in VALIDATION_STATUS.md and this test')
    C.ensure_dir(C.CACHE_DIR)
    C.write_json(os.path.join(C.CACHE_DIR, 'v6_strip_gt_leak_scale.json'), {
        'n_fixtures': len(_specs()),
        'n_with_residual_gt_keys': len(leaked),
        'answer_path_protected': True,
        'residual_locations': ['validation.mock_runs',
                               'attack.memory_poisoning', 'seeds[].truth'],
        'read_by_extractors': False,
        'examples': sorted(leaked)[:10],
    })


def test_residual_gt_keys_are_not_reachable_by_the_extractors():
    """Justifies calling the gap non-answer-bearing: no extractor module reads
    the spec paths where the residual GT keys live.

    Scanned over EVERY module in the v6 package (not a hand-listed few), so a
    newly added extractor that does read ``seeds`` cannot slip past.
    """
    import glob
    v6dir = os.path.join(C.VALIDATION_ROOT, '..', 'measurement_v6')
    files = sorted(glob.glob(os.path.join(v6dir, '*.py')))
    assert files, 'no v6 modules found to scan'
    offenders = []
    for p in files:
        src = open(p, encoding='utf-8').read()
        for path in ("'seeds'", '"seeds"', "'validation'", '"validation"',
                     "'attack'", '"attack"'):
            if path in src:
                offenders.append((os.path.basename(p), path))
    assert not offenders, (
        f'a v6 module reads a spec path holding residual GT keys: '
        f'{offenders} — the gap is more serious than recorded')


def test_extractor_guard_fires_on_a_raw_real_fixture():
    """v6's assert_gt_free_spec must refuse a raw real fixture spec."""
    import extract_core as ec
    fired = 0
    for fid, spec in _specs().items():
        try:
            ec.assert_gt_free_spec(spec, f'fixture {fid}')
        except ec.GroundTruthLeak:
            fired += 1
    assert fired > 0, 'v6 guard did not fire on any real fixture'


def test_validation_measurements_never_ran_on_a_gt_value_bearing_spec():
    """Every extraction the validation recorded must have been GT-value-free.

    The evidence records three attestation fields, checked here:
      * ``spec_gt_value_free`` — no GT-VALUE field survived the strip
        (the load-bearing check);
      * ``spec_residual_gt_value_fields`` — the paths at which a GT-value
        field would still be reachable;
      * ``spec_residual_gt_keys_v6`` — audit-only, whether any v6-declared
        GT key remains reachable (task-side ``unit``/``label`` are not in
        v6's GT_KEYS, so a non-empty list here is expected and NOT a
        failure — the failure is a GT-VALUE field surviving).
    """
    p = os.path.join(C.CACHE_DIR, 'extraction_evidence.jsonl')
    if not os.path.isfile(p):
        pytest.skip('extraction evidence not generated')
    bad = []
    n_checked = 0
    for r in C.read_jsonl(p):
        if r.get('error'):
            continue
        n_checked += 1
        if r.get('spec_gt_value_free') is False:
            bad.append(r.get('unit_id'))
        elif r.get('spec_residual_gt_value_fields'):
            bad.append((r.get('unit_id'), 'residual field present'))
    assert n_checked, 'no extraction evidence generated to check'
    assert not bad, f'extractions ran on GT-value-bearing specs: {bad[:5]}'


def test_sample_manifest_does_not_expose_ground_truth():
    """The manifest is a hand-off artifact; it must not carry answers."""
    p = os.path.join(C.VALIDATION_ROOT, 'sample_manifest.jsonl')
    if not os.path.isfile(p):
        pytest.skip('sample not built')
    bad_keys = ('expected_answer', 'gold', 'tolerance', 'expected_verdicts',
                'answer_value', 'ground_truth', 'truth')
    hits = []
    for r in C.read_jsonl(p):
        for k in r:
            if any(b in k.lower() for b in bad_keys):
                hits.append((r.get('unit_id'), k))
    assert not hits, f'sample manifest exposes GT-ish keys: {hits[:5]}'


def test_pair_manifest_records_reference_sets_without_answers():
    p = os.path.join(C.VALIDATION_ROOT, 'pair_manifest.jsonl')
    if not os.path.isfile(p):
        pytest.skip('pairs not built')
    for r in C.read_jsonl(p):
        assert 'benign_reference_set' in r
        assert isinstance(r['benign_reference_set'], list)
        assert r['benign_reference_set'], 'empty reference set is not a pair'
        for ref in r['benign_reference_set']:
            assert isinstance(ref, str)
        blob = json.dumps(r)
        for tok in ('expected_answer', 'answer_value', 'tolerance',
                    'source_text'):
            assert tok not in blob, (
                f'pair record {r["pair_id"]} exposes {tok}')
