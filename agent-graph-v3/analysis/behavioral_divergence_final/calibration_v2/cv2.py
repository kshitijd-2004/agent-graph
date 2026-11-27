#!/usr/bin/env python3.11
"""cv2 — calibration_v2 computation library.

Read-only over every input (traces, ledgers, caches, sealed references);
writes only under calibration_v2/.  Implements CALIBRATION_DESIGN.md.

Key objects (see the design doc for the full definitions):
  T_lep[c,d]  shared v6 LEP statistic (mean over the 3 matched benigns)
  T_p2[i][d]  clean 2-ref benign↔benign mean over the OTHER two benigns
  T_p3[i][d]  p3 exchangeable probe (benign↔benign under H0)
  B[c,d]      mean in-cell benign-pair distance (v6 ledger per_dimension)
  θ1[c,d]     max in-cell benign-pair distance (v6 ledger threshold; the
              LEP-free fixture-local extremal)
  F[c,d]      family-pooled q98 exact leave-one-cell-out threshold of the p3
              pool (the audited G5 object; byte-compatible loo_quantile)

Policies (a/d/a channels) — shared raw-T_d statistic T_d = mean of the
LEP-vs-benign pair distances (T_lep), thresholds in the same units:
  C0 original (frozen)      stat δ=T−B, θ=θ1        ⇔ T_d > B_d + θ1_d
                           (the V4.1 recentered-extremal construct, = the
                            spec-anchored FROZEN M1 4,150 reference)
  C1 previous calibrated    stat T_d, θ=F
                           (family-pooled q98 exact LOO of the p3 probe
                            pool — the audited G5 pooled-tail null;
                            outcome channel disabled at q98)
  S1 fixture-shrinkage      stat T_d, θ=z·F+(1−z)·(B_d + θ1_d)   [primary]
                           (data-driven EB weight z; z→0 = C0, z→1 = C1;
                            the fixture-aware blend that recovers stable-
                            cell sensitivity without the uncontrolled
                            zero-threshold band)
  S2 fixture-local bound    stat T_d, θ=B_d + θ1_d (the C0 endpoint;
                           sensitivity bound, B2B-uncontrolled)
  S3 min-floor bound        stat T_d, θ=min(F, B_d + θ1_d)
                           (specificity bound)
  S4 stratum shrinkage      S1 with the (fam, topo, mode) prior pool
                           (guard against domination by unrelated
                           high-variability fixtures — audit §4)

Outcome channel O1 (all rules): one-sided, tie-aware exceedance test on
the family-pooled DISCRETE disagree-mass null (3·T_p3 outcome probe
scores; LOO-excluding the cell; fire iff P(null ≥ s) ≤ α_out=0.05
pre-registered; s=3·T_lep_outcome; parametric Binomial(3, p̂) tail
reported as robustness).  Re-enables the outcome dimension that q98
disables (threshold = 1.0 = max attainable).

B2B controls (per-rule, matched + non-circular): EVERY rule uses the p3
probe control — each cell's 3 p3 probes (benign↔benign means under H0;
the LEP row is the exchangeable 4th execution, no ground truth) scored
against the SAME per-cell threshold the LEP faces (b2b_control).
probe-union FPR (observation; the audit's 1430/50319=2.84% object) and
the execution-level row-OR (cluster multiple-opportunity) are reported
separately, never conflated.  An earlier "clean p2 leave-self-out"
control was rejected: a 2-ref mean probe vs a 3-ref-matched threshold is
a cardinality mismatch (the exact G5 defect the audit repaired) — see
b2b_note_p2_rejected.

Determinism: all draws seeded (N_BOOT, SEED_SHRINK, SEED_CI named); the TV
float artifact is PYTHONHASHSEED-sensitive (pin for heavy runs, as cp_lib).
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(HERE)
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))
CALP = os.path.join(FINAL, 'calibrated_production')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')
CONT = ('assertions', 'decisions', 'actions')
FAMS = ('code_review', 'financial_analysis', 'research_synthesis')

# named constants (pre-registered; see VALIDATION_PREREGISTRATION.md §6)
Q = 0.98
ALPHA_OUT = 0.05          # outcome one-sided test level (pre-registered)
N_BOOT = 2000             # bootstrap size for σ²_obs (named)
N_ITER_CI = 2000          # fixture-cluster bootstrap iterations
SEED_SHRINK = 20270201    # σ²_obs bootstrap
SEED_CI = 20270202        # fixture-cluster bootstrap
SEED_DEVSPLIT = 'agentprop-cv2-devsplit-202702'
SEED_CONFIRM = 'agentprop-cv2-confirm-202702'

V6_LEDGER = os.path.join(REPO, 'analysis', 'measurement_v6', 'out',
                         'per_trace_v6.jsonl')
FROZEN_LEDGER = os.path.join(REPO, 'analysis',
                             'consolidated_20261121_bnd_v41', 'bnd_v41_run',
                             'bnd_v41_per_trace.jsonl')
DOWNSTREAM = os.path.join(REPO, 'analysis',
                          'consolidated_20261121_bnd_v41', 'downstream_results',
                          'per_trace.jsonl')
CALLEDGER = os.path.join(CALP, 'out', 'ledger_v6_calibrated.jsonl')
CELLS = os.path.join(CALP, 'out', 'cache_cells_full.json')
REFPAIRS = os.path.join(FINAL, 'out', 'reference_heldout_pairs.jsonl')
PARTITIONS = os.path.join(FINAL, 'out', 'partitions_final.json')
V7_PARTITIONS = os.path.join(REPO, 'analysis', 'measurement_v7', 'out',
                             'partitions.json')


def _jsonl(p: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(p) if l.strip()]


def _by(p: str) -> Dict[str, Dict[str, Any]]:
    return {json.loads(l)['trace_id']: json.loads(l)
            for l in open(p) if l.strip()}


# ── exact multiset-LOO quantile (byte-identical to cp_lib.loo_rank) ─────────
def _loo_rank(P: List[float], i_target: int,
              R: List[float]) -> Optional[int]:
    n = len(P)
    r = len(R)
    if n - r <= i_target:
        return None
    R = sorted(R)
    for m in range(0, r + 1):
        j = i_target + m
        if j >= n:
            continue
        v = P[j]
        pos = bisect.bisect_left(P, v)
        c = bisect.bisect_right(P, v) - pos
        r_less = bisect.bisect_left(R, v)
        r_eq = bisect.bisect_right(R, v) - r_less
        if pos - r_less <= i_target < pos - r_less + (c - r_eq):
            return i_target + r_less
    return None


def loo_quantile(P_sorted: List[float], R: List[float],
                 q: float = Q) -> Optional[float]:
    """Exact leave-one-cell-out q-quantile of P with the cell's own R
    excluded (cp_lib-compatible audited routine)."""
    if len(P_sorted) - len(R) <= 0:
        return None
    i_target = int(math.ceil(q * (len(P_sorted) - len(R)))) - 1
    j = _loo_rank(P_sorted, max(0, i_target), R)
    return P_sorted[j] if j is not None else None


# ── population ────────────────────────────────────────────────────────────────
def load_cells() -> Tuple[List[Dict[str, Any]], Dict[str, List[int]]]:
    """Unique-view cells (first occurrence of each trace_id retained; the 6
    accidental duplicate records are excluded from the canonical P1*) plus
    the map trace_id -> full-row indices (disclosed P1 view)."""
    cached = json.load(open(CELLS))
    raw = cached['cells']
    cells: List[Dict[str, Any]] = []
    seen: Dict[str, List[int]] = {}
    for i, c in enumerate(raw):
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
        tid = c['trace_id']
        seen.setdefault(tid, []).append(i)
        if seen[tid][0] != i:
            continue
        c['row_idx'] = i
        cells.append(c)
    dupes = {t: idx for t, idx in seen.items() if len(idx) > 1}
    return cells, dupes


def load_full_cells() -> List[Dict[str, Any]]:
    """All 16,773 rows (disclosed P1 view; family pools span the full row
    set exactly as the audited caches do)."""
    raw = json.load(open(CELLS))['cells']
    for c in raw:
        if isinstance(c.get('stratum'), list):
            c['stratum'] = tuple(c['stratum'])
    return raw


def load_v6_dim() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in _jsonl(V6_LEDGER):
        out[r['trace_id']] = r.get('per_dimension') or {}
    return out


def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def cluster_bootstrap_ci(fixtures: Dict[str, List[float]],
                         n_iter: int = N_ITER_CI,
                         seed: int = SEED_CI) -> List[float]:
    """Fixture-cluster bootstrap of an execution-level rate (resample WHOLE
    fixtures with replacement; seed-named, deterministic)."""
    rng = random.Random(seed)
    keys = sorted(fixtures)
    vals_all = [v for row in fixtures.values() for v in row]
    point = (sum(vals_all) / len(vals_all)) if vals_all else 0.0
    if len(keys) < 5:
        return ([min(vals_all), max(vals_all), point] if vals_all
                else [0.0, 0.0, 0.0])
    ests = []
    for _ in range(n_iter):
        pool: List[float] = []
        for _i in range(len(keys)):
            pool.extend(fixtures[rng.choice(keys)])
        ests.append((sum(pool) / len(pool)) if pool else 0.0)
    ests.sort()
    return [ests[int(0.025 * (n_iter - 1))],
            ests[int(0.975 * (n_iter - 1))], point]


def fixture_of(tid: str) -> str:
    return tid.split('.')[0]


# ── per-cell derived quantities ───────────────────────────────────────────────
def cell_theta1(c: Dict[str, Any], d: str,
                 v6dim: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """θ1_d = max in-cell benign-pair distance (v6 ledger threshold)."""
    return (v6dim.get(c['trace_id'], {}).get(d) or {}).get('threshold')


def cell_Bd(c: Dict[str, Any], d: str,
            v6dim: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """B_d = mean of the in-cell benign↔benign pair distances
    (== lep_distance − delta in the v6 ledger; the fixture's ordinary
    mean benign spread — LEP-free, a fixture property)."""
    e = v6dim.get(c['trace_id'], {}).get(d) or {}
    T, delta, bd = e.get('lep_distance'), e.get('delta'), \
        e.get('benign_distances')
    if T is not None and delta is not None:
        return T - delta
    vals = [x for x in (bd or []) if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def cell_fixture_thr(c: Dict[str, Any], d: str,
                     v6dim: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """The audited C0 (original) threshold on the shared raw-T_d scale:
    B_d + θ1_d.  The original rule fires iff δ_d = T_d−B_d > θ1_d ⇔
    T_d > B_d + θ1_d.  Requires BOTH B_d and θ1_d evaluable (the recentered
    original construct); if either is unavailable the dimension is
    UNCALIBRATED → None (missing is not zero; no silent B=0 fallback)."""
    B = cell_Bd(c, d, v6dim)
    th = cell_theta1(c, d, v6dim)
    if B is None or th is None:
        return None
    return B + th


def cell_pairs(c: Dict[str, Any], d: str) -> List[Optional[float]]:
    """Recover the 3 direct in-cell benign↔benign pair distances from the
    clean T_p2 cache (LEP-free): a=D(b0,b1), b=D(b0,b2), c=D(b1,b2) with
    T_p2[0]=(a+b)/2, T_p2[1]=(a+c)/2, T_p2[2]=(b+c)/2.  A pair is None when
    its value is not recoverable (needs the other two T_p2 evaluable)."""
    p = c.get('T_p2')
    if not p:
        return [None, None, None]
    t0 = p[0].get(d)
    t1 = p[1].get(d)
    t2 = p[2].get(d)
    a = b = cc = None
    if t0 is not None and t1 is not None and t2 is not None:
        a = t0 + t1 - t2
        b = t0 + t2 - t1
        cc = t1 + t2 - t0
        return [a, b, cc]
    # partial: no pair is recoverable when any two T_p2 are missing
    return [None, None, None]


def build_pools(cells: List[Dict[str, Any]], null: str = 'T_p3'
                ) -> Dict[str, Dict[str, List[float]]]:
    """Per-family sorted pools of the p3 probe null values (the F object)."""
    pools: Dict[str, Dict[str, List[float]]] = {}
    for c in cells:
        ent = pools.setdefault(c['fam'], {d: [] for d in DIMS})
        for tp in c[null]:
            for d in DIMS:
                v = tp.get(d)
                if v is not None:
                    ent[d].append(v)
    for ent in pools.values():
        for d in DIMS:
            ent[d] = sorted(ent[d])
    return pools


def build_pair_pools(cells: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[float]]]:
    """Family pools of the DIRECT in-cell pair distances (cell-unique pairs;
    only when all 3 T_p2 are evaluable on d).  The σ²_obs bootstrap pool."""
    pools: Dict[str, Dict[str, List[float]]] = {}
    cov: Dict[str, Dict[str, int]] = {}
    for c in cells:
        f = c['fam']
        pools.setdefault(f, {d: [] for d in CONT})
        for d in CONT:
            ps = cell_pairs(c, d)
            allv = [x for x in ps if x is not None]
            if len(allv) == 3:
                pools[f][d].extend(allv)
    return pools


def shrinkage_weights(cells: List[Dict[str, Any]],
                      v6dim: Dict[str, Dict[str, Any]],
                      pair_pools: Dict[str, Dict[str, List[float]]],
                      pools: Dict[str, Dict[str, List[float]]],
                      seed: int = SEED_SHRINK,
                      n_boot: int = N_BOOT) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Data-driven EB weight z per (family, dim):
        z = σ²_obs / (σ²_obs + σ²_b)
      θ_S1 = z·F + (1−z)·(B_d + θ1_d): z is the weight on the FAMILY prior
      F; (1−z) is the weight on the fixture-local recentered threshold.
      σ²_obs: bootstrap variance of the fixture-local endpoint
      (mean+max of 3 resampled direct pairs) — its sampling noise;
      σ²_b:   between-cell variance of the fixture-local endpoint across
      the family — genuine benign-variability heterogeneity.
      z small ⇔ high between-cell heterogeneity (trust each fixture's own
      threshold); z large ⇔ noisy local estimator (trust the family).
      Pure function of the data (no arbitrary constant; n_boot is named)."""
    # between-cell fixture-local endpoint (B_d + θ1_d) aggregates
    fam_loc: Dict[str, Dict[str, List[float]]] = {f: {d: [] for d in CONT}
                                                   for f in FAMS}
    for c in cells:
        for d in CONT:
            loc = cell_fixture_thr(c, d, v6dim)
            if loc is not None:
                fam_loc[c['fam']][d].append(loc)
    ztab: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rng = random.Random(seed)
    for f in FAMS:
        for d in CONT:
            pool = sorted(pair_pools[f][d])
            if len(pool) >= 3:
                boots = []
                for _ in range(n_boot):
                    res = [pool[rng.randrange(len(pool))]
                           for _ in range(3)]
                    B_r = sum(res) / 3
                    boots.append(B_r + max(res))   # mean + max of 3 pairs
                mb = sum(boots) / len(boots)
                s2_obs = sum((x - mb) ** 2 for x in boots) / len(boots)
            else:
                s2_obs = None
            Ls = fam_loc[f][d]
            if Ls:
                mbar = sum(Ls) / len(Ls)
                s2_b = sum((x - mbar) ** 2 for x in Ls) / len(Ls)
            else:
                s2_b = 0.0
            ztab[(f, d)] = {'s2_obs': s2_obs, 's2_b': s2_b,
                            'sum_loc': sum(Ls),
                            'sumsq_loc': sum(x * x for x in Ls),
                            'n_loc': len(Ls)}
    return ztab


def loo_between_var(zt: Dict[str, Any], exclude: Optional[float]) -> float:
    """LOO closed-form between-cell variance of the fixture-local endpoint
    (B_d + θ1_d), excluding one cell's value."""
    n = zt['n_loc']
    if exclude is None:
        if n <= 1:
            return 0.0
        mbar = zt['sum_loc'] / n
        return max(0.0, (zt['sumsq_loc'] - n * mbar * mbar) / n)
    nm = n - 1
    if nm <= 1:
        return 0.0
    ssum = zt['sum_loc'] - exclude
    ssum2 = zt['sumsq_loc'] - exclude * exclude
    mbar = ssum / nm
    return max(0.0, (ssum2 - nm * mbar * mbar) / nm)


def shrinkage_theta(cells, v6dim, pools, ztab, q: float = Q
                    ) -> List[Dict[str, Optional[float]]]:
    """θ_S1[c,d] = z·F + (1−z)·(B_d + θ1_d) — the construct-faithful,
    fixture-aware blend of the two audited rules on the shared raw-T scale:
      * z → 0  ⇒  θ = B_d + θ1_d   (the C0 original recentered-extremal rule,
        = the V4.1 recentered-extremal reference construct)
      * z → 1  ⇒  θ = F            (the C1 family-pooled exact-LOO q98 rule,
        = the audited G5 pooled-tail null)
    with LOO-corrected z (the cell's own endpoint value B_d + θ1_d
    excluded from σ²_b, the same operand S4 excludes) and explicit None
    handling (a missing endpoint falls back to the evaluable one; both ->
    None, never a forced negative).  B_d is the fixture's ordinary mean
    benign spread — the 'accounting for stochastic variability' term."""
    out: List[Dict[str, Optional[float]]] = []
    for c in cells:
        row: Dict[str, Optional[float]] = {}
        for d in CONT:
            zt = ztab[(c['fam'], d)]
            loc = cell_fixture_thr(c, d, v6dim)   # B_d + θ1_d (C0 endpoint)
            Fv = loo_quantile(pools[c['fam']][d],
                              sorted(_cell_probe_vals(c, d)), q=q)
            if zt['s2_obs'] is None:
                z = 0.0 if loc is not None else 1.0
            else:
                s2_b = loo_between_var(zt, loc)
                denom = zt['s2_obs'] + s2_b
                z = (zt['s2_obs'] / denom) if denom > 0 else 0.0
            if Fv is None and loc is None:
                row[d] = None
            elif Fv is None:
                row[d] = loc
            elif loc is None:
                row[d] = Fv
            else:
                row[d] = z * Fv + (1.0 - z) * loc
        out.append(row)
    return out


def _cell_probe_vals(c: Dict[str, Any], d: str) -> List[float]:
    return [tp[d] for tp in c['T_p3'] if tp.get(d) is not None]


def _fam_loo_all(cells, pools, q: float = Q) -> List[Dict[str, Optional[float]]]:
    out = []
    for c in cells:
        P = pools[c['fam']]
        row = {}
        for d in CONT:
            R = sorted(tp[d] for tp in c['T_p3'] if tp.get(d) is not None)
            row[d] = loo_quantile(P[d], R, q=q)
        out.append(row)
    return out


def local_theta1(cells, v6dim) -> List[Dict[str, Optional[float]]]:
    """θ1_d = in-cell benign-pair max (v6 ledger threshold; LEP-free).
    The audited C0 original rule is δ_d = T_d−B_d > θ1_d."""
    return [{d: cell_theta1(c, d, v6dim) for d in CONT} for c in cells]


def fixture_theta(cells, v6dim) -> List[Dict[str, Optional[float]]]:
    """The audited C0 (original) threshold on the shared raw-T scale:
    B_d + θ1_d (the C0 rule δ_d = T_d−B_d > θ1_d ⇔ T_d > B_d + θ1_d),
    None-safe — the S2 fixture-local endpoint."""
    return [{d: cell_fixture_thr(c, d, v6dim) for d in CONT} for c in cells]


def minfloor_theta(cells, v6dim, pools, q: float = Q) -> List[Dict[str, Optional[float]]]:
    fam = _fam_loo_all(cells, pools, q)
    out = []
    for ci, c in enumerate(cells):
        row = {}
        for d in CONT:
            loc = cell_fixture_thr(c, d, v6dim)   # B_d + θ1_d
            Fv = fam[ci][d]
            row[d] = (min(loc, Fv) if (loc is not None and Fv is not None)
                      else (loc if Fv is None else Fv))
        out.append(row)
    return out


def build_policy_theta(cells, v6dim, pools, ztab, policy: str,
                       q: float = Q):
    """Returns (theta_per_cell, stat_per_dim) for the a/d/a channels."""
    if policy == 'C0_original':
        # The audited original (frozen) rule: δ_d = T_d − B_d > θ1_d
        # (v6 ledger per_dimension: stat = delta, threshold = θ1 = the
        # in-cell benign-pair max).  On the shared raw-T scale this is
        # T_d > B_d + θ1_d — the S2 fixture-local endpoint.
        stat = {d: [((v6dim.get(c['trace_id'], {}).get(d)) or {}).get('delta')
                    for c in cells] for d in CONT}
        th = local_theta1(cells, v6dim)
        return th, stat
    if policy == 'C1_family_q98':
        th = _fam_loo_all(cells, pools, q=q)
    elif policy == 'S1_shrinkage':
        th = shrinkage_theta(cells, v6dim, pools, ztab, q=q)
    elif policy == 'S2_localmax':
        th = fixture_theta(cells, v6dim)
    elif policy == 'S3_minfloor':
        th = minfloor_theta(cells, v6dim, pools, q=q)
    elif policy == 'S4_shrinkage_stratum':
        # S1 with the stratum prior: the q98 LOO pool is
        # (fam, topology, mode)-local instead of family-wide — guards
        # against an unrelated high-variability fixture (different
        # topology/mode) dominating the prior (audit §4, requirement).
        stratum_pools = _stratum_pools(cells)
        th = _shrinkage_theta_pools(cells, v6dim, stratum_pools, ztab, q=q)
    else:
        raise ValueError(policy)
    stat = {d: [c['T_lep'].get(d) for c in cells] for d in CONT}
    return th, stat


def _stratum_pools(cells) -> Dict[tuple, Dict[str, List[float]]]:
    """(fam, topo, mode)-local p3 pools (the audit's stratum pool object;
    c['stratum'] is already the (fam, topo, mode) tuple)."""
    pools: Dict[tuple, Dict[str, List[float]]] = {}
    for c in cells:
        key = c['stratum']
        ent = pools.setdefault(key, {d: [] for d in CONT})
        for tp in c['T_p3']:
            for d in CONT:
                v = tp.get(d)
                if v is not None:
                    ent[d].append(v)
    for ent in pools.values():
        for d in CONT:
            ent[d] = sorted(ent[d])
    return pools


def _shrinkage_theta_pools(cells, v6dim, pools, ztab, q: float = Q,
                           key_of=lambda c: c['stratum']
                           ) -> List[Dict[str, Optional[float]]]:
    """S1 shrinkage with an arbitrary pool object (family or stratum)."""
    out: List[Dict[str, Optional[float]]] = []
    for c in cells:
        row: Dict[str, Optional[float]] = {}
        for d in CONT:
            zt = ztab[(c['fam'], d)]
            loc = cell_fixture_thr(c, d, v6dim)   # B_d + θ1_d (C0 endpoint)
            R = sorted(_cell_probe_vals(c, d))
            P = pools[key_of(c)][d]
            Fv = loo_quantile(P, R, q=q)
            if zt['s2_obs'] is None:
                z = 0.0 if loc is not None else 1.0
            else:
                s2_b = loo_between_var(zt, loc)
                denom = zt['s2_obs'] + s2_b
                z = (zt['s2_obs'] / denom) if denom > 0 else 0.0
            if Fv is None and loc is None:
                row[d] = None
            elif Fv is None:
                row[d] = loc
            elif loc is None:
                row[d] = Fv
            else:
                row[d] = z * Fv + (1.0 - z) * loc
        out.append(row)
    return out


# ── outcome O1: one-sided, tie-aware test on the discrete disagree-mass ─────
def outcome_null(cells) -> Dict[str, Dict[str, Any]]:
    """Family pools of the p3 outcome disagree-mass j=3·T_p3[i] ∈ {0,1,2,3}
    (matched-cardinality empirical null: each pool value is a mean over 3
    exchangeable distances, exactly the LEP object; under H0 the LEP row is
    the exchangeable 4th benign).  p̂ = P(null ≥ 1) for the parametric
    Binomial(3, p̂) robustness check."""
    pools: Dict[str, List[float]] = {f: [] for f in FAMS}
    for c in cells:
        for tp in c.get('T_p3') or []:
            v = tp.get('outcomes')
            if v is not None:
                pools[c['fam']].append(3.0 * v)
    info: Dict[str, Dict[str, Any]] = {}
    for f in FAMS:
        P = sorted(pools[f])
        N = len(P)
        p_hat = (sum(1 for v in P if v >= 1.0 - 1e-9) / N) if N else None
        info[f] = {'n_pool': N,
                   'counts': {k: sum(1 for v in P
                                      if abs(v - k) < 1e-9)
                              for k in (0, 1, 2, 3)},
                   'p_hat_3': p_hat,
                   'min': P[0] if P else None, 'max': P[-1] if P else None}
    return info


def binom_tail(n: int, k: int, p: float) -> float:
    """P(Bin(n, p) ≥ k) — the parametric robustness check for O1."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j)
               for j in range(k, n + 1))


def _fam_p3_out_pool(cells) -> Dict[str, List[float]]:
    fam: Dict[str, List[float]] = {}
    for c in cells:
        fam.setdefault(c['fam'], [])
    for c in cells:
        fam[c['fam']].extend(3.0 * tp['outcomes'] for tp in c['T_p3']
                             if tp.get('outcomes') is not None)
    for f in fam:
        fam[f] = sorted(fam[f])
    return fam


_FULL_OUT_POOL_CACHE: Optional[Dict[str, List[float]]] = None


def fam_p3_out_pool_full() -> Dict[str, List[float]]:
    """The O1 outcome null is a POPULATION-level object (like F): the family
    p3 disagree-mass pool built on the full P1 row view (13,074 / 8,642 /
    16,581 per CALIBRATION_DESIGN §B).  Partition scoring MUST use this
    same population null — never a partition-local pool — so the O1 channel
    is calibrated identically everywhere (consistent with F)."""
    global _FULL_OUT_POOL_CACHE
    if _FULL_OUT_POOL_CACHE is None:
        _FULL_OUT_POOL_CACHE = _fam_p3_out_pool(load_full_cells())
    return _FULL_OUT_POOL_CACHE


def outcome_score(cells, out_null_info, alpha: float = ALPHA_OUT,
                  P_override: Optional[Dict[str, List[float]]] = None
                  ) -> List[Dict[str, Any]]:
    """O1: fire the outcome iff s=3·T_lep_outcome is evaluable and
    P(null ≥ s) ≤ alpha on the LOO-excluded family p3 disagree-mass null
    (one-sided; ties at s are counted in the exceedance mass).  The null
    pool is the POPULATION-level object (fam_p3_out_pool_full: 13,074 /
    8,642 / 16,581 over P1 — CALIBRATION_DESIGN §B) unless P_override is
    supplied; partition scoring therefore uses the SAME population null as
    the full-frame scoring, consistent with the a/d/a F object.  The cell's
    own 3 p3 outcome probes are excluded (LOO).  Parametric Binomial(3, p̂)
    tail reported as robustness, not the primary."""
    P = P_override if P_override is not None else fam_p3_out_pool_full()
    out: List[Dict[str, Any]] = []
    for c in cells:
        T = c['T_lep'].get('outcomes')
        s = 3.0 * T if T is not None else None
        R = sorted(3.0 * tp['outcomes'] for tp in c['T_p3']
                   if tp.get('outcomes') is not None)
        if s is None:
            out.append({'fire': None, 'reason': 'lep_outcome_unevaluable',
                        's': None})
            continue
        Pp = P[c['fam']]
        E_all = len(Pp) - bisect.bisect_left(Pp, s)
        E_R = len(R) - bisect.bisect_left(R, s)
        E, Nlo = E_all - E_R, len(Pp) - len(R)
        if Nlo <= 0:
            out.append({'fire': None, 'reason': 'no_benign_null_support',
                        's': s})
            continue
        p_emp = E / Nlo
        k = int(round(s))
        ph = out_null_info[c['fam']]['p_hat_3']
        p_bin = binom_tail(3, k, ph) if ph is not None else None
        out.append({'fire': bool(p_emp <= alpha), 'p_emp': p_emp,
                    'p_binom': p_bin, 's': s, 'k': k, 'n_loo': Nlo,
                    'E': E})
    return out


# ── B2B controls ──────────────────────────────────────────────────────────────
def b2b_p3_probe(cells, thr_per_cell: List[Dict[str, Optional[float]]]
                 ) -> Dict[str, Any]:
    """The matched, non-circular B2B control (the audit's G5 construction):
    each cell's 3 p3 probes T_p3[i] (benign↔benign means under H0; the LEP
    row enters as the exchangeable 4th execution — no ground truth) are
    scored against the SAME per-cell threshold object the LEP faces under
    the rule (strict `>`).  Same statistic, same threshold, same operator
    ⇒ the probe and the LEP are calibrated identically (non-circular).

    Reports BOTH (never conflated, requirement 6):
      * probe-union FPR  — a probe fires when ANY of its evaluable dims
        exceeds the threshold; the denominator is the count of evaluable
        probes (3 × 16,773 = 50,319 on the full frame — the audited
        construct that reproduced 1,430/50,319 = 2.84%).
      * row-OR FPR       — the execution-level rate: any of a cell's probes
        fired.  This is the per-execution figure the pre-registered V4 gate
        uses (the audit's RECOMMENDED §3.5 open item: it had only ever been
        derived at probe level; we derive it here at the cell level).
    """
    probe_union_fire = probe_union_n = 0
    row_or = 0
    per_dim = {d: [0, 0] for d in CONT}
    for c, thr in zip(cells, thr_per_cell):
        row_fired = 0
        for tp in c['T_p3']:
            this_probe_app = False
            this_probe_fire = 0
            for d in CONT:
                v, t = tp.get(d), thr.get(d)
                if v is None or t is None:
                    continue
                this_probe_app = True
                per_dim[d][1] += 1
                if v > t:
                    this_probe_fire += 1
                    per_dim[d][0] += 1
            if this_probe_app:
                probe_union_n += 1
                if this_probe_fire:
                    probe_union_fire += 1
                    row_fired += 1
        if row_fired:
            row_or += 1
    return {'probe_union_n': probe_union_n, 'probe_union_fire': probe_union_fire,
            'probe_union_fpr': (probe_union_fire / probe_union_n)
            if probe_union_n else None,
            'row_or': row_or, 'n_cells': len(cells),
            'row_or_fpr': (row_or / len(cells)) if cells else None,
            'per_dim': {d: {'fired': per_dim[d][0], 'eval': per_dim[d][1]}
                        for d in CONT},
            'construction': ('p3 probe scored vs the SAME per-cell threshold '
                             'the LEP faces (matched + non-circular; '
                             'probe-union and row-OR reported separately)')}


def b2b_control(cells, thr_per_cell, out_null_info,
                alpha: float = ALPHA_OUT) -> Dict[str, Any]:
    """The matched, non-circular B2B control for a rule (the audit's G5
    construction), computed in ONE per-cell pass over the 3 p3 probes:

      * probe-union (a/d/a): a probe fires when ANY of its evaluable a/d/a
        dims exceeds the rule's per-cell threshold; the denominator is the
        count of evaluable probes (3 × n_cells — 50,319 on the full frame,
        the audited construct that reproduced 1,430/50,319 = 2.84%).
      * outcome-probe: a probe's outcome fires under the O1 one-sided test
        (same test the LEP faces — matched + non-circular).
      * execution-level row-OR (the pre-registered V4 gate figure): ANY
        probe in the cell fires on ANY channel (a/d/a OR outcome).

    Probe-union (observation) and row-OR (execution) are reported
    SEPARATELY and never conflated (requirement 6)."""
    P = fam_p3_out_pool_full()
    probe_union_fire = probe_union_n = 0
    o_probe_fire = o_probe_n = 0
    row_or = 0
    per_dim = {d: [0, 0] for d in CONT}
    for c, thr in zip(cells, thr_per_cell):
        Pp = P[c['fam']]
        R = sorted(3.0 * tp['outcomes'] for tp in c['T_p3']
                   if tp.get('outcomes') is not None)
        row_fired = False
        for tp in c['T_p3']:
            # ── a/d/a probe-union under the rule's own threshold ─────────
            this_probe_app = False
            this_probe_fire = 0
            for d in CONT:
                v, t = tp.get(d), thr.get(d)
                if v is None or t is None:
                    continue
                this_probe_app = True
                per_dim[d][1] += 1
                if v > t:
                    this_probe_fire += 1
                    per_dim[d][0] += 1
            if this_probe_app:
                probe_union_n += 1
                if this_probe_fire:
                    probe_union_fire += 1
                    row_fired = True
            # ── outcome probe under O1 ────────────────────────────────────
            so = 3.0 * tp['outcomes'] if tp.get('outcomes') is not None \
                else None
            if so is not None:
                E_all = len(Pp) - bisect.bisect_left(Pp, so)
                E_R = len(R) - bisect.bisect_left(R, so)
                E, Nlo = E_all - E_R, len(Pp) - len(R)
                if Nlo > 0:
                    o_probe_n += 1
                    if E / Nlo <= alpha:
                        o_probe_fire += 1
                        row_fired = True
        if row_fired:
            row_or += 1
    return {'probe_union_n': probe_union_n,
            'probe_union_fire': probe_union_fire,
            'probe_union_fpr': (probe_union_fire / probe_union_n)
            if probe_union_n else None,
            'outcome_probe_n': o_probe_n, 'outcome_probe_fire': o_probe_fire,
            'outcome_probe_fpr': (o_probe_fire / o_probe_n)
            if o_probe_n else None,
            'row_or': row_or, 'n_cells': len(cells),
            'row_or_fpr': (row_or / len(cells)) if cells else None,
            'per_dim': {d: {'fired': per_dim[d][0], 'eval': per_dim[d][1]}
                        for d in CONT},
            'construction': ('p3 probes vs the SAME per-cell threshold the '
                            'LEP faces (matched + non-circular); '
                            'probe-union (observation), outcome-probe and '
                            'row-OR (execution) reported separately')}


def b2b_note_p2_rejected() -> Dict[str, str]:
    """Why the earlier "clean p2 leave-self-out" control was NOT adopted
    (recorded design decision): scoring a 2-ref mean probe T_p2[i] against
    a 3-ref-matched threshold is a cardinality mismatch — the exact G5
    defect the audit repaired.  Under H0 the p3 probes are the matched,
    exchangeable, non-circular B2B object (the LEP row enters as the 4th
    exchangeable execution), so b2b_p3_probe (same-threshold) is the
    control for EVERY rule."""
    return {'rejected': 'p2 probe vs 3-ref threshold (cardinality mismatch)',
            'adopted': 'b2b_p3_probe (matched + non-circular, per rule)'}


# ── per-candidate scoring ─────────────────────────────────────────────────────
def score_candidate(cells, v6dim, pools, ztab, out_null_info, policy: str,
                    alpha_out: float = ALPHA_OUT) -> List[Dict[str, Any]]:
    th, stat = build_policy_theta(cells, v6dim, pools, ztab, policy)
    o = outcome_score(cells, out_null_info, alpha=alpha_out)
    rows: List[Dict[str, Any]] = []
    for ci, c in enumerate(cells):
        fires: Dict[str, Optional[bool]] = {}
        for d in CONT:
            s, t = stat[d][ci], th[ci][d]
            fires[d] = None if (s is None or t is None) else bool(s > t)
        fires['outcomes'] = o[ci]['fire']
        eval_dims = [d for d in DIMS if fires[d] is not None]
        pos = [d for d in eval_dims if fires[d]]
        row = {
            'trace_id': c['trace_id'], 'fam': c['fam'],
            'lep_code': c['lep_code'], 'stratum': c['stratum'],
            'fires': fires, 'triggered': pos, 'union': bool(pos),
            'n_eval': len(eval_dims), 'undetermined': (not eval_dims),
            'outcome_p_emp': o[ci].get('p_emp'),
            'theta': th[ci],
        }
        rows.append(row)
    return rows


# ── reference scoring ────────────────────────────────────────────────────────
def load_frozen_ref() -> Dict[str, Optional[bool]]:
    """The independent frozen V4.1 structural reference (comparable-gated;
    None = not comparable, excluded, never forced)."""
    out: Dict[str, Optional[bool]] = {}
    for r in _jsonl(FROZEN_LEDGER):
        out[r['trace_id']] = (bool(r.get('behavioral_anomaly'))
                              if r.get('comparable') else None)
    return out


def load_sealed_pair_ref() -> Dict[str, Optional[bool]]:
    """The sealed 216-pair adjudicated reference at LEP-row level."""
    out: Dict[str, Optional[bool]] = {}
    for r in _jsonl(REFPAIRS):
        out[r['lep_trace_id']] = r.get('union_ref')
    return out


def confusion(rows, ref: Dict[str, Optional[bool]]) -> Dict[str, Any]:
    tp = fp = fn = tn = excl = 0
    by_fam = {f: {'pos': 0, 'neg': 0} for f in FAMS}
    by_lep = {}
    for r in rows:
        rr = ref.get(r['trace_id'])
        if rr is None:
            excl += 1
            continue
        by_fam[r['fam']]['pos' if rr else 'neg'] += 1
        lc = by_lep.setdefault((r['fam'], r['lep_code']),
                                {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0})
        pred = r['union']
        if pred and rr:
            tp += 1
            lc['TP'] += 1
        elif pred and not rr:
            fp += 1
            lc['FP'] += 1
        elif not pred and rr:
            fn += 1
            lc['FN'] += 1
        else:
            tn += 1
            lc['TN'] += 1
    n = tp + fp + fn + tn
    p = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * p * rec / (p + rec)) if (p is not None and rec is not None
                                       and (p + rec)) else None
    return {'TP': tp, 'FP': fp, 'FN': fn, 'TN': tn, 'n_eval': n,
            'n_excluded': excl, 'precision': p, 'recall': rec, 'f1': f1,
            'per_family_ref': by_fam,
            'per_lep': {'|'.join(k): v for k, v in by_lep.items()}}


def union_rate_ci(rows, key: str = 'union') -> List[float]:
    fix: Dict[str, List[float]] = {}
    for r in rows:
        fix.setdefault(fixture_of(r['trace_id']),
                       []).append(1.0 if r[key] else 0.0)
    return cluster_bootstrap_ci(fix)
