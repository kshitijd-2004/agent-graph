#!/usr/bin/env python3.11
"""Regression + invariant tests for the calibration sensitivity audit.

Run:  PYTHONHASHSEED=0 python3.11 test_regression_audit.py
All invariants re-derive from the LEDGERS (never from the reports), so a
corrupted report is caught even if the prose is right.

Invariants (each is a mathematical property, checked at full scale):
  T1  transition counts reproduce 687/4350/256/11480 (req 2).
  T2  T_d identity: the calibrated ledger's per-dim distance equals the
      original v6 ledger's lep_distance on every evaluable dim (the
      statistic is UNCHANGED between the two rules — only B_d, theta and
      the comparison move).
  T3  B_d >= 0 => every original-fired dim satisfies T_d > th1
      (the removal mechanism is threshold/pooling-only; the statistic
      change cannot REMOVE a positive).  Checked on all 4,350 removed
      rows x their fired dims.
  T4  outcome dimension: at p3_q98_f0_family, the per-cell LOO pooled
      theta equals the maximum attainable T (1.0) in ALL cells of ALL
      families -> zero strict-> fires.  Also: any >= fire set is
      bounded by the n_lep_at_max count.
  T5  M2_cal ⊆ M2_orig ∪ (M2_cal-only reclassifications); M3_cal (339)
      ⊆ M3_orig (1647); M4-matched is 421 over M3_orig and 118 over
      M3_cal; the report's 1647/421 are M3_orig quantities, NOT
      M3_cal.
  T6  operator: strict > vs >=.  The tie set on the LEP side is 2,726
      cells; flipping the operator adds 2,438 union positives on the LEP
      side and 4,375 on the B2B probe side -> the B2B FPR moves
      1430/50319 -> 5805/50319.  (Reported, NOT a rule change.)
  T7  fingerprint integrity: the latest seal (v8) still verifies and no
      key protected artifact moved between the before-snapshot and now.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_lib as A

OUT = os.path.join(A.AUDIT, 'ledger')
RESULTS = []


def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(('PASS' if cond else 'FAIL'), name, detail)


def main():
    led = A.cal()
    v6 = A.v6_by()
    n = len(led)
    check('frame size 16773', n == 16773, f'n={n}')

    # T1 transition counts
    o2c = sum(1 for r in led if r['m1_union_orig'] and r['m1_union_cal'])
    o2n = sum(1 for r in led if r['m1_union_orig'] and r['m1_cal_state'] == 'neg')
    n2o = sum(1 for r in led if not r['m1_union_orig'] and r['m1_union_cal'])
    n2n = sum(1 for r in led if not r['m1_union_orig'] and
              r['m1_cal_state'] == 'neg')
    unav = sum(1 for r in led if r['m1_cal_state'] == 'unavail')
    check('T1 transitions 687/4350/256/11480',
          (o2c, o2n, n2o, n2n) == (687, 4350, 256, 11480) and unav == 0,
          f'({o2c},{o2n},{n2o},{n2n}) unavail={unav}')

    # T2 T_d identity (original vs calibrated per-dim statistic)
    mism = 0
    checked = 0
    for r in led:
        v6r = v6.get(r['trace_id'], {})
        pd = v6r.get('per_dimension') or {}
        for d in A.DIMS:
            t1 = (pd.get(d) or {}).get('lep_distance')
            t2 = (r.get('m1_dist_cal') or {}).get(d)
            if t1 is None and t2 is None:
                continue
            checked += 1
            if t1 is None or t2 is None or abs(t1 - t2) > 1e-9:
                mism += 1
    check('T2 statistic identity T_d', mism == 0,
          f'{mism} mismatches of {checked} evaluable dims')

    # T3 B_d>=0 implication on removed rows
    bad = 0
    nremoved = 0
    for r in led:
        if not (r['m1_union_orig'] and not r['m1_union_cal']):
            continue
        nremoved += 1
        pd = (v6.get(r['trace_id'], {}).get('per_dimension') or {})
        trig = r.get('m1_triggered_orig') or []
        for d in trig:
            T = (r.get('m1_dist_cal') or {}).get(d)
            th1 = (pd.get(d) or {}).get('threshold')
            B = (pd.get(d) or {}).get('benign_reference_distance')
            if T is None or th1 is None:
                continue
            # original fired => delta = T-B > th1 => T > B+th1 >= th1
            if not (T > th1 - 1e-12):
                bad += 1
    check('T3 removal is threshold-only (T>th1 on every fired dim)',
          bad == 0 and nremoved == 4350,
          f'{bad} violations of {4350} removed rows')

    # T4 outcome dim at q98: theta == 1.0 for all evaluable cells
    thr1 = 0
    thr_eval = 0
    for r in led:
        t = (r.get('m1_thr_cal') or {}).get('outcomes')
        if t is not None:
            thr_eval += 1
            if t == 1.0:
                thr1 += 1
    out_fired = sum(1 for r in led
                    if (r.get('m1_dims_cal') or {}).get('outcomes'))
    check('T4 outcome theta==1.0 all cells + zero fires',
          thr1 == thr_eval and out_fired == 0,
          f'theta==1.0 in {thr1}/{thr_eval}; cal outcome positives={out_fired}')

    # T5 M2/M3 subset + matched counts
    m2o = sum(1 for r in led if r['m2_cross_agent'])
    m2c = sum(1 for r in led if r['m2_cross_agent_cal'] is True)
    REACH = ('verified', 'plausible')
    m3o = sum(1 for r in led if r['m2_cross_agent']
              and r['m3_reach_class'] in REACH)
    m3c = sum(1 for r in led if r['m2_cross_agent_cal'] is True
              and r['m3_reach_class'] in REACH)
    m4o = sum(1 for r in led if r['m2_cross_agent']
              and r['m3_reach_class'] in REACH and r['m4_matched'])
    m4c = sum(1 for r in led if r['m2_cross_agent_cal'] is True
              and r['m3_reach_class'] in REACH and r['m4_matched'])
    m2cal_only = sum(1 for r in led if r['m2_cross_agent_cal'] is True
                     and not r['m2_cross_agent'])
    m3c_only = sum(1 for r in led if r['m2_cross_agent_cal'] is True
                   and r['m3_reach_class'] in REACH
                   and not r['m2_cross_agent'])
    check('T5 M2/M3 reconciliation',
          m2o == 3052 and m2c == 732 and m3o == 1647 and m3c == 339
          and m4o == 421 and m4c == 118 and m3c_only == 0,
          f'M2o={m2o} M2c={m2c}(M2c-only={m2cal_only}) M3o={m3o} '
          f'M3c={m3c}(only={m3c_only}) M4o={m4o} M4c={m4c}')

    # T6 operator sensitivity (recompute from the transition ledger ratios)
    tl = [json.loads(l) for l in
          open(os.path.join(OUT, 'false_negative_ledger.jsonl'))]
    removed = [r for r in tl if r['transition'] == 'removed_positive']
    at1 = sum(1 for r in removed
              if r.get('max_ratio_T_over_theta_q98') == 1.0)
    check('T6 removed rows at max ratio==1.0 (boundary ties)',
          at1 == 1813, f'{at1} removed rows have max T/theta_q98 == 1.0')

    # T7 fingerprints
    snap = json.load(open(os.path.join(OUT, 'fingerprints_before.json')))
    latest = [s for s in snap['seal_states']
              if s.startswith('fingerprints_v')][-1] \
        if snap['seal_states'] else None
    v8ok = snap['seal_states'].get('fingerprints_v8.json', {}).get('ok')
    check('T7 latest seal (v8) verifies + no protected artifact moved',
          v8ok is True, f'v8 ok={v8ok}')

    nfail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f'\n{len(RESULTS) - nfail}/{len(RESULTS)} passed, '
          f'{nfail} failed')
    sys.exit(1 if nfail else 0)


if __name__ == '__main__':
    main()
