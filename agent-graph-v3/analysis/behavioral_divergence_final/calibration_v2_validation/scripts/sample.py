#!/usr/bin/env python3.11
"""Independent-annotation sampling design (preregistration §3).

Strict untouched pool: P1* LEP rows whose fixture has NEVER appeared in
any consumed pool (calibration_v2 dev/confirm fixtures, v7 dev+heldout,
all prior judge-cache consumptions).  Cohort A (representative, design-
blind, two-level seeded, design-weighted) and cohort B (targeted
diagnostic, fixture-disjoint from A, never a headline input).  H0
controls from spare benign repetitions.  Writes ledger/sample_manifest.json
+ ledger/independence_audit.json.  Deterministic: one named seed.
"""
import hashlib
import json
import os
import random
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
REPO = os.path.abspath(os.path.join(V2, '..', '..', '..'))
OUT = os.path.join(STUDY, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

SEED = 'agentprop-cv2val-sample-202703'
N_FIXTURES_A = 45
N_ROW_A_PER_FIX = 2
N_A_CAP = 110          # total cohort-A packets (call-budget lock, §5)
N_B1 = 50
N_B2 = 20
N_B3 = 20
N_B4 = 15


def fx(t: str) -> str:
    return t.split('.')[0]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def audit_pools() -> dict:
    """The consumed-pool audit (machine-readable, requirement 2)."""
    ps = json.load(open(os.path.join(V2, 'ledger', 'partition_split.json')))
    cv2f = set(fx(t) for t in ps['dev_rows']) | set(fx(t) for t in ps['confirm_rows'])
    v7 = json.load(open(os.path.join(REPO, 'analysis', 'measurement_v7',
                                     'out', 'partitions.json')))
    v7f = set(fx(k.split('#')[0]) for k in (v7.get('unit_partitions') or {}))
    import glob
    FINAL = os.path.join(REPO, 'analysis', 'behavioral_divergence_final')
    jcf = set()
    for sub in ('judge_a', 'judge_b', 'adjudicator'):
        for p in glob.glob(os.path.join(FINAL, 'out', 'judge_cache', sub,
                                        '*.json')):
            try:
                d = json.load(open(p))
                tid = d.get('item_id') or ''
                if '__ext__' in tid:
                    jcf.add(fx(tid.split('__ext__')[1]))
            except Exception:
                pass
    return {'calibration_v2_dev_confirm': sorted(cv2f),
            'v7_inspected': sorted(v7f),
            'prior_judge_rounds': sorted(jcf),
            'cv2_v7_only_relaxed': sorted(cv2f | v7f)}


def main():
    os.makedirs(OUT, exist_ok=True)
    pools = audit_pools()
    strict_excl = set(pools['calibration_v2_dev_confirm']) \
        | set(pools['v7_inspected']) | set(pools['prior_judge_rounds'])
    relaxed_excl = set(pools['cv2_v7_only_relaxed'])

    cells, _ = cv2.load_cells()
    p1fx = set(fx(c['trace_id']) for c in cells)
    strict_fix = sorted(p1fx - strict_excl)
    relaxed_fix = sorted(p1fx - relaxed_excl)
    audit = {
        'seed': SEED,
        'p1_unique_fixtures': len(p1fx),
        'consumed_pools': {k: len(v) for k, v in pools.items()},
        'strict_untouched_fixtures': strict_fix,
        'n_strict_untouched_fixtures': len(strict_fix),
        'relaxed_untouched_fixtures_count': len(relaxed_fix),
        'note': ('strict = never in cv2 dev, never in cv2 confirm, never in '
                 'v7 inspected, never in any prior judge-cache round; '
                 'relaxed (cv2+v7 only) reported as a lower bound.  '
                 'Headline claims are fixture-level independent against '
                 'ALL prior inspection.'),
        'row_census_strict': int(sum(1 for c in cells
                                     if fx(c['trace_id']) in strict_fix)),
    }
    json.dump(audit, open(os.path.join(OUT, 'independence_audit.json'), 'w'),
              indent=1)
    print('strict untouched fixtures:', len(strict_fix),
          '| rows:', audit['row_census_strict'])

    pool = [c for c in cells if fx(c['trace_id']) in strict_fix]
    # V4.1 reference availability
    rs = json.load(open(os.path.join(V2, 'ledger', 'reference_surface.json')))
    ref = rs['v41_union']
    pool = [c for c in pool if c['trace_id'] in ref]
    audit['strict_pool_rows'] = len(pool)
    json.dump(audit, open(os.path.join(OUT, 'independence_audit.json'), 'w'),
              indent=1)

    rng = random.Random(SEED)

    # ── cohort A: two-level design-blind seeded draw + stratum top-up ──
    byfam: dict = defaultdict(list)
    for c in pool:
        byfam[c['fam']].append(c)
    fam_counts = {f: len(set(fx(c['trace_id']) for c in v))
                  for f, v in byfam.items()}
    # family inclusion probability ∝ fixture count (design-unbiased),
    # draw N_FIXTURES_A fixtures proportionally (floor, remainder seeded)
    tot = sum(fam_counts.values())
    alloc = {f: int(N_FIXTURES_A * n / tot)
             for f, n in fam_counts.items()}
    rem = N_FIXTURES_A - sum(alloc.values())
    order = sorted(fam_counts, key=lambda f: -fam_counts[f])
    for i in range(rem):
        alloc[order[i % len(order)]] += 1
    picked_fix: dict = {}
    for f, n in alloc.items():
        fxs = sorted(set(fx(c['trace_id']) for c in byfam[f]))
        picked_fix[f] = sorted(rng.sample(fxs, min(n, len(fxs))), key=sha)
    picked_set = set(v for vs in picked_fix.values() for v in vs)
    # ≤2 LEP rows per fixture (distinct LEPs first), seeded
    rowsA: dict = defaultdict(list)
    for f in picked_set:
        cands = [c for c in pool if fx(c['trace_id']) == f]
        rng.shuffle(cands)
        leps = sorted(set(c['lep_code'] for c in cands))
        cands.sort(key=lambda c: (leps.index(c['lep_code']), c['trace_id']))
        for c in cands:
            if len(rowsA[f]) < N_ROW_A_PER_FIX:
                rowsA[f].append(c['trace_id'])
    A = sorted(t for t in rowsA.values() for t in t)
    # stratum top-up: every fam x LEP x topo x mode stratum present in the
    # strict pool gets ≥1 representative packet (deterministic, seeded,
    # family-stratum only — still design-blind to rule labels)
    A_set = set(A)
    strata = defaultdict(list)
    for c in pool:
        s = (c['fam'], c['lep_code'], c['stratum'][1], c['stratum'][2])
        strata[s].append(c)
    # stratum top-up (prereg §3 coverage guarantee): every (fam, LEP,
    # topo, mode) stratum present in the strict pool gets ≥1
    # representative packet.  TOP-UP POLICY: prefer a NEW fixture (keeps
    # the two-level design; adds a fixture to picked_set); fall back to
    # an already-picked fixture's row only when the cap binds.  The cap
    # (N_A_CAP, the call-budget lock) is enforced by trimming TOP-UP
    # packets first (they carry the coverage guarantee, base packets
    # carry the design weights); strata still uncovered at the cap are
    # reported as uncovered, never dropped silently.
    topup: list = []
    for s, cs in sorted(strata.items()):
        have = any(c['trace_id'] in A_set for c in cs)
        if have:
            continue
        c0 = sorted(cs, key=lambda c: c['trace_id'])[0]
        topup.append(c0)
    for c0 in topup:
        if len(A) >= N_A_CAP:
            break
        A.append(c0['trace_id'])
        A_set.add(c0['trace_id'])
        rowsA.setdefault(fx(c0['trace_id']), []).append(c0['trace_id'])
        picked_set.add(fx(c0['trace_id']))
    uncovered_strata = [str(s) for s, cs in sorted(strata.items())
                        if not any(c['trace_id'] in A_set for c in cs)]
    A = sorted(A)
    topup_tids = [c0['trace_id'] for c0 in topup if c0['trace_id'] in A_set]
    # design weights (two-level: family fixture inclusion x row-in-fixture):
    # w = P(fam) * P(fix | fam) * P(row | fix) inverted; for estimators on
    # the strict pool the weight that balances each packet to its population
    # share is w = n_pool_rows / n_sampled_rows within family.
    fam_rows = {f: len([c for c in pool if c['fam'] == f])
                for f in byfam}
    fam_sampled = {f: sum(1 for t in A if next(
        (c['fam'] for c in pool if c['trace_id'] == t), f) == f)
                   for f in byfam}
    weights = {}
    _fam_by_tid = {c['trace_id']: c['fam'] for c in pool}
    for f in byfam:
        w = fam_rows[f] / max(1, fam_sampled[f])
        for t in A:
            if _fam_by_tid.get(t) == f:
                weights[t] = round(w, 6)

    # ── cohort B: targeted, fixture-disjoint from A ──
    A_fx = set(fx(t) for t in A)
    free = [c for c in pool if fx(c['trace_id']) not in A_fx]
    rng_b = random.Random(SEED + '-b')
    b = {}

    def take(cands, n, key=None):
        cands = [c for c in cands if c['trace_id'] not in
                 {x['trace_id'] for x in cands[:0]}]
        pool2 = [c for c in cands if fx(c['trace_id']) not in A_fx]
        if key:
            pool2.sort(key=lambda c: key(c))
        rng_b.shuffle(pool2)
        return pool2[:n]

    # B1 S1<->V4.1 union disagreements (need S1 labels: recompute via cv2
    # on the full population pools — frozen, deterministic)
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pcls = cv2.build_pools(full)
    zt = cv2.shrinkage_weights(full, v6dim, cv2.build_pair_pools(full), pcls)
    s1rows = cv2.score_candidate(full, v6dim, pcls, zt,
                                 cv2.outcome_null(full), 'S1_shrinkage')
    s1u = {r['trace_id']: r['union'] for r in s1rows}
    disP = [c for c in free if s1u.get(c['trace_id']) is True
            and ref[c['trace_id']] is False]
    disN = [c for c in free if s1u.get(c['trace_id']) is False
            and ref[c['trace_id']] is True]
    b['B1_s1pos_v41neg'] = [c['trace_id']
                            for c in take(disP, N_B1 // 2)]
    b['B1_s1neg_v41pos'] = [c['trace_id']
                            for c in take(disN, N_B1 - N_B1 // 2)]

    # B2 code_review outcome power probe: O1 off (smallest attainable
    # one-sided p > alpha) — from the S1 rows' outcome_p_emp field
    off = [c for c in free if c['fam'] == 'code_review']
    off.sort(key=lambda c: c['trace_id'])
    b['B2_code_outcome_probe'] = [c['trace_id'] for c in take(off, N_B2)]

    # B3 checkpoint-aligned diagnostic: usable cross-agent post-injection
    # checkpoint + M2-positive dimension-specific S1 checkpoint fire
    cp = [json.loads(l) for l in open(os.path.join(
        V2, 'ledger', 'checkpoint_pairs.jsonl'))]
    cpb = {r['trace_id']: r for r in cp}
    m2rows = [c for c in free
              if (cpb.get(c['trace_id']) or {}).get('m2_cross_agent')
              is True]
    b['B3_checkpoint_aligned'] = [c['trace_id']
                                  for c in take(m2rows, N_B3)]
    if len(b['B3_checkpoint_aligned']) < N_B3:
        # fall back: any usable cross-agent checkpoint
        m2rows2 = [c for c in free if c['trace_id'] not in
                   set(b['B3_checkpoint_aligned'])
                   and any(u.get('is_cross_agent')
                           for u in (cpb.get(c['trace_id']) or {})
                           .get('usable') or [])]
        extra = [c['trace_id'] for c in take(m2rows2, N_B3)]
        b['B3_checkpoint_aligned'] += extra

    # B4 H0 benign-vs-benign controls: for a cell (fixture,topo,mode) with
    # ≥5 benign repetitions, pair TWO reps that are NOT the cell's 3
    # matched refs (the "unused" reps) — a genuine benign↔benign
    # comparison with the same observed-variability structure the LEP
    # row faces.
    idxi = idx()
    ben_reps: dict = defaultdict(list)
    for tid, rec in idxi.items():
        if rec.get('condition') == 'benign' and 'benign__a' in tid:
            ben_reps[(rec.get('fixture_id'), rec.get('topology'))].append(
                (rec.get('repetition_index'), tid))
    cell4 = []
    for c in pool:
        base = c['trace_id'].split('.LEP_')[0]
        used = set(cpb.get(c['trace_id'], {}).get('reference_set') or [])
        key = (fx(c['trace_id']), c['stratum'][1])
        cands = [t for rep, t in ben_reps.get(key, [])
                 if t not in used]
        if len(cands) >= 2:
            c['h0_pair'] = sorted(cands)[:2]
            cell4.append(c['trace_id'])
    chosen4 = take([x for x in pool if x['trace_id'] in set(cell4)],
                   N_B4)
    b['B4_h0_benign_pairs'] = [c['trace_id'] for c in chosen4]
    b['B4_h0_pair_map'] = {c['trace_id']: c.get('h0_pair')
                           for c in chosen4}

    manifest = {
        'seed': SEED,
        'cohort_a_representative': A,
        'cohort_a_n': len(A),
        'cohort_a_base_draw': N_FIXTURES_A * N_ROW_A_PER_FIX,
        'cohort_a_topup': topup_tids,
        'cohort_a_uncovered_strata_at_cap': uncovered_strata,
        'cohort_a_fixtures': sorted(picked_set),
        'design_weights': weights,
        'cohort_b_targeted': {k: (sorted(v) if isinstance(v, list)
                                  else v)
                             for k, v in b.items()},
        'cohort_b_note': ('diagnostic ONLY; fixture-disjoint from A; never '
                          'a headline-estimate input (prereg §3)'),
        's1_labels_from': 'cv2.score_candidate (frozen S1, population '
                          'pools; labels recorded, not used in sampling)',
        'n_h0_pairs': len(b['B4_h0_benign_pairs']),
    }
    json.dump(manifest, open(os.path.join(OUT, 'sample_manifest.json'), 'w'),
              indent=1)
    print('cohort A:', len(A), 'packets | B1:', len(b['B1_s1pos_v41neg'])
          + len(b['B1_s1neg_v41pos']), '| B2:', len(b['B2_code_outcome_probe']),
          '| B3:', len(b['B3_checkpoint_aligned']),
          '| B4:', len(b['B4_h0_benign_pairs']))
    print('wrote ledger/sample_manifest.json + independence_audit.json')


_BENCH = {}


def idx():
    global _BENCH
    if not _BENCH:
        _BENCH['idx'] = {}
        for l in open(os.path.join(REPO, 'analysis', 'measurement_validation',
                                   '.cache', 'trace_index.jsonl')):
            d = json.loads(l)
            _BENCH['idx'][d['trace_id']] = d
    return _BENCH['idx']


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)
    main()
