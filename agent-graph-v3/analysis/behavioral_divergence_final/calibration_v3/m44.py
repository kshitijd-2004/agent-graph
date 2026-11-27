#!/usr/bin/env python3.11
"""m44 — V4.4 calibration library (the statistically valid null machinery).

Replaces the defective V4.3 calibration constructions identified by the
independent investigation.  Design principles (pre-registered here; every
object below is built from ONE extractor version — the V4.4 ledger — and
targets and benign probes share the SAME statistic, reference
cardinality, missingness rules, and decision procedure):

* No fractional-count reconstruction.  The V4.3 M2 gate turned a
  fractional LEP mean into an implied disagree COUNT via round(mean*n)
  and ran a binomial tail on it; decisions/outcomes are not binary, so
  the count is fictitious (294/1088/668 fractional implied counts;
  82/219/9 positive means rounding to zero).  V4.4 records per-pair
  integer disagreement counts DIRECTLY (the LEP-vs-benign distances are
  themselves the recorded objects) and calibrates them on their actual
  discrete support against a LEP-free, fixture-disjoint family pool.

* No saturated-threshold binomial channels.  Where a cell's in-cell
  benign max saturates at 1.0 (the maximum possible distance), the
  recentered excess statistic (T - B > theta1) is mathematically
  incapable of firing; that is now an EXPLICIT, NAMED channel state
  ('saturated' / 'uncalibrated'), not a silently-disabled discrete test.

* No circular probe thresholds.  Every threshold pool EXCLUDES the
  probes' own cell at the FIXTURE level (all rows sharing the fixture's
  reference set are excluded together — the 1,474 byte-identical p3
  blobs in the V4.3 cache are sibling rows, and cell-level LOO did not
  remove that leakage).  A probe never participates in the threshold it
  is scored against, and no cached cross-version statistic (the v6
  cache_cells_full.json p3 pools) feeds a V4.4 gate.

* Execution-level OR, evaluated identically on targets and benign
  probes: a cell fires M1 iff ANY of its four dimension gates fires;
  the benign side evaluates the SAME execution-level OR on the
  exchangeable in-cell probes, and the fixture-cluster bootstrap (not
  the independent-multiplicity 1-(1-r)^3 model, which is retained as a
  NAMED diagnostic only) carries the dependence.

* Honest specificity objects, three scopes reported SEPARATELY:
  (1) in-cell structural property — the 4th-exchangeable-benign probe
      can never exceed its own recentered threshold (verified
      numerically on every evaluable cell, stated as a property);
  (2) CROSS-FITTURE matched null — the execution-level OR rate on
      LEP-free probe statistics against fixture-disjoint family
      thresholds (the object that can actually bound the false-
      positive behavior of an UNSEEN execution; the target <=5%
      execution-level FPR is read off HERE, not off the circular
      in-cell control);
  (3) semantic dev evidence — FPs on the round-2 reference-NEGATIVE
      LEP rows (an imperfect-label development estimate, never a
      confirmatory claim).

Deterministic: PYTHONHASHSEED pinned by the runners; all pools are
pure functions of the V4.4 ledger.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
FINAL = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(FINAL, '..', '..'))

V44_LEDGER = os.path.join(REPO, 'analysis',
                          'full_production_baseline_deviation_v44',
                          'bnd_v44_per_trace.jsonl')
V43_LEDGER = os.path.join(REPO, 'analysis',
                          'full_production_baseline_deviation_v43',
                          'bnd_v43_per_trace.jsonl')
V6_LEDGER = os.path.join(REPO, 'analysis', 'measurement_v6', 'out',
                         'per_trace_v6.jsonl')
V42_LEDGER = os.path.join(REPO, 'analysis',
                          'full_production_baseline_deviation_v42',
                          'bnd_v42_per_trace.jsonl')

DIMS = ('assertions', 'decisions', 'actions', 'outcomes')
FAMS = ('code_review', 'financial_analysis', 'research_synthesis')

ALPHA = 0.05          # one-sided level for the discrete-support nulls
Q_FIX = 0.98          # cross-fixture pooled threshold quantile (named)
N_FIX_BOOT = 4000     # fixture-cluster bootstrap iterations (named)
SEED_FIX_BOOT = 20270301
MIN_POOL = 30         # family pools smaller than this are NAMED uncalibrated
MIN_CELL_PROBES = 3   # a cell needs all 3 in-cell benign pairs per dim


def _jsonl(p: str) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in open(p) if l.strip()]


def load_ledger(path: str = V44_LEDGER) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in _jsonl(path):
        out[r['trace_id']] = r
    return out


def fixture_of(tid: str) -> str:
    return tid.split('.')[0]


# ── per-cell statistics (identical construction, both sides) ───────────────
def cell_stats(row: Dict[str, Any], d: str
               ) -> Dict[str, Optional[Any]]:
    """Per-dimension cell statistics from the V4.4 ledger, built ONLY from
    recorded per-pair objects (never from reconstructed counts):

      T_d    = mean of the 3 LEP-vs-benign pair component distances
      B_d    = mean of the 3 in-cell benign-pair component distances
      th_d   = max of the 3 in-cell benign-pair component distances
      T_d_max= max of the 3 LEP-vs-benign pair component distances
      k_d    = INTEGER count of LEP-vs-benign pairs with distance > 0
               (the recorded sparse statistic — the anti-dilution and
               discrete-support objects; NOT round(mean * n))

    A dimension is None (uncalibrated, recorded) when T_d, B_d or th_d
    cannot all be computed.  'saturated' flags th_d == 1.0 (the recentered
    excess is then mathematically incapable of firing — NAMED, not
    hidden)."""
    def _comps(rows: List[Dict[str, Any]]) -> List[Optional[float]]:
        return [((p.get('components') or {}).get(d))
                for p in (rows or [])]

    lep_all = [((p.get('components') or {}).get(d))
               for p in (row.get('pair_lep') or [])]
    lep_pairs = [i for i, x in enumerate(lep_all)
                 if x is not None]
    T_vals = [lep_all[i] for i in lep_pairs]
    B_vals = [((p.get('components') or {}).get(d))
              for p in (row.get('pair_benign') or [])]
    B_vals = [x for x in B_vals if x is not None]
    T = (sum(T_vals) / len(T_vals)) if T_vals else None
    B = (sum(B_vals) / len(B_vals)) if B_vals else None
    th = (max(B_vals)) if B_vals else None
    T_max = max(T_vals) if T_vals else None
    k = sum(1 for x in T_vals if x > 0)
    # V4.4 matched-cardinality objects (the investigation's cardinality
    # repair): the 3-reference TARGET statistic (T, T_max, k) is
    # paired with a LEP-free probe statistic at the SAME cardinality
    # when a same-cardinality probe exists; in-cell probes are
    # 2-reference (a cell has 3 benigns: each probe = the mean of the
    # 2 pairs NOT involving the probed member), so the matched target
    # object is the per-reference-pair 2-reference statistic:
    #   k2[pair {i,j}] = 1[D(lep,ri)>0] + 1[D(lep,rj)>0,
    #   T2[pair {i,j}] = (D(lep,ri) + D(lep,rj)) / 2,
    # with the LEP-free probes
    #   k2p[e] = 1[D(re,e)>0] + 1[D(re,rj)>0] over the 2 in-cell
    #     pairs involving the probed member e
    #   T2p[e]  = mean of those 2 in-cell pair distances.
    # The k2/T2 target objects fire per reference pair (OR, named
    # multiplicity); the null pools are LEP-free and fixture-disjoint.
    k2 = {}
    T2 = {}
    for a in range(len(lep_pairs)):
        for b in range(a + 1, len(lep_pairs)):
            i, j = lep_pairs[a], lep_pairs[b]
            key = (i, j)
            k2[key] = int(lep_all[i] > 0) + int(lep_all[j] > 0)
            T2[key] = (lep_all[i] + lep_all[j]) / 2
    ben = [((p.get('components') or {}).get(d))
           for p in (row.get('pair_benign') or [])]
    # in-cell pairs are indexed (0,1),(0,2),(1,2) -> members:
    # pair (i,j) involves refs i and j; the LEP-free probe "execution"
    # k (the remaining member) has distances D(k,i), D(k,j) = the two
    # in-cell pairs that involve k.
    k2p, T2p = {}, {}
    pmap = {(p['a'] if 'a' in p else None, p.get('b')): p.get('components', {}).get(d)
            for p in (row.get('pair_benign') or [])}
    # reconstruct per-member distances from the 3 in-cell pairs:
    # pairs: (0,1)=D01, (0,2)=D02, (1,2)=D12 (ledger order)
    D01 = ((row['pair_benign'][0].get('components') or {}).get(d)
           if len(row.get('pair_benign', [])) > 0 else None)
    D02 = ((row['pair_benign'][1].get('components') or {}).get(d)
           if len(row.get('pair_benign', [])) > 1 else None)
    D12 = ((row['pair_benign'][2].get('components') or {}).get(d)
           if len(row.get('pair_benign', [])) > 2 else None)
    for e, (de, dj) in enumerate(((D01, D02), (D01, D12), (D02, D12))):
        if de is None or dj is None:
            continue
        k2p[e] = int(de > 0) + int(dj > 0)
        T2p[e] = (de + dj) / 2
    return {'T': T, 'B': B, 'th': th, 'T_max': T_max, 'k': k,
            'n_T': len(T_vals), 'n_B': len(B_vals),
            'saturated': bool(th is not None and th >= 1.0 - 1e-12),
            'k2': k2, 'T2': T2, 'k2p': k2p, 'T2p': T2p,
            'lep_vals': [lep_all[i] for i in lep_pairs]}


def excess(row: Dict[str, Any], d: str, stat: str = 'mean'
           ) -> Optional[float]:
    """The recentered excess on dimension d: T_d - B_d (mean form) or
    T_d_max - B_d (anti-dilution max form).  None when either side is
    uncalibrated.  IDENTICAL construction is applied to the LEP row and
    to the in-cell benign probes below — that identity IS the matched
    null."""
    s = cell_stats(row, d)
    if s['T'] is None or s['B'] is None:
        return None
    t = s['T'] if stat == 'mean' else s['T_max']
    if t is None:
        return None
    return t - s['B']


def probe_stats(row: Dict[str, Any], d: str) -> List[Dict[str, Any]]:
    """The three IN-CELL benign probes (each in-cell member treated as the
    exchangeable 4th execution under H0; LEP-free): probe i's statistic =
    the mean of the two in-cell pair distances NOT involving member i, and
    the recorded integer count of those two pairs with distance > 0.  The
    decision procedure is the SAME as the target's; only the source set
    differs (2 refs — the cardinality limit of a 3-benign cell; the
    cross-fixture threshold corrects for it, see pooled_threshold)."""
    B_vals = [((p.get('components') or {}).get(d))
              for p in (row.get('pair_benign') or [])]
    B_vals = [x for x in B_vals if x is not None]
    if len(B_vals) < 3:
        return []
    B = sum(B_vals) / len(B_vals)
    out = []
    for i in range(3):
        others = [x for j, x in enumerate(B_vals) if j != i]
        if len(others) < 2:
            out.append({'T': None, 'T_max': None, 'k': 0, 'B': B})
            continue
        m = sum(others) / len(others)
        out.append({'T': m, 'T_max': max(others),
                    'k': sum(1 for x in others if x > 0), 'B': B})
    return out


# ── fixture-disjoint, single-version pools ──────────────────────────────────
def lep_pair_stats(row: Dict[str, Any], d: str
                   ) -> List[Dict[str, Any]]:
    """The matched-cardinality TARGET objects: one entry per LEP
    reference PAIR {i,j} (i<j over the evaluable LEP references):
      T2 = mean of the two LEP distances in the pair,
      M2 = max of the two LEP distances in the pair,
      k2 = recorded integer count of the two LEP distances > 0.
    These match the LEP-free probe statistic's cardinality (each
    in-cell probe is the mean/max of the 2 in-cell pairs NOT
    involving the probed member — probe_stats's T/T_max/k are the
    same objects LEP-free), so the cross-fixture thresholds built
    from probe pools compare like against like: the V4.3 defect was
    applying 2-reference probe thresholds to 3-reference target
    statistics (a cardinality mismatch)."""
    st = cell_stats(row, d)
    lv = st['lep_vals']
    n = len(lv)
    out = []
    for a in range(n):
        for b in range(a + 1, n):
            out.append({'pair': (a, b),
                        'T2': (lv[a] + lv[b]) / 2,
                        'M2': max(lv[a], lv[b]),
                        'k2': int(lv[a] > 0) + int(lv[b] > 0)})
    return out


def build_pools(led: Dict[str, Dict[str, Any]],
                population: Optional[List[str]] = None
                ) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    """Family pools of LEP-free probe values, built ONCE from the V4.4
    ledger (never from the mixed-version v6 cache).  The pools are keyed
    by (fam, dim, statistic) and carry the fixture of every value so
    thresholds can exclude a whole fixture (sibling rows share the
    reference set and byte-identical probe blobs).  Returned structure:
      pools[fam][d][stat] = [{'v': value, 'fixture': fx}, ...]
    where stat in {'excess_mean_probe', 'excess_max_probe',
                   'k_probe', 'th'} and value is the LEP-free quantity
    named (probe excess = probe statistic - the cell's B_d)."""
    pool_tids = population if population is not None else list(led)
    pools: Dict[str, Dict[str, Dict[str, List[Dict[str, Any]]]]] = {}
    for t in pool_tids:
        row = led.get(t)
        if not row or not row.get('comparable'):
            continue
        fam = row.get('task_family') or 'unknown'
        fx = fixture_of(t)
        ent = pools.setdefault(fam, {d: {} for d in DIMS})
        for d in DIMS:
            s = cell_stats(row, d)
            ent[d].setdefault('th', []).append(
                {'v': s['th'], 'fixture': fx}
                if s['th'] is not None else {'v': None, 'fixture': fx})
            for pr in probe_stats(row, d):
                if pr['T'] is None:
                    continue
                ent[d].setdefault('excess_mean_probe', []).append(
                    {'v': pr['T'] - pr['B'], 'fixture': fx})
                ent[d].setdefault('excess_max_probe', []).append(
                    {'v': (pr['T_max'] or 0.0) - pr['B'], 'fixture': fx})
                ent[d].setdefault('k_probe', []).append(
                    {'v': float(pr['k']), 'fixture': fx})
    for fam in pools:
        for d in pools[fam]:
            for stat in pools[fam][d]:
                pools[fam][d][stat] = sorted(
                    (x for x in pools[fam][d][stat]
                     if x['v'] is not None),
                    key=lambda x: x['v'])
    return pools


def fixture_loo(pool: List[Dict[str, Any]], fixture: str, q: float = Q_FIX
                ) -> Optional[float]:
    """Exact LOO (fixture-disjoint) q-quantile of a pool: EVERY value
    sharing the probe's fixture is excluded (the 1,474 sibling-row blobs
    that cell-level LOO leaked), and remaining ties are handled by rank."""
    kept = [x['v'] for x in pool if x['fixture'] != fixture]
    if len(kept) < MIN_POOL:
        return None
    kept.sort()
    i = int(math.ceil(q * len(kept))) - 1
    i = max(0, min(i, len(kept) - 1))
    return kept[i]


def pooled_threshold(fam: str, d: str, fixture: str, stat: str,
                     pools: Dict[str, Dict[str, Dict[str,
                                                       List[Dict[str,
                                                                Any]]]]]
                     ) -> Optional[float]:
    """The cross-fixture matched threshold for the EXECUTION-level null:
    q98 of the LEP-free probe pool, fixture-disjoint LOO.  None (NAMED
    uncalibrated) when the disjoint pool is too thin — never force a
    value, and a too-thin pool is reported, not hidden."""
    ent = (pools.get(fam) or {}).get(d) or {}
    pool = ent.get(stat) or []
    if not pool:
        return None
    return fixture_loo(pool, fixture)


class Thresholds:
    """Cached fixture-disjoint q98 thresholds: the exact multiset-LOO
    quantile of the LEP-free probe pool with the TARGET's whole fixture
    excluded (sibling LEP rows share a reference set, so cell-level
    LOO leaks their byte-identical probe blobs; excluding the fixture
    removes the leakage).  Pre-computed per (fam, dim, stat, fixture)
    so the 16,767-row evaluation is O(1) per lookup.  A thin disjoint
    pool (fewer than MIN_POOL remaining values) is None — NAMED
    uncalibrated, never forced."""

    def __init__(self, pools):
        self._pools = pools or {}
        # pre-sort each pool; cache keyed (fam, dim, stat, fixture)
        for fam, de in self._pools.items():
            for d, st in de.items():
                for stat_name in st:
                    st[stat_name].sort(key=lambda e: e['v'])
        self._cache: Dict[Tuple, Optional[float]] = {}

    def q(self, fam: str, d: str, stat: str, fixture: str,
          q: float = Q_FIX) -> Optional[float]:
        key = (fam, d, stat, fixture)
        if key in self._cache:
            return self._cache[key]
        ents = (((self._pools.get(fam) or {}).get(d) or {}).get(stat)
                or [])
        res = _multiset_loo(ents, fixture, q, MIN_POOL)
        self._cache[key] = res
        return res


def _multiset_loo(ents: List[Dict[str, Any]], fixture: str,
                  q: float, min_pool: int) -> Optional[float]:
    """Exact leave-one-FIXTURE-OUT q-quantile (cv2._loo_rank
    byte-compatible): every value tagged with the probe's fixture is
    excluded together; remaining ties handled by rank."""
    P = [e['v'] for e in ents]
    N = len(P)
    R = sorted(e['v'] for e in ents if e['fixture'] == fixture)
    rm = len(R)
    if N - rm < min_pool:
        return None
    P_sorted = sorted(P)
    i_target = int(math.ceil(q * (N - rm))) - 1
    i_target = max(0, min(i_target, N - rm - 1))
    import bisect
    Rc = Counter(R)
    for m in range(0, rm + 1):
        j = i_target + m
        if j >= N:
            continue
        v = P_sorted[j]
        pos = bisect.bisect_left(P_sorted, v)
        c = bisect.bisect_right(P_sorted, v) - pos
        r_less = sum(cnt for k, cnt in Rc.items() if k < v)
        r_eq = Rc.get(v, 0)
        if pos - r_less <= i_target < pos - r_less + (c - r_eq):
            return v
    return None


# ── the gate forms (targets and probes score identically) ──────────────────
def gate_fires(row: Dict[str, Any], stat: str, thr: Any = None,
               family_threshold: bool = False) -> Dict[str, Any]:
    """Per-dimension + logical-OR gate.  Two threshold sources, never
    mixed inside one object:

      * SELF-CELL (family_threshold=False): the recentered in-cell form
        T_d - B_d > th_d (mean) or T_d_max - B_d > th_d (max).  The
        matched benign probe is the in-cell 4th exchangeable benign; its
        non-firing under the SAME form is a structural property
        (verified, not assumed).  This is the primary M1/M2 channel.
      * CROSS-FITTURE (family_threshold=True): the probe-pool q98
        (fixture-disjoint LOO) replaces the in-cell th_d — the
        execution-level null object used for the specificity estimate.

    A dimension fires only when BOTH sides are evaluable on the SAME
    key basis (the ledger's per-pair component is None when the strict
    basis voided the pair; a None pair is recorded, never zeroed)."""
    fam_of = row.get('task_family') or 'unknown'
    fx = fixture_of(row['trace_id'])
    fires: Dict[str, Optional[bool]] = {}
    states: Dict[str, str] = {}
    for d in DIMS:
        s = cell_stats(row, d)
        if s['T'] is None or s['B'] is None or s['th'] is None:
            fires[d] = None
            states[d] = 'uncalibrated'
            continue
        if family_threshold:
            statkey = ('excess_max_probe' if stat == 'max'
                       else 'excess_mean_probe')
            th = thr.q(fam_of, d, statkey, fx)
            if th is None:
                fires[d] = None
                states[d] = 'uncalibrated_family_pool'
                continue
            statv = (s['T_max'] - s['B']) if stat == 'max' else (s['T'] - s['B'])
            fires[d] = bool(statv > th)
        else:
            ex = (s['T_max'] - s['B']) if stat == 'max' else (s['T'] - s['B'])
            if ex > s['th']:
                fires[d] = True
            else:
                fires[d] = False
                states[d] = 'saturated' if s['saturated'] else 'below'
    pos = [d for d in DIMS if fires.get(d) is True]
    ev = [d for d in DIMS if fires.get(d) is not None]
    return {'fires': fires, 'union': bool(pos) if ev else None,
            'fired_dims': pos, 'n_eval': len(ev), 'states': states,
            'undetermined': (not ev)}


def probe_or_fires(row: Dict[str, Any], stat: str,
                   thr: Any = None,
                   family_threshold: bool = False) -> Dict[str, Any]:
    """The execution-level OR evaluated on the BENIGN side — each in-cell
    exchangeable probe runs the SAME gate form as the target (matched
    statistic, cardinality, missingness, decision).  Reports the row-OR
    (the pre-registered execution-level object) and, separately, the
    probe-observation count (never conflated with the row-OR).  With a
    self-cell threshold this is 0 by construction (verified, stated as
    a property); with the cross-fixture threshold it is the empirical
    execution-level false-positive rate on an UNSEEN execution."""
    row_or = 0
    obs_eval = obs_fire = 0
    for pr in _all_probes(row, stat, thr, family_threshold):
        obs_eval += 1
        if pr['fire'] is True:
            obs_fire += 1
            row_or = 1
    return {'row_or': bool(row_or), 'obs_eval': obs_eval,
            'obs_fire': obs_fire}


def _all_probes(row: Dict[str, Any], stat: str, thr,
                family_threshold: bool
                ) -> List[Dict[str, Any]]:
    fam = row.get('task_family') or 'unknown'
    fx = fixture_of(row['trace_id'])
    out = []
    for d in DIMS:
        for pr in probe_stats(row, d):
            if pr['T'] is None or pr['B'] is None:
                continue
            if family_threshold:
                statkey = ('excess_max_probe' if stat == 'max'
                           else 'excess_mean_probe')
                th = thr.q(fam, d, statkey, fx)
                if th is None:
                    continue
                ex = (pr['T_max'] - pr['B']) if stat == 'max' \
                    else (pr['T'] - pr['B'])
                out.append({'dim': d, 'fire': bool(ex > th)})
            else:
                s = cell_stats(row, d)
                ex = (pr['T_max'] - pr['B']) if stat == 'max' \
                    else (pr['T'] - pr['B'])
                out.append({'dim': d,
                            'fire': bool(ex > s['th']) if s['th'] is not None
                            else None})
    return out


# ── the discrete-support null (recorded integer counts, no reconstruction) ──
def discrete_support_pools(led: Dict[str, Dict[str, Any]]
                           ) -> Dict[Tuple[str, str], List[float]]:
    """Family pools of the LEP-free INTEGER disagree count over the cell's
    3 IN-CELL benign pairs (the actual 3-of-3 support {0,1,2,3} — the
    matched-cardinality discrete null that replaces the V4.3
    round(mean*n)-then-binomial construct: the TARGET k_d is the count
    of the 3 LEP-vs-benign pairs with distance > 0, so the null pool
    values are counts over the SAME 3-pair cardinality, LEP-free by
    construction — a 2-ref probe count would be a cardinality mismatch).
    A row contributes one pool value per (fam, dim) where all three
    in-cell benign pair distances are evaluable (partial rows are
    named, not coerced)."""
    pools: Dict[Tuple[str, str], List[float]] = {}
    skipped = 0
    for t, row in led.items():
        if not row.get('comparable'):
            continue
        fam = row.get('task_family') or 'unknown'
        ben = row.get('pair_benign') or []
        if len(ben) < 3:
            skipped += 1
            continue
        for d in DIMS:
            vals = [((p.get('components') or {}).get(d)) for p in ben[:3]]
            if any(v is None for v in vals):
                skipped += 1
                continue
            pools.setdefault((fam, d), []).append(
                float(sum(1 for v in vals if v > 0)))
    for k in pools:
        pools[k].sort()
    return pools


def discrete_null_fires(fam: str, d: str, k_target: int,
                        pools: Dict[Tuple[str, str], List[float]],
                        alpha: float = ALPHA) -> Optional[bool]:
    """One-sided empirical null on the recorded integer support:
    P(null >= k_target) on the LEP-free k_probe pool (fixture-disjoint
    LOO is NOT applicable here — the pool values are 2-ref probe counts
    from the SAME cell, LEP-free by construction; the family pool pools
    across cells).  Ties (null == k_target) count in the tail.  None
    when the pool is too thin (NAMED)."""
    P = pools.get((fam, d)) or []
    if len(P) < MIN_POOL:
        return None
    E = sum(1 for v in P if v >= k_target)
    return bool(E / len(P) <= alpha) and k_target > 0


# ── inference ────────────────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = 1.959964) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - m), min(1.0, c + m)]


def fixture_cluster_ci(fixtures: Dict[str, List[float]],
                       n_iter: int = N_FIX_BOOT,
                       seed: int = SEED_FIX_BOOT) -> List[float]:
    """Fixture-cluster bootstrap of an execution-level rate: resample
    WHOLE fixtures (the dependence Astra required accounting for —
    sibling LEP rows share a reference set; the independent-multiplicity
    1-(1-r)^3 model is NOT used, it assumed independence that the
    corpus does not have)."""
    rng = random.Random(seed)
    keys = sorted(fixtures)
    if len(keys) < 5:
        vals = [v for row in fixtures.values() for v in row]
        return ([min(vals), max(vals), sum(vals) / len(vals)]
                if vals else [0.0, 0.0, 0.0])
    ests = []
    for _ in range(n_iter):
        pool: List[float] = []
        for _i in range(len(keys)):
            pool.extend(fixtures[rng.choice(keys)])
        ests.append(sum(pool) / len(pool))
    ests.sort()
    point = (sum(v for row in fixtures.values() for v in row) /
             sum(len(row) for row in fixtures.values()))
    return [ests[int(0.025 * (n_iter - 1))],
            ests[int(0.975 * (n_iter - 1))], point]


# ── corrected signal-availability ceiling (Astra §4) ────────────────────────
def s1_structural(led: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """S1: the in-cell 4th-exchangeable-benign probe can never exceed
    its OWN recentered threshold under the self-cell form (the probe is
    a member of the set whose max IS th_d; B_d >= 0) — verified
    numerically on every evaluable P1 cell and reported as a property
    (0 violations expected; a nonzero count is a machinery bug)."""
    viol = cells = 0
    for row in led.values():
        if not row.get('comparable'):
            continue
        cells += 1
        for d in DIMS:
            s = cell_stats(row, d)
            if s['th'] is None:
                continue
            for pr in probe_stats(row, d):
                if pr['T'] is None:
                    continue
                for statv in (pr['T'], pr['T_max']):
                    if statv is not None and (statv - pr['B']) > s['th']:
                        viol += 1
    return {'n_cells': cells, 'violations': viol,
            'note': 'structural property of the recentered form; '
                    'verified numerically, not assumed'}


def s2_cross_fixture(led: Dict[str, Dict[str, Any]],
                     pools: Dict[str, Dict[str, Dict[str,
                                                      List[Dict[str,
                                                                Any]]]]]
                     ) -> Dict[str, Any]:
    """S2: the execution-level OR on LEP-free in-cell probes scored
    against the FIXTURE-DISJOINT family threshold, over the full P1
    population — the object that can bound the false-positive behavior
    of an UNSEEN execution (the <=5% target is read off here).
    Dependence is accounted for by the fixture-cluster bootstrap CI
    (the independent-multiplicity model is NOT used; it assumed the
    1,474-sibling-row reference reuse away)."""
    thr = Thresholds(pools)
    row_or = 0
    n_cells = 0
    fix: Dict[str, List[float]] = {}
    for row in led.values():
        if not row.get('comparable'):
            continue
        n_cells += 1
        p = probe_or_fires(row, 'mean', thr, family_threshold=True)
        fired = p['row_or']
        row_or += int(fired)
        fix.setdefault(fixture_of(row['trace_id']), []).append(
            1.0 if fired else 0.0)
    rate = row_or / n_cells if n_cells else None
    ci = fixture_cluster_ci(fix) if fix else [None, None, rate]
    return {'n_cells': n_cells, 'row_or_fired': row_or, 'rate': rate,
            'wilson95': wilson(row_or, n_cells) if n_cells else None,
            'fixture_cluster_bootstrap95': ci,
            'n_fixtures': len(fix),
            'note': 'cross-fixture matched null: the same gate form on '
                    'LEP-free probes vs the fixture-disjoint family q98 '
                    'threshold; execution-level OR; dependence by the '
                    'fixture-cluster bootstrap'}


def corrected_ceiling(led: Dict[str, Dict[str, Any]],
                      refs: Dict[str, Dict[str, Any]],
                      dim_fallback_all: bool = True
                      ) -> Dict[str, int]:
    """The signal-availability bounds, computed CORRECTLY (the V4.3
    ceiling routine counted 'positive delta' whenever T and B were not
    both zero, never testing T > B):

      * nonzero:  any ref-dim (all dims when attribution is missing and
        dim_fallback_all) has T_d != 0 or B_d != 0 (signal exists);
      * excess:   any ref-dim has T_d > B_d (positive EXCESS distance —
        the actual gate input);
      * achievable: reported as a SEPARATE operating-point evaluation,
        never as a ceiling (a ceiling is not an operating point)."""
    nz = ex = pos = 0
    for t, ref in refs.items():
        if ref.get('label') != 'diverges':
            continue
        row = led.get(t)
        if row is None or not row.get('comparable'):
            continue
        pos += 1
        refdims = set(d for d in DIMS
                      if (ref.get('per_dim') or {}).get(d) is True)
        scope = refdims or (set(DIMS) if dim_fallback_all else set())
        if not scope:
            continue
        z = e = False
        for d in scope:
            s = cell_stats(row, d)
            if s['T'] is None and s['B'] is None:
                continue
            if s['T'] is not None and (s['T'] != 0.0
                                       or (s['B'] or 0) != 0.0):
                z = True
            if s['T'] is not None and s['B'] is not None and s['T'] > s['B']:
                e = True
        nz += int(z)
        ex += int(e)
    return {'n_ref_positive': pos, 'any_nonzero_distance': nz,
            'any_positive_excess_T_gt_B': ex,
            'note': 'signal-availability BOUNDS, not achievable operating '
                    'points; missing dimensional attribution falls back to '
                    'all dims (named), never to treating the union label '
                    'as wrong'}
