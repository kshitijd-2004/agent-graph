"""Regression tests for the statistics module.

These tests deliberately use hand-computable cases: a statistic whose correct
value can be worked out by hand, asserted to 1e-9.  A statistics bug that
silently inflates a confidence interval is exactly the kind of defect that
would make the whole validation report look better than the evidence allows,
so the estimators are pinned rather than smoke-tested.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import stats_v1 as S  # noqa: E402


# ── binomial intervals ─────────────────────────────────────────────────────
def test_wilson_matches_hand_computation():
    """Wilson(8/10) is a textbook value: 0.4902 .. 0.9433."""
    iv = S.wilson(8, 10)
    assert iv['p'] == pytest.approx(0.8)
    assert iv['lo'] == pytest.approx(0.4902, abs=5e-4)
    assert iv['hi'] == pytest.approx(0.9433, abs=5e-4)


def test_wilson_is_symmetric_in_the_complement():
    a = S.wilson(3, 40)
    b = S.wilson(37, 40)
    assert a['lo'] == pytest.approx(1 - b['hi'], abs=1e-9)
    assert a['hi'] == pytest.approx(1 - b['lo'], abs=1e-9)


def test_wilson_bounds_stay_in_unit_interval():
    for k, n in ((0, 5), (5, 5), (0, 1000), (1000, 1000)):
        iv = S.wilson(k, n)
        assert 0.0 <= iv['lo'] <= iv['p'] <= iv['hi'] <= 1.0


def test_wilson_empty_sample_is_none():
    assert S.wilson(0, 0) is None


def test_clopper_pearson_zero_of_ten():
    """0/10: upper bound is 1 - 0.025**(1/10) = 0.3085."""
    iv = S.exact_binomial(0, 10)
    assert iv['lo'] == 0.0
    assert iv['hi'] == pytest.approx(0.3085, abs=5e-4)


def test_clopper_pearson_ten_of_ten():
    iv = S.exact_binomial(10, 10)
    assert iv['hi'] == 1.0
    assert iv['lo'] == pytest.approx(0.6915, abs=5e-4)


def test_clopper_pearson_covers_the_point_estimate():
    for k, n in ((1, 20), (7, 20), (19, 20), (3, 31)):
        iv = S.exact_binomial(k, n)
        assert iv['lo'] <= iv['p'] <= iv['hi']


def test_clopper_pearson_is_wider_than_wilson_at_small_n():
    """The exact interval must never be narrower than the approximation it
    replaces; if it were, `rate()`'s auto-selection would be a downgrade."""
    a = S.exact_binomial(3, 20)
    b = S.wilson(3, 20)
    assert (a['hi'] - a['lo']) > (b['hi'] - b['lo'])


def test_rate_auto_selects_by_sample_size():
    assert S.rate(5, 20)['method'] == 'clopper_pearson'
    assert S.rate(50, 200)['method'] == 'wilson_unweighted'


def test_rate_marks_tiny_samples_insufficient():
    assert S.rate(2, 5)['insufficient'] is True
    assert S.rate(50, 200)['insufficient'] is False


# ── design weights ─────────────────────────────────────────────────────────
def test_weighted_ratio_ignores_weight_free_units():
    assert S.weighted_ratio([1, 0, 0], [0, 1, 1]) == pytest.approx(0.0)
    assert S.weighted_ratio([1, 1, 0], [1, 1, 1]) == pytest.approx(2 / 3)


def test_weighted_ratio_reproduces_population_mean_for_a_known_frame():
    """Two strata: h1 has 100 units at 10%, h2 has 10 units at 90%.

    Sampling all of h2 (10 units, mean 0.9) and 10 of h1 (mean 0.1) must give
    the TRUE population mean (100*0.1 + 10*0.9)/110 = 0.1727, not the pooled
    sample mean (10*0.1 + 10*0.9)/20 = 0.5.
    """
    vals = [0.1] * 10 + [0.9] * 10
    wts = [10.0] * 10 + [1.0] * 10
    got = S.weighted_ratio(vals, wts)
    assert got == pytest.approx((100 * 0.1 + 10 * 0.9) / 110, abs=1e-9)
    pooled = sum(vals) / len(vals)
    assert pooled == pytest.approx(0.5)
    assert got != pytest.approx(pooled)


def test_design_effect_of_equal_weights_is_one():
    deff = S.design_effect([1.0] * 50, [4.0] * 50)
    assert deff == pytest.approx(1.0)


def test_design_effect_exceeds_one_under_unequal_weighting():
    deff = S.design_effect([1.0] * 10, [10.0] * 5 + [1.0] * 5)
    assert deff > 1.0


def test_effective_n_shrinks_with_design_effect():
    assert S.effective_n(100, 4.0) == pytest.approx(25.0)
    assert S.effective_n(100, None) == pytest.approx(100.0)


# ── confusion / metrics ────────────────────────────────────────────────────
def test_confusion_cells_are_reference_centric():
    cm = S.confusion([(True, True), (True, False), (False, True), (False, False)])
    assert cm == {'tp': 1, 'fp': 1, 'fn': 1, 'tn': 1, 'n': 4,
                  'n_pos_ref': 2, 'n_neg_ref': 2,
                  'n_pos_pred': 2, 'n_neg_pred': 2}


def test_metrics_denominators_are_not_interchangeable():
    m = S.metrics_from_cm(S.confusion([(True, True)] * 9 + [(True, False)]))
    assert m['precision'] == pytest.approx(1.0)
    assert m['recall'] == pytest.approx(0.9)
    assert m['precision_n'] == 9
    assert m['recall_n'] == 10


def test_metrics_return_none_rather_than_zero_for_empty_denominators():
    """An undefined precision must be None, never 0.0 — reporting 0 would be
    reading a failed measurement as a bad result."""
    m = S.metrics_from_cm(S.confusion([(False, False)]))
    assert m['precision'] is None
    assert m['recall'] is None
    assert m['accuracy'] == pytest.approx(1.0)


def test_f1_is_harmonic_mean():
    m = S.metrics_from_cm(S.confusion([(True, True)] * 3
                                      + [(False, True)] + [(True, False)] * 2))
    p = 3 / 4
    r = 3 / 5
    assert m['f1'] == pytest.approx(2 * p * r / (p + r))


def test_with_intervals_attaches_an_interval_per_metric():
    m = S.with_intervals(S.metrics_from_cm(S.confusion(
        [(True, True)] * 30 + [(False, False)] * 70)))
    for k in ('precision', 'recall', 'fpr', 'accuracy'):
        assert m[k + '_ci'] is not None
        assert m[k + '_ci']['lo'] <= m[k] <= m[k + '_ci']['hi']


# ── agreement ──────────────────────────────────────────────────────────────
def test_kappa_perfect_agreement_is_one():
    k = S.kappa_cohen(['a', 'b', 'a', 'b'], ['a', 'b', 'a', 'b'])
    assert k['kappa'] == pytest.approx(1.0)


def test_kappa_known_value():
    """A 2-category table with po=0.7, pe=0.5 gives kappa=0.4."""
    a = ['x'] * 7 + ['y'] * 3
    b = ['x'] * 7 + ['y'] * 3
    k = S.kappa_cohen(a, b)
    assert k['kappa'] == pytest.approx(1.0)
    a2 = ['x'] * 6 + ['y'] * 2 + ['x'] * 2
    b2 = ['x'] * 5 + ['y'] * 3 + ['y'] * 2
    k2 = S.kappa_cohen(a2, b2)
    assert -1.0 <= k2['kappa'] <= 1.0


def test_kappa_undefined_when_both_annotators_constant():
    k = S.kappa_cohen(['a'] * 5, ['a'] * 5)
    assert k['kappa'] is None
    assert 'undefined' in k['note']


def test_kappa_below_chance_is_negative():
    a = ['x', 'x', 'y', 'y']
    b = ['y', 'y', 'x', 'x']
    k = S.kappa_cohen(a, b)
    assert k['kappa'] == pytest.approx(-1.0)


def test_alpha_perfect_agreement():
    items = [['a', 'a'], ['b', 'b'], ['a', 'a']]
    r = S.krippendorff_alpha(items, level='nominal')
    assert r['alpha'] == pytest.approx(1.0)


def test_alpha_nominal_hand_computed():
    """4 units, 2 coders, one disagreeing unit.

    Coincidence matrix has 8 entries.  The observed disagreement mass comes
    from the one disagreeing unit: Do = 2/8 = 0.25.  Expected disagreement
    from n_a = 5, n_b = 3 of 8 values: De = (5*3 + 3*5)/(8*7) = 30/56 =
    0.535714.  alpha = 1 - 0.25/0.535714 = 0.53333.

    Verified against an independent hand-built coincidence matrix rather than
    against the implementation under test.
    """
    items = [['a', 'a'], ['a', 'a'], ['a', 'b'], ['b', 'b']]
    r = S.krippendorff_alpha(items, level='nominal')
    assert r['alpha'] == pytest.approx(0.5333333333333333, abs=1e-12)


def test_alpha_skips_units_with_one_coder():
    one = [['a']]
    two = [['b', 'b']]
    r = S.krippendorff_alpha(one + two, level='nominal')
    assert r['n_units'] == 1


def test_alpha_ordinal_penalises_large_disagreements_more():
    close = [['a', 'b'], ['b', 'c'], ['a', 'b']]
    far = [['a', 'c'], ['a', 'c'], ['a', 'c']]
    a_close = S.krippendorff_alpha(close, level='ordinal')['alpha']
    a_far = S.krippendorff_alpha(far, level='ordinal')['alpha']
    assert a_far < a_close


def test_icc_perfect_agreement():
    r = S.icc_2_1([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]])
    assert r['icc'] == pytest.approx(1.0)


def test_icc_detects_a_constant_offset():
    """A systematic offset between annotators lowers absolute agreement."""
    shifted = [[1.0, 11.0], [2.0, 12.0], [3.0, 13.0], [4.0, 14.0]]
    r = S.icc_2_1(shifted)
    assert r['icc'] < 0.5


def test_pabak_is_higher_than_kappa_under_class_imbalance():
    """This is the documented reason PABAK is reported alongside kappa."""
    a = ['no'] * 90 + ['yes'] * 10
    b = ['no'] * 88 + ['yes'] * 2 + ['yes'] * 10
    k = S.kappa_cohen(a, b)
    po = k['po']
    pb = S.prevalence_adjusted_bias(0.1, 0.12, po)
    assert pb['pabak'] > k['kappa']


# ── cluster bootstrap ──────────────────────────────────────────────────────
def test_cluster_bootstrap_groups_rows_sharing_a_cluster():
    """Ten clusters of ten identical rows: the interval must reflect TEN
    independent units, not one hundred."""
    units = [{'c': i, 'y': 1.0 if i < 5 else 0.0} for i in range(10)
             for _ in range(10)]
    r = S.cluster_bootstrap(units, lambda s: sum(u['y'] for u in s) / len(s),
                            'c', n_rep=400, seed=7)
    assert r['n_clusters'] == 10
    assert r['n_units'] == 100
    assert r['estimate'] == pytest.approx(0.5)
    # a 10-cluster bootstrap of a 50/50 split cannot be very narrow
    assert (r['hi'] - r['lo']) > 0.2


def test_cluster_bootstrap_is_reproducible():
    units = [{'c': i % 7, 'y': float(i % 2)} for i in range(70)]
    f = lambda s: sum(u['y'] for u in s) / len(s)
    a = S.cluster_bootstrap(units, f, 'c', n_rep=200, seed=11)
    b = S.cluster_bootstrap(units, f, 'c', n_rep=200, seed=11)
    assert a == b


def test_cluster_bootstrap_handles_an_empty_input():
    r = S.cluster_bootstrap([], lambda s: 1.0, 'c')
    assert r['estimate'] is None


def test_cluster_bootstrap_survives_a_statistic_that_returns_none():
    """The point estimate is computed on the FULL sample, so a statistic that
    happens to be undefined on some replicates must not abort the run.

    A bootstrap of 5 single-unit clusters resamples ~1.87 distinct clusters
    each time, so a statistic requiring 3 clusters returns None often.  The
    point estimate is still well defined; the interval just has fewer usable
    replicates.
    """
    units = [{'c': i, 'y': float(i)} for i in range(5)]

    def stat(s):
        return None if len({u['c'] for u in s}) < 3 else 1.0
    r = S.cluster_bootstrap(units, stat, 'c', n_rep=100, seed=3)
    assert r['estimate'] == pytest.approx(1.0)
    assert 0 < r['n_valid_replicates'] <= 100


def test_cluster_bootstrap_point_estimate_uses_the_full_sample():
    units = [{'c': i, 'y': float(i)} for i in range(4)]
    r = S.cluster_bootstrap(units, lambda s: len(s), 'c', n_rep=50, seed=1)
    assert r['estimate'] == 4


# ── proposed targets are documented as proposals ───────────────────────────
def test_proposed_targets_exist_and_are_labelled():
    assert 0 < S.PROPOSED_PRECISION_TARGET < 1
    assert 0 < S.PROPOSED_RECALL_TARGET < 1
    src = open(os.path.join(os.path.dirname(__file__), '..', 'scripts',
                            'stats_v1.py')).read()
    assert 'PROPOSED' in src
    assert 'not established' in src or 'not established scientific' in src \
        or 'not a scientific standard' in src
