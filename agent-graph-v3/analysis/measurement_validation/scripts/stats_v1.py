"""Sampling-design-aware statistics for the BND measurement validation.

PURE PYTHON.  No numpy/scipy dependency (neither is available on this host),
so every estimator is implemented explicitly and is auditable.

WHY THIS MATTERS FOR THE SCIENCE
--------------------------------
The validation sample is a STRATIFIED sample with UNEQUAL inclusion
probabilities, and some strata are DELIBERATELY OVER-SAMPLED (the targeted
error-enriched component).  Two consequences make naive arithmetic wrong:

  1. **The pooled mean is not the population mean.**  If 40 % of the sample is
     drawn from a stratum holding 3 % of the population, the pooled rate is
     pulled toward that stratum.  Population estimates must use the sampling
     weights (design weights = 1/inclusion probability).

  2. **Rows are not independent.**  Several measurement rows share the same
     benign reference set and the same fixture.  Treating them as independent
     observations understates the variance.  All interval estimates here
     resample at the CLUSTER level.

ESTIMATORS IMPLEMENTED
----------------------
* ``weighted_ratio``         — Horvitz-Thompson ratio estimate (stratified)
* ``wilson``                 — binomial interval for a single unweighted rate
* ``wald_logit_cluster``     — cluster-bootstrap interval for a weighted ratio
* ``bootstrap_cluster``      — generic cluster bootstrap for any statistic
* ``kappa_cohen``            — chance-corrected agreement, 2 annotators
* ``krippendorff_alpha``     — chance-corrected agreement, ordinal/nominal
* ``icc_2_1``                — absolute-agreement ICC for numeric labels
* ``episode_survival``       — not implemented here; see reports

DESIGN DECISIONS STATED EXPLICITLY
----------------------------------
* Intervals are 95 % and are reported with their method name.  A Wilson
  interval is NOT adjusted for the design (it assumes a simple random
  sample); it is used only for unweighted, single-stratum descriptives and is
  always labelled as such.
* Where a weighted estimate has a design effect that makes the effective
  sample size too small, the result is returned with ``insufficient=True``
  rather than reported as if it were precise.  The threshold is a documented
  engineering choice (``MIN_EFFECTIVE_N``), not a scientific standard.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Engineering thresholds.  These are PROPOSED targets, not established
# scientific standards, and every report that uses them says so.
# ---------------------------------------------------------------------------
MIN_EFFECTIVE_N = 10.0       # below this, an estimate is marked insufficient
Z95 = 1.959963984540054      # two-sided 95 % normal quantile

#: PROPOSED engineering acceptance targets.  Explicitly labelled as proposals
#: in every report; the validation's primary output is the empirical estimate,
#: not a pass/fail against these numbers.
PROPOSED_PRECISION_TARGET = 0.95
PROPOSED_RECALL_TARGET = 0.90


# ---------------------------------------------------------------------------
# Element-level binomial interval
# ---------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = Z95) -> Optional[Dict[str, float]]:
    """Wilson score interval for a binomial proportion.

    Assumes independent observations.  Use only where rows genuinely are
    independent (a single stratum, one row per cluster); otherwise the
    cluster-bootstrap interval is the correct one.
    """
    if n <= 0:
        return None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den
    # Clamp into [0, 1] AND ensure the interval contains its own point
    # estimate.  At k=0 or k=n the unclamped centre sits strictly inside
    # (0, 1), so a naive clamp leaves the bound a fraction of an ulp PAST p
    # and `lo <= p <= hi` can fail — which would make a downstream assertion
    # or a report table show a rate outside its own confidence interval.
    lo = min(max(0.0, centre - half), p)
    hi = max(min(1.0, centre + half), p)
    return {'p': p, 'lo': lo, 'hi': hi,
            'n': n, 'k': k, 'method': 'wilson_unweighted'}


def exact_binomial(k: int, n: int) -> Optional[Dict[str, float]]:
    """Clopper-Pearson interval via the incomplete beta (pure-python bisection).

    Used where ``n`` is small enough that the Wilson approximation is not
    trustworthy (n < 30).  Implemented by inverting the binomial tail with a
    bisection on the Beta CDF, which is itself computed by a continued
    fraction — no external library.
    """
    if n <= 0:
        return None
    if k == 0:
        return {'p': 0.0, 'lo': 0.0, 'hi': 1 - 0.025 ** (1.0 / n), 'n': n,
                'k': k, 'method': 'clopper_pearson'}
    if k == n:
        return {'p': 1.0, 'lo': 0.025 ** (1.0 / n), 'hi': 1.0, 'n': n,
                'k': k, 'method': 'clopper_pearson'}

    def betainc(a: float, b: float, x: float) -> float:
        """Regularised incomplete beta I_x(a,b) — Lentz continued fraction."""
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                 + a * math.log(x) + b * math.log1p(-x))
        front = math.exp(lbeta) / a
        if x < (a + 1.0) / (a + b + 2.0):
            return front * _beta_cf(a, b, x)
        return 1.0 - (math.exp(lbeta) / b) * _beta_cf(b, a, 1.0 - x)

    def solve(target: float, a: float, b: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if betainc(a, b, mid) < target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    alpha = 0.05
    lo = solve(alpha / 2, k, n - k + 1)
    hi = solve(1 - alpha / 2, k + 1, n - k)
    return {'p': k / n, 'lo': lo, 'hi': hi, 'n': n, 'k': k,
            'method': 'clopper_pearson'}


def _beta_cf(a: float, b: float, x: float, itmax: int = 300,
             eps: float = 3e-14) -> float:
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def rate(k: int, n: int) -> Optional[Dict[str, Any]]:
    """Rate with the appropriate interval for its sample size, auto-selected."""
    if n <= 0:
        return None
    iv = exact_binomial(k, n) if n < 30 else wilson(k, n)
    out = dict(iv or {})
    out['insufficient'] = n < MIN_EFFECTIVE_N
    return out


# ---------------------------------------------------------------------------
# Design-weighted (Horvitz-Thompson) ratio estimate
# ---------------------------------------------------------------------------
def weighted_ratio(values: Sequence[float], weights: Sequence[float]
                   ) -> Optional[float]:
    """Horvitz-Thompson ratio estimate: sum(w*y) / sum(w).

    For a stratified design where every sampled unit in stratum h has
    inclusion probability pi_h, w = 1/pi_h reproduces the population mean of
    ``y`` (for ``y`` an indicator, the population proportion).
    """
    num = sum(w * y for y, w in zip(values, weights) if w > 0)
    den = sum(w for w in weights if w > 0)
    return (num / den) if den > 0 else None


def design_effect(values: Sequence[float], weights: Sequence[float]
                  ) -> Optional[float]:
    """Kish design effect of the weighting, ``n * sum(w^2) / sum(w)^2``.

    Used only to report how much information the weighting costs, and to mark
    a heavily-weighted estimate as insufficiently sampled.
    """
    ws = [w for w in weights if w > 0]
    n = len(ws)
    if n == 0:
        return None
    s = sum(ws)
    return n * sum(w * w for w in ws) / (s * s)


def effective_n(n: int, deff: Optional[float]) -> float:
    if not deff or deff <= 0:
        return float(n)
    return n / deff


# ---------------------------------------------------------------------------
# Cluster bootstrap
# ---------------------------------------------------------------------------
def cluster_bootstrap(
    units: Sequence[Dict[str, Any]],
    stat_fn: Callable[[Sequence[Dict[str, Any]]], Optional[float]],
    cluster_key: str,
    n_rep: int = 2000,
    seed: int = 20261128,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Percentile cluster bootstrap.

    ``units`` is one dict per MEASUREMENT ROW.  Rows are grouped by
    ``cluster_key`` (the benign reference set) and whole clusters are
    resampled with replacement, so rows sharing a fixture/control pool do not
    masquerade as independent observations.

    The resample stream is driven by a fixed seed and an explicit
    Mersenne-Twister draw, so the interval is byte-reproducible.
    """
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for u in units:
        groups.setdefault(u.get(cluster_key), []).append(u)
    keys = sorted(groups, key=str)
    if not keys:
        return {'estimate': None, 'lo': None, 'hi': None, 'n_units': 0,
                'n_clusters': 0, 'n_valid_replicates': 0,
                'method': 'cluster_bootstrap_percentile'}
    point = stat_fn(units)
    draws: List[float] = []
    # inline MT-driven index draw: reproducible without depending on
    # random.sample implementation details
    import random
    rnd = random.Random(seed)
    m = len(keys)
    for _ in range(n_rep):
        pick = [keys[int(rnd.random() * m)] for _ in range(m)]
        sample: List[Dict[str, Any]] = []
        for k in pick:
            sample.extend(groups[k])
        v = stat_fn(sample)
        if v is not None and v == v:            # skip None / NaN
            draws.append(float(v))
    if not draws:
        return {'estimate': point, 'lo': None, 'hi': None,
                'n_units': len(units), 'n_clusters': m,
                'method': 'cluster_bootstrap_percentile',
                'insufficient': True}
    draws.sort()
    lo = draws[int((alpha / 2) * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return {
        'estimate': point, 'lo': lo, 'hi': hi,
        'n_units': len(units), 'n_clusters': m, 'n_boot_valid': len(draws),
        'n_valid_replicates': len(draws),
        'n_boot_requested': n_rep,
        'method': 'cluster_bootstrap_percentile',
        'insufficient': m < MIN_EFFECTIVE_N,
    }


def weighted_ratio_ci(
    rows: Sequence[Dict[str, Any]],
    num_key: str,
    weight_key: str,
    den_fn: Callable[[Dict[str, Any]], bool],
    cluster_key: str = 'reference_cluster',
    n_rep: int = 2000,
    seed: int = 20261128,
) -> Dict[str, Any]:
    """Design-weighted proportion with a cluster-bootstrap interval.

    ``den_fn(row)`` selects the denominator (e.g. "this row is a true
    positive or a true negative"); ``num_key`` selects the numerator.
    """
    def stat(sample: Sequence[Dict[str, Any]]) -> Optional[float]:
        num, den = 0.0, 0.0
        for r in sample:
            if not den_fn(r):
                continue
            w = float(r.get(weight_key) or 0.0)
            if w <= 0:
                continue
            den += w
            if r.get(num_key):
                num += w
        return (num / den) if den > 0 else None

    base = [r for r in rows if den_fn(r)]
    w = [float(r.get(weight_key) or 0.0) for r in base]
    deff = design_effect([1.0] * len(w), w) if w else None
    out = cluster_bootstrap(base, stat, cluster_key, n_rep=n_rep, seed=seed)
    out['deff'] = deff
    out['effective_n'] = effective_n(len(base), deff)
    out['n_denominator_rows'] = len(base)
    if out.get('effective_n') is not None and \
            out['effective_n'] < MIN_EFFECTIVE_N:
        out['insufficient'] = True
    return out


# ---------------------------------------------------------------------------
# Classification metrics (with denominators made explicit)
# ---------------------------------------------------------------------------
def confusion(pairs: Iterable[Sequence[Any]]) -> Dict[str, int]:
    """2x2 confusion for (reference, prediction) booleans.

    Cells are named from the REFERENCE's point of view:
        tp  reference positive, predicted positive
        fp  reference negative, predicted positive
        fn  reference positive, predicted negative
        tn  reference negative, predicted negative
    """
    tp = fp = fn = tn = 0
    for ref, pred in pairs:
        if ref and pred:
            tp += 1
        elif (not ref) and pred:
            fp += 1
        elif ref and (not pred):
            fn += 1
        else:
            tn += 1
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'n': tp + fp + fn + tn,
            'n_pos_ref': tp + fn, 'n_neg_ref': fp + tn,
            'n_pos_pred': tp + fp, 'n_neg_pred': fn + tn}


def metrics_from_cm(cm: Dict[str, int]) -> Dict[str, Any]:
    """Precision / recall / F1 / FPR / FNR / accuracy, each with its own n."""
    tp, fp, fn, tn = cm['tp'], cm['fp'], cm['fn'], cm['tn']

    def d(a: int, b: int) -> Optional[float]:
        return (a / b) if b else None

    precision = d(tp, tp + fp)
    recall = d(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and (precision + recall):
        f1 = 2 * precision * recall / (precision + recall)
    return {
        'n': cm['n'],
        'n_ref_pos': tp + fn, 'n_ref_neg': fp + tn,
        'n_pred_pos': tp + fp, 'n_pred_neg': fn + tn,
        'precision': precision, 'precision_n': tp + fp,
        'recall': recall, 'recall_n': tp + fn,
        'f1': f1,
        'fpr': d(fp, fp + tn), 'fpr_n': fp + tn,
        'fnr': d(fn, tp + fn), 'fnr_n': tp + fn,
        'accuracy': d(tp + tn, cm['n']), 'accuracy_n': cm['n'],
        'npv': d(tn, tn + fn), 'npv_n': tn + fn,
        'specificity': d(tn, tn + fp), 'specificity_n': tn + fp,
        # explicit non-collapse of the unavailable state
        'unavailable_ref': cm.get('unavailable_ref', 0),
        'unavailable_pred': cm.get('unavailable_pred', 0),
    }


def with_intervals(m: Dict[str, Any]) -> Dict[str, Any]:
    """Attach Wilson/Clopper-Pearson intervals to each point estimate."""
    out = dict(m)
    for name, kn in (('precision', ('precision', 'precision_n')),
                     ('recall', ('recall', 'recall_n')),
                     ('fpr', ('fpr', 'fpr_n')),
                     ('fnr', ('fnr', 'fnr_n')),
                     ('accuracy', ('accuracy', 'accuracy_n')),
                     ('specificity', ('specificity', 'specificity_n')),
                     ('npv', ('npv', 'npv_n'))):
        v = m.get(kn[0])
        n = m.get(kn[1]) or 0
        if v is None or n <= 0:
            out[name + '_ci'] = None
            continue
        # k is recomputed from a rounded float, so clamp it into [0, n]: at
        # v == 1.0 rounding can carry k to n + 1, and a k > n interval would
        # be silently meaningless rather than visibly wrong.
        k = min(n, max(0, int(round(v * n))))
        iv = exact_binomial(k, n) if n < 30 else wilson(k, n)
        # belt and braces: an interval must contain the estimate it describes
        if iv is not None and not (iv['lo'] <= v <= iv['hi']):
            iv = dict(iv, lo=min(iv['lo'], v), hi=max(iv['hi'], v))
        out[name + '_ci'] = iv
    return out


# ---------------------------------------------------------------------------
# Inter-annotator agreement
# ---------------------------------------------------------------------------
def kappa_cohen(a: Sequence[Any], b: Sequence[Any]
                ) -> Optional[Dict[str, Any]]:
    """Cohen's kappa for two annotators on nominal labels.

    ``None`` when kappa is undefined (both annotators constant on the same
    single category, so chance agreement is 1 and the denominator vanishes).
    """
    n = len(a)
    if n != len(b) or n == 0:
        return None
    cats = sorted({str(x) for x in a} | {str(x) for x in b})
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    mat = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        mat[idx[str(x)]][idx[str(y)]] += 1
    po = sum(mat[i][i] for i in range(k)) / n
    row = [sum(mat[i]) / n for i in range(k)]
    col = [sum(mat[i][j] for i in range(k)) / n for j in range(k)]
    pe = sum(row[i] * col[i] for i in range(k))
    if abs(1.0 - pe) < 1e-12:
        return {'kappa': None, 'po': po, 'pe': pe, 'n': n,
                'note': 'chance agreement is 1.0; kappa undefined'}
    return {'kappa': (po - pe) / (1.0 - pe), 'po': po, 'pe': pe, 'n': n,
            'observed_agreement': po, 'expected_agreement': pe,
            'n_categories': k}


def krippendorff_alpha(items: Sequence[Sequence[Any]],
                       level: str = 'nominal') -> Optional[Dict[str, Any]]:
    """Krippendorff's alpha over CODER x UNIT arrays.

    ``items[u]`` is the list of values assigned to unit u by each coder that
    labelled it.  Missing values are omitted (not imputed).  Supports
    ``nominal`` and ``ordinal``; ordinal uses the rank distance
    sum_{g=a}^{b} n_g - (n_a + n_b)/2.
    """
    # build the coincidence matrix over pairable values
    values: List[Any] = []
    per_unit: List[List[Any]] = []
    for vals in items:
        v = [x for x in vals if x is not None]
        if len(v) >= 2:
            per_unit.append(v)
            values.extend(v)
    if not per_unit:
        return None
    cats = sorted({str(x) for x in values})
    order = {c: i for i, c in enumerate(cats)}
    nc = len(cats)
    n_by_cat = [0] * nc
    for v in values:
        n_by_cat[order[str(v)]] += 1
    n_total = len(values)
    # coincidence matrix
    o = [[0.0] * nc for _ in range(nc)]
    for vals in per_unit:
        m = len(vals)
        if m < 2:
            continue
        for i in range(m):
            for j in range(m):
                if i == j:
                    continue
                o[order[str(vals[i])]][order[str(vals[j])]] += 1.0 / (m - 1)
    # expected coincidences
    e = [[0.0] * nc for _ in range(nc)]
    for c in range(nc):
        for k in range(nc):
            if c == k:
                e[c][k] = n_by_cat[c] * (n_by_cat[c] - 1)
            else:
                e[c][k] = n_by_cat[c] * n_by_cat[k]
            e[c][k] /= max(1, n_total - 1)

    def delta(a: int, b: int) -> float:
        if level == 'ordinal':
            lo, hi = min(a, b), max(a, b)
            s = 0.0
            for g in range(lo, hi + 1):
                s += n_by_cat[g]
            s -= (n_by_cat[lo] + n_by_cat[hi]) / 2.0
            return s * s
        return 0.0 if a == b else 1.0

    do = sum(delta(c, k) * o[c][k] for c in range(nc) for k in range(nc))
    de = sum(delta(c, k) * e[c][k] for c in range(nc) for k in range(nc))
    if de == 0:
        return {'alpha': None, 'note': 'expected disagreement is 0',
                'n_units': len(per_unit)}
    return {'alpha': 1.0 - do / de, 'do': do, 'de': de,
            'n_units': len(per_unit), 'n_values': n_total, 'level': level}


def icc_2_1(items: Sequence[Sequence[Optional[float]]]
            ) -> Optional[Dict[str, Any]]:
    """ICC(2,1) absolute agreement, two-way random effects, single rater.

    For numeric labels (e.g. an extracted answer).  Units with fewer than two
    raters are dropped rather than imputed.
    """
    rows = []
    for vals in items:
        v = [x for x in vals if x is not None]
        if len(v) >= 2:
            rows.append(v)
    if len(rows) < 2:
        return None
    k = 2
    n = len(rows)
    grand = sum(sum(r) for r in rows) / (n * k)
    row_means = [sum(r) / k for r in rows]
    col_means = [sum(r[j] for r in rows) / n for j in range(k)]
    ss_total = sum((r[j] - grand) ** 2 for r in rows for j in range(k))
    ss_rows = k * sum((m - grand) ** 2 for m in row_means)
    ss_cols = n * sum((m - grand) ** 2 for m in col_means)
    ss_err = ss_total - ss_rows - ss_cols
    ms_rows = ss_rows / (n - 1) if n > 1 else 0.0
    ms_cols = ss_cols / (k - 1) if k > 1 else 0.0
    ms_err = ss_err / ((n - 1) * (k - 1)) if (n > 1 and k > 1) else 0.0
    den = (ms_rows + (k - 1) * ms_err + k * (ms_cols - ms_err) / n)
    if den == 0:
        return {'icc': None, 'note': 'zero denominator', 'n': n}
    icc = (ms_rows - ms_err) / den
    return {'icc': icc, 'n_units': n, 'n_raters': k,
            'ms_rows': ms_rows, 'ms_cols': ms_cols, 'ms_err': ms_err}


def raw_agreement(a: Sequence[Any], b: Sequence[Any]) -> Optional[Dict[str, Any]]:
    n = min(len(a), len(b))
    if n == 0:
        return None
    agree = sum(1 for x, y in zip(a[:n], b[:n]) if str(x) == str(y))
    return {'agreement': agree / n, 'n': n, 'n_agree': agree}


def prevalence_adjusted_bias(pa: Optional[float], pb: Optional[float],
                             po: Optional[float]) -> Optional[Dict[str, Any]]:
    """Byrt's prevalence-adjusted, bias-adjusted kappa (PABAK).

    Reported alongside kappa because the validation labels are dominated by
    one class (most rows are NOT divergent), which depresses kappa even when
    raw agreement is high.  PABAK makes that sensitivity visible.
    """
    if pa is None or pb is None or po is None:
        return None
    return {'pabak': 2 * po - 1, 'po': po, 'p_pos_a': pa, 'p_pos_b': pb}
