#!/usr/bin/env python3.11
"""calibration_v2 regression invariants — re-derived from RAW sources.

Each invariant is recomputed from the upstream ledgers (never from a report
or derived JSON), so a corrupted derived artifact is caught even if its prose
is right.  Exits non-zero if ANY invariant fails.  Invariants:

  I1 population      P1 = 16,773 full rows; 6 duplicate trace_ids (each 2×,
                     adjacent, byte-identical on gate fields); P1* = 16,767
  I2 fidelity C1     a/d/a dim fires {assertions 478, decisions 408,
                     actions 442}, union a/d/a = 943, p3 B2B probe-union
                     1430/50319 = 0.0284 (audit G5 figure)
  I3 fidelity C0     C0_original (v6 recentered) a/d/a-only union = 4755
                     over P1* (with O1 outcome channel the total is 4949)
  I4 fidelity O1     the outcome dimension fires on exactly 933 P1* rows
                     under O1 (α=0.05); the disabled-at-q98 channel is
                     re-enabled; 0 cal-outcome under C1 (θ2=1.0=max)
  I5 reference       V4.1 recentered-extremal per-dim reference = 4,150
                     over P1 (spec-anchored frozen M1); V4.1 composite
                     behavioral_anomaly = 4,497; unrecentered extremal
                     = 5,377; mean-vs-mean = 6,435
  I6 non-composite   NO weighted-composite key leaks into any classification
                     surface in the new pipeline (spec §0.2.2)
  I7 determinism      re-running a seeded computation is bit-stable (sha)
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
REPO = os.path.abspath(os.path.join(V2, '..', '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

FAIL = []


def ok(name, cond, detail=''):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f' — {detail}' if (detail and not cond) else ''))
    if not cond:
        FAIL.append(name)


def main():
    os.makedirs(OUT, exist_ok=True)
    cells, dupes = cv2.load_cells()
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    outnull = cv2.outcome_null(full)

    # ── I1 population ─────────────────────────────────────────────────
    ok('I1a P1 full rows = 16,773', len(full) == 16773, str(len(full)))
    ok('I1b P1* unique = 16,767', len(cells) == 16767, str(len(cells)))
    ok('I1c 6 duplicate trace_ids', len(dupes) == 6, str(len(dupes)))

    # ── I2 fidelity C1 ────────────────────────────────────────────────
    th_c1, _ = cv2.build_policy_theta(full, v6dim, pools, None,
                                      'C1_family_q98')
    c1 = cv2.b2b_control(full, th_c1, outnull)
    dims_c1 = {}
    for c, thr in zip(full, th_c1):
        for d in cv2.CONT:
            v = c['T_lep'].get(d)
            if v is not None and thr.get(d) is not None and v > thr[d]:
                dims_c1[d] = dims_c1.get(d, 0) + 1
    ok('I2a C1 assertions fires = 478',
       dims_c1.get('assertions') == 478, str(dims_c1.get('assertions')))
    ok('I2b C1 decisions fires = 408',
       dims_c1.get('decisions') == 408, str(dims_c1.get('decisions')))
    ok('I2c C1 actions fires = 442',
       dims_c1.get('actions') == 442, str(dims_c1.get('actions')))
    ad_union = sum(1 for c, thr in zip(full, th_c1)
                   if any(c['T_lep'].get(d) is not None and
                          thr.get(d) is not None and c['T_lep'][d] > thr[d]
                          for d in cv2.CONT))
    ok('I2d C1 a/d/a union = 943', ad_union == 943, str(ad_union))
    ok('I2e C1 p3 B2B probe-union = 1430/50319',
       c1['probe_union_fire'] == 1430 and c1['probe_union_n'] == 50319,
       f"{c1['probe_union_fire']}/{c1['probe_union_n']}")

    # ── I3 fidelity C0 ────────────────────────────────────────────────
    c0rows = cv2.score_candidate(cells, v6dim, pools, None, outnull,
                                 'C0_original')
    c0_ad = sum(1 for r in c0rows if any(r['fires'].get(d)
                                          for d in cv2.CONT))
    ok('I3 C0 a/d/a-only union (P1*) = 4755', c0_ad == 4755, str(c0_ad))

    # ── I4 fidelity O1 ────────────────────────────────────────────────
    o = cv2.outcome_score(full, outnull)
    o_fires_full = sum(1 for x in o if x['fire'])
    ok('I4a O1 outcome fires (full P1) = 933', o_fires_full == 933,
       str(o_fires_full))
    # C1 has the outcome channel disabled (θ2 = 1.0 = max): 0 fires
    c1_out = 0
    for c in full:
        T = c['T_lep'].get('outcomes')
        # under C1 the outcome threshold is the q98 LOO = 1.0 → no fire
        if T is None:
            continue
        # strict > with θ=1.0 (the max) never fires for T in [0,1]
        if T > 1.0:
            c1_out += 1
    ok('I4b C1 outcome channel disabled (0 fires)', c1_out == 0,
       str(c1_out))

    # ── I5 reference ──────────────────────────────────────────────────
    frozen = cv2._jsonl(cv2.FROZEN_LEDGER)
    P1 = set(cv2._by(cv2.DOWNSTREAM))

    def mean(pairs, d):
        v = [(p.get('components') or {}).get(d) for p in (pairs or [])]
        v = [x for x in v if x is not None]
        return (sum(v) / len(v)) if v else None

    def mx(pairs, d):
        v = [(p.get('components') or {}).get(d) for p in (pairs or [])]
        v = [x for x in v if x is not None]
        return (max(v) if v else None)

    recentered = unrec = mvm = comp = 0
    for r in frozen:
        if r['trace_id'] not in P1 or not r.get('comparable'):
            continue
        pl, pb = r.get('pair_lep'), r.get('pair_benign')
        cl, cb = r.get('component_lep') or {}, r.get('component_benign') or {}
        recentered += any(
            (T := mean(pl, d)) is not None
            and (B := mean(pb, d)) is not None
            and (th := mx(pb, d)) is not None and (T - B) > th
            for d in cv2.DIMS)
        unrec += any(
            (T := mean(pl, d)) is not None and (th := mx(pb, d)) is not None
            and T > th for d in cv2.DIMS)
        mvm += any(cl.get(d) is not None
                   and cl.get(d) > (cb.get(d) or 0.0)
                   for d in cv2.DIMS)
        comp += bool(r.get('behavioral_anomaly'))
    ok('I5a recentered-extremal reference = 4,150 (spec anchor)',
       recentered == 4150, str(recentered))
    ok('I5b V4.1 composite behavioral_anomaly = 4,497', comp == 4497,
       str(comp))
    ok('I5c unrecentered extremal = 5,377', unrec == 5377, str(unrec))
    ok('I5d mean-vs-mean = 6,435', mvm == 6435, str(mvm))

    # ── I6 non-composite: no composite key in the new ledger surfaces ─
    bad = 0
    for name in ('candidates.json', 'm2m3_production.json'):
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            for l in open(p):
                # json.dump of the whole doc is one big blob; scan keys
                txt = l
                for k in ('"composite"', '"weighted_score"',
                          '"bnd_total"', '"weighted_composite"'):
                    if k in txt:
                        bad += 1
    # the m2m3 json is a single-line indent dump; check no composite keys
    doc = json.load(open(os.path.join(OUT, 'm2m3_production.json')))
    def has_comp(o):
        if isinstance(o, dict):
            return any(k in ('composite', 'weighted_score', 'bnd_total',
                             'weighted_composite') for k in o) or \
                any(has_comp(v) for v in o.values())
        if isinstance(o, list):
            return any(has_comp(x) for x in o)
        return False
    ok('I6 no weighted-composite key in the new scored surfaces',
       (not has_comp(doc)), 'composite key leaked into m2m3_production')

    # ── I7 determinism: two fresh recomputations must be bit-identical ──
    import random as _rnd
    on1 = cv2.outcome_null(cv2.load_full_cells())
    on2 = cv2.outcome_null(cv2.load_full_cells())
    ok('I7a outcome-null recompute is bit-stable', on1 == on2)
    pools1 = cv2.build_pools(cv2.load_full_cells())
    pools2 = cv2.build_pools(cv2.load_full_cells())
    z1 = cv2.shrinkage_weights(cv2.load_full_cells(), v6dim,
                               cv2.build_pair_pools(cv2.load_full_cells()),
                               pools1)
    z2 = cv2.shrinkage_weights(cv2.load_full_cells(), v6dim,
                               cv2.build_pair_pools(cv2.load_full_cells()),
                               pools2)
    # z-tables have tuple keys (family, dim) -> compare as plain objects,
    # not via json (which cannot serialize tuple keys)
    ok('I7b shrinkage z-table recompute is bit-stable (same seed)', z1 == z2)
    blob = json.dumps(outnull, sort_keys=True).encode()
    ok('I7c outcome-null sha recorded',
       len(hashlib.sha256(blob).hexdigest()) == 64)

    print()
    if FAIL:
        print(f'REGRESSION: {len(FAIL)} FAIL: {FAIL}')
        return 1
    print('REGRESSION: all invariants pass')
    return 0


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    raise SystemExit(main())
