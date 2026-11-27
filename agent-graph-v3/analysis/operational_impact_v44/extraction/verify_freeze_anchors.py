#!/usr/bin/env python3.11
"""Verify the V4.4 freeze anchors before any operational analysis proceeds.

Asserts every expected anchor from the operational-impact contract against
the frozen per-execution file, and re-runs the official V4.4 freeze verifier.
Exit 0 = all anchors hold; exit 1 = anchor violation (analysis must not start).
"""
import json
import os
import subprocess
import sys

ROOT = '/u/kdhande/agent-graph/agent-graph-v3'
PEREXEC = os.path.join(
    ROOT, 'analysis/behavioral_divergence_final/calibration_v3/out_v44/final/per_execution_v44.jsonl')


def main():
    rows = [json.loads(l) for l in open(PEREXEC) if l.strip()]

    def n(pred):
        return sum(1 for r in rows if pred(r))

    m1 = lambda r: r['M1_selected']['positive']
    m2 = lambda r: r['M2_selected']['positive']
    m3 = lambda r: r['M3_selected']['matched_degraded_observed']  # OBSERVED class (frozen five-way, dep flag off)
    m3f = lambda r: r['M3_selected']['frozen_matched_degraded']    # frozen M4-supported object (internal breakdown)
    ge = lambda r: r['measurement_availability']['gradeable']

    anchors = {
        'comparable_executions': (len(rows), 16767),
        'fixtures': (len(set(r['fixture_id'] for r in rows)), 903),
        'M1': (n(m1), 5984),
        'M2': (n(m2), 4123),
        'M3_observed': (n(m3), 1992),
        'M1&M2': (n(lambda r: m1(r) and m2(r)), 2831),
        'M1&M3_obs': (n(lambda r: m1(r) and m3(r)), 1293),
        'M2&M3_obs': (n(lambda r: m2(r) and m3(r)), 948),
        'M1&M2&M3_obs': (n(lambda r: m1(r) and m2(r) and m3(r)), 840),
        'grading_eligible': (n(ge), 12625),
        # cohort partition
        'cohort_neither': (n(lambda r: not m1(r) and not m2(r)), 9491),
        'cohort_m1_only': (n(lambda r: m1(r) and not m2(r)), 3153),
        'cohort_m2_only': (n(lambda r: not m1(r) and m2(r)), 1292),
        'cohort_both': (n(lambda r: m1(r) and m2(r)), 2831),
        # derived denominators
        'M2_gradeable': (n(lambda r: m2(r) and ge(r)), 3545),   # 4123 - 578
        'M1_gradeable': (n(lambda r: m1(r) and ge(r)), 4887),
        'M2&M3_gradeable': (n(lambda r: m2(r) and m3(r) and ge(r)), 948),
        'M1&M3_gradeable': (n(lambda r: m1(r) and m3(r) and ge(r)), 1293),
    }
    bad = []
    print('ANCHOR VERIFICATION (frozen per_execution_v44.jsonl):')
    for k, (got, exp) in anchors.items():
        ok = got == exp
        print(f'  {k:28s} got={got:>7d} exp={exp:>7d} {"OK" if ok else "*** MISMATCH ***"}')
        if not ok:
            bad.append(k)
    # headline derived rates (informational)
    m2g = n(lambda r: m2(r) and ge(r))
    m1g = n(lambda r: m1(r) and ge(r))
    m1m3g = n(lambda r: m1(r) and m3(r) and ge(r))
    m2m3g = n(lambda r: m2(r) and m3(r) and ge(r))
    print()
    print('derived: task resilience      = 1 - %d/%d = %.4f' % (m1m3g, m1g, 1 - m1m3g / m1g))
    print('derived: downstream resilience= 1 - %d/%d = %.4f (anchor: 1 - 948/(4123-578) = %.4f)'
          % (m2m3g, m2g, 1 - m2m3g / m2g, 1 - 948 / (4123 - 578)))
    # official freeze verifier
    print()
    v = os.path.join(ROOT, 'analysis/behavioral_divergence_final/v44_freeze/verify.py')
    r = subprocess.run([sys.executable, v], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        bad.append('v44_freeze_verify')
    if bad:
        print('ANCHOR VIOLATIONS: %s' % bad)
        sys.exit(1)
    print('ALL ANCHORS VERIFIED — safe to proceed.')


if __name__ == '__main__':
    main()
