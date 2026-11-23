"""Tests for sample and pair integrity.

These check the properties that would silently bias every downstream number:
that inclusion probabilities and design weights are consistent with the frame,
that a "representative" sample really is representative, that the two
components stay separable, and that every matched pair is a legal comparison
under the repository's own rules.
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

SAMPLE = os.path.join(C.VALIDATION_ROOT, 'sample_manifest.jsonl')
PAIRS = os.path.join(C.VALIDATION_ROOT, 'pair_manifest.jsonl')
FRAME = os.path.join(C.CACHE_DIR, 'frame_units.jsonl')

needs_sample = pytest.mark.skipif(not os.path.isfile(SAMPLE),
                                  reason='sample not built')
needs_pairs = pytest.mark.skipif(not os.path.isfile(PAIRS),
                                 reason='pairs not built')
needs_frame = pytest.mark.skipif(not os.path.isfile(FRAME),
                                 reason='frame not built')


@pytest.fixture(scope='module')
def sample():
    return C.read_jsonl(SAMPLE)


@pytest.fixture(scope='module')
def pairs():
    return C.read_jsonl(PAIRS)


# ── size and coverage ──────────────────────────────────────────────────────
@needs_sample
def test_sample_is_about_three_hundred_unique_units(sample):
    ids = [r['unit_id'] for r in sample]
    assert len(ids) == len(set(ids)), 'sample contains duplicate unit ids'
    assert 280 <= len(ids) <= 320, f'sample size {len(ids)} is off target 300'


@needs_sample
def test_all_three_task_families_are_covered(sample):
    fams = collections.Counter(r['task_family'] for r in sample)
    assert set(fams) == {'code_review', 'financial_analysis',
                         'research_synthesis'}, fams
    for f, n in fams.items():
        assert n >= 80, f'{f} only has {n} units'


@needs_sample
def test_all_three_topologies_are_covered(sample):
    tops = collections.Counter(r['topology'] for r in sample)
    assert set(tops) == {'review_loop', 'branch_and_verify',
                         'coordinator_workers'}, tops


@needs_sample
def test_all_four_leps_and_benign_are_covered(sample):
    leps = {r['lep_code'] for r in sample if r['kind'] == 'lep'}
    assert leps == {'TOOL_RESULT_CORRUPTION', 'INDIRECT_PROMPT_INJECTION',
                    'HANDOFF_CORRUPTION', 'INPUT_DISREGARD'}, leps
    assert any(r['kind'] == 'benign' for r in sample)


@needs_sample
def test_no_memory_poisoning_anywhere(sample):
    for r in sample:
        blob = json.dumps(r).upper()
        assert 'MEMORY_POISONING' not in blob
        assert 'MEMORY' not in (r.get('lep_code') or '')


# ── the two components stay separable ─────────────────────────────────────
@needs_sample
def test_representative_and_targeted_components_are_labelled(sample):
    comps = collections.Counter(r['component'] for r in sample)
    assert set(comps) == {'A_representative', 'B_targeted'}, comps
    both = set()
    for kind in ('lep', 'benign'):
        per = {r['component'] for r in sample if r['kind'] == kind}
        both |= per
    assert both == {'A_representative', 'B_targeted'}


@needs_sample
def test_representative_units_carry_a_positive_inclusion_probability(sample):
    rep = [r for r in sample if r['component'] == 'A_representative']
    assert rep
    for r in rep:
        p = r.get('inclusion_probability')
        assert p is not None, f'{r["unit_id"]} has no inclusion probability'
        assert 0 < p <= 1.0, f'{r["unit_id"]} has pi={p}'


@needs_sample
def test_design_weight_is_the_inverse_of_inclusion_probability(sample):
    """A weight that is not 1/pi would make the population estimate wrong."""
    for r in sample:
        p = r.get('inclusion_probability')
        w = r.get('design_weight')
        if p and w:
            assert w == pytest.approx(1.0 / p, rel=1e-9), (
                f'{r["unit_id"]}: weight {w} != 1/pi {1/p}')


@needs_sample
def test_targeted_units_are_not_marked_population_estimable(sample):
    """Error-enriched units are selected on the outcome, so weighting them as
    if they were random would bias the population estimate.

    They legitimately carry a WITHIN-BUCKET draw probability (kept so the
    bucket draw is auditable), so the test checks the semantic flag rather
    than the presence of a number — the flag is what a downstream estimator
    must consult.
    """
    for r in sample:
        if r['component'] == 'B_targeted':
            assert r.get('population_estimable') is False, (
                f'targeted unit {r["unit_id"]} is marked population-'
                f'estimable; pooling it would bias the estimate')
            assert r.get('weight_meaning') == 'within_targeted_bucket_only'

    for r in sample:
        if r['component'] == 'A_representative' and r['kind'] == 'lep':
            assert r.get('population_estimable') is True
            assert r.get('weight_meaning') == 'srs_within_stratum'


@needs_sample
def test_stratum_sizes_are_recorded_and_consistent(sample):
    """Every unit must know how big its stratum was and how many were
    drawn, or the inclusion probability cannot be audited."""
    for r in sample:
        n_stratum = r.get('n_in_stratum')
        n_drawn = r.get('n_drawn_from_stratum')
        if r['component'] != 'A_representative':
            continue
        assert isinstance(n_stratum, int) and n_stratum > 0
        assert isinstance(n_drawn, int) and 0 < n_drawn <= n_stratum
        # pi must equal n_drawn / n_stratum for a SRSWOR within stratum
        expected = n_drawn / n_stratum
        assert r['inclusion_probability'] == pytest.approx(expected, rel=1e-9), (
            f'{r["unit_id"]}: pi={r["inclusion_probability"]} but '
            f'{n_drawn}/{n_stratum}={expected}')


@needs_sample
@needs_frame
def test_every_sampled_LEP_unit_is_in_the_frame(sample):
    """The frame is LEP-only: benign executions appear solely as reference
    sets, never as their own frame rows.  So only LEP units are checkable."""
    frame_ids = {r['unit_id'] for r in C.read_jsonl(FRAME)}
    lep = [r for r in sample if r['kind'] == 'lep']
    missing = [r['unit_id'] for r in lep if r['unit_id'] not in frame_ids]
    assert not missing, f'{len(missing)} sampled LEP units are not in the frame'


@needs_sample
@needs_frame
def test_benign_units_appear_in_some_frame_reference_set(sample):
    """A benign unit that no LEP execution referenced would be an invalid
    comparison partner, so it must be reachable from the frame.

    Sampled benign units carry a ``#benign`` suffix on ``unit_id`` to keep
    them distinct from LEP units of the same trace; the frame's reference
    sets use the bare trace_id.  Matching is therefore on ``trace_id``.
    """
    referenced = set()
    for r in C.read_jsonl(FRAME):
        for ref in (r.get('reference_set') or []):
            referenced.add(ref)
    ben = [r for r in sample if r['kind'] == 'benign']
    assert ben, 'no benign units sampled'
    missing = [r['unit_id'] for r in ben if r['trace_id'] not in referenced]
    assert not missing, (
        f'{len(missing)} sampled benign units are not referenced by any frame '
        f'row, so they cannot serve as a comparison reference: {missing[:3]}')
    # the suffix convention itself must hold, or pair matching breaks
    for r in ben:
        assert r['unit_id'].endswith('#benign'), (
            f'benign unit {r["unit_id"]} lacks the #benign convention')


@needs_sample
def test_every_sampled_unit_points_at_a_real_trace(sample):
    for r in sample:
        p = r.get('source_path')
        assert p, f'{r["unit_id"]} has no source path'
        assert os.path.isfile(p), f'missing trace file: {p}'


@needs_sample
def test_sample_manifest_does_not_embed_trace_contents(sample):
    """The manifest must reference traces, not duplicate them."""
    for r in sample:
        blob = json.dumps(r)
        assert 'final_response' not in blob
        assert 'completed_steps' not in blob
        assert len(blob) < 4000, f'{r["unit_id"]} record is suspiciously large'


# ── representativeness ────────────────────────────────────────────────────
@needs_sample
@needs_frame
def test_representative_sample_tracks_the_population_positive_rate(sample):
    """The whole point of component A is that it is not biased.  The frame is
    LEP-only (benign executions appear only as reference sets), so the
    comparable population rate is over all frame rows."""
    frame = {r['unit_id']: r for r in C.read_jsonl(FRAME)}
    lep = [r for r in sample
           if r['kind'] == 'lep' and r['component'] == 'A_representative']
    vals = [(frame.get(r['unit_id']) or {}).get('m1_frozen') for r in lep]
    known = [v for v in vals if v is not None]
    assert known, 'no representative LEP unit carries an M1 verdict'
    rate = sum(1 for v in known if v) / len(known)
    pop = C.read_jsonl(FRAME)
    pop_rate = sum(1 for r in pop if r.get('m1_frozen')) / len(pop)
    # An exact match is not expected at n=108; a gross deviation is.
    assert abs(rate - pop_rate) < 0.20, (
        f'representative M1-positive rate {rate:.3f} is far from the '
        f'population rate {pop_rate:.3f}: component A is not representative')


@needs_sample
@needs_frame
def test_population_m1_rate_matches_the_recorded_historical_figure():
    """Verify the 24.7 % baseline against the artifacts rather than trusting
    the task description."""
    frame = C.read_jsonl(FRAME)
    pos = sum(1 for r in frame if r.get('m1_frozen'))
    assert len(frame) == 16773, f'frame is {len(frame)} rows, not 16,773'
    assert pos == 4150, f'frozen M1 positives are {pos}, not 4,150'
    assert pos / len(frame) == pytest.approx(0.2474, abs=1e-3)
    v6 = sum(1 for r in frame if r.get('m1_v6'))
    assert v6 == 5037, f'v6 M1 positives are {v6}, not 5,037'


# ── pairs ─────────────────────────────────────────────────────────────────
@needs_pairs
def test_pairs_reuse_traces_from_the_sample_where_possible(pairs, sample):
    """Reuse is preferred; a pair whose members lie outside the sample is
    allowed but must be disclosed rather than silent."""
    in_sample = {r['unit_id'] for r in sample}
    reused = sum(1 for p in pairs if p.get('unit_id') in in_sample)
    assert reused > 0.3 * len(pairs), (
        f'only {reused}/{len(pairs)} pair targets reuse a sampled trace')
    for p in pairs:
        assert 'in_sample_300' in p, (
            f'{p["pair_id"]} does not disclose whether it is inside the '
            f'300-trace sample')
        assert p['in_sample_300'] == (p['unit_id'] in in_sample)


# ── reproducibility ───────────────────────────────────────────────────────
@needs_sample
def test_sample_records_its_seed_and_design(sample):
    p = os.path.join(C.VALIDATION_ROOT, 'SAMPLING_DESIGN.json')
    assert os.path.isfile(p), 'no SAMPLING_DESIGN.json recorded'
    d = C.read_json(p)
    assert 'reproducibility' in d
    seed = d['reproducibility'].get('seed')
    assert isinstance(seed, int), f'no integer seed recorded: {seed}'
    assert d['reproducibility'].get('reproducible_command')


@needs_sample
def test_design_documents_the_weight_semantics_of_both_components():
    """The A/B weight distinction is the difference between an unbiased
    population estimate and a silently biased one."""
    d = C.read_json(os.path.join(C.VALIDATION_ROOT, 'SAMPLING_DESIGN.json'))
    ws = d.get('weight_semantics') or {}
    assert 'A_representative' in ws and 'B_targeted' in ws
    assert 'NOT population' in ws['B_targeted'], (
        'the design does not warn that component B is not population-'
        'estimable')


def test_no_component_B_unit_is_marked_population_estimable(sample):
    for r in sample:
        if r['component'] == 'B_targeted':
            assert r.get('population_estimable') is not True, (
                f'targeted unit {r["unit_id"]} claims to be population-'
                f'estimable; pooling it would bias the estimate')


# ── reproducibility: seed derivation must not depend on the process hash ────
def test_mix_seed_is_stable_and_distinct():
    """RNG seeds derived from stratum/class keys must be identical across
    processes regardless of PYTHONHASHSEED — a regression here silently broke
    the whole sample (two 'seeded' runs drew nearly disjoint sets)."""
    import common as C
    assert C.mix_seed(1, 1000, 'financial_analysis|M1_positive') == \
        C.mix_seed(1, 1000, 'financial_analysis|M1_positive')
    assert C.mix_seed(1, 1000, 'a') != C.mix_seed(1, 1000, 'b')
    assert C.mix_seed(1, 1000, 'x') != C.mix_seed(2, 1000, 'x')
    # and it must not call the built-in hash() (process-randomized)
    import inspect
    src = inspect.getsource(C.mix_seed)
    assert 'hashlib' in src and '\n    hash(' not in src


def test_seed_derivation_does_not_call_builtin_hash():
    """Built-in ``hash()`` is randomized per process (PYTHONHASHSEED), so any
    sampling code that folds it into an RNG seed is non-reproducible.  This
    pins the exact regression that produced two disjoint 'seeded' samples."""
    import re
    import inspect
    import sample_validation
    import build_pairs
    pat = re.compile(r'(?<![\w.])hash\(')
    for mod in (sample_validation, build_pairs):
        src = inspect.getsource(mod)
        hits = [m.start() for m in pat.finditer(src)]
        assert not hits, (
            f'{mod.__name__} derives an RNG seed from the built-in hash(), '
            f'which is process-randomized; use C.mix_seed instead')
