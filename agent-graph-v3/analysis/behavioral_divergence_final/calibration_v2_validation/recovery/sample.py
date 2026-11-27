#!/usr/bin/env python3.11
"""CORRECTED stratified representative sampler (recovery).

Fixes addendum A2: the original sample.py selected a fixture's LEP rows
with `leps = sorted(set(...)); cands.sort(key=(leps.index(lep), trace_id))`
— lexicographic LEP order + trace_id tiebreak — so the earlier LEP
code (HANDOFF_CORRUPTION) always won and cohort A became 86% HANDOFF
vs the eligible population's 25%.

This sampler is a clean two-level stratified SRS:
  * PRIMARY stratum = LEP (4 strata, equal quota -> exactly 25% each
    by construction; this is the bug fix and is asserted by preflight
    + regression).
  * sub-allocation within a LEP stratum = family-proportional, so the
    task-family mix matches the eligible population.
  * topology is REPORTED (coverage check) but not a hard stratification
    layer, keeping the per-row inclusion probability uniform-within-
    cell and therefore known and auditable.
  * per-row inclusion probability pi(r) = q_cell / n_cell (constant
    within each (LEP, family) cell) and design weight w(r) = 1/pi(r).
  * fresh-only: the eligible pool is the strict-untouched pool minus
    every fixture inspected in round 1 (A + B), so the confirmatory
    cohort is fixture-level independent of all prior annotation.

Deterministic: one named seed. Writes CORRECTED_SAMPLE_MANIFEST.jsonl
(one line per selected row, with pi + weight + stratum + fixture) and
a machine-readable sampling record.  No LLM calls.
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
sys.path.insert(0, V2)
import cv2  # noqa: E402

SEED = 'agentprop-cv2val-recovery-r2-202703'
# primary-stratum quota per LEP (total representative draw)
PER_LEP = 60
LEPS = ('LEP_HANDOFF_CORRUPTION', 'LEP_INDIRECT_PROMPT_INJECTION',
        'LEP_INPUT_DISREGARD', 'LEP_TOOL_RESULT_CORRUPTION')
FAMS = ('code_review', 'financial_analysis', 'research_synthesis')
# diagnostic targeted block (B2) sizes — drawn on fresh fixtures too
B2_SIZE = 50


def fx(t):
    return t.split('.')[0]


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def eligible_pool():
    """strict-untouched pool (V4.1-ref available) minus every fixture
    inspected in round 1 (A + B) -> fixture-level independent."""
    aud = json.load(open(os.path.join(STUDY, 'ledger',
                                     'independence_audit.json')))
    strict = set(aud['strict_untouched_fixtures'])
    man = json.load(open(os.path.join(STUDY, 'ledger',
                                     'sample_manifest.json')))
    used_fx = set(fx(t) for t in man['cohort_a_representative'])
    for k, v in man['cohort_b_targeted'].items():
        if isinstance(v, list):
            used_fx |= set(fx(t) for t in v)
    rs = json.load(open(os.path.join(V2, 'ledger',
                                     'reference_surface.json')))
    ref = set(rs['v41_union'])
    cells, _ = cv2.load_cells()
    pool = [c for c in cells if fx(c['trace_id']) in strict
            and c['trace_id'] in ref]
    fresh = [c for c in pool if fx(c['trace_id']) not in used_fx]
    return fresh, used_fx


def _family_quota(n_cell, total_quota):
    """family-proportional sub-allocation of total_quota across the
    family cells present in one LEP stratum (largest-remainder)."""
    if not n_cell:
        return {}
    grand = sum(n_cell.values())
    raw = {f: total_quota * n / grand for f, n in n_cell.items()}
    q = {f: int(raw[f]) for f in raw}
    rem = total_quota - sum(q.values())
    # distribute the remainder by largest fractional part, seeded order
    fracs = sorted(raw, key=lambda f: (-(raw[f] - int(raw[f])), sha(f)))
    for i in range(rem):
        q[fracs[i % len(fracs)]] += 1
    # no cell can demand more than it has
    for f in q:
        q[f] = min(q[f], n_cell[f])
    return q


def draw_representative(pool, seed=SEED):
    rng = random.Random(seed)
    # (LEP, family) cell census
    cells = defaultdict(lambda: defaultdict(list))
    for c in pool:
        cells[c['lep_code']][c['fam']].append(c)
    selected = []
    for lep in LEPS:
        famcensus = cells.get(lep, defaultdict(list))
        n_cell = {f: len(famcensus.get(f, [])) for f in FAMS
                  if famcensus.get(f)}
        quota = _family_quota(n_cell, PER_LEP)
        for f, q in sorted(quota.items()):
            cands = sorted(famcensus.get(f, []), key=lambda c: sha(
                c['trace_id']))
            rng.shuffle(cands)
            for c in cands[:q]:
                pi = q / n_cell[f]
                selected.append({
                    'trace_id': c['trace_id'],
                    'fixture': fx(c['trace_id']),
                    'lep_code': c['lep_code'],
                    'fam': c['fam'],
                    'topology': c['stratum'][1],
                    'mode': c['stratum'][2],
                    'cohort': 'representative',
                    'pi': round(pi, 6),
                    'weight': round(1.0 / pi, 6),
                    'n_cell': n_cell[f],
                    'q_cell': q,
                })
    return selected


H0_PER_LEP = 3


def draw_h0(pool, seed=SEED + '-h0'):
    """H0 benign-vs-benign null-control block: fresh cells with >=5
    benign repetitions; the H0 pair = two UNUSED reps (not in the
    cell's 3 matched refs) -> a genuine benign<->benign comparison
    with the same p3 exchangeable structure.  3 per LEP.  No design
    weight (control, not a headline stratum)."""
    import os
    REPO = os.path.abspath(os.path.join(V2, '..', '..', '..'))
    idx = {}
    ip = os.path.join(REPO, 'analysis', 'measurement_validation',
                      '.cache', 'trace_index.jsonl')
    if os.path.exists(ip):
        pass
        for l in open(ip):
            d = json.loads(l)
            idx[d['trace_id']] = d
    cp = {}
    cp_p = os.path.join(V2, 'ledger', 'checkpoint_pairs.jsonl')
    if os.path.exists(cp_p):
        for l in open(cp_p):
            r = json.loads(l)
            cp[r['trace_id']] = r
    from collections import defaultdict
    ben = defaultdict(list)
    for tid, rec in idx.items():
        if rec.get('condition') == 'benign' and 'benign__a' in tid:
            ben[(rec.get('fixture_id'), rec.get('topology'))].append(
                (rec.get('repetition_index'), tid))
    out = []
    for lep in LEPS:
        cands = []
        for c in pool:
            if c['lep_code'] != lep:
                continue
            key = (fx(c['trace_id']), c['stratum'][1])
            refs = set(cp.get(c['trace_id'], {}).get('reference_set')
                       or [])
            unused = [t for rep, t in ben.get(key, [])
                      if t not in refs]
            if len(unused) >= 2:
                cands.append((c, sorted(unused)[:2]))
        cands.sort(key=lambda x: hashlib.sha256(
            x[0]['trace_id'].encode()).hexdigest())
        for c, pair in cands[:H0_PER_LEP]:
            out.append({'trace_id': c['trace_id'],
                        'fixture': fx(c['trace_id']),
                        'lep_code': c['lep_code'], 'fam': c['fam'],
                        'topology': c['stratum'][1],
                        'mode': c['stratum'][2],
                        'cohort': 'h0_benign_control', 'pi': None,
                        'weight': None,
                        'h0_pair': pair,
                        'note': 'benign-vs-benign null control: two '
                                'UNUSED benign reps (>=5-rep cell); '
                                'never a gate object (C4 is the '
                                'deterministic B2B control)'})
    return out


def draw_diagnostic(pool, seed=SEED + '-b', n=B2_SIZE):
    """targeted S1<->V4.1-union-disagreement block on fresh fixtures:
    recompute S1 vs V4.1 on the full population (frozen, deterministic)
    and sample union-disagreement rows, family-balanced.  Diagnostic
    only — never a headline input."""
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    zt = cv2.shrinkage_weights(full, v6dim, cv2.build_pair_pools(full),
                               pools)
    s1 = {r['trace_id']: r['union']
          for r in cv2.score_candidate(full, v6dim, pools, zt,
                                       cv2.outcome_null(full),
                                       'S1_shrinkage')}
    rs = json.load(open(os.path.join(V2, 'ledger',
                                     'reference_surface.json')))
    v41 = rs['v41_union']
    dis = [c for c in pool
           if s1.get(c['trace_id']) is not None
           and c['trace_id'] in v41
           and bool(s1[c['trace_id']]) != bool(v41[c['trace_id']])]
    rng = random.Random(seed)
    rng.shuffle(dis)
    out = []
    for c in dis[:n]:
        out.append({'trace_id': c['trace_id'], 'fixture': fx(c['trace_id']),
                    'lep_code': c['lep_code'], 'fam': c['fam'],
                    'topology': c['stratum'][1], 'mode': c['stratum'][2],
                    'cohort': 'diagnostic_s1v41', 'pi': None,
                    'weight': None, 'note': 'targeted diagnostic (no '
                    'design weight; never a headline input)'})
    return out


def main():
    pool, used_fx = eligible_pool()
    print(f'eligible fresh pool: {len(pool)} rows / '
          f'{len(set(fx(c["trace_id"]) for c in pool))} fixtures '
          f'(round-1 fixtures excluded: {len(used_fx)})')
    A = draw_representative(pool)
    A_fx = set(a['fixture'] for a in A)
    A_ids = {a['trace_id'] for a in A}
    B = [b for b in draw_diagnostic(pool)
         if b['trace_id'] not in A_ids]
    H = [h for h in draw_h0(pool)
         if h['trace_id'] not in A_ids and h['trace_id'] not in
         {b['trace_id'] for b in B}]
    manifest = [a for a in A] + B + H
    # write the JSONL manifest
    with open(os.path.join(HERE, 'CORRECTED_SAMPLE_MANIFEST.jsonl'),
              'w') as fh:
        for row in manifest:
            fh.write(json.dumps(row, sort_keys=True) + '\n')
    # machine record (preflight + plan read this)
    from collections import Counter
    rec = {
        'seed': SEED,
        'per_lep_quota': PER_LEP,
        'n_representative': len(A),
        'n_diagnostic': len(B),
        'n_h0_control': len(H),
        'lep_mix': dict(Counter(a['lep_code'] for a in A)),
        'fam_mix': dict(Counter(a['fam'] for a in A)),
        'topo_mix': dict(Counter(a['topology'] for a in A)),
        'n_rep_fixtures': len(A_fx),
        'round1_fixture_overlap': sorted(A_fx & used_fx),
        'pool_rows': len(pool),
        'pool_fixtures': len(set(fx(c['trace_id']) for c in pool)),
        'manifest_rows': len(manifest),
        'h0_per_lep': H0_PER_LEP,
        'inclusion_probability_model': ('pi(r) = q_(LEP,fam) / '
            'n_(LEP,fam), uniform within each (LEP, family) cell; '
            'weight = 1/pi.  LEP strata equal-quota -> exactly 25% '
            'each (the addendum-A2 fix).  Diagnostic rows carry pi='
            'None (targeted, not design-weighted).'),
        'note': ('representative cohort is fixture-level independent '
                 'of all round-1 annotation; per-row pi + weight '
                 'recorded for design-weighted / fixture-cluster CI.'),
    }
    json.dump(rec, open(os.path.join(HERE, 'sampling_record.json'),
                        'w'), indent=1)
    print('representative:', len(A), '| diagnostic:', len(B),
          '| H0 control:', len(H), '| manifest rows:', len(manifest))
    print('LEP mix (representative):', rec['lep_mix'])
    print('family mix:', rec['fam_mix'])
    print('topology mix:', rec['topo_mix'])
    print('wrote CORRECTED_SAMPLE_MANIFEST.jsonl + sampling_record.json')


if __name__ == '__main__':
    main()
