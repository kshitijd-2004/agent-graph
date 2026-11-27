#!/usr/bin/env python3.11
"""Fixture-cluster bootstrap CIs — vectorized numpy implementation.

Scheme (documented, fixed seed, pinned replication count):
  * unit of resampling = fixture (903 fixtures; shared benign references and
    repeated executions of a fixture are NOT independent, so fixtures are the
    cluster).
  * each of N_BOOT=4000 iterations: draw a fixture-multiset by sampling ALL
    fixtures in the group WITH REPLACEMENT (equivalently, C ~ Multinomial
    with equal probs); compute the statistic on that resample.
  * CI = 2.5 / 97.5 percentiles (linear interpolation) of the estimate
    sequence, matching numpy's 'linear' convention used elsewhere in this
    analysis.
  * seed SEED=20270301 (same pinned seed as the V4.4 ledger bootstrap);
    rng = numpy default_rng(SEED) per call (reproducible; independent across
    calls, so CIs for different statistics are independent draws).

Three exact variants (each matches its pure-Python reference on the same
resamples — see tests/test_boot.py):
  * ci_pool_median(values_by_fix)      statistic = median of the POOLED
                                       non-None numeric values of resampled
                                       fixtures (None = missing, never
                                       zero-filled).
  * ci_pool_rate(flags_by_fix)         statistic = mean of pooled 0/1 flags
                                       (rate). Undefined fixtures (no flags)
                                       contribute nothing.
  * ci_perfix_mean_mean(stat_by_fix)   statistic = mean over resampled
                                       fixtures of a precomputed per-fixture
                                       statistic (weighted by pick count);
                                       undefined fixtures excluded from both
                                       numerator and denominator.

Missing-value semantics are identical to the reference implementation:
missing observations are excluded, never imputed.
"""
from __future__ import annotations

import math

import numpy as np

SEED = 20270301
N_BOOT = 4000


def _ci_from_est(est: np.ndarray):
    """2.5/97.5 percentiles (linear) of the estimate vector; None if empty."""
    est = est[np.isfinite(est)]
    if est.size == 0:
        return None
    lo = float(np.percentile(est, 2.5))
    hi = float(np.percentile(est, 97.5))
    return [lo, hi]


def ci_pool_median(values_by_fix, n_boot: int = N_BOOT, seed: int = SEED):
    """Median-of-pooled-non-None CI (fixture-cluster bootstrap).

    Resamples all fixtures with replacement (Multinomial draws). The pooled
    median of the resample is computed via a searchsorted rank lookup:
    median = grid[r] where r is the rank of the (T-1)/2-th position in the
    pooled multiset, with linear interpolation between adjacent grid values.
    Equivalent (to float precision) to the pure-Python reference scheme.
    """
    # Resampling space = ALL fixtures with a NON-EMPTY list (the reference
    # ci_fixture resampled `if v`, i.e. len(list) > 0 — a fixture whose values
    # are all None still resamples, it just contributes nothing). This differs
    # from a "valid values" space and MUST be preserved to match the reference.
    fixes = []
    arrs = []
    for f, v in values_by_fix.items():
        if not v:
            continue
        fixes.append(f)
        vv = np.array([x for x in v if x is not None], dtype=float)
        arrs.append(np.sort(vv) if vv.size else np.array([], dtype=float))
    F = len(fixes)
    if F == 0:
        return None
    valid = [i for i, a in enumerate(arrs) if a.size]
    if not valid:
        return None
    grid = np.unique(np.concatenate([arrs[i] for i in valid]))
    G = grid.size
    # counts_le[j, i] = # of values in arrs[i] <= grid[j]  (0 for empty fixtures)
    # side='right' -> count of elements <= grid[j]
    counts_le = np.zeros((G, F), dtype=np.int64)
    for i in valid:
        counts_le[:, i] = np.searchsorted(arrs[i], grid, side='right')
    rng = np.random.default_rng(seed)
    C = rng.multinomial(F, np.full(F, 1.0 / F), size=n_boot).astype(np.int64)  # B x F
    P = C @ counts_le.T                                                          # B x G, P[b, j] = pooled count <= grid[j]
    T = P[:, -1]                                                                 # B, total pooled non-None size
    pos = 0.5 * (T - 1)                                                          # median position (0-indexed)
    pos_lo = np.floor(pos).astype(np.int64)
    pos_hi = np.minimum(pos_lo + 1, T - 1)
    Q = P.T                                                                       # G x B
    # value at position p is grid[first j with P[b, j] > p] (P monotone in j)
    rank_lo = (Q > pos_lo[None, :]).argmax(axis=0)                               # B
    rank_hi = (Q > pos_hi[None, :]).argmax(axis=0)                               # B
    val_lo = grid[rank_lo]
    val_hi = grid[rank_hi]
    est = val_lo + (val_hi - val_lo) * (pos - pos_lo)
    est = np.where(T > 0, est, np.nan)
    return _ci_from_est(est)


def ci_pool_rate(flags_by_fix, n_boot: int = N_BOOT, seed: int = SEED):
    """Pooled 0/1 rate CI (fixture-cluster bootstrap)."""
    nz = []
    for f, v in flags_by_fix.items():
        vv = np.array([int(bool(x)) for x in v], dtype=np.int32)
        if vv.size:
            nz.append((f, vv))
    F = len(nz)
    if F == 0:
        return None
    zeros = np.array([vv.size - int(vv.sum()) for _, vv in nz], dtype=np.int32)
    ones = np.array([int(vv.sum()) for _, vv in nz], dtype=np.int32)
    Z = np.stack([zeros, ones])                                    # 2 x F
    rng = np.random.default_rng(seed)
    C = rng.multinomial(F, np.full(F, 1.0 / F), size=n_boot)
    P = C @ Z.T                                                     # B x 2
    T = P.sum(1)
    est = np.where(T > 0, P[:, 1] / np.where(T > 0, T, 1), np.nan)
    return _ci_from_est(est)


def ci_ratio(num_by_fix, den_by_fix, n_boot: int = N_BOOT, seed: int = SEED):
    """Bootstrap CI for a ratio of two pooled counts.

    num_by_fix / den_by_fix: {fixture: list of 0/1}. In each resample the
    fixture-multiset is drawn the same way as the other estimators, both
    counts are pooled, and the ratio is computed on THAT resample (the
    ratio-of-percentiles shortcut would be wrong). Undefined fixtures
    (empty lists) contribute nothing; resamples with denominator 0 are
    excluded from the estimate sequence.
    """
    fixes = list(num_by_fix)
    F = len(fixes)
    if F == 0:
        return None
    ncol = np.array([sum(num_by_fix[f]) for f in fixes], dtype=np.int32)
    dcol = np.array([sum(den_by_fix.get(f, [])) for f in fixes], dtype=np.int32)
    rng = np.random.default_rng(seed)
    C = rng.multinomial(F, np.full(F, 1.0 / F), size=n_boot).astype(np.int64)  # B x F
    P = C @ np.column_stack([ncol, dcol])                                        # B x 2 (num, den)
    num_p = P[:, 0]
    den = P[:, 1]
    est = np.where(den > 0, num_p / np.where(den > 0, den, 1), np.nan)
    return _ci_from_est(est)


def ci_perfix_mean(stat_by_fix, n_boot: int = N_BOOT, seed: int = SEED):
    """Per-fixture-statistic mean CI.

    stat_by_fix: {fixture: per-fixture statistic or None (undefined, e.g. a
    fixture with no evaluable executions in the group)}. A bootstrap resample
    draws ALL fixtures with replacement; undefined fixtures are excluded from
    both numerator and denominator (exact match of the reference scheme).
    """
    fixes = list(stat_by_fix)
    F = len(fixes)
    if F == 0:
        return None
    s = np.full(F, np.nan)
    for i, f in enumerate(fixes):
        v = stat_by_fix[f]
        if v is not None:
            s[i] = v
    rng = np.random.default_rng(seed)
    C = rng.multinomial(F, np.full(F, 1.0 / F), size=n_boot)       # B x F
    den = C @ np.where(np.isfinite(s), 1.0, 0.0)
    num = C @ np.where(np.isfinite(s), s, 0.0)
    est = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    return _ci_from_est(est)
