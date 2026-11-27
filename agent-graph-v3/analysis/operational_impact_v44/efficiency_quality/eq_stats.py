#!/usr/bin/env python3.11
"""Fixture-cluster bootstrap helpers for the efficiency/quality extension.

Complements ../boot.py (single-quantity pool estimators) with JOINT
estimators: the statistic is computed on the POOLED resample, so the
resampling covariance between the faster and slower arms is carried in
the CI (a ratio-of-percentiles shortcut would be wrong).

All estimators use the pinned V4.4 bootstrap protocol:
  * resampling unit = fixture; Multinomial(n_fixtures, uniform) draws
    (equivalently, every fixture resampled with replacement).
  * N_BOOT = 4000, SEED = 20270301 (same seed as the V4.4 ledger).
  * 95% CI = 2.5/97.5 percentiles (linear) of the estimate sequence.
Missing executions are never imputed; per-fixture counts carry only
what is present.
"""
from __future__ import annotations

import math

import numpy as np

SEED = 20270301
N_BOOT = 4000


def _ci(est: np.ndarray):
    est = est[np.isfinite(est)]
    if est.size == 0:
        return None
    return [float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))]


def _multinomial_draws(F, n_boot, seed=SEED):
    rng = np.random.default_rng(seed)
    return rng.multinomial(F, np.full(F, 1.0 / F), size=n_boot).astype(np.int64)


def ci_2proportion(diff_fn, fixes, num_by_fix, den_by_fix,
                   n_boot=N_BOOT, seed=SEED):
    """CI for a statistic computed on the POOLED resample of two counts.

    diff_fn(p_num, p_den) -> float: the joint estimator, applied to the
    pooled numerator/denominator counts of the resample. `fixes` is the
    ordered fixture list; num_by_fix/den_by_fix are arrays aligned to it.
    """
    F = len(fixes)
    if F == 0:
        return None
    num = np.asarray(num_by_fix, dtype=np.int64)
    den = np.asarray(den_by_fix, dtype=np.int64)
    C = _multinomial_draws(F, n_boot, seed)
    P = C @ np.stack([num, den])                 # B x 2
    ests = np.empty(P.shape[0])
    for b in range(P.shape[0]):
        p_num, p_den = P[b, 0], P[b, 1]
        try:
            v = diff_fn(p_num, p_den)
        except (ZeroDivisionError, FloatingPointError):
            v = np.nan
        ests[b] = v
    return _ci(ests)


# ── joint estimators for the faster-vs-slower comparison ──────────────────
def _ard(p_fdeg, p_fn, p_sdeg, p_sn):
    if p_fn == 0 or p_sn == 0:
        return np.nan
    return p_fdeg / p_fn - p_sdeg / p_sn


def ci_ard(fixes, fdeg, fn, sdeg, sn, n_boot=N_BOOT, seed=SEED):
    """CI for absolute risk difference P(deg|faster) - P(deg|slower).

    `fdeg,fn,sdeg,sn` are per-fixture integer counts (faster numerator/den,
    slower numerator/den), aligned to `fixes`."""
    F = len(fixes)
    if F == 0:
        return None
    A = np.stack([np.asarray(fdeg, np.int64), np.asarray(fn, np.int64),
                  np.asarray(sdeg, np.int64), np.asarray(sn, np.int64)])
    C = _multinomial_draws(F, n_boot, seed)
    ests = np.empty(C.shape[0])
    P = C @ A.T                                     # B x 4
    for b in range(C.shape[0]):
        fdeg_b, fn_b, sdeg_b, sn_b = P[b]
        if fn_b == 0 or sn_b == 0:
            ests[b] = np.nan
            continue
        ests[b] = fdeg_b / fn_b - sdeg_b / sn_b
    return _ci(ests)


def ci_rr(fixes, fdeg, fn, sdeg, sn, n_boot=N_BOOT, seed=SEED):
    """CI for relative risk P(deg|faster)/P(deg|slower)."""
    F = len(fixes)
    if F == 0:
        return None
    A = np.stack([np.asarray(fdeg, np.int64), np.asarray(fn, np.int64),
                  np.asarray(sdeg, np.int64), np.asarray(sn, np.int64)])
    C = _multinomial_draws(F, n_boot, seed)
    ests = np.empty(C.shape[0])
    P = C @ A.T
    for b in range(C.shape[0]):
        fdeg_b, fn_b, sdeg_b, sn_b = P[b]
        if fn_b == 0 or sn_b == 0 or sdeg_b == 0:
            ests[b] = np.nan
            continue
        ests[b] = (fdeg_b / fn_b) / (sdeg_b / sn_b)
    return _ci(ests)


def ci_odds_ratio(fixes, fdeg, fn, sdeg, sn, n_boot=N_BOOT, seed=SEED):
    """CI for the odds ratio (faster vs slower), joint resampling."""
    F = len(fixes)
    if F == 0:
        return None
    A = np.stack([np.asarray(fdeg, np.int64), np.asarray(fn, np.int64),
                  np.asarray(sdeg, np.int64), np.asarray(sn, np.int64)])
    C = _multinomial_draws(F, n_boot, seed)
    ests = np.empty(C.shape[0])
    P = C @ A.T
    for b in range(C.shape[0]):
        fdeg_b, fn_b, sdeg_b, sn_b = P[b]
        f_ok = fn_b - fdeg_b
        s_ok = sn_b - sdeg_b
        den = sdeg_b * f_ok
        if den == 0:
            ests[b] = np.nan
            continue
        ests[b] = (fdeg_b * s_ok) / den
    return _ci(ests)


def cat_rates(rows_by_fix, label_fn):
    """Return per-category pooled counts {cat: (n_total, n_degraded)}."""
    cats = {}
    for f, rows in rows_by_fix.items():
        for r in rows:
            c = label_fn(r)
            if c is None:
                continue
            n, d = cats.get(c, (0, 0))
            cats[c] = (n + 1, d + (1 if r['M3_matched_degraded_observed'] else 0))
    return cats
