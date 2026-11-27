#!/usr/bin/env python3.11
"""Confirmatory evaluation of the FROZEN candidate (single consumption).

Consumes the confirmatory partition (agentprop-cv2-confirm-202702, 188
un-inspected fixtures) against the FROZEN candidate
(ledger/frozen_candidate.json + the frozen_pin_sha256 sidecar) and the
PRIMARY reference (frozen V4.1 RECENTRED-extremal per-dimension union —
the spec-anchored 4,150 construct, construct-matched, independent), and
evaluates the pre-registered gate battery.  Selection happened on the dev
partition + the pre-registered admissibility gates BEFORE this
consumption; confirmatory numbers are reported, never re-tuned.

Gate verdicts use strict accounting: a gate passes only when its `pass`
field is True; null (pending, e.g. V8 integrity) is NEVER counted as a
pass.  V6 (synthetic G2/G3 suites) is read from the sealed
out/synthetic_report.json (G2_invariance_pass_rate, G3_sensitivity_
flip_rate — on-disk evidence, not asserted).  V8 is filled by the
integrity step (seal v8 + before/after fingerprints + write-guard), and
remains pending here.
Writes ledger/validation_report.json (the current, reproducible report;
a prior, stale report generated against pre-final inputs was overwritten
— see its disclosed history in INDEPENDENT_VALIDATION_RESULTS.md).
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.abspath(os.path.join(HERE, '..'))
FINAL = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(V2, 'ledger')
sys.path.insert(0, V2)
import cv2  # noqa: E402

# PRIMARY reference = the recentered V4.1 per-dim union (v41_union);
# the composite (v41_composite_secondary) is disclosed, never the gate.
PRIMARY_REF_KEY = 'v41_union'


def main():
    os.makedirs(OUT, exist_ok=True)
    frozen = json.load(open(os.path.join(OUT, 'frozen_candidate.json')))
    pin_path = os.path.join(OUT, 'frozen_pin_sha256.txt')
    pin = None
    if os.path.exists(pin_path):
        pin = open(pin_path).read().strip()
    pol = frozen['selected_policy']
    split = json.load(open(os.path.join(OUT, 'partition_split.json')))
    refrep = json.load(open(os.path.join(OUT, 'reference_surface.json')))
    ref = {'union': refrep[PRIMARY_REF_KEY],
           'per_dim': refrep['v41_per_dim']}

    cells, _ = cv2.load_cells()
    by_tid = {c['trace_id']: c for c in cells}
    full = cv2.load_full_cells()
    v6dim = cv2.load_v6_dim()
    pools = cv2.build_pools(full)
    pair_pools = cv2.build_pair_pools(full)
    ztab = cv2.shrinkage_weights(full, v6dim, pair_pools, pools)
    outnull = cv2.outcome_null(full)

    # single confirmatory consumption
    conf_tids = [t for t in split['confirm_rows'] if t in by_tid]
    cells_c = [by_tid[t] for t in conf_tids]
    rows = cv2.score_candidate(cells_c, v6dim, pools, ztab, outnull, pol)
    cm = cv2.confusion(rows, ref['union'])
    n_pos = cm['TP'] + cm['FN']

    # B2B control at the execution level (M1 probe-union; the pre-
    # registered V4 for the whole-trace M1 rule)
    th, _ = cv2.build_policy_theta(full, v6dim, pools, ztab, pol)
    b2b = cv2.b2b_control(full, th, outnull)
    m1_v4 = (b2b['probe_union_fpr'] or 1.0) <= 0.05
    # M2 checkpoint execution-level B2B (row-OR) from the production ledger
    mp = json.load(open(os.path.join(OUT, 'm2m3_production.json')))
    m2_b2b = mp['m2_dimension_specific']['cross_agent_ckpt']
    m2_v4 = (m2_b2b.get('b2b_execution_row_or_fpr')
             or m2_b2b.get('row_or_fpr') or 1.0) <= 0.05

    # V5 coverage vs the v6 frozen baseline
    base = {'assertions': 12707, 'decisions': 12779, 'outcomes': 12068}
    full_rows = cv2.score_candidate(cells, v6dim, pools, ztab, outnull,
                                    pol)
    eval_counts = Counter()
    for r in full_rows:
        for d in cv2.DIMS:
            if r['fires'].get(d) is not None:
                eval_counts[d] += 1
    v5 = {d: eval_counts.get(d, 0) >= base.get(d, 0) for d in base}

    # V7 no disabled dimension: outcome O1 non-vacuous on confirm + B2B
    o_fires_conf = sum(1 for r in rows if r['fires'].get('outcomes'))
    o_ev = sum(1 for r in rows
               if r['fires'].get('outcomes') is not None)
    v7 = (o_fires_conf >= 1 and o_ev >= 1
          and b2b['outcome_probe_fpr'] is not None)

    # V6: read the sealed synthetic-suite report from disk (on-disk
    # evidence; G2 invariance + G3 controlled-sensitivity gates)
    syn = json.load(open(os.path.join(FINAL, 'out', 'synthetic_report.json')))
    gates = syn.get('gates') or {}
    v6 = bool(gates.get('G2_gate_pass') and gates.get('G3_gate_pass'))

    gate_dict = {
        'V1_union_F1_ge_0.65': {'value': cm['f1'],
                                 'pass': (cm['f1'] or 0) >= 0.65},
        'V2_union_precision_ge_0.70': {'value': cm['precision'],
                                       'pass': ((cm['precision'] or 0)
                                                 >= 0.70)},
        'V3_union_recall_ge_0.70': {'value': cm['recall'],
                                    'n_positive_support': n_pos,
                                    'support_ok': n_pos >= 30,
                                    'pass': ((cm['recall'] or 0) >= 0.70
                                             and n_pos >= 30)},
        'V4_B2B_exec_fpr_le_0.05': {'m1_probe_union':
                                    b2b['probe_union_fpr'],
                                    'm2_checkpoint_row_or':
                                    m2_b2b.get('b2b_execution_row_or_fpr')
                                    if m2_b2b.get(
                                        'b2b_execution_row_or_fpr')
                                    is not None else m2_b2b.get('row_or_fpr'),
                                    'pass': bool(m1_v4 and m2_v4)},
        'V5_coverage_ge_v6': {'eval': dict(eval_counts),
                              'baseline': base,
                              'per_dim': v5,
                              'pass': bool(all(v5.values()))},
        'V6_suites_green': {'G2_pass': gates.get('G2_gate_pass'),
                            'G3_pass': gates.get('G3_gate_pass'),
                            'G2_rate': gates.get('G2_invariance_pass_rate'),
                            'G3_rate': gates.get('G3_sensitivity_flip_rate'),
                            'evidence': 'out/synthetic_report.json '
                                        '(sealed, v6/candspec suites)',
                            'pass': v6},
        'V7_no_disabled_dimension': {'outcome_conf_fires': o_fires_conf,
                                     'outcome_conf_eval': o_ev,
                                     'outcome_b2b_fpr':
                                     b2b['outcome_probe_fpr'],
                                     'pass': bool(v7)},
        'V8_integrity': {'value': None, 'pass': None,
                         'note': 'pending the integrity verification step '
                                 '(seal v8 + before/after fingerprints + '
                                 'write-guard) — INTEGRITY_VERIFICATION.md'},
    }
    passed = sum(1 for g in gate_dict.values() if g.get('pass') is True)
    rep = {
        'frozen_candidate': pol,
        'frozen_pin_sha256': pin,
        'primary_reference': PRIMARY_REF_KEY
        + ' (V4.1 RECENTRED-extremal per-dim union = spec-anchored '
        '4,150; construct-matched, independent; the composite 4,497 is '
        'disclosed secondary only)',
        'confirmatory_rows': len(conf_tids),
        'confusion': cm,
        'union_ci_fixture_cluster': cv2.union_rate_ci(rows),
        'wilson95_union_rate': cv2.wilson(cm['TP'] + cm['FP'],
                                          cm['n_eval']),
        'gate_verdicts': gate_dict,
        'n_gates_passed': passed, 'n_gates': len(gate_dict),
        'accounting': 'a gate passes ONLY when pass is True; null '
                      '(pending) is never counted as a pass',
        'all_pass': all(g.get('pass') is True
                         for g in gate_dict.values()),
    }
    json.dump(rep, open(os.path.join(OUT, 'validation_report.json'), 'w'),
              indent=1)
    for g, v in gate_dict.items():
        print(f"  {g}: {v.get('pass')}  "
              f"(V1 F1={cm['f1']:.3f} P={cm['precision']:.3f} "
              f"R={cm['recall']:.3f})" if 'V' in g and g[1].isdigit()
              else f'  {g}: {v.get("pass")}')
    print()
    print('GATES PASSED (strict):', passed, '/', len(gate_dict),
          '| ALL:', rep['all_pass'])


if __name__ == '__main__':
    if 'PYTHONHASHSEED' not in os.environ:
        os.environ['PYTHONHASHSEED'] = '0'
        os.execve(sys.executable, [sys.executable] + sys.argv,
                  os.environ)
    main()
