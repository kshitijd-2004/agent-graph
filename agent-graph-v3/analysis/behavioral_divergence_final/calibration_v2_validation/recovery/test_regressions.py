#!/usr/bin/env python3.11
"""Regression tests for the corrected sampler (recovery, requirement 4).

The PRIMARY guard is lexicographic-LEP selection: the original
sample.py picked a fixture's rows with `leps = sorted(set(lep_codes));
cands.sort(key=(leps.index(lep), trace_id))`, i.e. lexicographic LEP
order (HANDOFF < INDIRECT < INPUT < TOOL_RESULT) + trace_id tiebreak,
so the earlier LEP code always won a fixture's row slots -> 86%
HANDOFF.  T1-T3 pin that the corrected sampler does NOT do this; T4-T8
pin the rest of the manifest contract.  Deterministic; no LLM calls;
exit 0 iff all pass.

Run: PYTHONHASHSEED=0 python3.11 test_regressions.py
"""
import glob
import json
import os
import random
import sys
import hashlib
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.path.abspath(os.path.join(HERE, '..'))
V2 = os.path.abspath(os.path.join(STUDY, '..', 'calibration_v2'))
sys.path.insert(0, V2)
sys.path.insert(0, HERE)
import cv2  # noqa: E402
import sample  # noqa: E402

LEPS = sample.LEPS
RESULTS = []


def check(name, cond, msg=''):
    RESULTS.append((name, bool(cond), msg))
    print(('PASS ' if cond else 'FAIL ') + name
          + (f'  -> {msg}' if not cond else ''))


def fx(t):
    return t.split('.')[0]


def load_manifest():
    p = os.path.join(HERE, 'CORRECTED_SAMPLE_MANIFEST.jsonl')
    if not os.path.exists(p):
        sys.exit('CORRECTED_SAMPLE_MANIFEST.jsonl missing — run sample.py')
    return [json.loads(l) for l in open(p) if l.strip()]


def T1_lep_equal_quota(man):
    rep = [r for r in man if r['cohort'] == 'representative']
    got = Counter(r['lep_code'] for r in rep)
    n = sum(got.values())
    check('T1a: LEP mix exactly equal-quota (60/60/60/60)',
          all(got.get(L, 0) == sample.PER_LEP for L in LEPS),
          f'got {dict(got)}')
    fr = {L: round(got.get(L, 0) / n, 4) for L in LEPS}
    check('T1b: no LEP outside +/-0.02 of 25%',
          all(abs(got.get(L, 0) / n - 0.25) <= 0.02 for L in LEPS),
          f'fracs {fr}')


def T2_lexicographic_trap(man):
    """The bug is not 'which LEPs appear' but the ROW-SLOT allocation
    within a fixture: lexicographic order lets HANDOFF fill every
    fixture's slots first.  Guard: in the corrected draw, within each
    fixture the rows span MULTIPLE LEPs whenever the fixture's pool
    supports it, AND the per-LEP allocation is quota-driven (not
    lexicographic-first).  Concretely: if the corrected sampler were
    still lexicographic, the HANDOFF share of the sample would track
    the fixture-level HANDOFF-availability order, not the 0.25
    quota.  Assert per-fixture LEP diversity > 1 for the majority of
    fixtures (a lexicographic 2-slot fill would give HANDOFF-dominant
    1-or-2 LEP fixtures)."""
    pool, _ = sample.eligible_pool()
    byfx = defaultdict(set)
    for c in pool:
        byfx[fx(c['trace_id'])].add(c['lep_code'])
    rep = [r for r in man if r['cohort'] == 'representative']
    perfx = defaultdict(Counter)
    for r in rep:
        perfx[r['fixture']][r['lep_code']] += 1
    multi_lep_fixtures = 0
    for f, cnt in perfx.items():
        avail = len(byfx.get(f, set()))
        if avail >= 2 and len(cnt) >= 2:
            multi_lep_fixtures += 1
    nfix = len(perfx)
    check('T2a: corrected draw is LEP-diverse within fixtures '
          f'({multi_lep_fixtures}/{nfix} multi-LEP fixtures; a '
          f'lexicographic slot-fill would be HANDOFF-dominated)',
          multi_lep_fixtures / max(1, nfix) > 0.5,
          f'{multi_lep_fixtures}/{nfix}')
    # T2b: the buggy selector, run on the same pool+seed, MUST skew
    pool_cells = [c for c in pool]
    buggy = _run_buggy_selector(pool_cells, sample.SEED)
    bf = buggy.get('LEP_HANDOFF_CORRUPTION', 0) / max(
        1, sum(buggy.values()))
    check('T2b: the original lexicographic selector re-reproduces the '
          f'HANDOFF skew ({bf:.2f} > 0.40) — the defect is real',
          bf > 0.40, f'buggy HANDOFF frac {bf:.4f}')
    # T2c: corrected sampler does NOT match the buggy mix
    got = Counter(r['lep_code'] for r in rep)
    cf = got.get('LEP_HANDOFF_CORRUPTION', 0) / sum(got.values())
    check('T2c: corrected HANDOFF frac ~0.25 (not the buggy mix)',
          abs(cf - 0.25) <= 0.02, 'corrected %.4f' % cf)


def _run_buggy_selector(pool_cells, seed):
    """Faithful re-implementation of the ORIGINAL sample.py row
    selection (addendum A2's defect): family-proportional 45-fixture
    draw, 2 rows/fixture, lexicographic LEP slot order + trace_id
    tiebreak."""
    byfam = defaultdict(list)
    for c in pool_cells:
        byfam[c['fam']].append(c)
    fam_counts = {f: len(set(fx(c['trace_id']) for c in byfam[f]))
                  for f in byfam}
    n_fix = min(45, sum(fam_counts.values()))
    tot = sum(fam_counts.values())
    alloc = {f: int(n_fix * n / tot) for f, n in fam_counts.items()}
    rng = random.Random(seed)
    picked = set()
    for f in sorted(alloc):
        fxs = sorted(set(fx(c['trace_id']) for c in byfam[f]))
        picked |= set(rng.sample(fxs, min(alloc[f], len(fxs))))
    by_fx = defaultdict(list)
    for c in pool_cells:
        by_fx[fx(c['trace_id'])].append(c)
    buggy = Counter()
    for f in sorted(picked):
        cands = by_fx[f]
        leps = sorted(set(c['lep_code'] for c in cands))
        cands = sorted(cands, key=lambda c: (leps.index(c['lep_code']),
                                             c['trace_id']))
        for c in cands[:2]:
            buggy[c['lep_code']] += 1
    return buggy


def T3_independence(man):
    _, used_fx = sample.eligible_pool()
    sfx = set(r['fixture'] for r in man)
    check('T3a: zero round-1 fixture overlap', not (sfx & used_fx),
          f'overlap {sorted(sfx & used_fx)[:3]}')
    dups = [t for t, c in Counter(r['trace_id']
                                  for r in man).items() if c > 1]
    check('T3b: no duplicate trace_id in the manifest', not dups,
          f'dups {dups[:3]}')
    rep = {r['trace_id'] for r in man if r['cohort'] ==
           'representative'}
    dia = {r['trace_id'] for r in man if r['cohort'] !=
           'representative'}
    check('T3c: representative and diagnostic cohorts row-disjoint',
          not (rep & dia))


def T4_weights_and_pi(man):
    bad = [r for r in man if r['cohort'] == 'representative'
           and (r['pi'] is None or r['weight'] is None
                or abs(r['weight'] - 1.0 / r['pi']) > 1e-3)]
    check('T4a: weight == 1/pi for every representative row',
          not bad, f'{len(bad)} bad rows')
    dia = [r for r in man if r['cohort'] != 'representative']
    check('T4b: diagnostic rows carry NO design weight',
          all(r['pi'] is None and r['weight'] is None for r in dia),
          f'{sum(1 for r in dia if r["pi"])} weighted diagnostic rows')


def T5_determinism():
    pool, _ = sample.eligible_pool()
    a = sample.draw_representative(pool, sample.SEED)
    b = sample.draw_representative(pool, sample.SEED)
    ha = hashlib.sha256(json.dumps(
        sorted(a, key=lambda r: r['trace_id']), sort_keys=True).
        encode()).hexdigest()
    hb = hashlib.sha256(json.dumps(
        sorted(b, key=lambda r: r['trace_id']), sort_keys=True).
        encode()).hexdigest()
    check('T5: re-draw on the same seed is byte-identical', ha == hb,
          'sampler is not deterministic')


def T6_schema(man):
    req_rep = {'trace_id', 'fixture', 'lep_code', 'fam', 'topology',
               'mode', 'cohort', 'pi', 'weight', 'n_cell', 'q_cell'}
    bad = [r for r in man if r['cohort'] == 'representative'
           and not req_rep.issubset(r.keys())]
    check('T6a: representative rows carry the full schema', not bad,
          f'{len(bad)} short rows')
    check('T6b: manifest has a representative + diagnostic cohort',
          any(r['cohort'] == 'representative' for r in man) and
          any(r['cohort'] != 'representative' for r in man))


def T7_preflight_record():
    p = os.path.join(HERE, 'preflight_record.json')
    if not os.path.exists(p):
        check('T7: preflight_record.json present + all PASS', False,
              'run preflight.py first')
        return
    d = json.load(open(p))
    check('T7: all preflight checks PASS', d.get('all_pass') is True,
          f'failed {[c["id"] for c in d["checks"] if not c["pass"]]}')


def T8_pool_census():
    """the eligible pool must itself be LEP-balanced (so the 25% quota
    is achievable and the mix is not an artifact of pool skew)."""
    pool, _ = sample.eligible_pool()
    got = Counter(c['lep_code'] for c in pool)
    n = sum(got.values())
    pfr = {L: round(got.get(L, 0) / n, 4) for L in LEPS}
    check('T8: eligible pool is LEP-balanced (+/-0.03 of 25%)',
          all(abs(got.get(L, 0) / n - 0.25) <= 0.03 for L in LEPS),
          f'pool fracs {pfr}')


def main():
    man = load_manifest()
    T1_lep_equal_quota(man)
    T2_lexicographic_trap(man)
    T3_independence(man)
    T4_weights_and_pi(man)
    T5_determinism()
    T6_schema(man)
    T7_preflight_record()
    T8_pool_census()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f'\n{passed}/{len(RESULTS)} regression tests pass.')
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
